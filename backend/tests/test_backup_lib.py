"""Unit tests for the backup storage library (no database, no subprocesses).

Covers the format contract: authenticated encryption round-trip, tamper and
header-bound detection, key rotation, checksum sidecars, retention policy,
state file atomicity and freshness semantics.
"""

from __future__ import annotations

import io
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.backup import (
    BackupFormatError,
    BackupIntegrityError,
    BackupKeyError,
    BackupRecord,
    BackupState,
    backup_filename,
    decrypt_to_stream,
    encrypt_stream,
    freshness_ok,
    list_backup_files,
    load_keys_from_env,
    load_state,
    parse_backup_filename,
    retention_plan,
    save_state,
    update_state_backup,
    validate_key_material,
    verify_checksum_file,
    write_checksum_file,
)

NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)


def _key() -> bytes:
    return os.urandom(32)


def _b64(raw: bytes) -> str:
    import base64

    return base64.b64encode(raw).decode("ascii")


# --- key material -------------------------------------------------------------


def test_validate_key_material_accepts_32_bytes_and_rejects_weak() -> None:
    assert len(validate_key_material(_b64(os.urandom(32)), label="K")) == 32
    with pytest.raises(BackupKeyError, match="not set"):
        validate_key_material("", label="K")
    with pytest.raises(BackupKeyError, match="base64"):
        validate_key_material("not-base64!!", label="K")
    with pytest.raises(BackupKeyError, match="32 bytes"):
        validate_key_material(_b64(b"short"), label="K")
    with pytest.raises(BackupKeyError, match="all-zero"):
        validate_key_material(_b64(b"\x00" * 32), label="K")


def test_load_keys_from_env_requires_paired_id_and_key() -> None:
    key = _b64(_key())
    assert load_keys_from_env({"BACKUP_KEY_ID": "k1", "BACKUP_ENC_KEY": key}) == {
        "k1": validate_key_material(key, label="unused")
    }
    with pytest.raises(BackupKeyError, match="BACKUP_KEY_ID is not"):
        load_keys_from_env({"BACKUP_ENC_KEY": key})
    with pytest.raises(BackupKeyError, match="BACKUP_ENC_KEY is not"):
        load_keys_from_env({"BACKUP_KEY_ID": "k1"})


def test_load_keys_from_env_legacy_ring() -> None:
    legacy = _b64(_key())
    keys = load_keys_from_env(
        {
            "BACKUP_KEY_ID": "k2",
            "BACKUP_ENC_KEY": _b64(_key()),
            "BACKUP_LEGACY_KEYS": f'{{"k1": "{legacy}"}}',
        }
    )
    assert set(keys) == {"k2", "k1"}
    with pytest.raises(BackupKeyError, match="not valid JSON"):
        load_keys_from_env({"BACKUP_LEGACY_KEYS": "{"})


# --- encrypt / decrypt round trip ---------------------------------------------


def _roundtrip(payload: bytes, key: bytes, key_id: str = "k1") -> bytes:
    source = io.BytesIO(payload)
    destination = io.BytesIO()
    encrypt_stream(source, destination, key=key, key_id=key_id, created_at=NOW)
    ciphertext = destination.getvalue()
    restored = io.BytesIO()
    decrypt_to_stream(io.BytesIO(ciphertext), restored, keys={key_id: key})
    return restored.getvalue()


def test_roundtrip_small_multichunk_and_empty() -> None:
    key = _key()
    assert _roundtrip(b"hello world", key) == b"hello world"
    # multi-chunk (3 records) round trip with known content
    payload = bytes(range(256)) * 9000  # ~2.3 MiB
    assert _roundtrip(payload, key) == payload
    assert _roundtrip(b"", key) == b""


def test_ciphertext_tamper_is_detected() -> None:
    key = _key()
    source = io.BytesIO(b"important data" * 100)
    destination = io.BytesIO()
    encrypt_stream(source, destination, key=key, key_id="k1", created_at=NOW)
    blob = bytearray(destination.getvalue())
    blob[-1] ^= 0xFF  # flip a tag/ciphertext byte
    with pytest.raises(BackupIntegrityError, match="GCM authentication"):
        decrypt_to_stream(io.BytesIO(bytes(blob)), io.BytesIO(), keys={"k1": key})


def test_header_tamper_is_detected() -> None:
    key = _key()
    destination = io.BytesIO()
    encrypt_stream(io.BytesIO(b"payload" * 100), destination, key=key, key_id="k1", created_at=NOW)
    blob = bytearray(destination.getvalue())
    index = blob.find(b'"key_id"')
    blob[index + 12] ^= 1  # mutate the key id inside the AAD-bound header
    with pytest.raises((BackupIntegrityError, BackupFormatError, BackupKeyError)):
        decrypt_to_stream(io.BytesIO(bytes(blob)), io.BytesIO(), keys={"k1": key})


def test_unknown_key_id_fails_cleanly() -> None:
    key = _key()
    destination = io.BytesIO()
    encrypt_stream(io.BytesIO(b"x"), destination, key=key, key_id="k-old", created_at=NOW)
    with pytest.raises(BackupKeyError, match="no key for key_id"):
        decrypt_to_stream(io.BytesIO(destination.getvalue()), io.BytesIO(), keys={"k-new": _key()})


def test_legacy_key_restores_old_backup() -> None:
    legacy = _key()
    destination = io.BytesIO()
    encrypt_stream(
        io.BytesIO(b"historical data"), destination, key=legacy, key_id="k-old", created_at=NOW
    )
    restored = io.BytesIO()
    decrypt_to_stream(
        io.BytesIO(destination.getvalue()),
        restored,
        keys={"k-current": _key(), "k-old": legacy},
    )
    assert restored.getvalue() == b"historical data"


def test_truncated_file_fails_authentication() -> None:
    key = _key()
    destination = io.BytesIO()
    encrypt_stream(io.BytesIO(b"data" * 5000), destination, key=key, key_id="k1", created_at=NOW)
    blob = destination.getvalue()[:-5]
    with pytest.raises((BackupIntegrityError, BackupFormatError)):
        decrypt_to_stream(io.BytesIO(blob), io.BytesIO(), keys={"k1": key})


def test_bad_magic_is_a_format_error() -> None:
    with pytest.raises(BackupFormatError, match="magic"):
        decrypt_to_stream(io.BytesIO(b"NOTABACKUP...."), io.BytesIO(), keys={})


# --- checksum sidecar ----------------------------------------------------------


def test_checksum_sidecar_roundtrip_and_tamper(tmp_path: Path) -> None:
    target = tmp_path / "hr-manager-20260904T120000Z-abcdef12.pgdump.enc"
    target.write_bytes(b"encrypted blob")
    digest = write_checksum_file(target)
    assert len(digest) == 64
    assert (tmp_path / (target.name + ".sha256")).exists()
    assert verify_checksum_file(target)
    target.write_bytes(b"encrypted blob!")
    assert not verify_checksum_file(target)


# --- naming and retention ------------------------------------------------------


def test_backup_filename_and_parser() -> None:
    name = backup_filename(NOW, "abcdef12")
    assert name == "hr-manager-20260904T120000Z-abcdef12.pgdump.enc"
    assert parse_backup_filename(name) == NOW
    assert parse_backup_filename("other-file.txt") is None
    assert parse_backup_filename("hr-manager-20261304T999999Z-x.pgdump.enc") is None


def test_retention_plan_keeps_minimum_copies_even_when_stale() -> None:
    files = [
        (backup_filename(NOW - timedelta(days=30), f"{i:08x}"), NOW - timedelta(days=30))
        for i in range(5)
    ]
    files.append((backup_filename(NOW, "9e57e500"), NOW))
    deleted, kept = retention_plan(files, now=NOW, retention_days=7, min_copies=2)
    # 6 recognized files, 5 of them stale: the 2 newest (min_copies floor,
    # one of which is the fresh backup) are kept, the other 4 are deleted.
    assert len(deleted) == 4
    assert len(kept) == 2
    kept_names = set(kept)
    assert "hr-manager-20260904T120000Z-9e57e500.pgdump.enc" in kept_names


def test_retention_plan_ignores_foreign_files() -> None:
    files = [("random.log", NOW - timedelta(days=60))]
    deleted, kept = retention_plan(files, now=NOW, retention_days=7, min_copies=1)
    assert deleted == []
    assert kept == []


def test_retention_plan_deletes_only_old() -> None:
    old = backup_filename(NOW - timedelta(days=9), "0d4f1e0a")
    fresh = backup_filename(NOW - timedelta(hours=1), "f2e50f11")
    files = [
        (old, NOW - timedelta(days=9)),
        (fresh, NOW - timedelta(hours=1)),
    ]
    deleted, kept = retention_plan(files, now=NOW, retention_days=7, min_copies=1)
    assert deleted == [old]
    assert kept == [fresh]


# --- state file ----------------------------------------------------------------


def test_state_roundtrip_and_atomic_save(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = BackupState(
        last_backup=BackupRecord(
            file="hr-manager-20260904T120000Z-abcdef12.pgdump.enc",
            at=NOW.isoformat(),
            size=12345,
            enc_sha256="a" * 64,
            status="ok",
            reason="перед обновлением",
            request_id="req-1",
        )
    )
    save_state(path, state)
    loaded = load_state(path)
    assert loaded.last_backup is not None
    assert state.last_backup is not None
    last = loaded.last_backup
    assert last.file == state.last_backup.file
    assert last.request_id == "req-1"
    # no stray temp files
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]
    assert (path.stat().st_mode & 0o777) == 0o600


def test_missing_state_is_empty(tmp_path: Path) -> None:
    state = load_state(tmp_path / "nope.json")
    assert state.last_backup is None
    assert state.recent == []


def test_update_state_backup_keeps_recent_capped(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = load_state(path)
    for i in range(25):
        state = update_state_backup(
            path,
            state,
            BackupRecord(
                file=f"hr-manager-20260904T{i:02d}0000Z-abcdef12.pgdump.enc",
                at=NOW.isoformat(),
                size=100 + i,
                enc_sha256="b" * 64,
                status="ok",
            ),
        )
    assert len(state.recent) == 20
    assert state.last_backup is not None
    assert state.last_backup.size == 124


# --- freshness -----------------------------------------------------------------


def test_freshness_semantics() -> None:
    fresh_record = BackupRecord(
        file="f",
        at=(NOW - timedelta(hours=2)).isoformat(),
        size=1,
        enc_sha256="c" * 64,
        status="ok",
    )
    stale_record = BackupRecord(
        file="f",
        at=(NOW - timedelta(hours=30)).isoformat(),
        size=1,
        enc_sha256="c" * 64,
        status="ok",
    )
    failed_record = BackupRecord(
        file="f",
        at=(NOW - timedelta(hours=1)).isoformat(),
        size=1,
        enc_sha256="c" * 64,
        status="failed",
    )
    ok, age = freshness_ok(BackupState(last_backup=fresh_record), now=NOW, max_age_hours=26)
    assert ok and age is not None and age <= 26 * 3600
    ok, _ = freshness_ok(BackupState(last_backup=stale_record), now=NOW, max_age_hours=26)
    assert not ok
    ok, _ = freshness_ok(BackupState(last_backup=failed_record), now=NOW, max_age_hours=26)
    assert not ok
    ok, age = freshness_ok(BackupState(), now=NOW, max_age_hours=26)
    assert not ok and age is None


# --- directory listing ----------------------------------------------------------


def test_list_backup_files_filters_by_pattern(tmp_path: Path) -> None:
    (tmp_path / "hr-manager-20260904T120000Z-abcdef12.pgdump.enc").write_bytes(b"x")
    (tmp_path / "hr-manager-20260904T130000Z-abcdef13.pgdump.enc.sha256").write_text("h")
    (tmp_path / "notes.txt").write_text("n")
    files = list_backup_files(tmp_path)
    assert [name for name, _ in files] == ["hr-manager-20260904T120000Z-abcdef12.pgdump.enc"]
    assert list_backup_files(tmp_path / "missing") == []
