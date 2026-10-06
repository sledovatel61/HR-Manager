"""Режим ``--pair`` скрипта контраста: замер нельзя испортить двойным слоем.

Ревью раунда 8: ``python3 scripts/measure-contrast.py --pair --surface-raised
--cat-warning-1`` накладывал базовую поверхность дважды — как фон и как
«состояние» — и показывал провал AA (3,71:1 в тёмной теме), которого нет:
честный замер даёт 4,82:1. Число попало и в комментарий CSS, и в тело PR.

Инструмент, которым получают числа для отчёта, не должен позволять такую
ошибку молча, поэтому первый аргумент ``--pair`` обязан быть состоянием из
``STATE_SURFACES`` (или ``-``). Эти тесты фиксируют и сам отказ, и то, что
отказ не заменяет честный замер: отвергая вызов, скрипт печатает настоящее
число и подсказывает команду, которой его воспроизводят.

Скрипт — репозиторный инструмент, а не часть backend; тесты живут здесь
потому, что backend-набор уже прогоняется в CI (job «Backend checks»),
и новый прогон для одного файла не нужен.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "measure-contrast.py"
RATIO_RE = re.compile(r"(\d+\.\d+):1")


def _load() -> ModuleType:
    """Импортировать скрипт по пути: он не пакет, но модуль обычный."""
    spec = importlib.util.spec_from_file_location("measure_contrast_module", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _ratios(stdout: str) -> list[float]:
    return [float(value) for value in RATIO_RE.findall(stdout)]


def test_pair_rejects_a_base_surface_and_prints_the_honest_number() -> None:
    """Отвергнутый вызов обязан показать правду, а не только поругаться."""
    rejected = _run("--pair", "--surface-raised", "--cat-warning-1")
    honest = _run("--pair", "-", "--cat-warning-1")

    assert honest.returncode == 0
    assert rejected.returncode == 2, rejected.stdout + rejected.stderr
    assert "--surface-raised" in rejected.stderr
    assert "BASE_SURFACES" in rejected.stderr
    # Подсказка должна быть командой, а не описанием: её можно скопировать.
    assert "measure-contrast.py --pair - --cat-warning-1" in rejected.stderr
    # И число в отказе — то же, что даёт честный замер (обе темы).
    assert _ratios(rejected.stdout) == _ratios(honest.stdout)
    assert len(_ratios(honest.stdout)) == 2


def test_pair_rejects_every_base_surface() -> None:
    """Не только --surface-raised: любая база — ошибка использования."""
    module = _load()
    for base in module.BASE_SURFACES:
        result = _run("--pair", base, "--text-primary")
        assert result.returncode == 2, f"{base}: {result.stdout}{result.stderr}"


def test_pair_rejects_an_unknown_state_token() -> None:
    result = _run("--pair", "--not-a-token", "--text-primary")

    assert result.returncode == 2
    assert "--surface-hover" in result.stderr  # список допустимых состояний


def test_pair_requires_two_arguments() -> None:
    result = _run("--pair", "--surface-raised")

    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_base_and_state_surfaces_do_not_overlap() -> None:
    """Инвариант, из которого вырос дефект: база — не состояние."""
    module = _load()

    assert not set(module.BASE_SURFACES) & set(module.STATE_SURFACES)


def test_pair_rejects_an_unknown_text_token() -> None:
    """Опечатка в имени токена — ошибка, а не молчаливое «ок» без чисел."""
    result = _run("--pair", "-", "--not-a-text-token")

    assert result.returncode == 2
    assert "неизвестный токен" in result.stderr


def test_pair_returns_nonzero_when_a_theme_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """«ПРОВАЛ AA» в выводе обязан быть виден по коду выхода."""
    module = _load()
    monkeypatch.setattr(module, "AA_NORMAL", 21.0)

    assert module.print_pair(None, "--text-primary") == 1
    assert "ПРОВАЛ AA" in capsys.readouterr().out


def test_pair_returns_zero_for_a_passing_pair() -> None:
    module = _load()

    assert module.print_pair(None, "--text-tertiary") == 0


def test_notes_and_refs_have_no_stale_line_numbers() -> None:
    """``--check-refs`` проверяет и машинные refs, и прозу заметок."""
    module = _load()

    assert module.check_refs() == 0


def test_evidence_notes_name_a_literal_for_every_line_reference() -> None:
    """Класс дефекта раунда 8: номер строки без литерала непроверяем."""
    module = _load()

    for label, evidence in module.all_evidence():
        for rel, start, end, literal in module.note_refs(evidence.note):
            assert rel, f"{label}: ссылка без файла"
            assert literal, f"{label}: {rel}:{start}-{end} без ожидаемого текста"
