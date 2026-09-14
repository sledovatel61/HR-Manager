# -*- coding: utf-8 -*-
"""Контракт кодовой подписи Windows-установщика (Authenticode, Phase 14).

Второй, НЕЗАВИСИМЫЙ уровень подписи рядом с Ed25519-подписью канала
(Phase 13 остаётся обязательной проверкой manifest/пакета и не заменяется
Authenticode). Сама подпись выполняется ``signtool`` на Windows-раннере
(``installer/sign-installer.ps1``); этот модуль — машиночитаемый
fail-closed контракт, который:

* проверяет блок ``signing`` в ``installer/release-manifest.json`` после
  подписи (status/publisher/timestamp/certificate_kind/signtool_verify);
* используется release-pipeline'ом ДО публикации: production-режим обязан
  отказать, если installer не подписан, timestamp отсутствует/невалиден,
  publisher не совпадает с ожидаемым или сертификат — тестовый/самоподписанный;
* позволяет тестовый режим (ephemeral self-signed certificate) только для
  CI/fixture — тестовый сертификат НИКОГДА не проходит production-политику.

Секреты (PFX и пароль) живут только в GitHub environment и попадают в
sign-installer.ps1 ТОЛЬКО через переменные окружения: ни в командную строку,
ни в логи, ни в артефакты, ни в репозиторий.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SIGNING_STATUSES = ("signed", "unsigned")
SIGNING_MODES = ("production", "test", "disabled")
CERTIFICATE_KINDS = ("production", "test-self-signed")


class SigningContractError(ValueError):
    """Нарушение контракта подписи: безопасный код + сообщение."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def evaluate_signing_contract(
    signing: dict,
    *,
    production: bool,
    expected_publisher: str = "",
) -> list[str]:
    """Проверить блок signing релиз-манифеста против политики.

    production=True — политика production-выпуска (fail closed на любом
    нарушении). production=False — тестовый режим CI/fixture: допустимы
    режимы test/disabled, но тестовый сертификат всё равно не считается
    production-подписью. Возвращает список нарушений (пустой = контракт
    соблюдён). Пересчёт SHA256 самого exe выполняет verify_release_manifest.
    """
    problems: list[str] = []
    if not isinstance(signing, dict):
        return ["signing: блок отсутствует или не объект"]

    status = signing.get("status")
    mode = signing.get("mode", "disabled")
    if status not in SIGNING_STATUSES:
        problems.append(f"signing.status некорректен: {status!r}")
    if mode not in SIGNING_MODES:
        problems.append(f"signing.mode некорректен: {mode!r}")

    certificate_kind = signing.get("certificate_kind")
    if certificate_kind is not None and certificate_kind not in CERTIFICATE_KINDS:
        problems.append(f"signing.certificate_kind некорректен: {certificate_kind!r}")

    if production:
        if mode != "production":
            problems.append(f"production-выпуск требует signing.mode=production (получено {mode!r})")
        if status != "signed":
            problems.append(f"production-выпуск требует подписанный installer (получено {status!r})")
        if certificate_kind is not None and certificate_kind != "production":
            problems.append(
                f"тестовый/самоподписанный сертификат не проходит production-политику "
                f"(certificate_kind={certificate_kind!r})"
            )
        if signing.get("signtool_verify") != "passed":
            problems.append(
                f"signtool verify /pa /all не пройден (получено {signing.get('signtool_verify')!r})"
            )
        timestamp_url = (signing.get("timestamp_url") or "").strip()
        if not timestamp_url.startswith(("http://", "https://")):
            problems.append("timestamp отсутствует или задан некорректно")
        if signing.get("timestamp_valid") is not True:
            problems.append("timestamp подписи отсутствует или невалиден")
        if expected_publisher:
            publisher = (signing.get("publisher") or "").strip()
            if publisher != expected_publisher.strip():
                problems.append(
                    "publisher подписи не совпадает с ожидаемым "
                    f"({publisher!r} != {expected_publisher!r})"
                )
    else:
        # Тестовый контур: подпись не обязательна, но ЛОЖЬ о подписи запрещена.
        if status == "signed":
            if signing.get("signtool_verify") not in (None, "passed"):
                problems.append(
                    f"signtool verify не пройден (получено {signing.get('signtool_verify')!r})"
                )
            if certificate_kind == "production":
                problems.append(
                    "тестовый прогон заявляет production-сертификат: контракт CI не допускает "
                    "production-секретов"
                )
        if mode == "production":
            problems.append("production-режим подписи не разрешён в тестовом контуре CI")
    return problems


def _verify_exe_hash(manifest: dict, exe_path: Path) -> list[str]:
    """SHA256 подписанного exe обязан совпадать с манифестом (пересчёт)."""
    installer = manifest.get("installer_exe")
    if not isinstance(installer, dict):
        return ["манифест не содержит блок installer_exe"]
    declared = installer.get("sha256")
    if not isinstance(declared, str) or not declared:
        return ["installer_exe.sha256 отсутствует"]
    if not exe_path.exists():
        return [f"подписанный installer не найден: {exe_path.name}"]
    actual = hashlib.sha256(exe_path.read_bytes()).hexdigest()
    if actual != declared.lower():
        return ["SHA256 подписанного installer не совпал с манифестом (пересчёт после подписи обязателен)"]
    return []


def verify_release_manifest(
    manifest_path: str | Path,
    *,
    production: bool,
    expected_publisher: str = "",
    exe_dir: str | Path | None = None,
) -> list[str]:
    """Полная проверка release-manifest.json (включая блок signing)."""
    path = Path(manifest_path)
    if not path.exists():
        return [f"манифест не найден: {path.name}"]
    try:
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return [f"некорректный JSON манифеста: {exc}"]
    if not isinstance(manifest, dict):
        return ["манифест не является объектом"]
    problems = evaluate_signing_contract(
        manifest.get("signing", {}),
        production=production,
        expected_publisher=expected_publisher,
    )
    if production:
        installer = manifest.get("installer_exe") or {}
        file_name = installer.get("file")
        if isinstance(file_name, str) and file_name:
            exe_path = (Path(exe_dir) if exe_dir else path.parent) / file_name
            problems.extend(_verify_exe_hash(manifest, exe_path))
        else:
            problems.append("манифест не содержит installer_exe.file")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify-manifest", help="проверить контракт подписи release-manifest")
    verify.add_argument("--manifest", required=True)
    verify.add_argument(
        "--mode",
        choices=["production", "test"],
        default="test",
        help="production — политика выпуска (fail closed); test — контур CI/fixture",
    )
    verify.add_argument("--expected-publisher", default="")
    verify.add_argument("--exe-dir", default=None)
    args = parser.parse_args(argv)

    problems = verify_release_manifest(
        args.manifest,
        production=args.mode == "production",
        expected_publisher=args.expected_publisher,
        exe_dir=args.exe_dir,
    )
    if problems:
        for problem in problems:
            print(f"ОШИБКА: {problem}", file=sys.stderr)
        return 1
    print("Контракт подписи installer соблюдён.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
