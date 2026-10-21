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
    allowed = {"docker", "docker.exe", "icacls.exe", "git.exe", "netsh", "netsh.exe",
               "wsl.exe",  # wsl.exe — только чтение состояния WSL2 (--list/--status)
               # taskkill.exe — ЕДИНСТВЕННЫЙ вызов: /PID <pid> /T /F из
               # Stop-HrmProcessTree (Common.psm1) по таймауту внешней команды.
               # Без него зависший CLI контейнеров оставлял процесс движка и его
               # потомков (wsl/buildx) живыми навсегда. Команда не читает и не
               # меняет данные приложения: только завершает дерево процессов,
               # которое движок сам и запустил.
               "taskkill.exe"}
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
            ("Assert-HrmPinnedRootsPem", "нет pre-flight закреплённых PEM"),
            ("production pre-flight: PEM не найден", "нет отказа на отсутствующий PEM"),
            ("production pre-flight: PEM пуст", "нет отказа на пустой PEM"),
            ("production pre-flight: невалидный PEM", "нет отказа на невалидный PEM"),
            ("X509Certificate2", "PEM не разбирается как сертификат"),
        ):
            if required not in code:
                fail(f"sign.ps1: {why}")
        if 'Mode -eq "production"' not in code:
            fail("sign.ps1: нет production-ветки")
        call_at = code.find("Assert-HrmPinnedRootsPem -Mode")
        sign_at = code.find('Label "signtool sign"')
        if call_at < 0 or sign_at < 0 or call_at > sign_at:
            fail("sign.ps1: pre-flight PEM должен выполняться до signtool sign")
        if 'if ($Mode -ne "production") { return }' not in code:
            fail("sign.ps1: test-режим не должен требовать operator PEM")
        pinned = code.split("function Assert-HrmPinnedRootsPem", 1)[1]
        pinned = pinned.split("$signtool = Get-HrmSigntool", 1)[0]
        if "signtool" in pinned.lower():
            fail("sign.ps1: pre-flight не должен вызывать signtool")


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


FENCE_RE = re.compile(r"`{3,}")
# Одинарные строки в PowerShell не обрабатывают escape-последовательности,
# поэтому $fence = '```' — единственная безопасная форма ограждения.
PS_SINGLE_QUOTED_RE = re.compile(r"'(?:[^']|'')*'")


def check_no_backtick_fence(path: Path) -> None:
    """Запретить ``` в PowerShell-коде (в т.ч. в `run:` шагах workflow).

    Внутри двойной строки PowerShell разбирает ```text` как: `n -> LF, `` ->
    литерал, `t -> TAB, а завершающая ```" -> экранированная кавычка. Строка
    остаётся незакрытой, парсер роняет ВЕСЬ шаг с «Process completed with exit
    code 1» без внятной аннотации. Markdown-ограждение в job summary нужно
    собирать из одинарных кавычек: $fence = '```'.
    """
    for lineno, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # Ограждение внутри одинарных кавычек безопасно — вырезаем такие строки
        # перед поиском, чтобы не наказывать корректную форму $fence = '```'.
        if FENCE_RE.search(PS_SINGLE_QUOTED_RE.sub("''", line)):
            fail(
                f"{path}:{lineno}: ``` в PowerShell-коде ломает строковые литералы — "
                "ограждение кода собирайте из одинарных кавычек ($fence = '```')"
            )


WORKFLOWS = ROOT / ".github" / "workflows"


SMOKE_STEP_NAME = "- name: Silent install and uninstall smoke"
SMOKE_SCRIPT = "infra/windows/tests/ci-silent-smoke.ps1"


def check_smoke_step(path: Path, required: bool = False) -> None:
    """Шаг CI-смоука — ASCII-обёртка над сценарием из репозитория.

    Регрессия, стоившая нескольких прогонов: сценарий смоука лежал текстом в
    шаге workflow. Раннер пишет временный скрипт шага в UTF-8 БЕЗ BOM, а
    Windows PowerShell 5.1 читает такой файл в ANSI — кириллица превращается в
    мусор, шаг падает на разборе и НЕ оставляет аннотаций, по которым можно
    понять причину. Поэтому текст шага — только ASCII, а вся кириллица живёт в
    файле с BOM (его проверяют и lint, и static.tests.ps1 на Windows).
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.strip() != SMOKE_STEP_NAME:
            continue
        body = []
        for follower in lines[index + 1:]:
            if follower.startswith("      - name:"):
                break
            body.append(follower)
        step = "\n".join(body)
        if not all(ord(ch) < 128 for ch in step):
            bad = sorted({ch for ch in step if ord(ch) > 127})
            fail(
                f"{path}:{index + 1}: в шаге смоука есть нелатиница "
                f"({''.join(bad)[:20]!r}) — временный скрипт шага читается "
                "PowerShell 5.1 в ANSI-кодировке; текст переносите в файл с BOM"
            )
        if SMOKE_SCRIPT not in step:
            fail(f"{path}:{index + 1}: шаг смоука не вызывает {SMOKE_SCRIPT}")
        if "exit 1" not in step:
            fail(f"{path}:{index + 1}: шаг смоука не возвращает код возврата сценария")
        return
    # Шаг смоука живёт только в CI, который собирает установщик. В этом файле
    # его отсутствие (например, переименование при правке) — провал проверки,
    # а не «нечего проверять»: иначе защита молча исчезла бы вместе с шагом.
    if required:
        fail(f"{path}: не найден шаг смоука установки ({SMOKE_STEP_NAME})")


STEP_START = re.compile(r"^ {6}- ")
SHELL_WINDOWS_PS = re.compile(r"^\s*shell:\s*powershell(\.exe)?\s*$")
RUN_KEY = re.compile(r"^\s*run:\s*(\||>)?\s*$")


def check_windows_powershell_steps(path: Path) -> None:
    """Текст run-блоков шагов `shell: powershell` обязан быть ASCII.

    Раннер пишет временный скрипт шага в UTF-8 БЕЗ BOM, а Windows PowerShell
    5.1 читает такой файл в ANSI-кодировке. Часть кириллицы превращается при
    этом в символы, которые парсер считает строковыми кавычками: «ф» (D1 94) ->
    U+201D, «у» -> U+201C, «т» -> U+2019, «в» -> U+201A, «д» -> U+201E. Кавычка
    внутри строки в двойных кавычках закрывает её раньше времени, скрипт не
    разбирается целиком и шаг умирает за секунду, не оставив ни одной
    аннотации: именно так исчез гейт снимка в шаге смоука (слово «файлов» в
    throw "..." давало U+201D). Поэтому русский текст живёт в файле репозитория
    с BOM (его читают корректно), а шаг остаётся ASCII-обёрткой.

    Правило не касается pwsh/bash-шагов: они читают UTF-8 без BOM корректно.
    Регрессия закреплена тестом backend/tests/test_workflow_step_encoding.py.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    starts = [index for index, line in enumerate(lines) if STEP_START.match(line)]
    bounds = list(zip(starts, [*starts[1:], len(lines)]))
    for start, end in bounds:
        block = lines[start:end]
        if not any(SHELL_WINDOWS_PS.match(line) for line in block):
            continue
        run_index = next(
            (index for index, line in enumerate(block) if RUN_KEY.match(line)), None
        )
        if run_index is None:
            continue
        run_indent = len(block[run_index]) - len(block[run_index].lstrip())
        for offset in range(run_index + 1, len(block)):
            line = block[offset]
            if not line.strip():
                continue
            if len(line) - len(line.lstrip()) <= run_indent:
                break
            bad = sorted({ch for ch in line if ord(ch) > 127})
            if bad:
                fail(
                    f"{path}:{start + offset + 1}: в run-блоке шага с shell: powershell "
                    f"есть не-ASCII ({''.join(bad)[:20]!r}) — Windows PowerShell 5.1 "
                    "прочитает временный скрипт шага в ANSI-кодировке и упадёт на "
                    "разборе без аннотаций; перенесите текст в файл репозитория с BOM"
                )
                break


def check_workflows() -> None:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        check_no_backtick_fence(path)
        check_smoke_step(path, required=path.name == "ci.yml")
        check_windows_powershell_steps(path)

def check_no_self_referential_trust(path: Path, code: str) -> None:
    """Доверенные якоря Authenticode не должны браться из проверяемого файла.

    Если в --trust-roots положить сертификат подписанта, извлечённый из той же
    подписи, проверка цепочки становится тавтологией: _verify_chain сразу
    возвращает успех, когда DER листа есть среди корней. Подтверждено PoC —
    посторонний самоподписанный издатель проходил chain_verified=true и
    timestamp_chain_verified=true даже при --require-timestamp.
    """
    for lineno, line in enumerate(code.splitlines(), 1):
        if "$signature." not in line:
            continue
        lowered = line.lower()
        if "export-hrmcertificatepem" in lowered or "--trust-roots" in lowered or (
            "--timestamp-roots" in lowered
        ):
            fail(
                f"{path}:{lineno}: сертификат из подписи нельзя использовать как "
                "доверенный якорь (тавтологическая проверка цепочки)"
            )


def main() -> int:
    engine_files = (
        sorted(WINDOWS.glob("*.ps1"))
        + sorted((WINDOWS / "engine").glob("*.psm1"))
    )
    test_files = sorted((WINDOWS / "tests").glob("*.ps1"))
    # Инструменты владельца (infra/windows/tools) — как тесты: движок они не
    # образуют, но обязаны парситься Windows PowerShell 5.1 и не иметь битых
    # символов (сбор фактов с пилотной машины запускается на чужой машине, где
    # починить опечатку будет некому).
    tool_files = sorted((WINDOWS / "tools").glob("*.ps1"))
    for path in engine_files:
        check_file(path)
    for path in test_files + tool_files:
        # Тесты намеренно содержат примеры секретов и Start-Process —
        # проверяем только парсер-приближение и кодировку.
        text = path.read_text(encoding="utf-8")
        if "\ufffd" in text:
            fail(f"{path}: битый символ U+FFFD")
        check_balanced(text, path)
    installer_files = sorted((ROOT / "installer").glob("*.ps1"))
    for path in installer_files:
        check_installer_script(path)
        check_no_self_referential_trust(path, _strip_ps_comments(path.read_text(encoding="utf-8-sig")))
    files = engine_files + test_files + tool_files + installer_files
    for path in files:
        check_ps_encoding(path)
        check_no_backtick_fence(path)
    check_workflows()
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
    if '${HRM_PILOT_BIND:-127.0.0.1}:${HRM_PILOT_PORT:-8080}:8080' not in overlay and '127.0.0.1:${HRM_PILOT_PORT:-8080}:8080' not in overlay:
        fail("compose.pilot.yml: фронтенд не ограничен 127.0.0.1 (ожидается HRM_PILOT_BIND)")
    for line in overlay.splitlines():
        stripped = line.strip()
        # Allow ${HRM_PILOT_BIND:-127.0.0.1} variable; literal 127.0.0.1 only for static check
        if re.match(r'^"(?:\$\{HRM_PILOT_BIND[^}]*\}:)?\d+\.\d+\.\d+\.\d+:\d+:\d+"', stripped):
            if not (stripped.startswith('"127.0.0.1') or stripped.startswith('"${HRM_PILOT_BIND')):
                fail(f"compose.pilot.yml: порт вне loopback: {stripped}")
        elif re.match(r'^"\d+\.\d+\.\d+\.\d+:\d+:\d+"', stripped) and not stripped.startswith('"127.0.0.1'):
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


    # --- Docker: диагностика и ручная установка -----------------------------
    engine_dir = WINDOWS / "engine"
    tray_entry = WINDOWS / "hrm-tray.ps1"
    tray_module = engine_dir / "Tray.psm1"
    supervisor_module = engine_dir / "Supervisor.psm1"
    docker_module = engine_dir / "Docker.psm1"
    for path in (tray_entry, tray_module, supervisor_module, docker_module):
        if not path.exists():
            fail(f"Docker/Supervisor/Tray: отсутствует обязательный файл {path}")
    pilot_modules = ["Docker", "Supervisor", "Tray"]
    entry_text = entry
    tray_entry_text = tray_entry.read_text(encoding="utf-8") if tray_entry.exists() else ""
    for module in pilot_modules:
        if f'"{module}"' not in entry_text:
            fail(f"hr-manager.ps1: не импортирован модуль {module}")
        if f'"{module}"' not in tray_entry_text:
            fail(f"hrm-tray.ps1: не импортирован модуль {module}")

    tray_text = tray_module.read_text(encoding="utf-8") if tray_module.exists() else ""
    for required_action in ("open", "check", "restart", "support-bundle", "stop", "exit"):
        if f'action = "{required_action}"' not in tray_text:
            fail(f"Tray.psm1: в меню нет обязательного действия {required_action}")
    for required_text in ("Открыть HR Manager", "Перезапустить приложение", "Проверить состояние",
                          "Создать отчёт для поддержки", "Остановить приложение", "Выйти"):
        if required_text not in tray_text:
            fail(f"Tray.psm1: в меню нет пункта «{required_text}»")

    docker_text = docker_module.read_text(encoding="utf-8") if docker_module.exists() else ""
    if "https://www.docker.com/products/docker-desktop/" not in docker_text:
        fail("Docker.psm1: нет ссылки на официальный сайт Docker Desktop")
    for required in ("Get-HrmDockerInstallGuide", "Get-HrmDockerMissingMessage",
                     "Get-HrmDockerReadiness", "Invoke-HrmDockerPrepare",
                     "Start-HrmDockerDesktop", "Wait-HrmDockerEngine",
                     "VirtualizationFirmwareEnabled"):
        if required not in docker_text:
            fail(f"Docker.psm1: нет сохранённого диагностического контракта {required}")
    docker_code = _strip_ps_comments(docker_text)
    for forbidden in ("Install-HrmDockerDesktop", "Save-HrmDownload", "Start-HrmElevatedAndWait",
                      "Get-AuthenticodeSignature", "docker-pending.json", "pending_docker",
                      "--accept-license"):
        if forbidden in docker_code:
            fail(f"Docker.psm1: устаревшая автоустановка/pending-механика {forbidden}")

    # The engine never auto-accepts Docker's license, including in other modules.
    for path in engine_files:
        code = _strip_ps_comments(path.read_text(encoding="utf-8"))
        if "--accept-license" in code:
            fail(f"{path}: Docker license acceptance must remain a human action")

    # Supervisor: singleton, state, autostart.
    supervisor_text = supervisor_module.read_text(encoding="utf-8") if supervisor_module.exists() else ""
    for required in ("WaitOne", "supervisor.json", "autostart.json", "Get-HrmSupervisorStatusText"):
        if required not in supervisor_text:
            fail(f"Supervisor.psm1: нет обязательного механизма {required}")
    for state_word in ("Запускается", "Готово", "Ошибка"):
        if state_word not in supervisor_text:
            fail(f"Supervisor.psm1: нет понятного состояния «{state_word}»")

    # Preserve general resume and interactivity while deleting only Docker pending state.
    install_text = (engine_dir / "Install.psm1").read_text(encoding="utf-8")
    for required in ("Resume-HrmOperation", "Get-HrmUpdateJournal", "recordForResume"):
        if required not in install_text:
            fail(f"Install.psm1: общий resume-контракт отсутствует ({required})")
    tray_code = _strip_ps_comments(tray_text)
    install_code = _strip_ps_comments(install_text)
    for path, code in ((tray_module, tray_code), (engine_dir / "Install.psm1", install_code)):
        if "Get-HrmPendingDockerOperation" in code or "docker-pending.json" in code:
            fail(f"{path}: остался Docker-specific pending/resume-механизм")
    if "Test-HrmInteractive" not in (engine_dir / "Common.psm1").read_text(encoding="utf-8"):
        fail("Common.psm1: потерян общий Test-HrmInteractive helper")

    # Data volumes remain protected; only explicit, separately-gated purge is allowed.
    for path in engine_files:
        code = _strip_ps_comments(path.read_text(encoding="utf-8"))
        if re.search(r'"down"[^\n]*"-v"', code) or re.search(r'"--volumes"', code):
            fail(f"{path}: 'docker compose down -v' запрещён (удаление томов данных)")
        if re.search(r'"volume",\s*"prune"', code) or "volume prune" in code:
            fail(f"{path}: volume prune запрещён (удаление данных)")

    # Временный файл записи состояния обязан быть уникальным для процесса.
    # Общее "$target.tmp" гоняли трей и движок: мастер поднимает трей с nowait,
    # и Set-HrmSupervisorState выполнялся одновременно с обеих сторон. Один писал
    # .tmp, второй переносил его на место, и Move-Item первого падал с «Объект по
    # указанному пути ... .tmp не существует» — движок умирал с fatal, установка
    # отдавала код 10 при живом приложении.
    common_engine = (ROOT / "infra" / "windows" / "engine" / "Common.psm1").read_text(encoding="utf-8")
    if '$params["Body"] = ($Body | ConvertTo-Json' in common_engine:
        fail("Common.psm1: тело HTTP отдаётся строкой в -Body; PowerShell 5.1 "
             "кодирует её системной кодировкой, а сервер читает UTF-8 - "
             "кириллица приезжает символом замены и validate_surname отвечает 422")
    if '$tmp = "$target.tmp"' in common_engine:
        fail("Common.psm1: Set-HrmJsonFile пишет через общее имя $target.tmp - "
             "гонка трея и движка за файлом состояния (fatal, код 10)")

    # Inno: optional, external sidecar; no task/CLI install action; dynamic Finish + custom exit.
    iss = (ROOT / "installer" / "installer.iss").read_text(encoding="utf-8")
    for required in ("hrm-tray.ps1", 'Source: "{src}\\DockerDesktopInstaller.exe"',
                     'DestDir: "{app}\\docker"', "external skipifsourcedoesntexist",
                     "GetCustomSetupExitCode", "GetHrmSetupEngineStatus", "FinishIncompleteTitle",
                     "{userstartup}"):
        if required not in iss:
            fail(f"installer.iss: нет обязательного элемента {required}")
    for removed in ("[Tasks]", "dockerinstall", "GetEngineDockerArgs", "-InstallDocker"):
        if removed in iss:
            fail(f"installer.iss: удалённый Docker auto-install элемент всё ещё есть: {removed}")
    run_section = re.search(r"(?ms)^\[Run\]\s*(.*?)(?=^\[UninstallRun\])", iss)
    if not run_section:
        fail("installer.iss: нет [Run] секции")
    elif "DockerDesktopInstaller.exe" in run_section.group(1):
        fail("installer.iss: DockerDesktopInstaller.exe нельзя запускать из [Run]")
    if "Result := 10" not in iss:
        fail("installer.iss: движковый отказ не меняет custom Setup exit code на 10")
    if 'hr-manager.ps1"" -Action start' in iss:
        fail("installer.iss: автозапуск всё ещё запускает консольный -Action start")
    # Мастер и движок делят один каталог состояния (first-run-input.json,
    # installed.json, setup-run.json). Мастер пишет туда жёстко через HrmStateDir(),
    # а движок вычисляет путь сам и уважает HRM_STATE_DIR. Если запуск не передаёт
    # -StateDir явно, при заданной переменной окружения движок читает другой каталог:
    # фамилия не находится, Get-HrmFirstRunInput молча подставляет "Owner"
    # (Bootstrap.psm1), установка «успешна» с кодом 0. Живой дефект приёмки CI625.
    # -InstallDir тоже обязан передаваться: иначе движок ставит в свой путь по
    # умолчанию и игнорирует выбор пользователя в /DIR.
    for label, needle in (("движку", '-Action install'),
                          ("трею", 'hrm-tray.ps1'),
                          ("деинсталляции", '-Action uninstall')):
        lines = [l for l in run_section.group(1).splitlines()
                 if needle in l] if needle != '-Action uninstall' else \
                [l for l in iss.splitlines() if '-Action uninstall' in l]
        if not lines:
            fail(f"installer.iss: не найдена запись запуска ({label})")
            continue
        entry = lines[0]
        for flag in ("-StateDir", "-InstallDir"):
            if flag not in entry:
                fail(f"installer.iss: {label} не передают {flag}: мастер и движок "
                     f"могут разойтись в путях каталога состояния/установки")

    # Build records the exact optional sidecar SHA/URL; the installed release.json
    # and external manifest must reuse the same object (no second hash calculation).
    build_script = (ROOT / "installer" / "build.ps1").read_text(encoding="utf-8")
    for required in ("DockerDesktopInstallerPath", "DockerDesktopInstallerSha256",
                     "docker_desktop_installer", "DockerInstallerSourceUrl", "SHA256SUMS.txt"):
        if required not in build_script:
            fail(f"installer/build.ps1: отсутствует optional sidecar/hash contract {required}")
    if "Invoke-WebRequest -Uri $DockerInstallerSourceUrl" in _strip_ps_comments(build_script):
        fail("installer/build.ps1: не скачивать Docker Desktop Installer автоматически")
    for variable, label in (("releaseJson", "release.json"), ("manifest", "release-manifest")):
        match = re.search(rf"(?ms)\${variable}\s*=\s*\[ordered\]@\{{(.*?)^\}}", build_script)
        if not match or "docker_desktop_installer = $dockerInstallerInfo" not in match.group(1):
            fail(f"installer/build.ps1: {label} должен использовать общий $dockerInstallerInfo")

    ci_text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    if "docker-publisher-audit" in ci_text or "Audit-HrmDockerPublisher.ps1" in ci_text:
        fail("ci.yml: устаревший Docker publisher audit job скачивает сторонний installer")
    pilot_release = (ROOT / ".github" / "workflows" / "pilot-release.yml").read_text(encoding="utf-8")
    for required in ("docker_desktop_installer_sha256", "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe",
                     "if (-not $expected)", "Get-FileHash -LiteralPath $sidecar", "$actual -ne $expected",
                     "id: docker-sidecar", "pinned=false", "pinned=true",
                     "Verify Docker Desktop sidecar Authenticode identity",
                     "if: steps.docker-sidecar.outputs.pinned == 'true'",
                     "Assert-HrmDockerInstallerSignature.ps1", "-ExpectedSha256 $env:HRM_DOCKER_DESKTOP_INSTALLER_SHA256",
                     "-DockerDesktopInstallerSha256", "installer/output/*"):
        if required not in pilot_release:
            fail(f"pilot-release.yml: отсутствует optional pinned sidecar publish contract {required}")
    trigger_match = re.search(r"(?ms)^on:\s*(.*?)(?=^permissions:)", pilot_release)
    if not trigger_match or "workflow_dispatch" not in trigger_match.group(1):
        fail("pilot-release.yml: sidecar workflow должен запускаться вручную через workflow_dispatch")
    elif "pull_request" in trigger_match.group(1) or re.search(r"(?m)^\s+push:", trigger_match.group(1)):
        fail("pilot-release.yml: PR/push не должны запускать sidecar download")

    signature_tool = WINDOWS / "tools" / "Assert-HrmDockerInstallerSignature.ps1"
    if not signature_tool.is_file():
        fail("infra/windows/tools/Assert-HrmDockerInstallerSignature.ps1: отсутствует локальный verifier")
    else:
        tool_bytes = signature_tool.read_bytes()
        tool = tool_bytes.decode("utf-8-sig")
        if not tool_bytes.startswith(b"\xef\xbb\xbf"):
            fail(f"{signature_tool}: отсутствует UTF-8 BOM для Windows PowerShell 5.1")
        for required in ("Get-AuthenticodeSignature", "CN=Docker Inc, O=Docker Inc, L=Palo Alto, S=California, C=US, SERIALNUMBER=4817464, OID.2.5.4.15=Private Organization, OID.1.3.6.1.4.1.311.60.2.1.2=Delaware, OID.1.3.6.1.4.1.311.60.2.1.3=US",
                         "b6bd29272b07ad4d0f1322a739499d67ca3bac3f", "exit 0", "exit 1", "exit 2"):
            if required not in tool:
                fail(f"{signature_tool}: отсутствует pin/Authenticode/exit contract {required}")
        if "Invoke-WebRequest" in tool or "Invoke-RestMethod" in tool:
            fail(f"{signature_tool}: локальный verifier не должен скачивать installer")
    for path in engine_files:
        engine_text = path.read_text(encoding="utf-8")
        if "Assert-HrmDockerInstallerSignature.ps1" in engine_text or "Get-AuthenticodeSignature" in engine_text:
            fail(f"{path}: Authenticode verifier не должен вызываться из движка")

    print(f"Проверено файлов: {len(files)}")
    if FAILURES:
        print(f"ПРОВАЛОВ: {len(FAILURES)}")
        return 1
    print("Структурная проверка пройдена (полная проверка — run-tests.ps1 в CI).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
