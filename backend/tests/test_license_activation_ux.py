"""Активация лицензии глазами пользователя (пилот 0.15.0).

Проверяются человеческие сценарии, а не только криптография:
- выбран файл приватного ключа вместо лицензии;
- файл лицензии повреждён / это не лицензия;
- подпись не совпадает (лицензия выпущена другим ключом);
- лицензия просрочена;
- лицензия другого владельца (подмена client_name);
- сообщения понятны и не содержат секретов/технических команд.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.license import (
    LicenseError,
    detect_private_key_upload,
    sign_license,
)
from app.services.license_service import parse_and_verify_license_text


def gen_keypair() -> tuple[bytes, bytes]:
    priv = Ed25519PrivateKey.generate()
    return priv.private_bytes_raw(), priv.public_key().public_bytes_raw()


def make_license(
    private_raw: bytes,
    *,
    days: int = 30,
    client_name: str = "Пилот Марии",
    issued_days_ago: int = 0,
) -> str:
    issued = datetime.now(UTC) - timedelta(days=issued_days_ago)
    data = {
        "license_id": str(uuid.uuid4()),
        "client_name": client_name,
        "issued_at": issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (date.today() + timedelta(days=days)).isoformat(),
        "max_active_users": 2,
    }
    signature = sign_license(data, private_raw.hex())
    return json.dumps({**data, "signature": signature}, ensure_ascii=False)


# --- Файл приватного ключа вместо лицензии -----------------------------------


def test_private_key_hex_is_detected_with_clear_message() -> None:
    private_raw, _ = gen_keypair()
    text = private_raw.hex()
    note = detect_private_key_upload(text)
    assert "ПРИВАТНОГО ключа" in note
    assert ".hrmlicense" in note
    with pytest.raises(LicenseError) as exc:
        parse_and_verify_license_text(text, base64.b64encode(gen_keypair()[1]).decode())
    assert exc.value.code == "private_key_upload"


def test_private_key_pem_is_detected() -> None:
    pem = "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n"
    note = detect_private_key_upload(pem)
    assert "ПРИВАТНОГО ключа" in note


def test_private_key_json_is_detected() -> None:
    text = json.dumps({"private_key": "ab" * 32})
    note = detect_private_key_upload(text)
    assert "приватный ключ" in note.lower()
    with pytest.raises(LicenseError) as exc:
        parse_and_verify_license_text(text, base64.b64encode(gen_keypair()[1]).decode())
    assert exc.value.code == "private_key_upload"


def test_public_key_base64_is_not_mistaken_for_private_key() -> None:
    # Открытый ключ — не секрет и не должен блокироваться этим распознаванием.
    _, public_raw = gen_keypair()
    assert detect_private_key_upload(base64.b64encode(public_raw).decode()) == ""


# --- Повреждённый файл / не лицензия -----------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "не json вовсе",
        "{",
        '{"license_id": "не-uuid"}',
        json.dumps({"license_id": str(uuid.uuid4())}),  # нет обязательных полей
        json.dumps([1, 2, 3]),
    ],
)
def test_corrupted_or_wrong_file(payload: str) -> None:
    _, public_raw = gen_keypair()
    public_b64 = base64.b64encode(public_raw).decode()
    with pytest.raises(LicenseError) as exc:
        parse_and_verify_license_text(payload, public_b64)
    assert exc.value.code in {
        "malformed_json",
        "missing_field",
        "bad_license_id",
        "bad_type",
        "private_key_upload",
    }
    # Сообщение должно быть на русском и объяснять, что делать.
    assert str(exc.value)


# --- Подпись, срок, владелец -------------------------------------------------


def test_signature_from_another_keypair_rejected() -> None:
    private_raw, _ = gen_keypair()
    _, other_public = gen_keypair()
    license_text = make_license(private_raw)
    with pytest.raises(LicenseError) as exc:
        parse_and_verify_license_text(license_text, base64.b64encode(other_public).decode())
    assert exc.value.code == "bad_signature"


def test_tampered_license_rejected() -> None:
    private_raw, public_raw = gen_keypair()
    public_b64 = base64.b64encode(public_raw).decode()
    data = json.loads(make_license(private_raw))
    data["max_active_users"] = 999  # подмена лимита после подписи
    with pytest.raises(LicenseError) as exc:
        parse_and_verify_license_text(json.dumps(data, ensure_ascii=False), public_b64)
    assert exc.value.code == "bad_signature"


def test_owner_substitution_rejected() -> None:
    private_raw, public_raw = gen_keypair()
    public_b64 = base64.b64encode(public_raw).decode()
    data = json.loads(make_license(private_raw))
    data["client_name"] = "Другой владелец"  # подмена владельца без перевыпуска
    with pytest.raises(LicenseError) as exc:
        parse_and_verify_license_text(json.dumps(data, ensure_ascii=False), public_b64)
    assert exc.value.code == "bad_signature"


def test_expired_license_parses_but_is_reported_expired() -> None:
    private_raw, public_raw = gen_keypair()
    public_b64 = base64.b64encode(public_raw).decode()
    text = make_license(private_raw, days=-1, issued_days_ago=30)
    data = parse_and_verify_license_text(text, public_b64)  # подпись валидна
    assert data["expires_at"] < date.today().isoformat()


def test_private_key_never_in_error_message() -> None:
    private_raw, public_raw = gen_keypair()
    public_b64 = base64.b64encode(public_raw).decode()
    text = private_raw.hex()
    with pytest.raises(LicenseError) as exc:
        parse_and_verify_license_text(text, public_b64)
    assert private_raw.hex() not in str(exc.value)
