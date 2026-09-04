"""Backup storage library: authenticated encryption, integrity and retention.

This module implements the long-term backup format of HR Manager (roadmap
phase 7). It is pure storage/security logic — no FastAPI or database imports —
so the same code runs inside the API process, the backup CLI and the test
suite.

Security model
--------------
* Backups contain personal data and are treated as a secret asset. Only
  ciphertext (plus a manifest header and a checksum file) ever reaches
  long-term storage; the encryption key is a 32-byte AES-256-GCM key passed
  via environment/secret storage, never written to disk by this module.
* Encryption is authenticated: the header is bound as AAD, the GCM tag and
  the payload SHA-256 are verified before any restored byte is used. The
  ciphertext file is additionally protected by a detached SHA-256 checksum
  file so storage-level corruption is detectable without the key.
* Key rotation is supported: backups record the key id in the header and
  decryption tries the current key first, then the legacy keys. Losing every
  key for a backup makes restore impossible — this is explicit, not silent.
* Retention never removes the newest ``min_copies`` backups and only runs
  after a successful backup, so a failed run cannot shrink the archive.

File format (``.pgdump.enc``)
-----------------------------
``[MAGIC "HRMBCK1\\n" (8 bytes)][header_len u64 LE][header JSON][ciphertext||tag]``

Header JSON fields: ``v`` (format version), ``key_id``, ``created_at``
(ISO-8601 UTC), ``cipher`` (always ``aes-256-gcm``), ``nonce`` (base64),
``plain_size``, ``plain_sha256``. The serialized header is the GCM AAD.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import struct
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# --- Format constants ---------------------------------------------------------

MAGIC = b"HRMBCK1\n"
HEADER_LEN_STRUCT = struct.Struct("<Q")
FORMAT_VERSION = 1
CIPHER_NAME = "aes-256-gcm"
KEY_BYTES = 32
NONCE_BYTES = 12

# Name pattern of backup files published by this module. Retention and
# freshness only ever consider files matching it, so foreign files in the
# backup directory are never deleted.
BACKUP_FILE_RE = re.compile(
    r"^hr-manager-(?P<ts>\d{8}T\d{6}Z)-(?P<run_id>[0-9a-f]{8})\.pgdump\.enc$"
)

# --- Errors -------------------------------------------------------------------


class BackupError(Exception):
    """Base class for backup failures. Messages never contain secrets."""


class BackupKeyError(BackupError):
    """Key material is missing, weak or does not match the backup."""


class BackupIntegrityError(BackupError):
    """Ciphertext, header or checksum failed authentication/verification."""


class BackupFormatError(BackupError):
    """The backup file is not a recognizable HR Manager backup."""


# --- Key management -----------------------------------------------------------


def validate_key_material(raw: str, *, label: str) -> bytes:
    """Decode and validate a base64-encoded 256-bit key.

    Never includes the key value in messages. Rejects empty, all-zero and
    wrongly-sized material so a misconfigured secret fails fast.
    """
    if not raw:
        raise BackupKeyError(f"{label} is not set (provide a base64-encoded 32-byte key)")
    try:
        key = base64.b64decode(raw.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise BackupKeyError(f"{label} is not valid base64") from exc
    if len(key) != KEY_BYTES:
        raise BackupKeyError(f"{label} must decode to exactly {KEY_BYTES} bytes")
    if not any(key):
        raise BackupKeyError(f"{label} must not be all-zero")
    return key


def load_keys_from_env(env: dict[str, str] | None = None) -> dict[str, bytes]:
    """Load the key ring from ``BACKUP_KEY_ID``/``BACKUP_ENC_KEY`` and
    ``BACKUP_LEGACY_KEYS`` (JSON object ``{key_id: base64 key}``)."""
    environment = os.environ if env is None else env
    key_id = (environment.get("BACKUP_KEY_ID") or "").strip()
    primary_b64 = environment.get("BACKUP_ENC_KEY") or ""
    keys: dict[str, bytes] = {}
    if key_id or primary_b64:
        if not key_id:
            raise BackupKeyError("BACKUP_ENC_KEY is set but BACKUP_KEY_ID is not")
        if not primary_b64:
            raise BackupKeyError("BACKUP_KEY_ID is set but BACKUP_ENC_KEY is not")
        keys[key_id] = validate_key_material(primary_b64, label="BACKUP_ENC_KEY")
    legacy_raw = environment.get("BACKUP_LEGACY_KEYS") or ""
    if legacy_raw:
        try:
            legacy = json.loads(legacy_raw)
        except ValueError as exc:
            raise BackupKeyError("BACKUP_LEGACY_KEYS is not valid JSON") from exc
        if not isinstance(legacy, dict):
            raise BackupKeyError("BACKUP_LEGACY_KEYS must be a JSON object")
        for legacy_id, legacy_b64 in legacy.items():
            if not isinstance(legacy_id, str) or not isinstance(legacy_b64, str):
                raise BackupKeyError("BACKUP_LEGACY_KEYS must map key ids to base64 strings")
            if legacy_id == key_id:
                continue
            keys[legacy_id] = validate_key_material(legacy_b64, label=f"legacy key {legacy_id!r}")
    return keys


# --- Encrypt / decrypt --------------------------------------------------------


def _header_bytes(header: dict[str, Any]) -> bytes:
    return json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")


# Fixed-width placeholders keep the patched header byte-identical in length.
_PLAIN_SIZE_WIDTH = 20
CHUNK_SIZE = 1024 * 1024


def encrypt_stream(
    source: BinaryIO,
    destination: BinaryIO,
    *,
    key: bytes,
    key_id: str,
    created_at: datetime,
) -> tuple[int, str]:
    """Encrypt ``source`` to ``destination`` with AES-256-GCM.

    Returns ``(cipher_size_with_header, plain_sha256)``. The payload is
    streamed in ``CHUNK_SIZE`` plaintext chunks; each chunk becomes an
    independent authenticated record (ciphertext + 16-byte GCM tag), so
    memory stays bounded for arbitrarily large dumps. Every record is
    authenticated against the canonical placeholder header (the final
    size/hash are unknown until the payload pass completes; the decryptor
    reconstructs the same placeholder from the patched header), which binds
    key id, nonce and metadata to the ciphertext. ``plain_size`` is stored
    as a fixed-width zero-padded string, which lets the real values be
    patched into the placeholder header after the payload pass without
    moving data.
    """
    nonce = os.urandom(NONCE_BYTES)
    placeholder = {
        "v": FORMAT_VERSION,
        "key_id": key_id,
        "created_at": created_at.astimezone(UTC).isoformat(),
        "cipher": CIPHER_NAME,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "chunk_size": CHUNK_SIZE,
        "plain_size": "0" * _PLAIN_SIZE_WIDTH,
        "plain_sha256": "0" * 64,
    }
    header_bytes = _header_bytes(placeholder)
    header_offset = len(MAGIC) + HEADER_LEN_STRUCT.size
    destination.write(MAGIC)
    destination.write(HEADER_LEN_STRUCT.pack(len(header_bytes)))
    destination.write(header_bytes)

    encryptor = AESGCM(key)
    digester = hashlib.sha256()
    total = 0
    records = 0
    for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
        digester.update(chunk)
        total += len(chunk)
        destination.write(encryptor.encrypt(nonce, chunk, header_bytes))
        records += 1

    final_header = {
        **placeholder,
        "plain_size": f"{total:0{_PLAIN_SIZE_WIDTH}d}",
        "plain_sha256": digester.hexdigest(),
    }
    final_header_bytes = _header_bytes(final_header)
    if len(final_header_bytes) != len(header_bytes):
        raise BackupFormatError("internal error: patched backup header changed size")
    destination.seek(header_offset)
    destination.write(final_header_bytes)
    return header_offset + len(final_header_bytes) + total + records * 16, digester.hexdigest()


def decrypt_to_stream(
    source: BinaryIO,
    destination: BinaryIO,
    *,
    keys: dict[str, bytes],
    expected_sha256: str | None = None,
) -> int:
    """Decrypt ``source`` to ``destination``; verify GCM auth and SHA-256.

    Returns the plaintext size. Raises ``BackupIntegrityError`` on any
    authentication or hash mismatch and ``BackupKeyError`` when the key id
    from the header is not in the key ring.
    """
    magic = source.read(len(MAGIC))
    if magic != MAGIC:
        raise BackupFormatError("not an HR Manager encrypted backup (bad magic)")
    (header_len,) = HEADER_LEN_STRUCT.unpack(source.read(HEADER_LEN_STRUCT.size))
    if header_len > 16 * 1024:
        raise BackupFormatError("backup header is unreasonably large")
    raw_header = source.read(header_len)
    try:
        header = json.loads(raw_header.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BackupFormatError("backup header is not valid JSON") from exc
    if header.get("v") != FORMAT_VERSION or header.get("cipher") != CIPHER_NAME:
        raise BackupFormatError("unsupported backup format version or cipher")

    key_id = header.get("key_id")
    key = keys.get(key_id) if isinstance(key_id, str) else None
    if key is None:
        raise BackupKeyError(
            f"no key for key_id {key_id!r}; restore requires the key that created the backup"
        )
    try:
        nonce = base64.b64decode(header["nonce"].encode("ascii"), validate=True)
        chunk_size = int(header.get("chunk_size", CHUNK_SIZE))
    except (KeyError, ValueError) as exc:
        raise BackupFormatError("backup header nonce or chunk size is invalid") from exc
    if chunk_size <= 0 or chunk_size > 64 * 1024 * 1024:
        raise BackupFormatError("backup header chunk size is out of range")

    decryptor = AESGCM(key)
    # AAD binding: encryption authenticated every record against the
    # PLACEHOLDER header (final size/hash unknown before the payload pass);
    # the decryptor reconstructs the same canonical placeholder from the
    # patched header by zeroing the two placeholder fields.
    aad_header = {**header, "plain_size": "0" * _PLAIN_SIZE_WIDTH, "plain_sha256": "0" * 64}
    aad = _header_bytes(aad_header)
    digester = hashlib.sha256()
    total = 0
    while True:
        chunk = source.read(chunk_size + 16)
        if not chunk:
            break
        try:
            plain = decryptor.decrypt(nonce, chunk, aad)
        except InvalidTag as exc:
            raise BackupIntegrityError("backup failed GCM authentication") from exc
        digester.update(plain)
        total += len(plain)
        destination.write(plain)

    if expected_sha256 is None:
        expected_sha256 = header.get("plain_sha256")
    if not expected_sha256 or digester.hexdigest() != expected_sha256:
        raise BackupIntegrityError("backup plaintext SHA-256 mismatch")
    plain_size = header.get("plain_size")
    if plain_size is not None and int(plain_size) != total:
        raise BackupIntegrityError("backup plaintext size mismatch")
    return total


# --- Checksum sidecar ---------------------------------------------------------


def write_checksum_file(backup_path: Path) -> str:
    """Write ``<name>.sha256`` (sha256sum-compatible) next to ``backup_path``.

    Returns the hex digest. The checksum protects against storage-level
    corruption and is verified by ``backup check`` without the encryption key.
    """
    digester = hashlib.sha256()
    with backup_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digester.update(chunk)
    digest = digester.hexdigest()
    sidecar = backup_path.with_name(backup_path.name + ".sha256")
    sidecar.write_text(f"{digest}  {backup_path.name}\n", encoding="ascii")
    os.chmod(sidecar, 0o600)
    return digest


def verify_checksum_file(backup_path: Path) -> bool:
    """Verify the detached checksum of ``backup_path`` without the key."""
    sidecar = backup_path.with_name(backup_path.name + ".sha256")
    try:
        expected = sidecar.read_text(encoding="ascii").strip().split()[0]
    except (OSError, IndexError):
        return False
    if not re.fullmatch(r"[0-9a-f]{64}", expected or ""):
        return False
    digester = hashlib.sha256()
    with backup_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digester.update(chunk)
    return digester.hexdigest() == expected


def checksum_hex(path: Path) -> str:
    """Return the SHA-256 hex digest of ``path``."""
    digester = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digester.update(chunk)
    return digester.hexdigest()


# --- Naming and retention -----------------------------------------------------


def backup_filename(at: datetime, run_id: str) -> str:
    """Canonical backup file name: UTC wall time + short run id."""
    stamp = at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"hr-manager-{stamp}-{run_id}.pgdump.enc"


def parse_backup_filename(name: str) -> datetime | None:
    """Return the UTC timestamp embedded in a valid backup file name."""
    match = BACKUP_FILE_RE.match(name)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group("ts"), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def retention_plan(
    files: list[tuple[str, datetime]],
    *,
    now: datetime,
    retention_days: int,
    min_copies: int,
) -> tuple[list[str], list[str]]:
    """Decide which backup files to delete.

    ``files`` are ``(name, mtime_utc)`` pairs from the backup directory.
    Only files matching ``BACKUP_FILE_RE`` are ever considered. Everything
    older than ``retention_days`` is a deletion candidate EXCEPT the newest
    ``min_copies`` files, which are always kept (a failed future run must
    never be able to empty the archive). Returns ``(to_delete, to_keep)``.
    """
    recognized = sorted(
        ((name, mtime) for name, mtime in files if BACKUP_FILE_RE.match(name)),
        key=lambda item: item[1],
    )
    cutoff = now - timedelta(days=max(retention_days, 0))
    keep_minimum = recognized[-min_copies:] if min_copies > 0 else []
    keep_minimum_names = {name for name, _ in keep_minimum}
    to_delete: list[str] = []
    to_keep: list[str] = []
    for name, mtime in recognized:
        if name in keep_minimum_names or mtime >= cutoff:
            to_keep.append(name)
        else:
            to_delete.append(name)
    return to_delete, to_keep


# --- State file ---------------------------------------------------------------


@dataclass
class BackupRecord:
    """One published backup in the state file (no PII, no key material)."""

    file: str
    at: str
    size: int
    enc_sha256: str
    status: str
    reason: str = ""
    request_id: str = ""


@dataclass
class BackupState:
    """Persistent backup state, shared with the monitoring endpoints."""

    last_backup: BackupRecord | None = None
    last_check: dict[str, Any] | None = None
    last_drill: dict[str, Any] | None = None
    recent: list[BackupRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "last_backup": None if self.last_backup is None else self.last_backup.__dict__,
            "last_check": self.last_check,
            "last_drill": self.last_drill,
            "recent": [record.__dict__ for record in self.recent],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> BackupState:
        if not isinstance(payload, dict) or payload.get("schema") != 1:
            raise BackupFormatError("unsupported backup state schema")

        def _record(item: Any) -> BackupRecord | None:
            if not isinstance(item, dict):
                return None
            return BackupRecord(
                file=str(item.get("file", "")),
                at=str(item.get("at", "")),
                size=int(item.get("size") or 0),
                enc_sha256=str(item.get("enc_sha256", "")),
                status=str(item.get("status", "")),
                reason=str(item.get("reason", "")),
                request_id=str(item.get("request_id", "")),
            )

        records = [record for item in payload.get("recent") or [] if (record := _record(item))]
        last_backup = _record(payload.get("last_backup")) or (records[0] if records else None)
        return cls(
            last_backup=last_backup,
            last_check=payload.get("last_check"),
            last_drill=payload.get("last_drill"),
            recent=records,
        )


def load_state(path: Path) -> BackupState:
    """Load the state file; a missing file is an empty (fresh) state."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return BackupState()
    except (ValueError, OSError) as exc:
        raise BackupFormatError("backup state file is not valid JSON") from exc
    return BackupState.from_dict(payload)


def save_state(path: Path, state: BackupState) -> None:
    """Atomically persist the state file (0600, fsync, rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    payload = json.dumps(state.to_dict(), indent=2, sort_keys=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(tmp_name)


def update_state_backup(
    path: Path, state: BackupState, record: BackupRecord, *, max_recent: int = 20
) -> BackupState:
    """Insert a fresh backup record at the head of the state and persist."""
    state.last_backup = record
    state.recent = [record, *state.recent][:max_recent]
    save_state(path, state)
    return state


# --- Freshness ----------------------------------------------------------------


def freshness_ok(
    state: BackupState,
    *,
    now: datetime,
    max_age_hours: int,
) -> tuple[bool, float | None]:
    """Return ``(fresh_enough, age_seconds)`` for the newest backup.

    A state without any successful backup is never fresh.
    """
    record = state.last_backup
    if record is None or record.status != "ok":
        return False, None
    try:
        at = datetime.fromisoformat(record.at)
    except ValueError:
        return False, None
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    age = (now - at.astimezone(UTC)).total_seconds()
    return age <= max_age_hours * 3600.0, age


# --- Directory helpers --------------------------------------------------------


def list_backup_files(directory: Path) -> list[tuple[str, datetime]]:
    """List recognized backup files with their mtimes (UTC)."""
    result: list[tuple[str, datetime]] = []
    try:
        entries = list(directory.iterdir())
    except FileNotFoundError:
        return []
    for entry in entries:
        if not entry.is_file() or not BACKUP_FILE_RE.match(entry.name):
            continue
        mtime = datetime.fromtimestamp(entry.stat().st_mtime, tz=UTC)
        result.append((entry.name, mtime))
    return result


def secure_tempdir(parent: Path, prefix: str) -> Path:
    """Create a 0700 temporary directory for plaintext staging."""
    parent.mkdir(parents=True, exist_ok=True)
    os.chmod(parent, 0o700)
    temp = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
    os.chmod(temp, 0o700)
    return temp
