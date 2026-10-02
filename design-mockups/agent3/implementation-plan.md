# План реализации редизайна — Agent 3

Макеты являются отдельными статическими HTML-файлами. В production-коде изменений нет.

## Semantic-токены

Значения ниже — исходная точка для выбранного направления. Статусы, интервалы, плотность, высота строки и общая шкала шрифтов остаются semantic-токенами из `frontend/src/design-system/tokens.css`; в production меняются значения токенов, а не логика компонентов.

| Направление | Semantic-токен | Light | Dark |
|---|---|---|---|
| **№1 Calm Precision** | `--surface-canvas` | `#F5F7FA` | `#11141B` |
| | `--surface-app` | `#FFFFFF` | `#191D26` |
| | `--surface-sidebar` | `#FBFCFD` | `#171B24` |
| | `--text-primary` | `#171A22` | `#F4F6FA` |
| | `--accent-default` | `#3F4BD1` | `#6879FF` |
| | `--accent-hover` | `#333CAC` | `#8794FF` |
| | `--border-subtle` | `#E9EDF2` | `rgba(255,255,255,.075)` |
| | `--border-focus` | `#4F5EEA` | `#9AA7FF` |
| | `--radius-lg` / `--shadow-1` | `11px` / `0 1px 2px rgba(18,27,44,.035)` | те же значения |
| **№2 Warm Soft** | `--surface-canvas` | `#F5F0E7` | `#1C1A17` |
| | `--surface-app` | `#FFFDF8` | `#25221E` |
| | `--surface-sidebar` | `#EEE7DB` | `#211E1A` |
| | `--text-primary` | `#343028` | `#F5EEE4` |
| | `--accent-default` | `#A44D35` | `#DC9272` |
| | `--accent-hover` | `#8E402C` | `#EDA98A` |
| | `--border-subtle` | `#EEE5D9` | `rgba(255,239,220,.08)` |
| | `--border-focus` | `#A44D35` | `#E4A181` |
| | `--radius-lg` / `--shadow-1` | `19px` / `0 2px 7px rgba(89,63,37,.045)` | те же значения |
| **№4 Bento Showcase** | `--surface-canvas` | `#F1F2FF` | `#141521` |
| | `--surface-app` | `rgba(255,255,255,.88)` | `rgba(30,31,48,.88)` |
| | `--surface-sidebar` | `#282644` | `#191927` |
| | `--text-primary` | `#23213A` | `#F4F2FF` |
| | `--accent-default` | `#4C46C8` | `#8378FF` |
| | `--accent-hover` | `#3C36AA` | `#9C94FF` |
| | `--border-subtle` | `rgba(72,66,184,.12)` | `rgba(190,183,255,.1)` |
| | `--border-focus` | `#635CF0` | `#BEB7FF` |
| | `--radius-lg` / `--shadow-1` | `21px` / `0 3px 10px rgba(53,47,137,.055)` | те же значения |

Обязательные переменные плотности — `--row-height`, `--control-height-md`, `--control-height-sm`, `--table-cell-padding-y`; значения `comfortable/compact` уже показаны переключателем в каждом макете. Для всех вариантов нужно отдельно проверить WCAG AA для основного/вторичного текста, фокуса и status-chip в обеих темах.

## Файлы для restyle в одном PR

1. Токены и оболочка: `frontend/src/design-system/tokens.css`, `frontend/src/design-system/global.css`, `frontend/src/app-shell/workspace.css`.
2. Общие компоненты: `frontend/src/design-system/components/{button,field,drawer,modal,statusChip,tabs,toast,avatar}.css`.
3. Главные рабочие экраны: `frontend/src/features/candidates/{candidates,drawer,kanban}.css`, `frontend/src/features/analytics/analytics.css`, `frontend/src/features/calendar/calendar.css`.
4. Материалы и выходы: `frontend/src/features/library/library.css`, `frontend/src/features/documents/documents.css`, `frontend/src/features/schedule/schedule.css`.

Перечень — планируемый охват, а не утверждение, что все файлы потребуется менять одинаково: победивший визуальный язык сначала переносится в токены, затем в общие компоненты и перечисленные feature-стили.

## Риски и границы

- **Не меняем** роутинг, бизнес-логику, backend/API, роли и проверки доступа, модель данных, импорты/экспорт, drag-and-drop и поведение существующих форм.
- **Доступность:** не заменять подписи цветом или иконкой; сохранить клавиатурную навигацию, семантику таблиц/таба/диалогов, видимый focus-ring и `prefers-reduced-motion`. Проверить контраст каждой пары в light/dark.
- **Шрифты:** production остаётся без внешних запросов и веб-шрифтов. Bento-эффект должен деградировать до непрозрачных surface без `backdrop-filter`; спокойная и тёплая темы не должны зависеть от декоративного слоя.
- **Мобильный экран:** это desktop-first продукт; не скрывать критичные действия и обеспечить прокрутку таблиц/Kanban на узких ширинах.
- **Данные и приватность:** тексты макетов синтетические; в боевую сборку заглушки не переносить.

## Оценка

Реализуемо одним PR: ориентировочно 3–5 инженерных дней на перенос токенов и компонентных стилей плюс визуальная проверка light/dark, comfortable/compact и контрольных ширин. Мокапы предназначены только для выбора направления; побеждает один вариант, поэтому направления не объединять.
