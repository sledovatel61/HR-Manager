# -*- coding: utf-8 -*-
"""Тесты контракта канала обновлений (канонизация, подпись, негативы).

Запуск:  pytest infra/release/test_channel_contract.py -q
Зависимость: cryptography (есть в requirements-dev backend).
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from channel_contract import (  # noqa: E402
    ChannelError,
    canonical_bytes,
    parse_manifest_json,
    sha256_hex,
    sign_manifest,
    validate_manifest_fields,
    verify_signature,
)

TESTDATA = Path(__file__).resolve().parent / "testdata"


def load(name: str) -> str:
    return (TESTDATA / name).read_text(encoding="utf-8")


def load_json(name: str) -> dict:
    return parse_manifest_json(load(name))


def test_keys() -> tuple[str, str]:
    return load("test_key.pub").strip(), load("test_key.priv").strip()


def signed_valid() -> dict:
    return load_json("manifest.valid.json")


# --- Канонизация (golden) ---------------------------------------------------

def test_canonical_bytes_match_golden_fixture() -> None:
    manifest = signed_valid()
    payload = {k: v for k, v in manifest.items() if k != "signature"}
    expected = load("manifest.canonical.txt").encode("utf-8")
    assert canonical_bytes(payload) == expected


def test_canonical_is_field_order_independent_of_json_layout() -> None:
    manifest = signed_valid()
    payload = {k: v for k, v in manifest.items() if k != "signature"}
    reserialized = json.loads(json.dumps(payload))
    assert canonical_bytes(reserialized) == canonical_bytes(payload)


def test_canonical_rejects_unknown_field() -> None:
    payload = {k: v for k, v in signed_valid().items() if k != "signature"}
    payload["extra"] = "x"
    with pytest.raises(ChannelError) as _exc:
            canonical_bytes(payload)
    assert _exc.value.code == "unknown_field"


def test_canonical_rejects_control_characters() -> None:
    payload = {k: v for k, v in signed_valid().items() if k != "signature"}
    payload["notes_ru"] = "строка\nс переводом"
    with pytest.raises(ChannelError) as _exc:
            canonical_bytes(payload)
    assert _exc.value.code == "bad_value"


# --- Подпись -----------------------------------------------------------------

def test_sign_and_verify_roundtrip() -> None:
    pub, priv = test_keys()
    manifest = signed_valid()
    payload = {k: v for k, v in manifest.items() if k != "signature"}
    signed = sign_manifest(payload, "pilot-test-key", priv)
    assert verify_signature(signed, pub) == canonical_bytes(payload)


def test_verify_rejects_flipped_signature_bit() -> None:
    pub, _ = test_keys()
    with pytest.raises(ChannelError) as _exc:
            verify_signature(load_json("manifest.bad_sig.json"), pub)
    assert _exc.value.code == "bad_signature"


def test_verify_rejects_tampered_signed_field() -> None:
    pub, _ = test_keys()
    with pytest.raises(ChannelError) as _exc:
            verify_signature(load_json("manifest.tampered.json"), pub)
    assert _exc.value.code == "bad_signature"


def test_verify_rejects_malformed_signature_encoding() -> None:
    pub, _ = test_keys()
    with pytest.raises(ChannelError) as _exc:
            verify_signature(load_json("manifest.malformed_sig.json"), pub)
    assert _exc.value.code == "bad_signature"


def test_verify_rejects_wrong_public_key() -> None:
    wrong_pub = load("other_key.pub").strip()
    with pytest.raises(ChannelError) as _exc:
            verify_signature(signed_valid(), wrong_pub)
    assert _exc.value.code == "bad_signature"


def test_unknown_key_id_fail_closed_via_trusted_set() -> None:
    # key_id отсутствует в доверенном наборе — клиент обязан отказать ДО
    # проверки подписи; здесь фиксируем, что key_id читается из manifest.
    assert signed_valid()["signature"]["key_id"] == "pilot-test-key"
    assert load_json("manifest.unknown_key.json")["signature"]["key_id"] == "pilot-unknown-key"


def test_revoked_key_marked_in_trusted_set() -> None:
    trusted = json.loads(load("trusted_keys.json"))
    assert trusted["pilot-revoked-key"]["revoked"] is True
    assert trusted["pilot-test-key"]["revoked"] is False


# --- Валидация полей ---------------------------------------------------------

def test_unknown_schema_version_rejected_before_signature() -> None:
    with pytest.raises(ChannelError) as _exc:
            validate_manifest_fields(load_json("manifest.schema2.json"))
    assert _exc.value.code == "unknown_schema_version"


def test_bad_semver_rejected() -> None:
    with pytest.raises(ChannelError) as _exc:
            validate_manifest_fields(load_json("manifest.bad_semver.json"))
    assert _exc.value.code == "bad_semver"


def test_duplicate_json_keys_rejected() -> None:
    with pytest.raises(ChannelError) as _exc:
            parse_manifest_json(load("manifest.duplicate_key.json"))
    assert _exc.value.code == "duplicate_key"


@pytest.mark.parametrize(
    "url",
    [
        "http://updates.example.com/p.zip",
        "https://updates.example.com/p.zip?token=secret",
        "https://user:pass@updates.example.com/p.zip",
        "https://updates.example.com/p.zip#frag",
        "ftp://updates.example.com/p.zip",
    ],
)
def test_package_url_policy_rejections(url: str) -> None:
    payload = {k: v for k, v in signed_valid().items() if k != "signature"}
    payload["package_url"] = url
    with pytest.raises(ChannelError) as _exc:
            validate_manifest_fields(payload)
    assert _exc.value.code == "bad_url"


def test_package_size_bounds() -> None:
    payload = {k: v for k, v in signed_valid().items() if k != "signature"}
    payload["package_size"] = 0
    with pytest.raises(ChannelError) as _exc:
            validate_manifest_fields(payload)
    assert _exc.value.code == "bad_type"
    payload["package_size"] = 2**40
    with pytest.raises(ChannelError) as _exc:
            validate_manifest_fields(payload)
    assert _exc.value.code == "bad_type"


# --- Пакет -------------------------------------------------------------------

def test_valid_package_zip_matches_manifest() -> None:
    data = (TESTDATA / "package.valid.zip").read_bytes()
    manifest = signed_valid()
    assert manifest["package_size"] == len(data)
    assert manifest["package_sha256"] == sha256_hex(data)


def test_attack_packages_are_zip_archives_with_expected_entries() -> None:
    expectations = {
        "package.zipslip.zip": "../evil.txt",
        "package.abs.zip": "/abs/evil.txt",
        "package.unc.zip": "\\\\server\\share\\evil.txt",
        "package.ads.zip": "release.json:stream",
        "package.backslash.zip": "backend\\evil.txt",
        "package.extra_root.zip": "unexpected.txt",
        "package.exe.zip": "backend/evil.exe",
        "package.symlink.zip": "link.txt",
    }
    for name, entry in expectations.items():
        with zipfile.ZipFile(TESTDATA / name) as zf:
            assert entry in zf.namelist(), name


def test_symlink_fixture_has_symlink_mode() -> None:
    with zipfile.ZipFile(TESTDATA / "package.symlink.zip") as zf:
        info = zf.getinfo("link.txt")
        assert (info.external_attr >> 16) & 0o170000 == 0o120000


# --- SemVer: таблица общая с PowerShell-тестами ------------------------------

def test_semver_cases_table_consistent() -> None:
    cases = json.loads(load("semver_cases.json"))
    assert len(cases) >= 14
    for left, right, expected in cases:
        assert expected in (-1, 0, 1)
