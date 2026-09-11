# -*- coding: utf-8 -*-
"""Подпись channel manifest закрытым Ed25519-ключом (только у владельца/CI-secret).

Использование:
    # генерация ключевой пары (выполняется ОДИН раз владельцем; закрытый ключ
    # попадает только в GitHub Actions secret/environment):
    python infra/release/sign_channel.py --gen-key --key-out release-key.hex

    # подпись manifest:
    python infra/release/sign_channel.py --manifest manifest.json \
        --private-key release-key.hex --key-id pilot-release-2026 \
        --out manifest.signed.json

    # генерация публичного ключа из закрытого:
    python infra/release/sign_channel.py --public-key-from release-key.hex

Закрытый ключ никогда не коммитится и не попадает в installer artifact,
логи или diagnostics.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from channel_contract import (  # noqa: E402
    ChannelError,
    canonical_bytes,
    parse_manifest_json,
    sign_manifest,
    validate_manifest_fields,
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


def _public_key_from_private(private_hex: str) -> str:
    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
    raw = key.public_key().public_bytes_raw()
    return base64.b64encode(raw).decode()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", help="manifest без подписи (JSON)")
    parser.add_argument("--private-key", help="файл закрытого ключа (hex/PEM)")
    parser.add_argument("--key-id", default="pilot-release", help="идентификатор ключа")
    parser.add_argument("--out", help="куда записать подписанный manifest")
    parser.add_argument("--gen-key", action="store_true", help="сгенерировать новую пару ключей")
    parser.add_argument("--key-out", help="файл для закрытого ключа (--gen-key)")
    parser.add_argument("--public-key-from", help="напечатать публичный ключ из закрытого")
    args = parser.parse_args()

    try:
        if args.gen_key:
            if not args.key_out:
                raise ChannelError("usage", "укажите --key-out для --gen-key")
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

            key = Ed25519PrivateKey.generate()
            private_hex = key.private_bytes_raw().hex()
            Path(args.key_out).write_text(private_hex + "\n", encoding="utf-8")
            print(f"закрытый ключ записан в {args.key_out} (в git/артефакты не добавлять)")
            print(f"публичный ключ: {_public_key_from_private(private_hex)}")
            return 0
        if args.public_key_from:
            private_hex = _read_private_key(Path(args.public_key_from))
            print(_public_key_from_private(private_hex))
            return 0
        if not args.manifest or not args.private_key:
            raise ChannelError("usage", "нужны --manifest и --private-key")
        raw = parse_manifest_json(Path(args.manifest).read_text(encoding="utf-8"))
        validate_manifest_fields(raw)
        private_hex = _read_private_key(Path(args.private_key))
        signed = sign_manifest(raw, args.key_id, private_hex)
        out = args.out or (str(args.manifest) + ".signed.json")
        Path(out).write_text(json.dumps(signed, ensure_ascii=False, indent=2) + "\n", "utf-8")
        print(f"подписанный manifest записан в {out}")
        print(f"канонический payload: {len(canonical_bytes(raw))} байт")
        return 0
    except ChannelError as exc:
        print(f"ОШИБКА[{exc.code}]: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"ОШИБКА: файл не найден: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
