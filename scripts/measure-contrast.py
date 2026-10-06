#!/usr/bin/env python3
"""Контраст AA по фактическим значениям токенов — с композитингом.

Зачем нужен пересчёт, а не «цвет текста из токена»: поверхности полупрозрачные
(--surface-app / -raised / -glass — это rgba), и лежат они не на «бумаге», а на
canvas, под которым нарисован mesh: три цветных пятна (--blob-1..3) с общей
прозрачностью --mesh-opacity. Фактический фон текста = canvas → пятно → стекло,
и именно с ним надо считать контраст.

Запуск из корня репозитория:

    python3 scripts/measure-contrast.py

Порог WCAG AA для обычного текста — 4.5:1. Скрипт печатает каждую пару
«текст / фон» и худший результат по теме; ненулевой код выхода, если есть провал.

Режимы:

    python3 scripts/measure-contrast.py              # гейт: все пары из списка
    python3 scripts/measure-contrast.py --audit      # пары, найденные в CSS
    python3 scripts/measure-contrast.py --check-refs # ссылки в доказательствах
    python3 scripts/measure-contrast.py --pair <состояние|-> <текст>  # одна пара

Первые три запускаются в CI (job «Frontend checks»): гейт ловит провал
контраста, аудит — пары в вёрстке, которых нет в гейте, `--check-refs` —
устаревшие ссылки в доказательствах. `--pair` — режим для отчёта: печатает
одну пару в двух темах, чтобы любое число в тексте воспроизводилось одной
командой, и возвращает 1, если хотя бы в одной теме пара ниже AA.

Первый аргумент `--pair` — **состояние** (токен из ``STATE_SURFACES``) или
``-``, если состояния нет. Базовая поверхность в первом аргументе — ошибка
использования: режим и так перебирает ``BASE_SURFACES`` и накладывает
состояние вторым слоем, поэтому ``--pair --surface-raised …`` накладывал бы
поверхность дважды и показывал провал там, где его нет. Такой вызов падает
с кодом 2 и подсказывает честную команду. Локально то же самое:
`make contrast`.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    ROOT / "frontend/src/design-system/tokens.css",
    ROOT / "frontend/src/design-system/components/bentoSurface.css",
]
AA_NORMAL = 4.5

# Что именно мерим. Базовые поверхности — на них лежит весь интерфейс.
# Состояния (hover/pressed/selected/sunken) полупрозрачны и накладываются поверх
# базовой, поэтому перебираются вторым слоем. Текст: primary/secondary/tertiary/
# link; disabled исключён (WCAG освобождает неактивные элементы), inverse и
# on-accent считаются не против подложки, а против плотного акцента.
BASE_SURFACES = ["--surface-app", "--surface-raised", "--surface-glass", "--surface-sidebar"]
STATE_SURFACES = [
    None,
    "--surface-hover",
    "--surface-pressed",
    "--surface-selected",
    "--surface-selected-hover",
    "--surface-sunken",
    "--surface-disabled",
    # Сайдбар: свои токены состояний, и активный пункт — самое частое
    # «выделение» в интерфейсе. Исключать их из гейта нельзя ровно по той же
    # причине, по которой нельзя было исключать остальные состояния.
    "--surface-sidebar-hover",
    "--surface-sidebar-active",
    # Заливка «акцент-чипов»: на ней лежит акцентный текст
    # (candidates.css:156-157, workspace.css:172-173, tabs.css:42-43).
    "--accent-subtle",
    # Заливки статусных плашек и баннеров: на них лежит обычный текст
    # (queue.css .queue-stale, statusChip.css:20, stateViews.css:23-24).
    "--status-warning-bg",
    "--status-danger-bg",
]
TEXTS = [
    "--text-primary",
    "--text-secondary",
    "--text-tertiary",
    # Акцентный текст лежит на выделенных поверхностях: .icon-btn-ghost.is-active
    # (button.css:144), .sidebar-link.is-active (workspace.css:87).
    "--text-link",
    "--accent-default",
    "--accent-on-subtle",
    "--accent-on-subtle-hover",
    # Статусные тексты: .field-error (field.css:24), .status-chip-danger
    # (statusChip.css:22), .toast-danger svg (toast.css:42).
    "--status-warning-fg",
    "--status-danger-fg",
]

# ---------------------------------------------------------------------------
# Какие пары «состояние × текст» реально есть в вёрстке.
#
# Полный перебор «каждая поверхность × каждое состояние × каждый текст» мерить
# бессмысленно: он содержит комбинации, которых в разметке нет (третичный текст
# на выбранной строке), и требует ради них портить палитру. Поэтому гейт — это
# список пар, найденных грепом; у каждой пары ниже указан файл и строка правила,
# где состояние и текст встречаются вместе. Комбинации вне списка скрипт не
# молча пропускает, а печатает отдельно — вместе с grep-командой, которой любой
# может проверить, что их правда нет.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Evidence:
    """Доказательство пары: заметка для человека + ссылки для машины.

    ``refs`` — тройки ``(файл от frontend/src, строка, токен)``: на этой строке
    обязан встречаться ``var(<токен>)``. Их проверяет режим ``--check-refs``,
    потому что ссылки в доказательствах устаревают от любой правки CSS —
    ревью раунда 7 нашло пять таких, и доказательство, которое нельзя
    проверить, перестаёт быть доказательством.

    ``note`` — не только текст для человека: каждое упоминание
    ``файл:строка`` (и короткое ``:строка``) в заметке обязано называть
    литерал, который на этой строке должен быть, и ``--check-refs`` его
    проверяет. Ревью раунда 8 нашло три ссылки, у которых номера строк
    разошлись с кодом при зелёном гейте: машинные ``refs`` были верны, а
    прозу никто не проверял. Правило закрывает класс: номер строки без
    литерала считается устаревшим.
    """

    note: str
    refs: tuple[tuple[str, int, str], ...] = ()


PAIR_EVIDENCE: dict[tuple[str, str | None], Evidence] = {
    ("--surface-hover", "--text-secondary"): Evidence(
        "candidates.css:125-126 `.candidates-table tbody tr:hover` — фон (candidates.css:126 "
        "`var(--surface-hover)`); цвет ячейки наследуется от candidates.css:112 "
        "`var(--text-secondary)`",
        refs=(
            ("features/candidates/candidates.css", 126, "--surface-hover"),
            ("features/candidates/candidates.css", 112, "--text-secondary"),
        ),
    ),
    ("--surface-hover", "--text-tertiary"): Evidence(
        "stateViews.css:12-21 `.state-view-icon` (фон — stateViews.css:19 "
        "`var(--surface-hover)`, цвет — stateViews.css:20 `var(--text-tertiary)`); "
        "calendar.css:128-131 `.calendar-chip:hover` (фон — calendar.css:130 "
        "`var(--surface-hover)`) с `.calendar-chip-time` (цвет — calendar.css:134 "
        "`var(--text-tertiary)`)",
        refs=(
            ("design-system/components/stateViews.css", 19, "--surface-hover"),
            ("design-system/components/stateViews.css", 20, "--text-tertiary"),
            ("features/calendar/calendar.css", 130, "--surface-hover"),
            ("features/calendar/calendar.css", 134, "--text-tertiary"),
        ),
    ),
    ("--surface-hover", "--text-link"): Evidence(
        "candidates.css:145-146 `.row-name:hover .row-fullname` — цвет (candidates.css:146 "
        "`var(--text-link)`); фон hover-строки задаёт candidates.css:125-126 "
        "`.candidates-table tbody tr:hover` (candidates.css:126 `var(--surface-hover)`)",
        refs=(
            ("features/candidates/candidates.css", 146, "--text-link"),
            ("features/candidates/candidates.css", 126, "--surface-hover"),
        ),
    ),
    ("--surface-selected", "--text-secondary"): Evidence(
        "candidates.css:129-130 `.candidates-table tbody tr:focus-within` — фон "
        "(candidates.css:130 `var(--surface-selected)`); цвет ячейки наследуется от "
        "candidates.css:112 `var(--text-secondary)`",
        refs=(
            ("features/candidates/candidates.css", 130, "--surface-selected"),
            ("features/candidates/candidates.css", 112, "--text-secondary"),
        ),
    ),
    ("--surface-selected", "--text-tertiary"): Evidence(
        "calendar.css:107-108 `.calendar-cell:has(.calendar-chip)` — фон (calendar.css:108 "
        "`var(--surface-selected)`); чип — calendar.css:113-126 `.calendar-chip`; время чипа "
        "— calendar.css:133-134 `.calendar-chip-time` (цвет — calendar.css:134 "
        "`var(--text-tertiary)`)",
        refs=(
            ("features/calendar/calendar.css", 108, "--surface-selected"),
            ("features/calendar/calendar.css", 134, "--text-tertiary"),
        ),
    ),
    ("--surface-selected", "--accent-default"): Evidence(
        "button.css:143-146 .icon-btn-ghost.is-active",
        refs=(
            ("design-system/components/button.css", 144, "--surface-selected"),
            ("design-system/components/button.css", 145, "--accent-default"),
        ),
    ),
    ("--accent-subtle", "--accent-default"): Evidence(
        "queue.css:657-670 `.queue-link` — базовое состояние кнопки «Непрочитанных "
        "уведомлений»; фон — queue.css:665 `var(--accent-subtle)`, цвет — queue.css:666 "
        "`var(--accent-default)`; рендерится в MyQueuePage.tsx:763 `queue-link`",
        refs=(
            ("features/queue/queue.css", 665, "--accent-subtle"),
            ("features/queue/queue.css", 666, "--accent-default"),
        ),
    ),
    ("--surface-selected-hover", "--accent-on-subtle-hover"): Evidence(
        "queue.css:672-677 `.queue-link:hover` — фон (queue.css:673 "
        "`var(--surface-selected-hover)`), цвет (queue.css:676 "
        "`var(--accent-on-subtle-hover)`); базовый цвет — queue.css:666 "
        "`var(--accent-default)`; рендер — MyQueuePage.tsx:763 `queue-link`. До правки здесь "
        "был --accent-default, и пара давала 3.88:1 в светлой теме",
        refs=(
            ("features/queue/queue.css", 673, "--surface-selected-hover"),
            ("features/queue/queue.css", 676, "--accent-on-subtle-hover"),
        ),
    ),
    ("--surface-hover", "--text-primary"): Evidence(
        "button.css:81-84 `.btn-ghost:hover:not(:disabled)` (фон — button.css:82 "
        "`var(--surface-hover)`, цвет — button.css:83 `var(--text-primary)`); "
        "button.css:68-71 `.btn-secondary:hover:not(:disabled)` — фон, цвет наследуется от "
        "button.css:65 `var(--text-primary)`; button.css:139-142 `.icon-btn-ghost:hover`; "
        "toast.css:61 `.toast-close`; workspace.css:214-217 `.topbar-logout:hover`; "
        "workspace.css:280-284 `.topbar-settings`",
        refs=(
            ("design-system/components/button.css", 82, "--surface-hover"),
            ("design-system/components/button.css", 83, "--text-primary"),
            ("design-system/components/toast.css", 61, "--surface-hover"),
            ("app-shell/workspace.css", 215, "--surface-hover"),
            ("app-shell/workspace.css", 216, "--text-primary"),
        ),
    ),
    ("--surface-pressed", "--text-primary"): Evidence(
        "candidates.css:207-225 .filter-chip-remove:hover",
        refs=(
            ("features/candidates/candidates.css", 223, "--surface-pressed"),
            ("features/candidates/candidates.css", 224, "--text-primary"),
        ),
    ),
    ("--surface-sunken", "--text-primary"): Evidence(
        "calendar.css:287-295 `.event-form-readonly` (фон — calendar.css:292 "
        "`var(--surface-sunken)`, цвет — calendar.css:293 `var(--text-primary)`); "
        "documentTemplates.css:192-203 `.template-token-picker button` (цвет — "
        "documentTemplates.css:198 `var(--text-primary)`, фон — documentTemplates.css:199 "
        "`var(--surface-sunken)`)",
        refs=(
            ("features/calendar/calendar.css", 292, "--surface-sunken"),
            ("features/calendar/calendar.css", 293, "--text-primary"),
            ("features/document-templates/documentTemplates.css", 199, "--surface-sunken"),
            ("features/document-templates/documentTemplates.css", 198, "--text-primary"),
        ),
    ),
    ("--accent-subtle", "--accent-on-subtle"): Evidence(
        "candidates.css:189-200 `.filter-chip` (фон — candidates.css:196 "
        "`var(--accent-subtle)`, цвет — candidates.css:197 `var(--accent-on-subtle)`); "
        "workspace.css:165-173 `.topbar-avatar` (фон — workspace.css:172 "
        "`var(--accent-subtle)`, цвет — workspace.css:173 `var(--accent-on-subtle)`); "
        "workspace.css:286-288 `.topbar-settings.is-active` (фон — workspace.css:287 "
        "`var(--accent-subtle)`); workspace.css:335-342 `.settings-card-icon` (цвет — "
        "workspace.css:341 `var(--accent-on-subtle)`); tabs.css:41-44 `.tab-item.is-active "
        ".tab-count`",
        refs=(
            ("features/candidates/candidates.css", 156, "--accent-subtle"),
            ("features/candidates/candidates.css", 157, "--accent-on-subtle"),
            ("app-shell/workspace.css", 172, "--accent-subtle"),
            ("app-shell/workspace.css", 173, "--accent-on-subtle"),
            ("design-system/components/tabs.css", 42, "--accent-subtle"),
            ("design-system/components/tabs.css", 43, "--accent-on-subtle"),
        ),
    ),
    ("--surface-pressed", "--text-secondary"): Evidence(
        "button.css:85-87 `.btn-ghost:active:not(:disabled)` — фон (button.css:86 "
        "`var(--surface-pressed)`); цвет — button.css:78 `var(--text-secondary)`",
        refs=(
            ("design-system/components/button.css", 86, "--surface-pressed"),
            ("design-system/components/button.css", 78, "--text-secondary"),
        ),
    ),
    ("--surface-sunken", "--text-tertiary"): Evidence(
        "calendar.css:93-99 `.calendar-hour-col` — фон (calendar.css:99 "
        "`var(--surface-sunken)`); цвет (calendar.css:95 `var(--text-tertiary)`)",
        refs=(
            ("features/calendar/calendar.css", 99, "--surface-sunken"),
            ("features/calendar/calendar.css", 95, "--text-tertiary"),
        ),
    ),
    ("--surface-sunken", "--text-secondary"): Evidence(
        "calendar.css:86-91 `.calendar-table thead th` (фон — calendar.css:90 "
        "`var(--surface-sunken)`, цвет — calendar.css:88 `var(--text-secondary)`); "
        "tabs.css:32-39 `.tab-count` (фон — tabs.css:33 `var(--surface-sunken)`); "
        "analytics.css:50-58 `.analytics-empty-note` (фон — analytics.css:55 "
        "`var(--surface-sunken)`), цвет — analytics.css:56 `var(--text-secondary)`",
        refs=(
            ("features/calendar/calendar.css", 90, "--surface-sunken"),
            ("features/calendar/calendar.css", 88, "--text-secondary"),
            ("design-system/components/tabs.css", 33, "--surface-sunken"),
            ("design-system/components/tabs.css", 34, "--text-secondary"),
            ("features/analytics/analytics.css", 55, "--surface-sunken"),
            ("features/analytics/analytics.css", 56, "--text-secondary"),
        ),
    ),
    ("--surface-sidebar-hover", "--text-primary"): Evidence(
        "workspace.css:80-83 `.sidebar-link:hover` (фон — workspace.css:81 "
        "`var(--surface-sidebar-hover)`, цвет — workspace.css:82 `var(--text-primary)`)",
        refs=(
            ("app-shell/workspace.css", 81, "--surface-sidebar-hover"),
            ("app-shell/workspace.css", 82, "--text-primary"),
        ),
    ),
    ("--status-warning-bg", "--text-primary"): Evidence(
        "queue.css:545-557 `.queue-stale` — баннер «сводка устарела», рендерится "
        "MyQueuePage.tsx; фон — queue.css:554 `var(--status-warning-bg)`, цвет — "
        "queue.css:555 `var(--text-primary)`",
        refs=(
            ("features/queue/queue.css", 554, "--status-warning-bg"),
            ("features/queue/queue.css", 555, "--text-primary"),
        ),
    ),
    ("--status-warning-bg", "--status-warning-fg"): Evidence(
        "statusChip.css:20 `.status-chip-amber`; stateViews.css:24 `.state-view-warning`; "
        "license.css:14-18 `.license-alert--warn` (фон — license.css:15 "
        "`var(--status-warning-bg)`, цвет — license.css:17 `var(--status-warning-fg)`)",
        refs=(
            ("design-system/components/statusChip.css", 20, "--status-warning-bg"),
            ("design-system/components/statusChip.css", 20, "--status-warning-fg"),
            ("design-system/components/stateViews.css", 24, "--status-warning-bg"),
            ("design-system/components/stateViews.css", 24, "--status-warning-fg"),
            ("features/license/license.css", 15, "--status-warning-bg"),
            ("features/license/license.css", 17, "--status-warning-fg"),
        ),
    ),
    ("--status-danger-bg", "--status-danger-fg"): Evidence(
        "statusChip.css:22 .status-chip-danger; stateViews.css:23 .state-view-danger",
        refs=(
            ("design-system/components/statusChip.css", 22, "--status-danger-bg"),
            ("design-system/components/statusChip.css", 22, "--status-danger-fg"),
            ("design-system/components/stateViews.css", 23, "--status-danger-bg"),
            ("design-system/components/stateViews.css", 23, "--status-danger-fg"),
        ),
    ),
}

# Пары, которых в вёрстке нет, с командой, доказывающей отсутствие.
# Ключ — (состояние, текст) или (состояние, None) для «ни с каким текстом».
ABSENT_EVIDENCE: dict[tuple[str, str | None], Evidence] = {
    # Токен объявлен в tokens.css, но как фон не используется нигде:Pairs с ним
    # раньше лежали в гейте, хотя в вёрстке их нет — это та же ошибка, что и с
    # записью про «--surface-selected-hover не встречается»: доказательством
    # отсутствия была команда grep, и она это отсутствие подтверждает.
    ("--surface-sidebar-active", None): Evidence(
        "grep -rn 'var(--surface-sidebar-active)' frontend/src → 0 вхождений; активный пункт "
        "навигации красится --nav-active-bg (workspace.css:85-88 `.sidebar-link.is-active` — "
        "фон workspace.css:86 `var(--nav-active-bg)`)",
        refs=(("app-shell/workspace.css", 86, "--nav-active-bg"),),
    ),
    ("--surface-pressed", "--text-tertiary"): Evidence(
        "--surface-pressed используется в button.css:72-73 "
        "`.btn-secondary:active:not(:disabled)` (фон — button.css:73 "
        "`var(--surface-pressed)`), button.css:85-87 `.btn-ghost:active:not(:disabled)` и "
        "candidates.css:222-224 `.filter-chip-remove:hover` — везде с `var(--text-primary)`, "
        "третичного текста на этой заливке нет",
        refs=(
            ("design-system/components/button.css", 73, "--surface-pressed"),
            ("design-system/components/button.css", 86, "--surface-pressed"),
            ("features/candidates/candidates.css", 223, "--surface-pressed"),
        ),
    ),
}


def blank_comments(text: str) -> str:
    """Выкинуть комментарии, сохранив номера строк.

    Комментарий вида «--accent-subtle: в светлой теме …» иначе парсится как
    объявление и затирает настоящее значение токена; просто вырезать его нельзя
    — поехали бы номера строк в аудите.
    """
    return re.sub(
        r"/\*.*?\*/",
        lambda m: "\n" * m.group(0).count("\n"),
        text,
        flags=re.S,
    )


def extract_block(text: str, selector: str) -> dict[str, str]:
    """Объявления токенов из одного блока (до закрывающей строки '}')."""
    start = text.find(selector)
    if start < 0:  # селектор оформлен иначе (":root{" и т.п.) — блок не берём
        return {}
    start += len(selector)
    end = text.find("\n}", start)
    if end < 0:
        end = len(text)
    # Комментарии выкидываем: иначе строка комментария вида
    # «--accent-subtle: в светлой теме …» парсится как объявление и затирает
    # настоящее значение токена.
    body = blank_comments(text[start:end])
    return dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", body))


def tokens_for(theme: str) -> dict[str, str]:
    """Токены темы: палитра объявлена в :root, тёмная тема ссылается на неё."""
    light: dict[str, str] = {}
    dark: dict[str, str] = {}
    for source in SOURCES:
        if not source.exists():  # в main нет bentoSurface.css — считаем без mesh
            continue
        text = source.read_text(encoding="utf-8")
        light.update(extract_block(text, ":root {"))
        dark.update(extract_block(text, '[data-theme="dark"] {'))
    return {**light, **dark} if theme == "dark" else light


def resolve(value: str, tokens: dict[str, str], depth: int = 0) -> tuple[float, float, float, float]:
    """Развернуть var() и hex/rgb(a) в (r, g, b, a): компоненты 0..255, alpha 0..1."""
    if depth > 10:
        raise ValueError(f"циклическая ссылка var(): {value}")
    value = value.strip()
    match = re.fullmatch(r"var\((--[a-z0-9-]+)(?:\s*,\s*([^)]+))?\)", value)
    if match:
        name, fallback = match.group(1), match.group(2)
        if name in tokens:
            return resolve(tokens[name], tokens, depth + 1)
        if fallback:
            return resolve(fallback, tokens, depth + 1)
        raise ValueError(f"необъявленный токен: {name}")
    if value.startswith("#"):
        digits = value[1:]
        if len(digits) == 3:
            digits = "".join(char * 2 for char in digits)
        r, g, b = (int(digits[i : i + 2], 16) for i in (0, 2, 4))
        return (r, g, b, 1.0)
    match = re.fullmatch(
        r"rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)(?:[\s,/]+([\d.]+%?))?\s*\)", value
    )
    if match:
        r, g, b = (float(match.group(i)) for i in (1, 2, 3))
        raw_alpha = match.group(4)
        if raw_alpha is None:
            alpha = 1.0
        elif raw_alpha.endswith("%"):
            alpha = float(raw_alpha[:-1]) / 100
        else:
            alpha = float(raw_alpha)
        return (r, g, b, alpha)
    raise ValueError(f"не понимаю цвет: {value!r}")


def over(top: tuple[float, float, float, float], bottom) -> tuple[float, float, float]:
    """Наложить полупрозрачный цвет на непрозрачный фон."""
    alpha = top[3]
    return tuple(top[i] * alpha + bottom[i] * (1 - alpha) for i in range(3))


def luminance(rgb) -> float:
    def channel(value: float) -> float:
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(value) for value in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


# ---------------------------------------------------------------------------
# --audit: механический поиск пар «состояние × цвет текста» в вёрстке.
#
# PAIR_EVIDENCE ведётся руками, а руки ошибаются — один раз это уже случилось:
# запись в «отсутствующих» сама называла существующую пару. Этот режим достаёт
# пары из CSS: правило, задающее фон состояния, и цвет текста, который на этом
# фоне окажется — из того же правила либо из базового правила того же селектора
# (`.queue-link:hover` наследует цвет от `.queue-link`).
# ---------------------------------------------------------------------------
STATE_SELECTORS = (
    ":hover",
    ":active",
    ":focus",
    ":focus-within",
    ":focus-visible",
    ".is-active",
    ".is-editing",
    ".is-current",
    ".is-selected",
)


def _strip_state(selector: str) -> str:
    """Базовый селектор: `.queue-link:hover` -> `.queue-link`."""
    base = selector.strip()
    for suffix in STATE_SELECTORS:
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return base


def audit_pairs(src_dir: Path) -> list[tuple[str, str, str, str]]:
    """[(состояние, текст, где фон, где цвет)] — по правилам CSS."""
    rules: dict[str, list[tuple[dict[str, str], str, int, int]]] = {}
    bodies: dict[str, str] = {}
    for path in sorted(src_dir.rglob("*.css")):
        rel = str(path.relative_to(src_dir))
        body = blank_comments(path.read_text(encoding="utf-8"))
        bodies[rel] = body
        for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", body):
            selector = " ".join(match.group(1).split())
            if selector.startswith("@"):
                continue
            decls = dict(
                (name.strip(), value.strip())
                for name, value in (
                    part.split(":", 1)
                    for part in match.group(2).split(";")
                    if ":" in part
                )
            )
            line = body[: match.start()].count("\n") + 1
            rules.setdefault(selector, []).append(
                (decls, str(path.relative_to(src_dir)), line, match.start())
            )

    def token(value: str, prefix: str) -> str | None:
        found = re.search(r"var\((--" + prefix + r"[a-z0-9-]*)\)", value or "")
        return found.group(1) if found else None

    def decl_line(rel_path: str, start_offset: int, prop: str) -> int:
        """Номер строки объявления, а не начала правила."""
        body = bodies[rel_path]
        segment = body[start_offset : start_offset + 400]
        found = re.search(r"(?:^|[;\s])" + prop + r"\s*:", segment)
        if not found:
            return body[:start_offset].count("\n") + 1
        return body[: start_offset + found.start()].count("\n") + 1

    def color_of(selector: str, seen: frozenset[str] = frozenset()) -> tuple[str, str, int] | None:
        for decls, path, line, _offset in rules.get(selector, []):
            colour = token(decls.get("color", ""), "")
            if colour and colour.startswith("--"):
                return colour, path, line
        base = _strip_state(selector)
        if base != selector and base not in seen and base in rules:
            return color_of(base, seen | {selector})
        return None

    pairs: list[tuple[str, str, str, str]] = []
    for selector, entries in rules.items():
        for decls, path, line, offset in entries:
            for prop in ("background", "background-color"):
                surface = token(decls.get(prop, ""), "")
                # Базовые поверхности (--surface-app, -raised, …) в гейте и так:
                # на них лежит любой текст. Интересуют только состояния
                # (None в STATE_SURFACES — это «без состояния», его тоже нет).
                if not surface:
                    continue
                if surface not in STATE_SURFACES and surface != "--accent-subtle":
                    continue
                colour = token(decls.get("color", ""), "")
                where_colour = f"{path}:{decl_line(path, offset, 'color')}"
                if not colour:
                    inherited = color_of(selector)
                    if inherited:
                        colour, cpath, cline = inherited
                        where_colour = (
                            f"{cpath}:{cline} (наследуется от {_strip_state(selector)})"
                        )
                if colour:
                    pairs.append(
                        (surface, colour, f"{path}:{decl_line(path, offset, prop)}", where_colour)
                    )
    return sorted(set(pairs))


def print_audit() -> int:
    src = ROOT / "frontend/src"
    known = set(PAIR_EVIDENCE)
    print("Пара «фон состояния × цвет текста», найденная в CSS:")
    print("(✔ — в PAIR_EVIDENCE, ⃠ — освобождена по WCAG, ✖ — не в гейте)")
    missing = 0
    for surface, colour, where_bg, where_fg in audit_pairs(src):
        if (surface, colour) in EXEMPT:
            mark = "⃠"
        elif (surface, colour) in known:
            mark = "✔"
        else:
            mark = "✖"
            missing += 1
        print(f"  {mark} {surface} + {colour}")
        print(f"      фон: {where_bg}")
        print(f"      цвет: {where_fg}")
    print(f"\nНе в гейте: {missing}")
    return 1 if missing else 0


def _css_files() -> list[Path]:
    return sorted((ROOT / "frontend/src").rglob("*.css"))



# --- Проверка прозы в заметках -------------------------------------------------
# Ревью раунда 8: --check-refs проверял только машинные refs, а номера строк,
# записанные в тексте заметки, не проверял никто — и три из них указывали мимо
# (queue.css:242-255 вместо 651-664 и т.п.). Класс дефекта закрывается здесь:
# каждая ссылка «файл:строка» в заметке обязана назвать литерал, который на
# этой строке должен быть, и он проверяется механически. Голое число без
# литерала — не доказательство, а украшение: такое сообщается как устаревшее.
NOTE_REF_RE = re.compile(
    # Полная ссылка «файл:строка» либо короткая «:строка» — вторая относится к
    # последнему названному в заметке файлу. Двоеточие короткой формы не может
    # стоять после цифры или точки: «5.61:1» — это отношение контраста, а не
    # номер строки.
    r"(?:(?P<file>[A-Za-z0-9_./-]+\.(?:css|tsx|ts|py))(?<!\d):|(?<![\d.,]):)"
    r"(?P<start>\d+)(?:-(?P<end>\d+))?"
)
# Что считается ожидаемым текстом сразу после ссылки: код в бэктиках либо
# селектор/токен (`.queue-link`, `#id`, `--token`, `var(--token)`).
NOTE_ANCHOR_RE = re.compile(r"\s*(?:`(?P<code>[^`]+)`|(?P<token>[.#][\w-]+|--[\w-]+|var\(--[\w-]+\)))")


def note_refs(note: str) -> list[tuple[str, int, int, str]]:
    """Ссылки ``файл:строка[-строка]`` (и короткие ``:строка``) с литералом.

    Короткая форма относится к последнему названному файлу — так заметки и
    пишутся («фон :250, цвет :251»). Ожидаемый литерал — код в бэктиках или
    селектор/токен сразу после ссылки; без него ссылка непроверяема, и это
    сообщается как устаревшее.
    """
    found: list[tuple[str, int, int, str]] = []
    current = ""
    for match in NOTE_REF_RE.finditer(note):
        if match.group("file"):
            current = match.group("file")
        anchor = NOTE_ANCHOR_RE.match(note[match.end() : match.end() + 80])
        literal = (anchor.group("code") or anchor.group("token")) if anchor else ""
        found.append(
            (
                current,
                int(match.group("start")),
                int(match.group("end") or match.group("start")),
                literal,
            )
        )
    return found


def all_evidence() -> list[tuple[str, Evidence]]:
    """Все доказательства с человекочитаемой подписью пары."""
    out: list[tuple[str, Evidence]] = []
    for (state, text), evidence in sorted(PAIR_EVIDENCE.items()):
        out.append((f"{state} + {text}", evidence))
    for (state, text), evidence in sorted(ABSENT_EVIDENCE.items()):
        out.append((f"нет пары: {state} + {text}", evidence))
    for (state, text), evidence in sorted(EXEMPT.items()):
        out.append((f"исключение: {state} + {text}", evidence))
    return out


def _resolve_note_ref(rel: str) -> Path | None:
    """Файл из заметки: и вёрстка, и скрипты, и по короткому имени.

    Заметки пишут пути кратко — ``queue.css``, ``MyQueuePage.tsx``,
    ``scripts/measure-contrast.py``, — поэтому после ROOT и frontend/src
    ищем по суффиксу пути среди файлов с расширением ссылки.
    """
    direct = ROOT / rel
    if direct.is_file():
        return direct
    src = ROOT / "frontend/src" / rel
    if src.is_file():
        return src
    hits = sorted(
        path
        for path in (ROOT / "frontend/src").rglob(f"*{Path(rel).name}")
        if path.is_file() and path.as_posix().endswith(rel)
    )
    return hits[0] if hits else None


def _resolve_ref(rel: str) -> Path | None:
    """Файл по имени (в доказательствах пути пишутся сокращённо)."""
    direct = ROOT / "frontend/src" / rel
    if direct.is_file():
        return direct
    hits = [path for path in _css_files() if path.as_posix().endswith(rel)]
    return hits[0] if hits else None


def check_refs() -> int:
    """Проверить ссылки в доказательствах: строка обязана содержать токен.

    Зачем отдельный режим: доказательство пары — это файл и строка. Стоит
    CSS-файлу сдвинуться, как ссылка начинает указывать не туда, а гейт при
    этом молчит — он считает токены, а не текст. Ревью раунда 7 нашло ровно
    это: пять ссылок указывали на строки, оставшиеся от предыдущего коммита,
    а ревью раунда 8 — ещё три, уже в прозе заметок. Поэтому проверяются обе
    части доказательства:

    * ``refs`` — машинные тройки ``(файл, строка, токен)``: на строке обязан
      быть ``var(<токен>)``;
    * ``note`` — каждое упоминание ``файл:строка`` (и короткое ``:строка``)
      обязано называть литерал, который на этой строке есть; ссылка без
      литерала непроверяема и считается устаревшей.

    Режим прогоняется в CI вместе с гейтом и аудитом.
    """
    problems = 0

    def report(message: str) -> None:
        nonlocal problems
        problems += 1
        print(f"  УСТАРЕЛО {message}")

    print("Ссылки в доказательствах (PAIR_EVIDENCE):")
    for (state, text), evidence in sorted(PAIR_EVIDENCE.items()):
        covered = {state, text}
        for rel, line, token in evidence.refs:
            path = _resolve_ref(rel)
            if path is None:
                report(f"{rel}: файл не найден ({state} + {text})")
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            if line > len(lines):
                report(f"{rel}:{line} — за концом файла ({len(lines)} строк)")
                continue
            content = lines[line - 1]
            if f"var({token})" not in content:
                report(
                    f"{rel}:{line} — ожидался var({token}), а там: {content.strip()[:70]}"
                )
            if token in covered:
                covered.discard(token)
        for token in sorted(covered):
            report(f"{state} + {text}: нет ссылки, подтверждающей var({token})")

    print("Ссылки в доказательствах (ABSENT_EVIDENCE):")
    for (state, text), evidence in sorted(ABSENT_EVIDENCE.items()):
        for rel, line, token in evidence.refs:
            path = _resolve_ref(rel)
            if path is None:
                report(f"{rel}: файл не найден ({state} + {text})")
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            if line > len(lines) or f"var({token})" not in lines[line - 1]:
                actual = lines[line - 1].strip() if line <= len(lines) else "<за концом>"
                report(f"{rel}:{line} — ожидался var({token}), а там: {actual[:70]}")
        if text is None:
            used = sum(
                1 for path in _css_files() if f"var({state})" in path.read_text(encoding="utf-8")
            )
            if used:
                report(
                    f"{state}: заявлено «не используется как фон», но файлов с "
                    f"var({state}) — {used}"
                )
        else:
            if not _absent_for_real(state, text):
                report(
                    f"{state} + {text}: заявлено отсутствие, но правило найдено "
                    f"({grep_command((state, text))})"
                )

    print("Ссылки в заметках (prose):")
    for label, evidence in all_evidence():
        for rel, start, end, literal in note_refs(evidence.note):
            path = _resolve_note_ref(rel)
            if path is None:
                report(f"{rel}: файл не найден — «{label}»")
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            if start < 1 or end < start or end > len(lines):
                report(f"{rel}:{start}-{end} — вне файла ({len(lines)} строк) — «{label}»")
                continue
            if not literal:
                report(
                    f"{rel}:{start}-{end} — ссылка без ожидаемого текста, "
                    f'добавьте в заметку литерал: «{label}»'
                )
                continue
            body = "\n".join(lines[start - 1 : end])
            if literal not in body:
                report(
                    f"{rel}:{start}-{end} — ожидался {literal}, а в строках: "
                    f"{body.strip()[:70] or '<пусто>'} — «{label}»"
                )

    print("Ссылки в исключениях (EXEMPT):")
    for (state, text), evidence in sorted(EXEMPT.items()):
        for rel, line, token in evidence.refs:
            path = _resolve_ref(rel)
            if path is None:
                report(f"{rel}: файл не найден ({state} + {text})")
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            if line > len(lines) or f"var({token})" not in lines[line - 1]:
                actual = lines[line - 1].strip() if line <= len(lines) else "<за концом>"
                report(f"{rel}:{line} — ожидался var({token}), а там: {actual[:70]}")

    print(f"\nИТОГО устаревших ссылок: {problems}")
    return 1 if problems else 0


def _absent_for_real(state: str, text: str) -> bool:
    """Правила «состояние + текст вместе» в вёрстке нет? (логика grep_command)."""
    for path in _css_files():
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if f"var({text})" in line and f"var({state})" in "\n".join(
                lines[max(0, index - 6) : index + 1]
            ):
                return False
    return True


def grep_command(pair: tuple[str, str | None]) -> str:
    """Команда, которой проверяется, что пара встречается в вёрстке."""
    state, text = pair
    if text is None:
        return f"grep -rn 'var({state})' frontend/src"
    return f"grep -rn -B6 'var({text})' frontend/src | grep 'var({state})'"


DEFAULT_ABSENT = "правила, задающие состояние и этот цвет текста вместе, не найдены"

# WCAG 1.4.3 освобождает неактивные контролы — такие пары не провал, а
# помеченное исключение (не «пропущенная пара»).
EXEMPT: dict[tuple[str, str], Evidence] = {
    ("--surface-disabled", "--text-disabled"): Evidence(
        "WCAG 1.4.3: неактивные элементы не обязаны проходить по контрасту (field.css:67-71 "
        "`.text-input:disabled` — фон field.css:69 `var(--surface-disabled)`, цвет "
        "field.css:70 `var(--text-disabled)`), то же правило field.css:67-71 "
        "`.text-input:disabled`",
        refs=(
            ("design-system/components/field.css", 69, "--surface-disabled"),
            ("design-system/components/field.css", 70, "--text-disabled"),
        ),
    ),
}


def main() -> int:
    problems = 0
    for theme in ("light", "dark"):
        tokens = tokens_for(theme)
        canvas = resolve(tokens["--surface-canvas"], tokens)[:3]
        mesh_opacity = float(tokens.get("--mesh-opacity", "0.5"))

        # Подложки: чистый canvas и canvas под каждым из трёх пятен mesh.
        # Худший фон для текста — самый светлый, поэтому перебираем все.
        backdrops = [("canvas", canvas)]
        for name in ("--blob-1", "--blob-2", "--blob-3"):
            if name not in tokens:  # в main mesh-подложки нет
                continue
            blob = resolve(tokens[name], tokens)
            backdrops.append((name, over(blob[:3] + (blob[3] * mesh_opacity,), canvas)))

        print(f"\n=== тема: {theme} ===")
        worst = (99.0, "")
        absent: dict[tuple[str, str | None], float] = {}
        for base_name in BASE_SURFACES:
            if base_name not in tokens:
                continue
            base_surface = resolve(tokens[base_name], tokens)
            for state_name in STATE_SURFACES:
                state_surface = (
                    resolve(tokens[state_name], tokens) if state_name in tokens else None
                )
                for backdrop_name, backdrop in backdrops:
                    background = over(base_surface, backdrop)
                    if state_surface is not None:
                        background = over(state_surface, background)
                    for text_name in TEXTS:
                        if text_name not in tokens:
                            continue
                        text = resolve(tokens[text_name], tokens)
                        ratio = contrast(over(text, background), background)
                        label = f"{text_name} на {base_name}"
                        if state_name:
                            label += f" + {state_name}"
                        label += f" ({backdrop_name})"

                        # Базовые поверхности без состояния встречаются всегда —
                        # любой текст лежит на какой-то из них. Состояния —
                        # только там, где пара подтверждена вёрсткой.
                        pair = (state_name, text_name)
                        if state_name is None or pair in PAIR_EVIDENCE:
                            if ratio < worst[0]:
                                worst = (ratio, label)
                            if ratio < AA_NORMAL:
                                problems += 1
                                print(f"  ПРОВАЛ {ratio:5.2f}:1  {label}")
                                if pair in PAIR_EVIDENCE:
                                    print(f"           вёрстка: {PAIR_EVIDENCE[pair].note}")
                        else:
                            key = pair if pair in ABSENT_EVIDENCE else (state_name, None)
                            absent[key] = min(absent.get(key, 99.0), ratio)

        # Активный пункт навигации: свои токены, поверх сайдбара и mesh.
        # workspace.css:86-87 — .sidebar-link.is-active.
        if "--nav-active-bg" in tokens and "--nav-active-fg" in tokens:
            for backdrop_name, backdrop in backdrops:
                background = over(resolve(tokens["--nav-active-bg"], tokens), backdrop)
                fg = resolve(tokens["--nav-active-fg"], tokens)
                ratio = contrast(over(fg, background), background)
                label = "--nav-active-fg на --nav-active-bg (сайдбар)"
                if ratio < worst[0]:
                    worst = (ratio, label)
                if ratio < AA_NORMAL:
                    problems += 1
                    print(f"  ПРОВАЛ {ratio:5.2f}:1  {label} ({backdrop_name})")
                    print("           вёрстка: workspace.css:86-87 .sidebar-link.is-active")

        verdict = "ок" if worst[0] >= AA_NORMAL else "ПРОВАЛ"
        print(f"  худший результат: {worst[0]:.2f}:1 ({verdict}) — {worst[1]}")
        if absent:
            print("  комбинации, которых в вёрстке нет (в гейт не входят):")
            for key in sorted(absent, key=lambda k: (k[0] or "", k[1] or "")):
                state, text = key
                what = f"{state} + {text}" if text else f"{state} + любой другой текст"
                evidence = ABSENT_EVIDENCE.get(key)
                why = evidence.note if evidence else DEFAULT_ABSENT
                print(f"    {what}: {absent[key]:.2f}:1")
                print(f"      проверка: {why.strip()}")
                print(f"      команда:  {grep_command(key)}")

    print(f"\nИТОГО проблем: {problems}")
    return 1 if problems else 0


def measure_pair(surface: str | None, text: str) -> dict[str, tuple[float, str] | None]:
    """Худший контраст пары «состояние + текст» в каждой теме.

    ``None`` — токен в теме не объявлен. Слои составляются так же, как в
    гейте: canvas (+ пятно mesh) → базовая поверхность (перебираются все
    ``BASE_SURFACES``) → состояние → текст. ``surface=None`` — состояния нет,
    текст лежит прямо на базовой поверхности.
    """
    result: dict[str, tuple[float, str] | None] = {}
    for theme in ("light", "dark"):
        tokens = tokens_for(theme)
        if text not in tokens or (surface is not None and surface not in tokens):
            result[theme] = None
            continue
        canvas = resolve(tokens["--surface-canvas"], tokens)[:3]
        mesh_opacity = float(tokens.get("--mesh-opacity", "0.5"))
        backdrops = [("canvas", canvas)]
        for name in ("--blob-1", "--blob-2", "--blob-3"):
            if name not in tokens:
                continue
            blob = resolve(tokens[name], tokens)
            backdrops.append((name, over(blob[:3] + (blob[3] * mesh_opacity,), canvas)))
        worst = (99.0, "")
        for base_name in BASE_SURFACES:
            if base_name not in tokens:
                continue
            for backdrop_name, backdrop in backdrops:
                background = over(resolve(tokens[base_name], tokens), backdrop)
                if surface is not None:
                    background = over(resolve(tokens[surface], tokens), background)
                ratio = contrast(over(resolve(tokens[text], tokens), background), background)
                if ratio < worst[0]:
                    worst = (ratio, f"{base_name} ({backdrop_name})")
        result[theme] = worst
    return result


def pair_usage() -> str:
    """Строка использования: первый аргумент — состояние, а не поверхность."""
    states = ", ".join(name for name in STATE_SURFACES if name)
    return (
        "usage: measure-contrast.py --pair <состояние|-> <текст>\n"
        f"  <состояние> — поверхность состояния: {states}\n"
        "  «-» — состояния нет: текст лежит прямо на базовой поверхности.\n"
        "  Базовая поверхность в первом аргументе — ошибка: режим сам перебирает\n"
        f"  {', '.join(BASE_SURFACES)} и накладывает состояние вторым слоем."
    )


def print_pair(surface: str | None, text: str) -> int:
    """Контраст одной пары в двух темах при худшей подложке.

    Тем же составлением слоёв, что и гейт: canvas (+ пятно mesh) → базовая
    поверхность → состояние → текст. Нужно, чтобы любое число в отчёте
    воспроизводилось одной командой, а не однократным расчётом вручную:

        python3 scripts/measure-contrast.py --pair --status-danger-bg --status-danger-fg
        python3 scripts/measure-contrast.py --pair - --text-tertiary   # без состояния

    Возвращает 1, если хотя бы в одной теме пара ниже AA: провал, который
    напечатан, но не виден по коду выхода, нельзя поймать ни скриптом, ни CI.
    """
    print(f"пара: {surface or 'без состояния'} + {text}")
    measured_themes = measure_pair(surface, text)
    failed = False
    for theme, measured in measured_themes.items():
        if measured is None:
            print(f"  {theme}: токен не объявлен")
            continue
        ratio, worst_label = measured
        verdict = "ок" if ratio >= AA_NORMAL else "ПРОВАЛ AA"
        if ratio < AA_NORMAL:
            failed = True
        print(f"  {theme}: {ratio:.2f}:1 ({verdict}) — худший фон {worst_label}")
    if all(value is None for value in measured_themes.values()):
        # Ни в одной теме токена нет: это опечатка в имени, а не «всё хорошо».
        print(f"неизвестный токен: {text}" + (f" / {surface}" if surface else ""), file=sys.stderr)
        return 2
    return 1 if failed else 0


def print_pair_rejected(surface: str, text: str) -> int:
    """Отказ на базовую поверхность в первом аргументе ``--pair``.

    Ревью раунда 8: ``--pair --surface-raised --cat-warning-1`` накладывал
    ``--surface-raised`` дважды (как базовую поверхность и как «состояние»),
    фон получался плотнее реального, и замер показывал провал AA там, где его
    нет. Правильное число — то, что печатается ниже без состояния; команда в
    подсказке воспроизводит его одной строкой.
    """
    print(f"{surface} — базовая поверхность (BASE_SURFACES), а не состояние.", file=sys.stderr)
    print(pair_usage(), file=sys.stderr)
    print("", file=sys.stderr)
    print(f"Честный замер для {text} — без состояния:", file=sys.stderr)
    print(f"  python3 scripts/measure-contrast.py --pair - {text}", file=sys.stderr)
    print(flush=True)
    print_pair(None, text)
    return 2


def run_pair(args: list[str]) -> int:
    """Разобрать аргументы ``--pair`` и либо отчитаться, либо отказать."""
    rest = [arg for arg in args if arg != "--pair"]
    if len(rest) != 2:
        print(pair_usage(), file=sys.stderr)
        return 2
    raw_state, text = rest
    if raw_state != "-" and raw_state not in STATE_SURFACES:
        return print_pair_rejected(raw_state, text)
    return print_pair(None if raw_state == "-" else raw_state, text)


if __name__ == "__main__":
    if "--audit" in sys.argv:
        sys.exit(print_audit())
    if "--check-refs" in sys.argv:
        sys.exit(check_refs())
    if "--pair" in sys.argv:
        sys.exit(run_pair(sys.argv[1:]))
    sys.exit(main())
