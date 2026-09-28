# -*- coding: utf-8 -*-
"""CLI for offline license issuer.

Usage (owner PC, offline):
  python cli.py gen-keypair --out-dir ./keys
  python cli.py issue --private-key-file ./keys/private_key.hex --client "Пилот Марии" --expires 2026-12-31 --max-users 5 --out ./licenses/maria.hrmlicense
  python cli.py verify --public-key-file ./keys/public_key.b64 --license-file ./licenses/maria.hrmlicense

Private key NEVER goes to git/installer/logs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from license_issuer import generate_keypair, issue_license, load_private_key_from_file, load_public_key_from_file, verify_license
except ImportError:
    # When run from a different working directory, in particular with the
    # bundled embeddable Python whose sys.path is defined by python*._pth and
    # does not include the script directory. (sys is imported above.)
    sys.path.insert(0, str(Path(__file__).parent))
    from license_issuer import generate_keypair, issue_license, load_private_key_from_file, load_public_key_from_file, verify_license


def cmd_gen_keypair(args):
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    priv_hex, pub_b64 = generate_keypair()

    priv_path = out_dir / "private_key.hex"
    pub_path = out_dir / "public_key.b64"

    if priv_path.exists() and not args.force:
        print(f"Файл {priv_path} уже существует. Используйте --force для перезаписи.", file=sys.stderr)
        sys.exit(1)

    priv_path.write_text(priv_hex + "\n", encoding="utf-8")
    pub_path.write_text(pub_b64 + "\n", encoding="utf-8")

    print(f"Ключи созданы:")
    print(f"  Приватный (СЕКРЕТНО, только у владельца!): {priv_path}")
    print(f"  Публичный (для сборки образа): {pub_path}")
    print(f"\nВАЖНО: Сделайте резервную копию приватного ключа в зашифрованном хранилище (VeraCrypt/BitLocker/зашифрованная флешка).")
    print(f"НИКОГДА не коммитьте private_key в git, не добавляйте в установщик, Docker, логи, диагностический архив.")
    print(f"Публичный ключ скопируйте в infra/license/public_key.b64 перед сборкой пилотного образа.")


def cmd_issue(args):
    priv_file = Path(args.private_key_file)
    priv_hex = load_private_key_from_file(priv_file)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    data = issue_license(
        client_name=args.client,
        expires_at=args.expires,
        max_active_users=args.max_users,
        private_hex=priv_hex,
        license_id=args.license_id,
        issued_at=args.issued_at,
    )

    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Лицензия выпущена:")
    print(f"  ID: {data['license_id']}")
    print(f"  Клиент: {data['client_name']}")
    print(f"  Действует до: {data['expires_at']} (включительно до 23:59:59 UTC)")
    print(f"  Лимит пользователей: {data['max_active_users']}")
    print(f"  Файл: {out_path}")
    print(f"\nОтправьте файл {out_path.name} Марии. Она загрузит его через UI: Настройки → Лицензия → Загрузить файл.")


def cmd_verify(args):
    pub_file = Path(args.public_key_file)
    pub_b64 = load_public_key_from_file(pub_file)
    lic_file = Path(args.license_file)
    data = json.loads(lic_file.read_text(encoding="utf-8"))

    try:
        verify_license(data, pub_b64)
        print("Подпись корректна.")
        print(f"  ID: {data.get('license_id')}")
        print(f"  Клиент: {data.get('client_name')}")
        print(f"  Истекает: {data.get('expires_at')}")
        print(f"  Лимит: {data.get('max_active_users')}")
    except Exception as exc:
        print(f"Ошибка проверки: {exc}", file=sys.stderr)
        sys.exit(2)


def main():
    parser = argparse.ArgumentParser(description="HR Manager — offline license issuer (owner only)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_gen = sub.add_parser("gen-keypair", help="Сгенерировать пару ключей Ed25519")
    p_gen.add_argument("--out-dir", default="./keys", help="Каталог для сохранения ключей")
    p_gen.add_argument("--force", action="store_true", help="Перезаписать существующие файлы")
    p_gen.set_defaults(func=cmd_gen_keypair)

    p_issue = sub.add_parser("issue", help="Выпустить лицензию")
    p_issue.add_argument("--private-key-file", required=True, help="Путь к private_key.hex")
    p_issue.add_argument("--client", required=True, help="Имя клиента/пилота, напр. 'Пилот Марии'")
    p_issue.add_argument("--expires", required=True, help="Дата окончания YYYY-MM-DD, включительно до конца дня UTC")
    p_issue.add_argument("--max-users", type=int, required=True, help="Лимит активных пользователей 1..1000")
    p_issue.add_argument("--out", required=True, help="Путь для сохранения .hrmlicense файла")
    p_issue.add_argument("--license-id", default=None, help="UUID лицензии (по умолчанию генерируется)")
    p_issue.add_argument("--issued-at", default=None, help="Дата выпуска YYYY-MM-DDTHH:MM:SSZ (по умолчанию сейчас UTC)")
    p_issue.set_defaults(func=cmd_issue)

    p_verify = sub.add_parser("verify", help="Проверить подпись лицензии")
    p_verify.add_argument("--public-key-file", required=True, help="Путь к public_key.b64")
    p_verify.add_argument("--license-file", required=True, help="Путь к .hrmlicense файлу")
    p_verify.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
