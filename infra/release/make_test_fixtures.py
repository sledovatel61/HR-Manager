# -*- coding: utf-8 -*-
"""Генерация детерминированных тестовых fixture канала обновлений.

ВСЁ здесь — ТЕСТОВЫЕ материалы: закрытый ключ fixture никогда не доверяется
production-клиентом и не публикует release (см. PHASE_13_PROMPT, раздел 6).

    python infra/release/make_test_fixtures.py

Генерирует в infra/release/testdata/:
    test_key.priv / test_key.pub        — основная тестовая пара
    revoked_key.priv / revoked_key.pub  — пара «отозванного» ключа
    other_key.pub                       — пара неизвестного ключа
    trusted_keys.json                   — набор доверенных ключей (с revoked)
    snapshot/                           — мини-снимок приложения
    package.valid.zip                   — детерминированный пакет
    manifest.valid.json                 — подписанный manifest (happy path)
    manifest.canonical.txt              — канонический payload (golden)
    manifest.*.json                     — негативные manifest'ы
    package.*.zip                       — атакующие/негативные пакеты
    semver_cases.json                   — таблица сравнений SemVer
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from channel_contract import canonical_bytes, sign_manifest  # noqa: E402

TESTDATA = Path(__file__).resolve().parent / "testdata"
SNAPSHOT = TESTDATA / "snapshot"
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

# Детерминированные ТЕСТОВЫЕ ключи (только для fixture; помечены маркером).
# Производные Ed25519 из фиксированного seed — длина ровно 64 hex (32 байта).
def _seed_key(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


TEST_PRIV = _seed_key("HR-MANAGER-PHASE13-TEST-KEY-0001")
REVOKED_PRIV = _seed_key("HR-MANAGER-PHASE13-REVOKED-KEY-0001")
OTHER_PRIV = _seed_key("HR-MANAGER-PHASE13-OTHER-KEY-0001")

RELEASE_SHA_VALID = "2" * 40
RELEASE_SHA_INSTALLED = "3" * 40  # «установленная» версия для негативных сценариев


def _write(name: str, data: bytes) -> None:
    path = TESTDATA / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    print(f"  {name} ({len(data)} байт)")


def _write_text(name: str, text: str) -> None:
    _write(name, text.encode("utf-8"))


def _pub_from_priv(priv_hex: str) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv_hex))
    return base64.b64encode(key.public_key().public_bytes_raw()).decode()


def _zip_entry(zf: zipfile.ZipFile, name: str, data: bytes, mode: int = 0o100644) -> None:
    info = zipfile.ZipInfo(name, date_time=ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = mode << 16
    zf.writestr(info, data)


def build_snapshot(release_sha: str = RELEASE_SHA_VALID, version: str = "0.14.0") -> None:
    for sub in ("backend", "frontend", "infra"):
        (SNAPSHOT / sub).mkdir(parents=True, exist_ok=True)
    (SNAPSHOT / "backend" / "marker.txt").write_text("fixture backend\n", encoding="utf-8")
    (SNAPSHOT / "frontend" / "marker.txt").write_text("fixture frontend\n", encoding="utf-8")
    (SNAPSHOT / "infra" / "compose.pilot.yml").write_text(
        "# test fixture overlay\nname: hr-manager-pilot\n", encoding="utf-8"
    )
    release = {"release_sha": release_sha, "version": version, "built_at": "2026-09-10T12:00:00Z"}
    (SNAPSHOT / "release.json").write_text(
        json.dumps(release, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def build_valid_package() -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for path in sorted(SNAPSHOT.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(SNAPSHOT).as_posix()
            _zip_entry(zf, relative, path.read_bytes())
    return buffer.getvalue()


def make_manifest(version: str, release_sha: str, package: bytes, schema: int = 1) -> dict:
    return {
        "schema_version": schema,
        "channel": "stable",
        "version": version,
        "release_sha": release_sha,
        "package_url": "https://updates.example.com/hrm/package.valid.zip",
        "package_size": len(package),
        "package_sha256": hashlib.sha256(package).hexdigest(),
        "minimum_supported_version": "0.13.0",
        "published_at": "2026-09-10T12:00:00Z",
        "notes_ru": "Тестовый канал обновлений (НЕ доверяется production-клиентом).",
    }


def _flip_hex(hex_str: str) -> str:
    first = hex_str[0]
    flipped = "0" if first != "0" else "1"
    return flipped + hex_str[1:]


def main() -> int:
    print("Генерация тестовых fixture канала обновлений:")
    _write_text("test_key.priv", TEST_PRIV + "\n")
    _write_text("test_key.pub", _pub_from_priv(TEST_PRIV) + "\n")
    _write_text("revoked_key.priv", REVOKED_PRIV + "\n")
    _write_text("revoked_key.pub", _pub_from_priv(REVOKED_PRIV) + "\n")
    _write_text("other_key.pub", _pub_from_priv(OTHER_PRIV) + "\n")
    trusted_keys = {
        "pilot-test-key": {"key": _pub_from_priv(TEST_PRIV), "revoked": False},
        "pilot-revoked-key": {"key": _pub_from_priv(REVOKED_PRIV), "revoked": True},
    }
    _write_text("trusted_keys.json", json.dumps(trusted_keys, indent=2) + "\n")

    build_snapshot()
    package = build_valid_package()
    _write("package.valid.zip", package)

    valid = make_manifest("0.14.0", RELEASE_SHA_VALID, package)
    signed = sign_manifest(valid, "pilot-test-key", TEST_PRIV)
    _write_text("manifest.valid.json", json.dumps(signed, ensure_ascii=False, indent=2) + "\n")
    _write_text("manifest.canonical.txt", canonical_bytes(valid).decode("utf-8"))

    # Негативные manifest'ы.
    bad_sig = json.loads(json.dumps(signed))
    bad_sig["signature"]["sig"] = _flip_hex(bad_sig["signature"]["sig"])
    _write_text("manifest.bad_sig.json", json.dumps(bad_sig, ensure_ascii=False) + "\n")

    unknown_key = sign_manifest(valid, "pilot-unknown-key", OTHER_PRIV)
    _write_text("manifest.unknown_key.json", json.dumps(unknown_key, ensure_ascii=False) + "\n")

    revoked = sign_manifest(valid, "pilot-revoked-key", REVOKED_PRIV)
    _write_text("manifest.revoked_key.json", json.dumps(revoked, ensure_ascii=False) + "\n")

    malformed_sig = json.loads(json.dumps(signed))
    malformed_sig["signature"]["sig"] = "zz" * 64
    _write_text("manifest.malformed_sig.json", json.dumps(malformed_sig, ensure_ascii=False) + "\n")

    tampered = json.loads(json.dumps(signed))
    tampered["version"] = "0.14.1"  # подпись покрывает version → невалидна
    _write_text("manifest.tampered.json", json.dumps(tampered, ensure_ascii=False) + "\n")

    # Неизвестная версия схемы отклоняется ДО проверки подписи: достаточно
    # взять валидно подписанный manifest и подменить schema_version.
    schema2 = json.loads(json.dumps(signed))
    schema2["schema_version"] = 2
    _write_text("manifest.schema2.json", json.dumps(schema2, ensure_ascii=False) + "\n")

    # Некорректный SemVer отклоняется на этапе валидации полей ДО проверки
    # подписи — fixture с заглушкой-подписью достаточен.
    bad_semver = make_manifest("1.0", RELEASE_SHA_VALID, package)
    bad_semver["signature"] = {
        "key_id": "pilot-test-key",
        "scheme": "ed25519",
        "sig": "00" * 64,
    }
    _write_text("manifest.bad_semver.json", json.dumps(bad_semver, ensure_ascii=False) + "\n")

    downgrade = make_manifest("0.12.0", RELEASE_SHA_VALID, package)
    _write_text(
        "manifest.downgrade.json",
        json.dumps(sign_manifest(downgrade, "pilot-test-key", TEST_PRIV), ensure_ascii=False)
        + "\n",
    )

    same_ver_diff_sha = make_manifest("0.13.0", "5" * 40, package)
    _write_text(
        "manifest.same_version_diff_sha.json",
        json.dumps(
            sign_manifest(same_ver_diff_sha, "pilot-test-key", TEST_PRIV), ensure_ascii=False
        )
        + "\n",
    )

    dup_keys = (
        '{"schema_version":1,"schema_version":1,"channel":"stable","version":"0.14.0"}'
    )
    _write_text("manifest.duplicate_key.json", dup_keys)

    # Атакующие пакеты (защита распаковки).
    def attack_zip(name: str, entries: list[tuple[str, bytes, int]]) -> None:
        import io

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            for entry_name, data, mode in entries:
                _zip_entry(zf, entry_name, data, mode)
        _write(name, buffer.getvalue())

    attack_zip(
        "package.zipslip.zip",
        [("../evil.txt", b"pwn", 0o100644), ("release.json", b"{}", 0o100644)],
    )
    attack_zip(
        "package.abs.zip",
        [("/abs/evil.txt", b"pwn", 0o100644), ("release.json", b"{}", 0o100644)],
    )
    attack_zip(
        "package.unc.zip",
        [("\\\\server\\share\\evil.txt", b"pwn", 0o100644), ("release.json", b"{}", 0o100644)],
    )
    attack_zip(
        "package.ads.zip",
        [("release.json:stream", b"pwn", 0o100644)],
    )
    attack_zip(
        "package.symlink.zip",
        [("link.txt", b"target", 0o120777), ("release.json", b"{}", 0o100644)],
    )
    attack_zip(
        "package.backslash.zip",
        [("backend\\evil.txt", b"pwn", 0o100644), ("release.json", b"{}", 0o100644)],
    )
    attack_zip(
        "package.extra_root.zip",
        [("unexpected.txt", b"pwn", 0o100644), ("release.json", b"{}", 0o100644)],
    )
    attack_zip(
        "package.exe.zip",
        [("backend/evil.exe", b"MZ", 0o100644), ("release.json", b"{}", 0o100644)],
    )
    attack_zip(
        "package.no_release.zip",
        [("backend/marker.txt", b"x", 0o100644)],
    )
    attack_zip(
        "package.mismatch_release.zip",
        [
            (
                "release.json",
                json.dumps(
                    {"release_sha": "6" * 40, "version": "0.14.0"}
                ).encode(),
                0o100644,
            ),
            ("backend/marker.txt", b"x", 0o100644),
        ],
    )

    # Таблица сравнений SemVer (общая для Python- и PowerShell-тестов).
    cases = [
        ["0.9.9", "0.10.0", -1],
        ["0.10.0", "0.9.9", 1],
        ["1.0.0", "1.0.0", 0],
        ["1.2.3", "1.2.4", -1],
        ["2.0.0", "10.0.0", -1],
        ["1.0.0-alpha", "1.0.0", -1],
        ["1.0.0-alpha.1", "1.0.0-alpha.2", -1],
        ["1.0.0-alpha.2", "1.0.0-alpha.10", -1],
        ["1.0.0-alpha", "1.0.0-alpha.1", -1],
        ["1.0.0-rc.1", "1.0.0", -1],
        ["1.0.0+build.1", "1.0.0+build.2", 0],
        ["1.0.0-alpha+build", "1.0.0-alpha+other", 0],
        ["1.0.0-1", "1.0.0-alpha", -1],  # числовой идентификатор < буквенного
        ["0.13.0", "0.14.0", -1],
        ["0.14.0", "0.13.0", 1],
    ]
    _write_text("semver_cases.json", json.dumps(cases, ensure_ascii=False, indent=1) + "\n")

    print("Готово.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
