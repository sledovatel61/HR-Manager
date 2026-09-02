# Accessibility — HR Manager Design

**Цель:** WCAG 2.2 Level AA
**Дата:** 2026-09-02
**Область:** design-prototype + guidelines for production

## 1. Принципы

1. Не полагаться только на цвет, анимацию или hover.
2. Клавиатура = полный паритет мыши для критических потоков.
3. Semantic HTML first; ARIA — когда семантики не хватает.
4. Русские строки и длинные ФИО не должны ломать layout (wrap/truncate with title).
5. `prefers-reduced-motion` отключает non-essential motion.

## 2. Контраст

| Пара | Ожидание | Проверка в прототипе |
|---|---|---|
| text-primary on surface | ≥4.5:1 | **Проверено** вручную (#111827 on #FFF ≈ 16:1) |
| text-secondary on surface | ≥4.5:1 | **Проверено** (#6B7280 on #FFF ≈ 5.0:1) |
| text-muted on surface | ≥4.5:1 для essential; decorative ok lower | **Частично** — muted не для essential labels |
| primary button white on indigo-600 | ≥4.5:1 | **Проверено** |
| status chips text on tint bg | ≥4.5:1 | **Проверено** на основных статусах |
| focus ring indigo on white | ≥3:1 UI | **Проверено** |
| borders for inputs | ≥3:1 non-text | **Проверено** gray-200 borderline — усилен gray on focus |

Инструмент: ручной расчёт contrast ratio; automated axe не запускался в CI (ограничение среды) — **требует отдельного аудита**.

## 3. Focus & keyboard

| Требование | Статус |
|---|---|
| focus-visible ring на controls | Проверено в CSS |
| Tab order logical (rail → header → main) | Проверено вручную |
| Skip link to main | Реализован |
| Modal focus trap | Реализован |
| Escape closes top overlay | Реализован |
| Return focus to invoker | Реализован для dialog/palette |
| Command palette arrow+enter | Реализован |
| Dropdown arrow keys | Частично (базовый) |
| Kanban without drag-only | Меню «Переместить» |
| Shortcuts documented `?` | Реализован |

## 4. Semantics & names

- Landmarks: `header`, `nav`, `main`, `complementary`
- Buttons have accessible names (text or aria-label)
- Form inputs associated labels
- Tables: `th scope="col"`
- Dialogs: `role="dialog"` `aria-modal="true"` `aria-labelledby`
- Toasts: `aria-live="polite"` region
- Status chips: text label always
- Icons decorative: `aria-hidden="true"`

## 5. Target size

Interactive controls ≥ 24×24 CSS px (WCAG 2.5.8); primary buttons 32+; icon buttons 32.

## 6. Motion

```css
@media (prefers-reduced-motion: reduce) { … }
```

Shimmer skeletons become static opacity. Palette open without scale bounce.

## 7. Zoom 200%

Layout should reflow; horizontal scroll only inside table region. **Проверено частично** на 1280 width simulation; full browser zoom audit — **требует отдельного аудита**.

## 8. Error & session states

- Login errors linked to fields
- Session expired: blocking, focus on CTA, no inert background interaction
- Permission denied: heading + explanation + exit path
- Network error: retry control focusable

## 9. What was NOT fully automated

- Screen reader pass (NVDA/VoiceOver) — not run in this environment
- axe-core / lighthouse CI — not integrated
- Color blindness simulation — manual spot-check only
- Full WCAG 2.2 checklist sign-off — **не заявляется как сертификация**

## 10. Production recommendations

1. Integrate `@axe-core/react` in dev + jest-axe tests for critical pages.
2. Use Radix/React Aria primitives for dialog, combobox, menu.
3. Document shortcuts in-app always, not only `?`.
4. Test with real Cyrillic long names and 200% zoom in QA.
5. Prefer visible labels over placeholder-only.
