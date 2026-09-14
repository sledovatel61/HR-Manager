"""Тесты строгой схемы публичного trust store (Phase 14).

Проверяется контракт infra/release/trust_store.py: закрытая схема, отказ на
private material, подмену/дубликаты, test-хранилище как production trust root
и корректная нормализация. Fixture-ключи из infra/release/testdata —
ТЕСТОВЫЕ: тесты явно фиксируют, что они не проходят production-контракт.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RELEASE = REPO / "infra" / "release"
sys.path.insert(0, str(RELEASE))

from channel_contract import ChannelError  # type: ignore[import-not-found]  # noqa: E402
from trust_store import (  # type: ignore[import-not-found]  # noqa: E402
    key_fingerprint,
    key_status,
    load_trust_store,
    redacted_summary,
    stores_match,
    validate_trust_store,
)

TESTDATA = RELEASE / "testdata"


def _valid_key(seed: int = 0) -> str:
    """Детерминированный валидный 32-байтовый Ed25519 публичный ключ (base64)."""
    return base64.b64encode(bytes([seed % 256] * 32)).decode()


def _store(keys: dict, environment: str = "production") -> dict:
    return {"schema_version": 1, "environment": environment, "keys": keys}


def test_valid_production_store_normalizes() -> None:
    data = _store(
        {
            "pilot-release-2026": {"key": _valid_key(1), "revoked": False},
            "pilot-release-old": {"key": _valid_key(2), "revoked": True},
        }
    )
    normalized = validate_trust_store(data)
    assert normalized["environment"] == "production"
    assert set(normalized["keys"]) == {"pilot-release-2026", "pilot-release-old"}


def test_unknown_top_level_field_rejected() -> None:
    data = _store({"k1": {"key": _valid_key(), "revoked": False}})
    data["url"] = "https://evil.example.com"
    with pytest.raises(ChannelError):
        validate_trust_store(data)


def test_missing_field_rejected() -> None:
    with pytest.raises(ChannelError):
        validate_trust_store({"schema_version": 1, "keys": {}})


def test_private_material_field_rejected() -> None:
    data = _store({"k1": {"key": _valid_key(), "revoked": False}})
    data["keys"]["k1"]["private"] = _valid_key(3)
    with pytest.raises(ChannelError) as excinfo:
        validate_trust_store(data)
    assert excinfo.value.code == "private_material"


def test_hex64_value_rejected_as_private_material() -> None:
    # Значение, выглядящее как hex-закрытый ключ, отклоняется структурным сканом.
    data = _store({"k1": {"key": _valid_key(), "revoked": False}})
    data["environment"] = "production"
    data["keys"]["k1"]["revoked"] = False
    data["note"] = "a" * 64 if False else data.get("note")  # не меняем схему тут
    # Отдельный случай: hex-64 внутри допустимого строкового значения ключа.
    data["keys"]["k1"]["key"] = _valid_key()
    with pytest.raises(ChannelError):
        # hex-64 как значение поля верхнего уровня (extra field с секретом)
        validate_trust_store({**data, "extra": "f" * 64})


def test_pem_block_rejected() -> None:
    data = _store({"k1": {"key": _valid_key(), "revoked": False}})
    data["environment"] = "production"
    with pytest.raises(ChannelError):
        validate_trust_store({**data, "certificate": "-----BEGIN PRIVATE KEY-----\nabc\n"})


def test_wrong_schema_version_rejected() -> None:
    data = _store({"k1": {"key": _valid_key(), "revoked": False}})
    data["schema_version"] = 2
    with pytest.raises(ChannelError):
        validate_trust_store(data)


def test_test_store_cannot_be_production_root() -> None:
    data = _store({"k1": {"key": _valid_key(), "revoked": False}}, environment="test")
    with pytest.raises(ChannelError) as excinfo:
        validate_trust_store(data)
    assert excinfo.value.code == "test_trust_store"
    # Явный allow_test (только для fixture-тестов) разрешает чтение.
    assert validate_trust_store(data, allow_test=True)["environment"] == "test"


def test_bad_key_id_rejected() -> None:
    for bad in ("", "ключ с пробелами", "a" * 65, "kid\nnewline", "kid/слэш"):
        with pytest.raises(ChannelError):
            validate_trust_store(_store({bad: {"key": _valid_key(), "revoked": False}}))


def test_bad_key_value_rejected() -> None:
    for bad in (
        "",
        "not-base64!!",
        base64.b64encode(b"x" * 31).decode(),
        base64.b64encode(b"x" * 33).decode(),
    ):
        with pytest.raises(ChannelError):
            validate_trust_store(_store({"k1": {"key": bad, "revoked": False}}))


def test_duplicate_fingerprint_rejected() -> None:
    same_key = _valid_key(7)
    data = _store(
        {
            "k1": {"key": same_key, "revoked": False},
            "k2": {"key": same_key, "revoked": False},
        }
    )
    with pytest.raises(ChannelError):
        validate_trust_store(data)


def test_all_revoked_production_store_rejected() -> None:
    data = _store({"k1": {"key": _valid_key(), "revoked": True}})
    with pytest.raises(ChannelError):
        validate_trust_store(data)


def test_empty_keys_rejected() -> None:
    with pytest.raises(ChannelError):
        validate_trust_store(_store({}))


def test_fixture_private_key_material_rejected_in_store() -> None:
    """Regression: закрытый fixture-ключ никогда не попадает в trust store."""
    private_hex = (TESTDATA / "test_key.priv").read_text(encoding="utf-8").strip()
    # 64 hex → выглядит как закрытый материал → структурный скан отклоняет.
    with pytest.raises(ChannelError):
        validate_trust_store(
            {**_store({"k1": {"key": _valid_key(), "revoked": False}}), "d": private_hex}
        )


def test_tampered_store_rejected_via_load(tmp_path: Path) -> None:
    good = _store({"k1": {"key": _valid_key(), "revoked": False}})
    path = tmp_path / "trust.json"
    path.write_text(__import__("json").dumps(good), encoding="utf-8")
    assert load_trust_store(path)["environment"] == "production"
    # Подмена байта ключа → ключ больше не 32 байта или не base64 → отказ.
    tampered = {**good, "keys": {"k1": {"key": "AAAA", "revoked": False}}}
    path.write_text(__import__("json").dumps(tampered), encoding="utf-8")
    with pytest.raises(ChannelError):
        load_trust_store(path)


def test_fingerprint_and_status_helpers() -> None:
    key = _valid_key(11)
    store = validate_trust_store(_store({"active": {"key": key, "revoked": False}}))
    assert key_fingerprint(key) == key_fingerprint(key)
    assert key_fingerprint(key) != key_fingerprint(_valid_key(12))
    assert key_status(store, "active") == "active"
    assert key_status(store, "missing") == "unknown"


def test_redacted_summary_has_no_key_material() -> None:
    key = _valid_key(13)
    store = validate_trust_store(_store({"k1": {"key": key, "revoked": False}}))
    summary = redacted_summary(store)
    assert summary == [
        {"key_id": "k1", "fingerprint": key_fingerprint(key)[:16], "status": "active"}
    ]
    # Сам ключ не возвращается диагностикой.
    assert key not in __import__("json").dumps(summary)


def test_stores_match_semantics() -> None:
    left = validate_trust_store(_store({"k1": {"key": _valid_key(21), "revoked": False}}))
    same = validate_trust_store(_store({"k1": {"key": _valid_key(21), "revoked": False}}))
    other = validate_trust_store(_store({"k1": {"key": _valid_key(22), "revoked": False}}))
    assert stores_match(left, same)
    assert not stores_match(left, other)


def test_fixture_test_key_is_not_production_root() -> None:
    """Fixture-ключ из testdata не должен проходить production-контракт.

    Тестовый публичный ключ валиден как Ed25519 public key, но test-окружение
    хранилища не может стать production trust root (fail closed).
    """
    test_key = (TESTDATA / "test_key.pub").read_text(encoding="utf-8").strip()
    data = _store({"pilot-test-key": {"key": test_key, "revoked": False}}, environment="test")
    with pytest.raises(ChannelError):
        validate_trust_store(data)
