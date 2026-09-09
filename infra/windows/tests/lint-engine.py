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
    # PowerShell: '...' — литерал ('' — экранированный апостроф);
    # внутри одинарных кавычек скобки не учитываются.
    out: list[str] = []
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "'":
            if in_string and i + 1 < len(text) and text[i + 1] == "'":
                i += 2
                continue
            in_string = not in_string
            out.append(" ")
            i += 1
            continue
        if in_string:
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
    files = engine_files + test_files
    # Секреты-литералы.
    patterns = [
        "AdminAdmin123",
        "Str0ng-Pass-2026",
        re.compile(r"[^0-9a-fA-F][0-9a-fA-F]{64}[^0-9a-fA-F]"),
        re.compile(r"[^0-9a-fA-F][0-9a-fA-F]{32}[^0-9a-fA-F]"),
    ]
    for path in files:
        if "/tests/" in str(path):
            continue  # тесты намеренно содержат примеры секретов
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
