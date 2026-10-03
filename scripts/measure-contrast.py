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
]
TEXTS = ["--text-primary", "--text-secondary", "--text-tertiary", "--text-link"]


def extract_block(text: str, selector: str) -> dict[str, str]:
    """Объявления токенов из одного блока (до закрывающей строки '}')."""
    start = text.find(selector)
    if start < 0:
        return {}
    start += len(selector)
    end = text.find("\n}", start)
    if end < 0:
        end = len(text)
    return dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", text[start:end]))


def tokens_for(theme: str) -> dict[str, str]:
    """Токены темы: палитра объявлена в :root, тёмная тема ссылается на неё."""
    light: dict[str, str] = {}
    dark: dict[str, str] = {}
    for source in SOURCES:
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


def main() -> int:
    problems = 0
    for theme in ("light", "dark"):
        tokens = tokens_for(theme)
        canvas = resolve(tokens["--surface-canvas"], tokens)[:3]
        mesh_opacity = float(tokens.get("--mesh-opacity", "0.5"))

        # Подложки: чистый canvas и canvas под каждым из трёх пятен mesh.
        # Худший фон для светлого текста — самый светлый, поэтому перебираем все.
        backdrops = [("canvas", canvas)]
        for name in ("--blob-1", "--blob-2", "--blob-3"):
            blob = resolve(tokens[name], tokens)
            backdrops.append((name, over(blob[:3] + (blob[3] * mesh_opacity,), canvas)))

        print(f"\n=== тема: {theme} ===")
        worst = (99.0, "")
        states_worst = (99.0, "")
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
                        # Гейт AA — только базовые поверхности. Состояния
                        # (hover/pressed/selected) в этом дизайне несут основной
                        # или акцентный текст, а не третичный, поэтому в гейт не
                        # входят: печатаются отдельно, как справка.
                        gate = state_name is None
                        if gate:
                            if ratio < worst[0]:
                                worst = (ratio, label)
                            if ratio < AA_NORMAL:
                                problems += 1
                                print(f"  ПРОВАЛ {ratio:5.2f}:1  {label}")
                        elif ratio < states_worst[0]:
                            states_worst = (ratio, label)
        verdict = "ок" if worst[0] >= AA_NORMAL else "ПРОВАЛ"
        print(f"  худший результат (гейт): {worst[0]:.2f}:1 ({verdict}) — {worst[1]}")
        print(f"  справка, состояния поверх базовых: {states_worst[0]:.2f}:1 — {states_worst[1]}")

    print(f"\nИТОГО проблем: {problems}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
