# -*- coding: utf-8 -*-
"""Phase 13/14 release channel pipeline: сборка пакета → внешний manifest →
подпись → НЕЗАВИСИМАЯ проверка публичным ключом клиента → артефакты.

Исполняемое ядро workflow `.github/workflows/update-channel.yml`
(workflow — тонкая обвязка, вся политика — здесь; тот же скрипт
прогоняется fixture-тестами `backend/tests/test_release_pipeline.py` без
production secret).

    python infra/release/publish_channel.py \
        --snapshot <dir с release.json> --version 0.14.0 \
        --release-sha <40-hex> --package-url <immutable https URL> \
        --private-key <файл закрытого ключа (hex/PEM)> \
        --key-id pilot-release-2026 \
        --public-keys-json <JSON trust store production-клиента> \
        --out-dir dist/channel

Phase 14 добавляет РЕЖИМ ВЫПУСКА и две независимые подписи:

* ``--release-mode test`` (по умолчанию) — fixture-ключи, как в CI-PR;
* ``--release-mode production`` — fail closed, если нет:
  - строго провалидированного production trust store (без fixture-ключей);
  - Authenticode-attestation от Windows-шага с ``mode=production``,
    ``signtool_verify_ok=true``, ``timestamp_present=true`` и совпадающим
    издателем;
  - совпадения SHA256 installer'а с attestation (никакой пересборки после
    подписи);
  - успешной независимой проверки Authenticode (``authenticode.py``), а
    также Ed25519-подписи канала доверенным публичным ключом.

Fail closed (оба режима):
  - нет signing key → ошибка, unsigned manifest НЕ создаётся никогда;
  - независимая проверка (trusted public key, размер и SHA256 пакета)
    не прошла → ошибка, артефакты не считаются готовыми;
  - в подписываемый пакет попадает приватный материал → ошибка;
  - публикуются только уже проверенные файлы (SHA256SUMS поверх них).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from authenticode import AuthentiCodeError, load_pem_certificates, verify_authenticode  # noqa: E402
from build_package import build_package  # noqa: E402
from channel_contract import (  # noqa: E402
    SEMVER_RE,
    ChannelError,
    sign_manifest,
    validate_manifest_fields,
    verify_signature,
)
from trust_store import (  # noqa: E402
    TrustStoreError,
    assert_no_fixture_keys,
    canonical_json,
    describe_trust_store,
    fixture_key_fingerprints,
    load_trust_store_file,
    trust_store_sha256,
)

RELEASE_MODES = ("test", "production")

# Каталоги, которые не попадают в публикуемый пакет: fixture-приватные ключи
# Phase 13 и локальные кеши не должны уезжать пользователю.
SNAPSHOT_EXCLUDED_DIRS = (
    "infra/release/testdata",
    ".git",
    ".github",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
)
PRIVATE_MARKER = b"PRIVATE KEY-----"
MAX_SCAN_FILE_BYTES = 8 * 1024 * 1024


class PolicyError(ChannelError):
    """Нарушение production-политики выпуска."""


def _read_private_key(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if "-----BEGIN" in text:  # PEM (OpenSSL): конвертируем в hex
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        key = serialization.load_pem_private_key(text.encode(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ChannelError("bad_key", "PEM не является Ed25519-ключом")
        raw = key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        return raw.hex()
    if len(text) == 64 and all(c in "0123456789abcdef" for c in text):
        return text
    raise ChannelError("bad_key", "закрытый ключ должен быть 64 hex-символами или PEM")


def _load_public_keys(value: str) -> dict:
    """Trust store production-клиента: JSON {key_id: {key, revoked}} или файл."""
    candidate = value.strip()
    path = Path(candidate)
    if path.is_file():
        candidate = path.read_text(encoding="utf-8")
    data = json.loads(candidate)
    if not isinstance(data, dict) or not data:
        raise ChannelError("bad_key_set", "набор публичных ключей пуст или не объект")
    return data


def _read_public_key(trusted: dict, key_id: str) -> str:
    entry = trusted.get(key_id)
    if not isinstance(entry, dict) or not isinstance(entry.get("key"), str):
        raise ChannelError("unknown_key", f"ключ {key_id!r} отсутствует в trust store")
    if entry.get("revoked"):
        raise ChannelError("revoked_key", f"ключ {key_id!r} отозван")
    return entry["key"]


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prepare_snapshot(snapshot: Path, staging_root: Path) -> Path:
    """Копия снимка без fixture-тестов и кешей + проверка на приватный материал."""
    target = staging_root / "app"
    shutil.copytree(
        snapshot,
        target,
        ignore=shutil.ignore_patterns(
            *[Path(item).name for item in SNAPSHOT_EXCLUDED_DIRS], ".git", "*.pyc"
        ),
    )
    # shutil.ignore_patterns работает по именам: убираем вложенные пути точно.
    for relative in SNAPSHOT_EXCLUDED_DIRS:
        candidate = target / relative
        if candidate.exists():
            shutil.rmtree(candidate, ignore_errors=True)
    if not (target / "release.json").exists():
        raise ChannelError("bad_release_json", "в снимке нет release.json")
    for file in sorted(target.rglob("*")):
        if not file.is_file() or file.stat().st_size > MAX_SCAN_FILE_BYTES:
            continue
        if PRIVATE_MARKER in file.read_bytes():
            raise ChannelError(
                "private_material_in_package",
                f"в публикуемом пакете найден приватный ключ: {file.relative_to(target)}",
            )
    return target


def _load_attestation(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PolicyError(
            "missing_authenticode_attestation",
            "production-релиз требует authenticode-attestation от Windows-шага",
        ) from exc
    if not isinstance(data, dict):
        raise PolicyError("bad_authenticode_attestation", "attestation обязан быть JSON-объектом")
    return data


def _enforce_production_policy(
    *,
    args: argparse.Namespace,
    trusted: dict,
    installer: Path | None,
    fixture_fingerprints: set[str],
    snapshot_trust_store: Path | None,
) -> dict:
    """Полный fail-closed контракт production-релиза."""
    if args.release_mode != "production":
        return {}
    expected_publisher = (args.expected_publisher or "").strip()
    if not expected_publisher:
        raise PolicyError(
            "missing_expected_publisher",
            "production-релиз требует --expected-publisher (из защищённого release input)",
        )

    assert_no_fixture_keys(trusted, fixture_fingerprints)
    if installer is None:
        raise PolicyError(
            "missing_installer",
            "production-релиз требует --installer: подписанный Setup.exe обязателен",
        )
    if not args.authenticode_roots:
        raise PolicyError(
            "missing_authenticode_roots",
            "production-релиз требует --authenticode-roots: цепочка подписи обязана "
            "доводиться до корня из защищённого release input",
        )
    attestation = _load_attestation(Path(args.authenticode_attestation))
    if attestation.get("mode") != "production":
        raise PolicyError(
            "test_certificate_in_production",
            "attestation помечена как тестовая: test-сертификат не проходит production policy",
        )
    if attestation.get("authenticode_present") is not True:
        raise PolicyError("installer_unsigned", "attestation сообщает об отсутствии Authenticode")
    if attestation.get("signtool_verify_ok") is not True:
        raise PolicyError(
            "signtool_verification_failed",
            "signtool verify /pa /all не подтвердил подпись installer'а",
        )
    if attestation.get("timestamp_present") is not True:
        raise PolicyError("missing_timestamp", "у Authenticode-подписи нет валидной метки времени")
    attestation_publisher = str(attestation.get("publisher") or "").strip()
    if attestation_publisher.casefold() != expected_publisher.casefold():
        raise PolicyError(
            "publisher_mismatch",
            "издатель в attestation не совпал с ожидаемым (--expected-publisher)",
        )
    installer_sha = _sha256_file(installer)
    if attestation.get("installer_sha256") != installer_sha:
        raise PolicyError(
            "installer_changed_after_signing",
            "SHA256 installer'а изменился после подписи (пересборка/подмена запрещены)",
        )

    # Независимая проверка Authenticode прямо здесь (не доверяем одному
    # attestation: проверяем содержимое подписи и Authenticode-хеш файла).
    roots = load_pem_certificates(Path(args.authenticode_roots))
    result = verify_authenticode(
        installer,
        expected_publisher=expected_publisher,
        require_timestamp=True,
        trust_roots=roots,
        timestamp_roots=roots,
    )
    if result["signer_thumbprint_sha256"] != attestation.get("signer_thumbprint_sha256"):
        raise PolicyError(
            "attestation_mismatch",
            "отпечаток сертификата подписанта не совпал с attestation",
        )

    # Встроенный в installer trust store обязан совпасть с release trust store:
    # подмена набора ключей на этапе подписи обязана ломать выпуск.
    attestation_store = attestation.get("trust_store")
    if not isinstance(attestation_store, dict):
        raise PolicyError(
            "missing_trust_store_attestation",
            "attestation не подтверждает встроенный в installer trust store",
        )
    if attestation_store.get("sha256") != trust_store_sha256(trusted):
        raise PolicyError(
            "installer_trust_store_mismatch",
            "trust store, встроенный в installer, не совпадает с release trust store",
        )

    if snapshot_trust_store is not None:
        embedded = load_trust_store_file(snapshot_trust_store)
        if trust_store_sha256(embedded) != trust_store_sha256(trusted):
            raise PolicyError(
                "embedded_trust_store_mismatch",
                "встроенный в пакет trust store не совпадает с release trust store",
            )
    return {
        "installer": {
            "file": installer.name,
            "sha256": installer_sha,
            "publisher": result["publisher"],
            "signer_subject": result["signer_subject"],
            "signer_thumbprint_sha256": result["signer_thumbprint_sha256"],
            "signer_not_after": result["signer_not_after"],
            "timestamp_present": result["timestamp_present"],
            "timestamp_gen_time": result["timestamp"].get("gen_time"),
            "tsa_thumbprint_sha256": result["timestamp"].get("tsa_thumbprint_sha256"),
            "signtool_verify_ok": True,
            "chain_verified": result["chain_verified"],
            "chain_root_checked": True,
            "trust_store_sha256": attestation_store.get("sha256"),
            "trust_store_key_ids": sorted(
                str(entry.get("key_id"))
                for entry in attestation_store.get("keys", [])
                if isinstance(entry, dict)
            ),
            "verified_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, help="снимок приложения (release.json)")
    parser.add_argument("--version", required=True, help="SemVer релиза")
    parser.add_argument("--release-sha", required=True, help="полный Git commit (40 hex)")
    parser.add_argument("--package-url", required=True, help="immutable HTTPS URL пакета")
    parser.add_argument("--channel", default="stable")
    parser.add_argument("--minimum-supported-version", required=True)
    parser.add_argument("--notes-ru", default="")
    parser.add_argument("--published-at", default=None, help="ISO UTC (по умолчанию now)")
    parser.add_argument("--private-key", help="файл закрытого ключа (hex/PEM)")
    parser.add_argument("--key-id", default="pilot-release", help="key_id подписи")
    parser.add_argument("--public-keys-json", dest="public_keys_json", help="trust store клиента")
    parser.add_argument(
        "--trust-store",
        dest="trust_store",
        help="trust store (строгая схема Phase 14); в production обязателен",
    )
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--release-mode",
        choices=RELEASE_MODES,
        default="test",
        help="test (CI-PR, fixture-ключи) или production (две подписи, fail closed)",
    )
    parser.add_argument("--installer", help="подписанный HR-Manager-Setup-<version>.exe")
    parser.add_argument(
        "--authenticode-attestation",
        dest="authenticode_attestation",
        help="attestation от Windows-шага (signtool verify + метка времени)",
    )
    parser.add_argument("--authenticode-roots", help="PEM с доверенными корнями Authenticode")
    parser.add_argument("--expected-publisher", help="ожидаемый издатель сертификата")
    parser.add_argument(
        "--release-metadata",
        default=None,
        help="куда записать release-metadata.json (по умолчанию <out-dir>/release-metadata.json)",
    )
    args = parser.parse_args()

    try:
        if not SEMVER_RE.match(args.version):
            raise ChannelError("bad_semver", f"некорректный SemVer: {args.version!r}")
        if len(args.release_sha) != 40 or any(c not in "0123456789abcdef" for c in args.release_sha):
            raise ChannelError("bad_release_sha", "release_sha должен быть 40 hex-символами")
        if not args.package_url.startswith("https://"):
            raise ChannelError("bad_url", "package_url обязан использовать https")
        if "?" in args.package_url or "#" in args.package_url:
            raise ChannelError("bad_url", "package_url не должен содержать query/fragment")
        if args.release_mode == "production" and not args.trust_store:
            raise PolicyError(
                "missing_trust_store",
                "production-релиз требует --trust-store (публичный trust store из защищённого входа)",
            )

        snapshot = Path(args.snapshot)
        release_json = snapshot / "release.json"
        release_data = json.loads(release_json.read_text(encoding="utf-8"))
        if release_data.get("version") != args.version:
            raise ChannelError(
                "bad_release_json", f"release.json version={release_data.get('version')!r}"
            )
        if release_data.get("release_sha") != args.release_sha:
            raise ChannelError(
                "bad_release_json", f"release.json release_sha={release_data.get('release_sha')!r}"
            )

        trust_store_path = args.trust_store or args.public_keys_json
        if not trust_store_path:
            raise ChannelError(
                "missing_trust_store", "нужен --trust-store (или --public-keys-json): без него нет независимой проверки"
            )
        try:
            trusted = load_trust_store_file(Path(trust_store_path))
        except TrustStoreError as exc:
            raise ChannelError(exc.code, str(exc)) from exc
        fixture_fingerprints = fixture_key_fingerprints(Path(__file__).resolve().parents[2])

        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        package_name = f"hr-manager-windows-{args.version}.zip"
        package_path = out_dir / package_name

        with tempfile.TemporaryDirectory() as staging:
            prepared = _prepare_snapshot(snapshot, Path(staging))
            embedded_trust_store = prepared / "infra" / "release" / "trust-store.json"
            installer_metadata = _enforce_production_policy(
                args=args,
                trusted=trusted,
                installer=Path(args.installer) if args.installer else None,
                fixture_fingerprints=fixture_fingerprints,
                snapshot_trust_store=embedded_trust_store if embedded_trust_store.exists() else None,
            )
            # 1a. Детерминированное встраивание ПУБЛИЧНОГО trust store в
            #     пакет: клиент получает ровно тот набор ключей, которым
            #     проверен выпуск (канонический JSON, сортировка ключей).
            #     Проверка «снимок собран с другим trust store» выполнена
            #     выше (_enforce_production_policy), здесь только запись.
            embedded_target = prepared / "infra" / "release" / "trust-store.json"
            embedded_target.parent.mkdir(parents=True, exist_ok=True)
            embedded_target.write_text(canonical_json(trusted), encoding="utf-8")

            # 1b. Детерминированный пакет (закреплённая toolchain сборки zip).
            build_package(prepared, package_path, args.version, args.release_sha)

        package_size = package_path.stat().st_size
        package_sha256 = _sha256_file(package_path)

        # 2. Внешний manifest (update-channel.json).
        published_at = args.published_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        manifest = {
            "schema_version": 1,
            "channel": args.channel,
            "version": args.version,
            "release_sha": args.release_sha,
            "package_url": args.package_url,
            "package_size": package_size,
            "package_sha256": package_sha256,
            "minimum_supported_version": args.minimum_supported_version,
            "published_at": published_at,
            "notes_ru": args.notes_ru,
        }
        validate_manifest_fields(manifest)

        # 3. Подпись — только ключом из секрета. Без ключа: ошибка, unsigned
        #    stable manifest НИКОГДА не создаётся (fail closed).
        if not args.private_key:
            raise ChannelError(
                "missing_signing_key",
                "нет signing key: unsigned stable manifest публиковать нельзя",
            )
        if args.release_mode == "production" and args.key_id not in trusted:
            raise PolicyError(
                "signing_key_not_trusted",
                f"key_id {args.key_id!r} отсутствует в production trust store клиента",
            )
        private_hex = _read_private_key(Path(args.private_key))
        signed = sign_manifest(manifest, args.key_id, private_hex)
        manifest_path = out_dir / "update-channel.json"
        manifest_path.write_text(
            json.dumps(signed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        # 4. НЕЗАВИСИМАЯ проверка тем ключом, которому доверяет клиент:
        #    подпись + размер/SHA256 пакета против manifest.
        public_key = _read_public_key(trusted, args.key_id)
        verify_signature(signed, public_key)
        if signed["package_size"] != package_size:
            raise ChannelError("package_hash_mismatch", "размер пакета не совпал с manifest")
        if signed["package_sha256"] != package_sha256:
            raise ChannelError("package_hash_mismatch", "SHA256 пакета не совпал с manifest")

        # 5. release-metadata.json: публичные факты выпуска (без секретов),
        #    включая trust store и Authenticode-аттестацию.
        metadata = {
            "product": "hr-manager-windows-pilot",
            "release_mode": args.release_mode,
            "version": args.version,
            "release_sha": args.release_sha,
            "channel": args.channel,
            "published_at": published_at,
            "package": {
                "file": package_name,
                "size": package_size,
                "sha256": package_sha256,
            },
            "signature": {
                "scheme": signed["signature"]["scheme"],
                "key_id": signed["signature"]["key_id"],
                "sig_prefix": signed["signature"]["sig"][:32],
            },
            "trust_store": {
                "sha256": trust_store_sha256(trusted),
                "keys": describe_trust_store(trusted),
            },
            "authenticode": installer_metadata.get("installer"),
        }
        metadata_path = (
            Path(args.release_metadata)
            if args.release_metadata
            else out_dir / "release-metadata.json"
        )
        metadata_path.write_text(canonical_json(metadata), encoding="utf-8")

        # 6. Публичный trust store выпуска — отдельным артефактом (его
        #    применяет install/channel-config; секретов в нём нет по схеме).
        trust_store_path_out = out_dir / "trust-store.json"
        trust_store_path_out.write_text(canonical_json(trusted), encoding="utf-8")

        # 7. SHA256SUMS поверх проверенных артефактов.
        artifacts = [package_path, manifest_path, metadata_path, trust_store_path_out]
        sums = "\n".join(f"{_sha256_file(item)}  {item.name}" for item in artifacts)
        (out_dir / "SHA256SUMS").write_text(sums + "\n", encoding="utf-8")

        print(f"канал собран и проверен: {args.version} (release_sha {args.release_sha[:12]})")
        print(f"режим: {args.release_mode}")
        print(f"пакет: {package_path} ({package_size} байт, sha256 {package_sha256[:16]}…)")
        print(f"manifest: {manifest_path} (key_id={args.key_id!r}, подпись проверена)")
        if installer_metadata:
            print("Authenticode installer проверен независимо (production policy)")
        print(f"артефакты: {out_dir}")
        return 0
    except (ChannelError, AuthentiCodeError, TrustStoreError) as exc:
        code = getattr(exc, "code", "error")
        print(f"ОШИБКА[{code}]: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"ОШИБКА: файл не найден: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
