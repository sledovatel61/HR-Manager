#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Кросс-платформенная структурная проверка движка (без PowerShell).

Это НЕ замена настоящей проверки парсером PowerShell: авторитетная
проверка — run-tests.ps1 (static.tests.ps1), она выполняется на
windows-latest в CI. Данный скрипт нужен для быстрой локальной проверки
в окружениях без pwsh (Linux/macOS, в т.ч. ревью в песочнице):
  - баланс скобок/фигурных скобок (приближение к парсеру);
  - отсутствие U+FFFD;
  - отсутствие секретов-литералов;
  - единственная точка запуска процессов (Invoke-HrmExternal);
  - белый список внешних команд.

Проверяются также installer/build.ps1 и installer/sign.ps1 (контракты Phase 14:
без certutil/UI, явный код возврата, честная attestation, JSON без BOM).

Использование:  python3 infra/windows/tests/lint-engine.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WINDOWS = ROOT / "infra" / "windows"

FAILURES: list[str] = []


def fail(message: str) -> None:
    FAILURES.append(message)
    print("  FAIL:", message)


def _strip_ps_strings(text: str) -> str:
    # PowerShell: '...' ('' — экранированный апостроф) и "..." (``"``);
    # комментарии #... тоже исключаются — скобки внутри не считаются.
    out: list[str] = []
    in_single = False
    in_double = False
    in_comment = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_comment:
            if ch == "\n":
                in_comment = False
                out.append("\n")
            i += 1
            continue
        if ch == "#" and not in_single and not in_double:
            in_comment = True
            i += 1
            continue
        if ch == "'":
            if in_single and i + 1 < len(text) and text[i + 1] == "'":
                i += 2
                continue
            if not in_double:
                in_single = not in_single
            out.append(" ")
            i += 1
            continue
        if ch == '"':
            if in_double and i + 1 < len(text) and text[i + 1] == '"':
                i += 2
                continue
            if not in_single:
                in_double = not in_double
            out.append(" ")
            i += 1
            continue
        if in_single or in_double:
            out.append(" ")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def check_balanced(text: str, path: Path) -> None:
    stripped = _strip_ps_strings(text)
    stack: list[str] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    for i, ch in enumerate(stripped):
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if not stack or stack[-1] != pairs[ch]:
                fail(f"{path}: непарная скобка '{ch}' на позиции {i}")
                return
            stack.pop()
    if stack:
        fail(f"{path}: незакрытые скобки {stack[-5:]}")


def check_file(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "\ufffd" in text:
        fail(f"{path}: битый символ U+FFFD")
    check_balanced(text, path)
    if "Start-Process" in text and "Common.psm1" not in path.name:
        fail(f"{path}: Start-Process вне Common.psm1")
    if "System.Diagnostics.Process" in text and "Common.psm1" not in path.name:
        fail(f"{path}: прямой Process вне Common.psm1")
    if re.search(r"(&\s*docker|docker\.exe\s+\S)", text) and "Common.psm1" not in path.name:
        # разрешаем упоминание имени в Common.psm1 и строках справки
        if re.search(r"Invoke-HrmExternal\s+-Name\s+\"docker", text) is None:
            fail(f"{path}: прямой вызов docker")
    allowed = {"docker", "docker.exe", "icacls.exe", "git.exe"}
    for name in re.findall(r'Invoke-HrmExternal\s+-Name\s+"([^"]+)"', text):
        if name not in allowed:
            fail(f"{path}: запрещённая внешняя команда '{name}'")
    if "secrets.json" in text and path.name not in ("Common.psm1", "Secrets.psm1", "lint-engine.py"):
        fail(f"{path}: прямое упоминание secrets.json вне Secrets/Common")


def _strip_ps_comments(text: str) -> str:
    """Убирает однострочные комментарии, сохраняя строковые литералы.

    Нужно, чтобы запреты (certutil, Set-Content -Encoding UTF8) ловили реальный
    код, а не поясняющие комментарии, и наоборот — требования к содержимому
    (пути, сообщения throw) проверялись по коду, а не по шапке скрипта.
    """
    out: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\n":
            in_single = False
            in_double = False
            out.append(ch)
        elif ch == "'" and not in_double:
            in_single = not in_single
            out.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
        elif ch == "#" and not in_single and not in_double:
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def check_installer_script(path: Path) -> None:
    """Инварианты installer/*.ps1 (сборка и подпись релиза).

    Эти скрипты выполняются на Windows и не покрыты PowerShell-парсером в
    окружениях без pwsh, поэтому ключевые контракты Phase 14 фиксируются здесь:
    без certutil/UI, с явным кодом возврата, с честной attestation и с JSON без
    BOM (Python-часть release-пайплайна читает его через json.loads).
    """
    text = path.read_text(encoding="utf-8")
    if "\ufffd" in text:
        fail(f"{path}: битый символ U+FFFD")
    check_balanced(text, path)
    code = _strip_ps_comments(text)
    if re.search(r"\bcertutil(\.exe)?\b", code):
        fail(f"{path}: certutil запрещён — добавление корня в Root/TrustedPublisher висит в CI")
    if re.search(r"Set-Content[^\n]*-Encoding\s+UTF8", code):
        fail(f"{path}: Set-Content -Encoding UTF8 пишет BOM (PowerShell 5.1); JSON обязан быть без BOM")
    if path.name == "sign.ps1":
        if not re.search(r"^exit\s+\$scriptExitCode\s*$", code, re.M):
            fail("sign.ps1: нет явного 'exit $scriptExitCode' — $LASTEXITCODE утечёт в CI")
        if re.search(r"signtool_verify_ok\s*=\s*\$true", code):
            fail("sign.ps1: signtool_verify_ok захардкожен — attestation обязана быть фактической")
        if '"authenticode.py"' not in code:
            fail("sign.ps1: нет независимой проверки infra/release/authenticode.py")
        for required, why in (
            ("Test-HrmUntrustedRootOnly", "нет явного различения «недоверенный корень»/другая ошибка"),
            ("authenticode_present", "attestation без authenticode_present"),
            ("windows_chain_trusted", "attestation без фактического статуса доверия Windows"),
            ("production: нужен -TimestampUrl", "production без обязательной метки времени"),
            ("production: нужен -ExpectedPublisher", "production без ожидаемого издателя"),
            ("HRM_AUTHENTICODE_PFX_PASSWORD", "production без пароля PFX из переменной окружения"),
        ):
            if required not in code:
                fail(f"sign.ps1: {why}")
        if 'Mode -eq "production"' not in code:
            fail("sign.ps1: нет production-ветки")


def check_ps_encoding(path: Path) -> None:
    """PowerShell-файл с кириллицей обязан быть UTF-8 **с BOM**.

    Windows PowerShell 5.1 читает .ps1 без BOM в текущей ANSI-кодировке
    (на GitHub-раннере cp1252), кириллица превращается в мусор и парсер
    рассыпается на незакрытых кавычках. pwsh читает UTF-8 корректно, поэтому
    локально/в линте ошибка не видна — только на windows-latest.
    """
    data = path.read_bytes()
    has_bom = data.startswith(b"\xef\xbb\xbf")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        fail(f"{path}: файл не в UTF-8")
        return
    if any(ord(ch) > 127 for ch in text) and not has_bom:
        fail(f"{path}: нет UTF-8 BOM — PowerShell 5.1 прочитает кириллицу как ANSI и не распарсит файл")


def main() -> int:
    engine_files = (
        sorted(WINDOWS.glob("*.ps1"))
        + sorted((WINDOWS / "engine").glob("*.psm1"))
    )
    test_files = sorted((WINDOWS / "tests").glob("*.ps1"))
    for path in engine_files:
        check_file(path)
    for path in test_files:
        # Тесты намеренно содержат примеры секретов и Start-Process —
        # проверяем только парсер-приближение и кодировку.
        text = path.read_text(encoding="utf-8")
        if "\ufffd" in text:
            fail(f"{path}: битый символ U+FFFD")
        check_balanced(text, path)
    installer_files = sorted((ROOT / "installer").glob("*.ps1"))
    for path in installer_files:
        check_installer_script(path)
    files = engine_files + test_files + installer_files
    for path in files:
        check_ps_encoding(path)
    # Секреты-литералы.
    patterns = [
        "AdminAdmin123",
        "Str0ng-Pass-2026",
        re.compile(r"[^0-9a-fA-F][0-9a-fA-F]{64}[^0-9a-fA-F]"),
        re.compile(r"[^0-9a-fA-F][0-9a-fA-F]{32}[^0-9a-fA-F]"),
    ]
    for path in files:
        if WINDOWS / "tests" in path.parents:
            continue  # тесты намеренно содержат примеры секретов
        if path.parent.name == "installer":
            continue  # SHA256 закреплённого установщика Inno Setup — не секрет
        if path.name == "Crypto.psm1":
            # Публичные константы кривой Ed25519 (RFC 8032) — не секреты.
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            if re.search(pattern, text):
                fail(f"{path}: секрет-литерал ({pattern})")
    # Точка входа: все действия ValidateSet реализованы.
    entry = (WINDOWS / "hr-manager.ps1").read_text(encoding="utf-8")
    m = re.search(r'\[ValidateSet\((.*?)\)\]', entry, re.S)
    if not m:
        fail("hr-manager.ps1: нет ValidateSet")
    else:
        for action in re.findall(r'"([^"]+)"', m.group(1)):
            if f'"{action}" {{' not in entry:
                fail(f"hr-manager.ps1: нет ветки switch для действия {action}")
    if "#requires -Version 5.1" not in entry:
        fail("hr-manager.ps1: нет #requires -Version 5.1")
    # Оверлей: loopback-only, стабильное имя проекта, тома, отключённый mailpit.
    overlay = (ROOT / "infra" / "compose.pilot.yml").read_text(encoding="utf-8")
    if "name: hr-manager-pilot" not in overlay:
        fail("compose.pilot.yml: нет стабильного имени проекта")
    if '127.0.0.1:${HRM_PILOT_PORT:-8080}:8080' not in overlay:
        fail("compose.pilot.yml: фронтенд не ограничен 127.0.0.1")
    for line in overlay.splitlines():
        stripped = line.strip()
        if re.match(r'^"\d+\.\d+\.\d+\.\d+:\d+:\d+"', stripped) and not stripped.startswith('"127.0.0.1'):
            fail(f"compose.pilot.yml: порт вне loopback: {stripped}")
    if 'APP_DEBUG: "true"' in overlay:
        fail("compose.pilot.yml: APP_DEBUG=true")
    if "pilot_pgdata" not in overlay or "pilot_backups" not in overlay:
        fail("compose.pilot.yml: нет томов pilot_pgdata/pilot_backups")
    if 'profiles: ["pilot-disabled"]' not in overlay:
        fail("compose.pilot.yml: mailpit не в неактивном профиле")
    for required in (
        "HRM_POSTGRES_PASSWORD", "HRM_SIGNING_KEY", "HRM_BOOTSTRAP_ADMIN_PASSWORD",
        "HRM_EXCHANGE_TOKEN", "HRM_BACKUP_KEY", "HRM_BACKUP_KEY_ID",
    ):
        if "${%s:?" % required not in overlay:
            fail(f"compose.pilot.yml: обязательная переменная {required} не затребована (:?)")

    print(f"Проверено файлов: {len(files)}")
    if FAILURES:
        print(f"ПРОВАЛОВ: {len(FAILURES)}")
        return 1
    print("Структурная проверка пройдена (полная проверка — run-tests.ps1 в CI).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
