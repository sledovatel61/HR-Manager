# -*- coding: utf-8 -*-
"""Независимая проверка подписанного channel manifest публичным ключом.

Используется в CI-шаге verification (тем же публичным ключом, что встроен
в клиент) и локально при приёмке:

    python infra/release/verify_channel.py --manifest manifest.signed.json \
        --public-key <base64|файл с base64> --key-id pilot-release-2026

Fail closed: любое несоответствие — ненулевой код возврата.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from channel_contract import (  # noqa: E402
    ChannelError,
    parse_manifest_json,
    validate_manifest_fields,
    verify_signature,
)


def _read_public_key(value: str) -> str:
    candidate = value.strip()
    path = Path(candidate)
    if path.is_file():
        candidate = path.read_text(encoding="utf-8").strip()
    if not candidate:
        raise ChannelError("bad_key", "пустой публичный ключ")
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="подписанный manifest (JSON)")
    parser.add_argument("--public-key", required=True, help="публичный ключ base64 или файл")
    args = parser.parse_args()

    try:
        raw = parse_manifest_json(Path(args.manifest).read_text(encoding="utf-8"))
        validate_manifest_fields(raw)
        public_key = _read_public_key(args.public_key)
        payload = verify_signature(raw, public_key)
        print(f"подпись валидна: key_id={raw['signature']['key_id']!r}")
        print(f"версия {raw['version']} (release_sha {raw['release_sha']})")
        print(f"канонический payload: {len(payload)} байт")
        return 0
    except ChannelError as exc:
        print(f"ОШИБКА[{exc.code}]: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"ОШИБКА: файл не найден: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
