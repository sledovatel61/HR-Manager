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
"""

from __future__ import annotations

import re
import sys
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
    # (candidates.css:151-152, workspace.css:172-173, tabs.css:43-44).
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
PAIR_EVIDENCE = {
    ("--surface-hover", "--text-secondary"): (
        "candidates.css:121 .candidates-table tbody tr:hover, цвет ячейки — :112"
    ),
    ("--surface-hover", "--text-tertiary"): (
        "stateViews.css:18-19 .state-view-icon; calendar.css:129 .calendar-chip:hover "
        "с .calendar-chip-time (цвет — :134)"
    ),
    ("--surface-hover", "--text-link"): (
        "candidates.css:141 .row-name:hover .row-fullname (на фоне hover строки :121)"
    ),
    ("--surface-selected", "--text-secondary"): (
        "candidates.css:125 tr:focus-within, цвет ячейки — :112"
    ),
    ("--surface-selected", "--text-tertiary"): (
        "calendar.css:108 .calendar-cell:has(.calendar-chip) → :118 .calendar-chip "
        "→ :134 .calendar-chip-time"
    ),
    ("--surface-selected", "--accent-default"): (
        "button.css:144 .icon-btn-ghost.is-active"
    ),
    ("--accent-subtle", "--accent-default"): (
        "queue.css:217-228 .queue-link (базовое состояние кнопки "
        "«Непрочитанных уведомлений», рендерится MyQueuePage.tsx:275)"
    ),
    ("--surface-selected-hover", "--accent-on-subtle-hover"): (
        "queue.css:232 .queue-link:hover — фон --surface-selected-hover, цвет из "
        "того же правила; базовый цвет — :228 (MyQueuePage.tsx:275). До правки "
        "здесь был --accent-default и пара давала 3.88:1 в светлой теме"
    ),
    ("--surface-hover", "--text-primary"): (
        "button.css:80 .btn-secondary:hover, :137 .icon-btn-ghost:hover; "
        "toast.css:60 .toast-close:hover; workspace.css:212, :278; "
        "documentTemplates.css:203"
    ),
    ("--surface-pressed", "--text-primary"): (
        "candidates.css:215 .filter-chip-remove:hover"
    ),
    ("--surface-sunken", "--text-primary"): (
        "calendar.css:284; documentTemplates.css:148, :190"
    ),
    ("--accent-subtle", "--accent-on-subtle"): (
        "candidates.css:151-152 фильтр-чип; workspace.css:172-173 .topbar-avatar; "
        "workspace.css:287-288 .topbar-settings.is-active; workspace.css:341-342 "
        ".settings-card-icon; tabs.css:43-44 .tab-item.is-active .tab-count"
    ),
    ("--surface-pressed", "--text-secondary"): (
        "button.css:86 .btn-ghost:active, цвет — .btn-ghost :78"
    ),
    ("--surface-sunken", "--text-tertiary"): (
        "calendar.css:99 .calendar-hour-col, цвет — :95"
    ),
    ("--surface-sunken", "--text-secondary"): (
        "calendar.css:90 .calendar-table thead th, цвет — :87"
    ),
    ("--surface-sidebar-hover", "--text-primary"): (
        "workspace.css:81 .sidebar-link:hover, цвет — :82"
    ),
    ("--status-warning-bg", "--text-primary"): (
        "queue.css .queue-stale — баннер «сводка устарела» (MyQueuePage.tsx); "
        "цвет задан в том же правиле"
    ),
    ("--status-warning-bg", "--status-warning-fg"): (
        "design-system/components/statusChip.css:20 .status-chip-amber; "
        "design-system/components/stateViews.css:24 .state-view-warning; "
        "features/license/license.css:15-17"
    ),
    ("--status-danger-bg", "--status-danger-fg"): (
        "design-system/components/statusChip.css:22 .status-chip-danger; "
        "design-system/components/stateViews.css:23 .state-view-danger"
    ),
    ("--surface-sidebar-active", "--text-primary"): (
        "токен объявлен в tokens.css, но как фон нигде не используется: "
        "grep -rn 'var(--surface-sidebar-active)' frontend/src → 0"
    ),
}

# Пары, которых в вёрстке нет, с командой, доказывающей отсутствие.
# Ключ — (состояние, текст) или (состояние, None) для «ни с каким текстом».
ABSENT_EVIDENCE = {
    ("--surface-sidebar-active", None): (
        "grep -rn 'var(--surface-sidebar-active)' frontend/src → 0 вхождений, "
        "активный пункт навигации красится --nav-active-bg (workspace.css:86)"
    ),
    ("--surface-pressed", "--text-tertiary"): (
        "--surface-pressed используется в button.css:73, :86 и candidates.css:218 — "
        "везде с --text-primary"
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


def grep_command(pair: tuple[str, str | None]) -> str:
    """Команда, которой проверяется, что пара встречается в вёрстке."""
    state, text = pair
    if text is None:
        return f"grep -rn 'var({state})' frontend/src"
    return f"grep -rn -B6 'var({text})' frontend/src | grep 'var({state})'"


DEFAULT_ABSENT = "правила, задающие состояние и этот цвет текста вместе, не найдены"

# WCAG 1.4.3 освобождает неактивные контролы — такие пары не провал, а豁免.
EXEMPT = {
    ("--surface-disabled", "--text-disabled"): (
        "WCAG 1.4.3: неактивные элементы не обязаны проходить по контрасту "
        "(field.css:65 .text-input:disabled)"
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
                                    print(f"           вёрстка: {PAIR_EVIDENCE[pair]}")
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
                why = ABSENT_EVIDENCE.get(key, DEFAULT_ABSENT)
                print(f"    {what}: {absent[key]:.2f}:1")
                print(f"      проверка: {why.strip()}")
                print(f"      команда:  {grep_command(key)}")

    print(f"\nИТОГО проблем: {problems}")
    return 1 if problems else 0


if __name__ == "__main__":
    if "--audit" in sys.argv:
        sys.exit(print_audit())
    sys.exit(main())
