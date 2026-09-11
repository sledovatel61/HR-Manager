# -*- coding: utf-8 -*-
"""Phase 13 release channel pipeline: сборка пакета → внешний manifest →
подпись → НЕЗАВИСИМАЯ проверка публичным ключом клиента → артефакты.

Исполняемое ядро workflow `.github/workflows/update-channel.yml`
(workflow — тонкая обвязка, вся политика — здесь; тот же скрипт
прогоняется fixture-тестом `backend/tests/test_release_pipeline.py` без
production secret).

    python infra/release/publish_channel.py \
        --snapshot <dir с release.json> --version 0.14.0 \
        --release-sha <40-hex> --package-url <immutable https URL> \
        --private-key <файл закрытого ключа (hex/PEM)> \
        --key-id pilot-release-2026 \
        --public-keys-json <JSON trust store production-клиента> \
        --out-dir dist/channel

Fail closed:
  - нет signing key → ошибка, unsigned manifest НЕ создаётся никогда;
  - независимая проверка (trusted public key, размер и SHA256 пакета)
    не прошла → ошибка, артефакты не считаются готовыми;
  - публикуются только уже проверенные файлы (SHA256SUMS поверх них).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_package import build_package  # noqa: E402
from channel_contract import (  # noqa: E402
    SEMVER_RE,
    ChannelError,
    parse_manifest_json,
    sign_manifest,
    validate_manifest_fields,
    verify_signature,
)


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
    parser.add_argument(
        "--public-keys-json",
        required=True,
        help="trust store production-клиента (JSON или файл с JSON)",
    )
    parser.add_argument("--out-dir", required=True)
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

        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        package_name = f"hr-manager-windows-{args.version}.zip"
        package_path = out_dir / package_name

        # 1. Детерминированный пакет (закреплённая toolchain сборки zip).
        build_package(snapshot, package_path, args.version, args.release_sha)
        package_size = package_path.stat().st_size
        package_sha256 = hashlib.sha256(package_path.read_bytes()).hexdigest()

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
        private_hex = _read_private_key(Path(args.private_key))
        signed = sign_manifest(manifest, args.key_id, private_hex)
        manifest_path = out_dir / "update-channel.json"
        manifest_path.write_text(
            json.dumps(signed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        # 4. НЕЗАВИСИМАЯ проверка тем ключом, которому доверяет клиент:
        #    подпись + размер/SHA256 пакета против manifest.
        trusted = _load_public_keys(args.public_keys_json)
        public_key = _read_public_key(trusted, args.key_id)
        verify_signature(signed, public_key)
        if signed["package_size"] != package_size:
            raise ChannelError("package_hash_mismatch", "размер пакета не совпал с manifest")
        if signed["package_sha256"] != package_sha256:
            raise ChannelError("package_hash_mismatch", "SHA256 пакета не совпал с manifest")

        # 5. SHA256SUMS поверх проверенных артефактов.
        sums = "\n".join(
            [
                f"{package_sha256}  {package_name}",
                f"{hashlib.sha256(manifest_path.read_bytes()).hexdigest()}  update-channel.json",
            ]
        )
        (out_dir / "SHA256SUMS").write_text(sums + "\n", encoding="utf-8")

        print(f"канал собран и проверен: {args.version} (release_sha {args.release_sha[:12]})")
        print(f"пакет: {package_path} ({package_size} байт, sha256 {package_sha256[:16]}…)")
        print(f"manifest: {manifest_path} (key_id={args.key_id!r}, подпись проверена)")
        print(f"артефакты: {out_dir}")
        return 0
    except ChannelError as exc:
        print(f"ОШИБКА[{exc.code}]: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"ОШИБКА: файл не найден: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
