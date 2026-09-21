# -*- coding: utf-8 -*-
"""Phase 14: строгая валидация и детерминированная подготовка trust store
канала обновлений Windows-пилота.

Trust store — это ЕДИНСТВЕННЫЙ источник доверия клиента к подписи канала.
Формат (совместим с Phase 13 `UPDATE_CHANNEL_PUBLIC_KEYS` и с host-движком):

    {
      "<key_id>": {"key": "<base64 32 байта Ed25519>", "revoked": false},
      ...
    }

Инварианты (fail closed, все проверяются здесь и зеркалятся в движке):

* плоский объект: каждый ключ верхнего уровня — ``key_id`` (никаких
  служебных полей, иначе backend/движок примет их за ключи);
* ``key`` — канонический base64 ровно 32 байт (публичный ключ Ed25519);
* ``revoked`` — обязательный булев флаг (отзыв немедленный и fail closed);
* уникальные ``key_id`` (повтор ключа JSON в файле — отказ);
* отсутствие приватного материала (PEM/hex-приватный ключ/JWK ``d``);
* хотя бы один НЕ отозданный ключ (иначе доверия нет вовсе);
* никакого «доверять всем» и никакого неявного dev-набора.

Клиенту и в диагностику попадают только ``key_id`` и отпечаток
(``SHA256:<hex16>``); приватный материал в репозиторий, installer и логи не
попадает никогда.

CLI:

    python infra/release/trust_store.py validate --file trust-store.json [--json]
    python infra/release/trust_store.py describe --file trust-store.json [--json]
    python infra/release/trust_store.py merge --current a.json --update b.json --out c.json
    python infra/release/trust_store.py ci-contract [--file <fixture trusted_keys.json>]
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import sys
from pathlib import Path

# Разумный предел: trust store — маленький публичный документ.
TRUST_STORE_MAX_BYTES = 64 * 1024
ED25519_PUBLIC_KEY_BYTES = 32
KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Маркеры приватного материала: даже если такой файл случайно попадёт в
# release input, он не должен быть принят.
PRIVATE_MATERIAL_MARKERS = (
    "-----BEGIN",
    "PRIVATE KEY",
    "OPENSSH PRIVATE",
    "PuTTY-User-Key-File",
    "BEGIN RSA",
    "BEGIN EC",
)
# Приватный Ed25519-ключ Phase 13 — 64 hex-символа; значение поля key
# такой длины обязано быть отвергнуто.
PRIVATE_HEX_KEY_LENGTH = 64


class TrustStoreError(ValueError):
    """Ошибка trust store: безопасный код + человекочитаемое сообщение."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    data: dict[str, object] = {}
    for key, value in pairs:
        if key in data:
            raise TrustStoreError("duplicate_key_id", f"повторяющийся ключ JSON: {key!r}")
        data[key] = value
    return data


def assert_no_private_material(text: str) -> None:
    """Отказ при любом признаке приватного ключа в исходном тексте."""
    upper = text.upper()
    for marker in PRIVATE_MATERIAL_MARKERS:
        if marker.upper() in upper:
            raise TrustStoreError(
                "private_material",
                "trust store содержит приватный материал — публичный ключ обязан быть публичным",
            )


def _decode_public_key(value: object, key_id: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise TrustStoreError("bad_key", f"у ключа {key_id!r} нет значения key")
    candidate = value.strip()
    if len(candidate) == PRIVATE_HEX_KEY_LENGTH and all(
        c in "0123456789abcdefABCDEF" for c in candidate
    ):
        raise TrustStoreError(
            "private_material",
            f"значение key у {key_id!r} похоже на приватный hex-ключ, а не на публичный",
        )
    try:
        raw = base64.b64decode(candidate, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise TrustStoreError("bad_key", f"у ключа {key_id!r} некорректный base64: {exc}") from exc
    if len(raw) != ED25519_PUBLIC_KEY_BYTES:
        raise TrustStoreError(
            "bad_key",
            f"публичный ключ {key_id!r} должен быть {ED25519_PUBLIC_KEY_BYTES} байтами "
            f"Ed25519 (получено {len(raw)})",
        )
    return raw


def parse_trust_store(text: str) -> dict[str, dict]:
    """Разбор и ПОЛНАЯ валидация trust store из текста."""
    if len(text.encode("utf-8")) > TRUST_STORE_MAX_BYTES:
        raise TrustStoreError("too_large", "trust store превышает допустимый размер")
    assert_no_private_material(text)
    try:
        data = json.loads(text, object_pairs_hook=_no_duplicates)
    except TrustStoreError:
        raise
    except Exception as exc:
        raise TrustStoreError("malformed_json", f"некорректный JSON trust store: {exc}") from exc
    if not isinstance(data, dict):
        raise TrustStoreError("bad_schema", "trust store должен быть JSON-объектом {key_id: {...}}")
    validate_trust_store(data)
    return data


def load_trust_store_file(path: Path) -> dict[str, dict]:
    try:
        # utf-8-sig handles BOM that git on Windows may produce; CRLF is fine (JSON allows whitespace).
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise TrustStoreError("not_found", f"файл trust store не найден: {path}") from exc
    return parse_trust_store(text)


def validate_trust_store(data: object, *, allow_empty: bool = False) -> None:
    """Проверка уже разобранного trust store (без чтения файла)."""
    if not isinstance(data, dict):
        raise TrustStoreError("bad_schema", "trust store должен быть JSON-объектом")
    if not data and not allow_empty:
        raise TrustStoreError("empty_store", "trust store пуст: доверять нечему (fail closed)")
    active = 0
    for key_id, entry in data.items():
        if not isinstance(key_id, str) or not KEY_ID_RE.match(key_id):
            raise TrustStoreError(
                "bad_key_id",
                f"некорректный key_id {key_id!r}: допустимы буквы, цифры, '.', '_', '-'",
            )
        if not isinstance(entry, dict):
            raise TrustStoreError("bad_schema", f"запись ключа {key_id!r} должна быть объектом")
        unknown = sorted(set(entry) - {"key", "revoked"})
        if unknown:
            raise TrustStoreError(
                "unknown_field",
                f"у ключа {key_id!r} неизвестные поля: {', '.join(unknown)}",
            )
        _decode_public_key(entry.get("key"), key_id)
        revoked = entry.get("revoked")
        if not isinstance(revoked, bool):
            raise TrustStoreError(
                "bad_revoked",
                f"у ключа {key_id!r} обязателен булев флаг revoked (отзыв fail closed)",
            )
        if not revoked:
            active += 1
    if active == 0 and not allow_empty:
        raise TrustStoreError(
            "all_keys_revoked",
            "все ключи trust store отозваны: активного доверия нет (fail closed)",
        )


def key_fingerprint(key_b64: str) -> str:
    """Детерминированный публичный отпечаток ключа (не секрет)."""
    raw = base64.b64decode(key_b64.strip(), validate=True)
    return "SHA256:" + hashlib.sha256(raw).hexdigest()[:16]


def describe_trust_store(data: dict[str, dict]) -> list[dict[str, object]]:
    """Безопасное описание для диагностики и release-метаданных.

    Возвращаются только ``key_id``, отпечаток и статус отзыва — сам ключ
    (публичный, но ненужный) в отчёты не попадает.
    """
    return [
        {"key_id": key_id, "fingerprint": key_fingerprint(entry["key"]), "revoked": entry["revoked"]}
        for key_id, entry in sorted(data.items())
    ]


def canonical_json(data: dict[str, dict]) -> str:
    """Детерминированное представление: сортировка ключей, LF, UTF-8, финал LF."""
    return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def trust_store_sha256(data: dict[str, dict]) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def merge_trust_store(current: dict[str, dict], update: dict[str, dict]) -> dict[str, dict]:
    """Ротация Phase 13: двухключевое окно + отзыв, без потери старых ключей.

    Новый набор = старый набор, обновлённый записями из ``update``. Удаление
    существующего ``key_id`` запрещено (это сломало бы двухключевое окно);
    отзыв выполняется явным ``revoked: true``.
    """
    validate_trust_store(current)
    validate_trust_store(update)
    merged = {key_id: dict(entry) for key_id, entry in current.items()}
    for key_id, entry in update.items():
        merged[key_id] = dict(entry)
    validate_trust_store(merged)
    return merged


def fixture_key_fingerprints(repo_root: Path) -> set[str]:
    """Отпечатки ТЕСТОВЫХ ключей репозитория (fixture Phase 13).

    Production policy обязана отказать, если такой ключ попал в trust store:
    fixture-ключ никогда не становится доверенным production-ключом.
    """
    fingerprints: set[str] = set()
    testdata = repo_root / "infra" / "release" / "testdata"
    for name in ("test_key.pub", "revoked_key.pub", "other_key.pub"):
        path = testdata / name
        if not path.exists():
            continue
        value = path.read_text(encoding="utf-8").strip()
        try:
            fingerprints.add(key_fingerprint(value))
        except Exception:
            continue
    trusted = testdata / "trusted_keys.json"
    if trusted.exists():
        try:
            data = json.loads(trusted.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if isinstance(data, dict):
            for entry in data.values():
                if isinstance(entry, dict) and isinstance(entry.get("key"), str):
                    try:
                        fingerprints.add(key_fingerprint(entry["key"]))
                    except Exception:
                        continue
    return fingerprints


def assert_no_fixture_keys(data: dict[str, dict], fixture_fingerprints: set[str]) -> None:
    """Production policy: тестовые ключи репозитория не бывают доверенными."""
    for entry in describe_trust_store(data):
        if entry["fingerprint"] in fixture_fingerprints:
            raise TrustStoreError(
                "fixture_key_in_production",
                f"ключ {entry['key_id']!r} — тестовый fixture-ключ репозитория; "
                "production trust store обязан содержать только production-ключи",
            )


def assert_no_revoked_keys(data: dict[str, dict]) -> None:
    """Production/release-контракт: в наборе не должно быть отозванных ключей.

    Используется и флагом ``validate --require-unrevoked``, и проверкой
    CI-набора (которая обязана убедиться, что этот отказ по-прежнему работает).
    """
    for key_id, entry in data.items():
        if entry.get("revoked") is True:
            raise TrustStoreError(
                "revoked_key_present",
                f"ключ {key_id!r} отозван (revoked:true) — требуется набор без отозванных ключей",
            )


def keys_json_for_env(data: dict[str, dict]) -> str:
    """Компактная JSON-строка для переменной окружения/installer."""
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _cmd_validate(args: argparse.Namespace) -> int:
    file_path = Path(args.file)
    data = load_trust_store_file(file_path)
    if args.require_unrevoked:
        # Строгая production-проверка: ни один ключ не должен быть отозван.
        # Fixture-набор с revoked (для негативных тестов) намеренно содержит
        # отозванный ключ и не должен проверяться с этим флагом — CI проверяет
        # его подкомандой ``ci-contract``, а production/release — этим флагом.
        assert_no_revoked_keys(data)
        validate_trust_store(data, allow_empty=False)
    if args.json:
        payload = {
            "ok": True,
            "sha256": trust_store_sha256(data),
            "keys": describe_trust_store(data),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"trust store валиден: {len(data)} ключ(а), sha256 {trust_store_sha256(data)[:16]}…")
    return 0


def _cmd_describe(args: argparse.Namespace) -> int:
    data = load_trust_store_file(Path(args.file))
    payload = {"sha256": trust_store_sha256(data), "keys": describe_trust_store(data)}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for entry in payload["keys"]:
            status = "отозван" if entry["revoked"] else "активен"
            print(f"{entry['key_id']}: {entry['fingerprint']} ({status})")
    return 0


def _cmd_merge(args: argparse.Namespace) -> int:
    current = load_trust_store_file(Path(args.current))
    update = load_trust_store_file(Path(args.update))
    merged = merge_trust_store(current, update)
    Path(args.out).write_text(canonical_json(merged), encoding="utf-8")
    print(f"trust store объединён: {len(merged)} ключ(а) → {args.out}")
    return 0


def _cmd_ci_contract(args: argparse.Namespace) -> int:
    """Проверка CI-набора fixtures — отдельный контракт от production-политики.

    Fixture-набор намеренно содержит ОТОЗВАННЫЙ ключ: на нём проверяются
    негативные сценарии канала. Поэтому его нельзя проверять флагом
    ``--require-unrevoked`` (тот описывает production/release-набор). Здесь
    фиксируется ровно то, что обязан гарантировать CI:

    1. файл проходит строгую схему trust store;
    2. активный и отозванный ключи на месте и помечены верно;
    3. production-политика по-прежнему ОТКАЗЫВАЕТ этому набору — и по отзыву
       (``--require-unrevoked``), и по fixture-отпечаткам
       (``assert_no_fixture_keys``). То есть ослабления production-политики
       ради зелёного CI не произошло.
    """
    repo_root = Path(__file__).resolve().parents[2]
    file_path = Path(args.file) if args.file else (
        repo_root / "infra" / "release" / "testdata" / "trusted_keys.json"
    )
    # Перекрёстная сверка SHA: CI-шаг передаёт хеш, посчитанный в PowerShell
    # (Get-FileHash) до вызова инструмента, и требует, чтобы инструмент
    # прочитал ровно тот же файл. Расхождение — отказ (fail closed).
    raw_sha256 = hashlib.sha256(file_path.read_bytes()).hexdigest()
    if args.expected_sha256 and raw_sha256.lower() != str(args.expected_sha256).strip().lower():
        raise TrustStoreError(
            "ci_fixture_sha_mismatch",
            f"SHA256 trust store не совпал: файл={raw_sha256} ожидался={args.expected_sha256}",
        )
    data = load_trust_store_file(file_path)

    described = {entry["key_id"]: entry for entry in describe_trust_store(data)}
    expected = {
        args.active_key_id: False,
        args.revoked_key_id: True,
    }
    for key_id, revoked in expected.items():
        entry = described.get(key_id)
        if entry is None:
            raise TrustStoreError(
                "ci_fixture_missing_key",
                f"в CI-наборе нет ключа {key_id!r}: негативные сценарии останутся без проверки",
            )
        if entry["revoked"] is not revoked:
            raise TrustStoreError(
                "ci_fixture_bad_revoked_flag",
                f"ключ {key_id!r}: revoked={entry['revoked']!r}, ожидалось {revoked!r}",
            )

    # Production-инвариант 1: набор с отозванным ключом не проходит строгую
    # production-проверку (та же функция, что и у --require-unrevoked).
    try:
        assert_no_revoked_keys(data)
    except TrustStoreError as exc:
        if exc.code != "revoked_key_present":
            raise
        # ASCII-only: вывод идёт в pipe под cp1252 на Windows-раннере,
        # кириллица вызвала бы UnicodeEncodeError и ложный отказ CI.
        print("production policy: set with a revoked key was rejected (expected for CI fixture)")
    else:
        raise TrustStoreError(
            "ci_fixture_not_production_rejected",
            "CI-набор прошёл бы --require-unrevoked: отозванный ключ исчез из fixture",
        )

    # Production-инвариант 2: тестовые ключи репозитория не бывают доверенными.
    try:
        assert_no_fixture_keys(data, fixture_key_fingerprints(repo_root))
    except TrustStoreError as exc:
        if exc.code != "fixture_key_in_production":
            raise
        print("production policy: repository fixture keys were rejected (expected)")
    else:
        raise TrustStoreError(
            "ci_fixture_accepted_by_production",
            "CI-набор принят production-политикой: fixture-ключи попали в доверенные",
        )

    payload = {
        "ok": True,
        "file": str(file_path),
        "file_sha256": raw_sha256,
        "sha256": trust_store_sha256(data),
        "keys": describe_trust_store(data),
        "production_rejects_this_set": True,
    }
    # ensure_ascii (по умолчанию True): вывод должен оставаться чистым ASCII —
    # под cp1252-консолью Windows-раннера кириллица в pipe = UnicodeEncodeError.
    print(json.dumps(payload, indent=2))
    # Одна строка для записи в GITHUB_ENV без разбора JSON в PowerShell.
    print(f"TRUST_STORE_SHA256={raw_sha256}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 14 trust store tooling")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="строгая проверка trust store")
    validate.add_argument("--file", required=True)
    validate.add_argument("--require-unrevoked", action="store_true")
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=_cmd_validate)

    describe = sub.add_parser("describe", help="key_id + отпечатки (без секретов)")
    describe.add_argument("--file", required=True)
    describe.add_argument("--json", action="store_true")
    describe.set_defaults(func=_cmd_describe)

    ci_contract = sub.add_parser(
        "ci-contract",
        help="проверить CI-набор fixtures (и что production-политика его отклоняет)",
    )
    ci_contract.add_argument("--file", default="")
    ci_contract.add_argument("--active-key-id", dest="active_key_id", default="pilot-test-key")
    ci_contract.add_argument("--revoked-key-id", dest="revoked_key_id", default="pilot-revoked-key")
    ci_contract.add_argument(
        "--expected-sha256",
        dest="expected_sha256",
        default="",
        help="независимо посчитанный SHA256 файла: расхождение -> отказ",
    )
    ci_contract.set_defaults(func=_cmd_ci_contract)

    merge = sub.add_parser("merge", help="ротация: старый набор + новый набор")
    merge.add_argument("--current", required=True)
    merge.add_argument("--update", required=True)
    merge.add_argument("--out", required=True)
    merge.set_defaults(func=_cmd_merge)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except TrustStoreError as exc:
        print(f"ОШИБКА[{exc.code}]: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"ОШИБКА: файл не найден: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
