"""Phase 14: тесты строгой валидации trust store канала обновлений.

Trust store — единственный root доверия клиента к обновлениям. Здесь
проверяются: строгая схема, уникальные key_id, ровно 32 байта Ed25519,
fail-closed отзыв, отсутствие приватного материала, детерминированная
сериализация, ротация двухключевым окном и запрет fixture-ключей
репозитория в production trust store.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RELEASE = REPO / "infra" / "release"
sys.path.insert(0, str(RELEASE))

from trust_store import (  # type: ignore[import-not-found]  # noqa: E402
    TrustStoreError,
    assert_no_fixture_keys,
    canonical_json,
    describe_trust_store,
    fixture_key_fingerprints,
    key_fingerprint,
    keys_json_for_env,
    load_trust_store_file,
    merge_trust_store,
    parse_trust_store,
    trust_store_sha256,
    validate_trust_store,
)

PUBLIC_KEY_A = base64.b64encode(bytes(range(32))).decode()
PUBLIC_KEY_B = base64.b64encode(bytes(range(32, 64))).decode()


def _store(**entries: dict) -> dict:
    return {key: value for key, value in entries.items()}


def _payload(entry: object, key_id: str = "k") -> str:
    """JSON одного ключа: без процентов и f-string-скобок в тестах."""
    return json.dumps({key_id: entry})


def test_parse_accepts_strict_schema() -> None:
    data = parse_trust_store(
        json.dumps(_store(**{"pilot-2026": {"key": PUBLIC_KEY_A, "revoked": False}}))
    )
    assert list(data) == ["pilot-2026"]
    validate_trust_store(data)


def test_parse_rejects_duplicate_key_id_in_json() -> None:
    first = json.dumps({"key": PUBLIC_KEY_A, "revoked": False})
    second = json.dumps({"key": PUBLIC_KEY_B, "revoked": False})
    text = '{"k1": ' + first + ', "k1": ' + second + "}"
    with pytest.raises(TrustStoreError) as excinfo:
        parse_trust_store(text)
    assert excinfo.value.code == "duplicate_key_id"


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("", "malformed_json"),
        ("[]", "bad_schema"),
        (
            _payload({"key": PUBLIC_KEY_B}, key_id="k"),
            "bad_revoked",
        ),
        ('{"k": {"key": "not-base64!!", "revoked": false}}', "bad_key"),
        (_payload({"key": PUBLIC_KEY_A, "revoked": False, "note": "x"}), "unknown_field"),
        (_payload({"key": PUBLIC_KEY_A, "revoked": True}), "all_keys_revoked"),
        (_payload({"key": PUBLIC_KEY_A, "revoked": False}, key_id="bad key id!"), "bad_key_id"),
        (_payload({"key": PUBLIC_KEY_A, "revoked": "yes"}), "bad_revoked"),
    ],
)
def test_parse_fails_closed(text: str, code: str) -> None:
    with pytest.raises(TrustStoreError) as excinfo:
        parse_trust_store(text)
    assert excinfo.value.code == code


def test_short_and_long_keys_are_rejected() -> None:
    for raw in (b"\x01" * 16, b"\x01" * 64):
        text = json.dumps({"k": {"key": base64.b64encode(raw).decode(), "revoked": False}})
        with pytest.raises(TrustStoreError) as excinfo:
            parse_trust_store(text)
        assert excinfo.value.code == "bad_key"


def test_private_material_is_rejected() -> None:
    private_pem = "-----BEGIN PRIVATE KEY-----\nMIIB\n-----END PRIVATE KEY-----\n"
    with pytest.raises(TrustStoreError) as excinfo:
        parse_trust_store(private_pem)
    assert excinfo.value.code == "private_material"

    hex_private = "a" * 64  # ровно формат приватного Ed25519-ключа Phase 13
    text = json.dumps({"k": {"key": hex_private, "revoked": False}})
    with pytest.raises(TrustStoreError) as excinfo:
        parse_trust_store(text)
    assert excinfo.value.code == "private_material"


def test_oversized_store_is_rejected() -> None:
    filler = {f"k{index:04d}": {"key": PUBLIC_KEY_A, "revoked": False} for index in range(900)}
    with pytest.raises(TrustStoreError) as excinfo:
        parse_trust_store(json.dumps(filler))
    assert excinfo.value.code == "too_large"


def test_fingerprint_and_description_are_deterministic_and_public() -> None:
    data = {
        "b-key": {"key": PUBLIC_KEY_B, "revoked": True},
        "a-key": {"key": PUBLIC_KEY_A, "revoked": False},
    }
    described = describe_trust_store(data)
    assert [entry["key_id"] for entry in described] == ["a-key", "b-key"]
    assert described[0]["fingerprint"] == key_fingerprint(PUBLIC_KEY_A)
    assert described[0]["fingerprint"].startswith("SHA256:")
    assert described[0]["revoked"] is False
    # В описании нет самого ключа — только отпечаток.
    assert PUBLIC_KEY_A not in json.dumps(described)
    assert canonical_json(data) == canonical_json(data)
    assert canonical_json(data).endswith("\n")
    assert trust_store_sha256(data) == trust_store_sha256(
        {k: dict(v) for k, v in reversed(list(data.items()))}
    )
    # keys_json_for_env — то, что попадает в конфигурацию клиента: только
    # публичные ключи и статус отзыва, никаких служебных полей.
    env_payload = json.loads(keys_json_for_env(data))
    assert set(env_payload) == {"a-key", "b-key"}
    assert all(set(entry) == {"key", "revoked"} for entry in env_payload.values())


def test_rotation_two_key_window_and_revocation() -> None:
    current = {"old-key": {"key": PUBLIC_KEY_A, "revoked": False}}
    update = {
        "new-key": {"key": PUBLIC_KEY_B, "revoked": False},
        "old-key": {"key": PUBLIC_KEY_A, "revoked": True},
    }
    merged = merge_trust_store(current, update)
    assert set(merged) == {"old-key", "new-key"}
    assert merged["old-key"]["revoked"] is True
    # Удалить существующий ключ нельзя: двухключевое окно не должно рваться.
    assert "old-key" in merged


def test_rotation_rejects_update_that_revokes_everything() -> None:
    current = {"old-key": {"key": PUBLIC_KEY_A, "revoked": False}}
    update = {"old-key": {"key": PUBLIC_KEY_A, "revoked": True}}
    with pytest.raises(TrustStoreError) as excinfo:
        merge_trust_store(current, update)
    assert excinfo.value.code == "all_keys_revoked"


def test_fixture_keys_are_detected_and_rejected() -> None:
    fingerprints = fixture_key_fingerprints(REPO)
    assert fingerprints, "fixture-ключи Phase 13 должны находиться в testdata"
    test_key = (REPO / "infra" / "release" / "testdata" / "test_key.pub").read_text().strip()
    data = {"pilot-test-key": {"key": test_key, "revoked": False}}
    with pytest.raises(TrustStoreError) as excinfo:
        assert_no_fixture_keys(data, fingerprints)
    assert excinfo.value.code == "fixture_key_in_production"


def test_real_looking_store_is_allowed() -> None:
    fingerprints = fixture_key_fingerprints(REPO)
    data = {"pilot-release-2026": {"key": PUBLIC_KEY_B, "revoked": False}}
    assert_no_fixture_keys(data, fingerprints)


def test_load_from_file_and_cli(tmp_path: Path) -> None:
    path = tmp_path / "trust-store.json"
    path.write_text(
        json.dumps({"pilot-release-2026": {"key": PUBLIC_KEY_A, "revoked": False}}),
        encoding="utf-8",
    )
    assert load_trust_store_file(path)["pilot-release-2026"]["key"] == PUBLIC_KEY_A

    import trust_store

    assert trust_store.main(["validate", "--file", str(path), "--json"]) == 0
    assert trust_store.main(["describe", "--file", str(path), "--json"]) == 0
    assert trust_store.main(["validate", "--file", str(tmp_path / "missing.json")]) == 1


def test_default_env_json_is_flat_and_engine_compatible() -> None:
    """Формат обязан совпадать с UPDATE_CHANNEL_PUBLIC_KEYS и host-движком."""
    data = {"pilot-release-2026": {"key": PUBLIC_KEY_A, "revoked": False}}
    parsed = json.loads(keys_json_for_env(data))
    assert set(parsed) == {"pilot-release-2026"}
    assert set(parsed["pilot-release-2026"]) == {"key", "revoked"}


# --- Phase 14: backend (клиентский контур) и release-валидатор совпадают ---------


def _release_rejects(text: str) -> str | None:
    """Код ошибки release-валидатора или None, если набор принят."""
    try:
        parse_trust_store(text)
    except TrustStoreError as exc:
        return exc.code
    return None


def _backend_rejects(text: str) -> str | None:
    """Код ошибки backend-валидатора или None, если набор принят."""
    from app.channel import ChannelError
    from app.trust_store import parse_trust_store_text

    try:
        parse_trust_store_text(text)
    except ChannelError as exc:
        return exc.code
    return None


@pytest.mark.parametrize(
    "payload",
    [
        '{"k1": {"key": "%s", "revoked": false}}',
        '{"k1": {"key": "%s", "revoked": true}, "k2": {"key": "%s", "revoked": false}}',
        '{"k1": {"key": "%s", "revoked": false}, "k1": {"key": "%s", "revoked": false}}',
        '{"k1": {"key": "%s", "revoked": "нет"}}',
        '{"k1": {"key": "-----BEGIN PRIVATE KEY-----", "revoked": false}}',
        '{"k1": {"key": "%s", "revoked": false, "extra": 1}}',
        '{"bad id!": {"key": "%s", "revoked": false}}',
        '{"k1": {"key": "%s", "revoked": false}}',
        "[]",
    ],
)
def test_backend_and_release_validators_agree(payload: str) -> None:
    """Оба валидатора обязаны одинаково принимать/отвергать один и тот же вход.

    Backend исполняет клиентскую проверку, release-пайплайн — серверную:
    расхождение правил означало бы, что принятый релиз не примет клиент.
    """
    text = payload.replace("%s", PUBLIC_KEY_A)
    if text == '{"k1": {"key": "%s", "revoked": false}}':  # pragma: no cover
        text = text.replace("%s", PUBLIC_KEY_A)
    release_code = _release_rejects(text)
    backend_code = _backend_rejects(text)
    assert (release_code is None) == (backend_code is None), (
        f"расхождение вердиктов: release={release_code}, backend={backend_code}, вход={text[:60]}"
    )


def test_backend_validator_is_strict_on_real_inputs() -> None:
    from app.channel import ChannelError
    from app.trust_store import describe_trust_store as backend_describe
    from app.trust_store import parse_trust_store_text

    private_hex = "a" * 64
    with pytest.raises(ChannelError):
        parse_trust_store_text(_payload({"key": private_hex, "revoked": False}, key_id="k1"))
    with pytest.raises(ChannelError):
        parse_trust_store_text(_payload({"key": PUBLIC_KEY_A[:-2], "revoked": False}, key_id="k1"))
    assert parse_trust_store_text(
        _payload({"key": PUBLIC_KEY_A, "revoked": False}, key_id="k1")
    ) == {"k1": {"key": PUBLIC_KEY_A, "revoked": False}}
    # Полностью отозванный набор — валидное аварийное состояние backend
    # (канал честно отвечает revoked_key), но release его не собирает.
    assert parse_trust_store_text(
        _payload({"key": PUBLIC_KEY_A, "revoked": True}, key_id="k1")
    ) == {"k1": {"key": PUBLIC_KEY_A, "revoked": True}}
    assert (
        _release_rejects(_payload({"key": PUBLIC_KEY_A, "revoked": True}, key_id="k1"))
        == "all_keys_revoked"
    )
    assert backend_describe({"k1": {"key": PUBLIC_KEY_A, "revoked": True}}) == [
        {"key_id": "k1", "fingerprint": key_fingerprint(PUBLIC_KEY_A), "revoked": True}
    ]
