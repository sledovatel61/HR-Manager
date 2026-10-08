"""Регрессия: не-ASCII в run-блоках шагов `shell: powershell` (Windows PowerShell 5.1).

Раннер пишет временный скрипт шага в UTF-8 БЕЗ BOM, а Windows PowerShell 5.1
читает такой файл в ANSI-кодировке. Часть кириллических букв превращается при
этом в символы, которые парсер PowerShell считает строковыми кавычками
(«ф» -> U+201D, «у» -> U+201C, «т» -> U+2019, «в» -> U+201A, «д» -> U+201E).
Кавычка внутри строки в двойных кавычках закрывает её раньше времени: скрипт не
разбирается целиком, шаг умирает примерно за секунду и НЕ оставляет аннотаций —
по журналу шага (логи шагов из среды сопровождения не читаются) причину сбоя не
видно вообще. Именно так пропал гейт снимка в шаге смоука установки: слово
«файлов» в `throw "..."` давало U+201D.

Правило реализовано в infra/windows/tests/lint-engine.py и проверяется здесь на
синтетических workflow: тест падает, если правило исчезнет или ослабнет.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
LINT = REPO / "infra" / "windows" / "tests" / "lint-engine.py"
WORKFLOWS = REPO / ".github" / "workflows"

CYRILLIC_LINE = 'Write-Host "файлов: $count"'


def _load_lint() -> ModuleType:
    spec = importlib.util.spec_from_file_location("hrm_lint_engine", LINT)
    if spec is None or spec.loader is None:  # pragma: no cover - защита от опечатки в пути
        raise AssertionError(f"не удалось загрузить {LINT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workflow_text(shell: str, body: list[str], step_name: str = "Sample step") -> str:
    indented = "\n".join("          " + line for line in body)
    return (
        "jobs:\n"
        "  windows:\n"
        "    runs-on: windows-latest\n"
        "    steps:\n"
        f"      - name: {step_name}\n"
        f"        shell: {shell}\n"
        "        run: |\n"
        f"{indented}\n"
    )


def _lint_failures(workflow_text: str, tmp_path: Path) -> list[str]:
    workflow = tmp_path / "ci.yml"
    workflow.write_text(workflow_text, encoding="utf-8")
    module = _load_lint()
    module.FAILURES.clear()
    module.check_windows_powershell_steps(workflow)
    return list(module.FAILURES)


def test_cyrillic_run_block_on_powershell_is_rejected(tmp_path: Path) -> None:
    failures = _lint_failures(_workflow_text("powershell", [CYRILLIC_LINE]), tmp_path)
    assert len(failures) == 1
    assert "shell: powershell" in failures[0]
    assert "не-ASCII" in failures[0]


def test_cyrillic_run_block_on_pwsh_is_allowed(tmp_path: Path) -> None:
    # pwsh читает UTF-8 без BOM корректно: правило не должно мешать русским
    # сообщениям в шагах на PowerShell 7.
    assert _lint_failures(_workflow_text("pwsh", [CYRILLIC_LINE]), tmp_path) == []


def test_ascii_run_block_on_powershell_is_allowed(tmp_path: Path) -> None:
    assert _lint_failures(_workflow_text("powershell", ["Write-Host 'ok'"]), tmp_path) == []


def test_cyrillic_outside_run_block_is_allowed(tmp_path: Path) -> None:
    # Исполняется только run-блок; русское имя или комментарий шага безопасно.
    text = _workflow_text("powershell", ["Write-Host 'ok'"], step_name="Шаг с русским именем")
    assert _lint_failures(text, tmp_path) == []


@pytest.mark.parametrize("workflow", sorted(path.name for path in WORKFLOWS.glob("*.yml")))
def test_repository_workflows_keep_powershell_steps_ascii(workflow: str) -> None:
    module = _load_lint()
    module.FAILURES.clear()
    module.check_windows_powershell_steps(WORKFLOWS / workflow)
    assert list(module.FAILURES) == []
