# -*- coding: utf-8 -*-
"""Offline license issuer — owner utility (Windows PC).

- Generates Ed25519 keypair (private stays ONLY with owner)
- Issues license JSON with expiry and user limit, signs it
- No network, no dev tools needed for Maria (she only uploads file via UI)

Private key storage: owner must backup securely (encrypted USB, VeraCrypt, etc.)
Never commit private key to git/installer/frontend/Docker/logs.

Public key: base64 32 bytes, goes to infra/license/public_key.b64 and env LICENSE_PUBLIC_KEY.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

LICENSE_FIELDS = ["license_id", "client_name", "issued_at", "expires_at", "max_active_users"]


def generate_keypair() -> tuple[str, str]:
    """Return (private_hex 64 chars, public_b64 44 chars)."""
    priv = Ed25519PrivateKey.generate()
    priv_bytes = priv.private_bytes_raw()  # 32 bytes
    pub_bytes = priv.public_key().public_bytes_raw()  # 32 bytes
    priv_hex = priv_bytes.hex()
    pub_b64 = base64.b64encode(pub_bytes).decode("ascii")
    return priv_hex, pub_b64


def _canonical_bytes(data: dict) -> bytes:
    lines = [f"{name}:{data[name]}" for name in LICENSE_FIELDS]
    return ("\n".join(lines) + "\n").encode("utf-8")


def sign_license(data: dict, private_hex: str) -> str:
    priv_bytes = bytes.fromhex(private_hex)
    if len(priv_bytes) != 32:
        raise ValueError("private key must be 32 bytes")
    key = Ed25519PrivateKey.from_private_bytes(priv_bytes)
    payload = _canonical_bytes(data)
    sig = key.sign(payload)
    return sig.hex()


def verify_license(data: dict, public_b64: str) -> bool:
    pub_bytes = base64.b64decode(public_b64, validate=True)
    if len(pub_bytes) != 32:
        raise ValueError("public key must be 32 bytes")
    pub_key = Ed25519PublicKey.from_public_bytes(pub_bytes)
    sig_hex = data.get("signature")
    if not isinstance(sig_hex, str):
        raise ValueError("missing signature")
    payload = _canonical_bytes({k: v for k, v in data.items() if k != "signature"})
    pub_key.verify(bytes.fromhex(sig_hex), payload)
    return True


def issue_license(
    *,
    client_name: str,
    expires_at: str,  # YYYY-MM-DD
    max_active_users: int,
    private_hex: str,
    license_id: str | None = None,
    issued_at: str | None = None,
) -> dict:
    if not client_name.strip():
        raise ValueError("client_name empty")
    if len(client_name) > 200:
        raise ValueError("client_name too long")
    # Validate expires_at
    try:
        exp_date = date.fromisoformat(expires_at)
    except ValueError as exc:
        raise ValueError("expires_at must be YYYY-MM-DD") from exc
    if not (1 <= max_active_users <= 1000):
        raise ValueError("max_active_users must be 1..1000")

    lic_id = license_id or str(uuid.uuid4())
    try:
        uuid.UUID(lic_id)
    except ValueError as exc:
        raise ValueError("license_id must be UUID") from exc

    if issued_at:
        # Validate ISO8601 UTC
        try:
            datetime.strptime(issued_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as exc:
            raise ValueError("issued_at must be YYYY-MM-DDTHH:MM:SSZ") from exc
        iss_str = issued_at
    else:
        iss_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Ensure issued date <= expires date
    iss_date = datetime.strptime(iss_str, "%Y-%m-%dT%H:%M:%SZ").date()
    if iss_date > exp_date:
        raise ValueError("issued_at date cannot be after expires_at")

    payload = {
        "license_id": lic_id,
        "client_name": client_name.strip(),
        "issued_at": iss_str,
        "expires_at": expires_at,
        "max_active_users": int(max_active_users),
    }
    sig = sign_license(payload, private_hex)
    return {**payload, "signature": sig}


def save_license_file(data: dict, path: Path) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_private_key_from_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    # Support hex or base64? We use hex
    # Allow file containing hex or base64 private key? We'll accept hex 64 chars
    # Also allow file with "private_key: <hex>"
    if len(text) == 64 and all(c in "0123456789abcdefABCDEF" for c in text):
        return text.lower()
    # Try to parse JSON with private_key field
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "private_key" in obj:
            return str(obj["private_key"]).strip().lower()
    except Exception:
        pass
    # Try base64 32 bytes -> hex
    try:
        raw = base64.b64decode(text, validate=True)
        if len(raw) == 32:
            return raw.hex()
    except Exception:
        pass
    raise ValueError("Не удалось прочитать приватный ключ: ожидается 64 hex символа или base64 32 байта")


def load_public_key_from_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    # Validate base64 32 bytes
    raw = base64.b64decode(text, validate=True)
    if len(raw) != 32:
        raise ValueError("public key must decode to 32 bytes")
    return text
