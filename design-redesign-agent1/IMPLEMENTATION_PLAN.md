# План реализации победившего направления: Mission Control

## Направление и визуальная система

**№3 — Mission Control (dark-first).** Тёмный графитовый операционный центр, ровные плотные панели, холодная мятная подсветка, неоновые статусные точки. Градиенты ограничены KPI-дельтами и небольшими спарклайнами; фростинг — только в диалогах и command palette. В мокапе нет runtime-зависимостей; DM Sans и IBM Plex Mono загружаются с CDN только для превью, переключатель проверяет тот же макет на системных стеках.

Палитра направления: тёмный canvas `#090E14`, панели `#101720` / `#151F29`, мятный акцент `#51D7B7`; светлый canvas `#EEF3F5`, app `#FBFDFE`, светлый вариант акцента `#087762`. Сайдбар остаётся глубоким графитовым в обеих темах.

## Семантические токены

Значения ниже — целевые значения для `tokens.css`. Все строки соответствуют semantic-переменным production; палитра не должна применяться напрямую из CSS компонентов.

| Группа / токены | Light | Dark |
|---|---|---|
| `--surface-canvas`, `--surface-app`, `--surface-raised`, `--surface-sunken` | `#EEF3F5`, `#FBFDFE`, `#FFFFFF`, `#E8EFF1` | `#090E14`, `#101720`, `#151F29`, `#0C1219` |
| `--surface-overlay`, `--surface-hover`, `--surface-pressed` | `rgba(14,29,35,.46)`, `rgba(22,52,59,.045)`, `rgba(22,52,59,.09)` | `rgba(3,8,13,.78)`, `rgba(216,236,242,.055)`, `rgba(216,236,242,.10)` |
| `--surface-selected`, `--surface-selected-hover`, `--surface-disabled` | `#E0F4EF`, `#D2EEE7`, `#E7EEF0` | `rgba(71,213,181,.12)`, `rgba(71,213,181,.18)`, `rgba(224,238,240,.055)` |
| `--surface-sidebar`, `--surface-sidebar-hover`, `--surface-sidebar-active` | `#111B23`, `rgba(235,248,249,.06)`, `rgba(90,224,190,.13)` | `#0C131A`, `rgba(220,238,240,.05)`, `rgba(68,219,182,.105)` |
| `--text-primary`, `--text-secondary`, `--text-tertiary`, `--text-disabled` | `#14252B`, `#425D65`, `#526B73`, `#566F77` | `#EEF5F6`, `#B2C1C9`, `#899BA5`, `#7A8B94` |
| `--text-inverse`, `--text-link`, `--text-on-accent`, `--text-sidebar` | `#F3FAF9`, `#145C78`, `#F6FFFB`, `#EDF4F4` | `#07120F`, `#92D7FF`, `#071611`, `#E6EFF1` |
| `--border-subtle`, `--border-default`, `--border-strong`, `--border-focus` | `#E5EDEF`, `#D7E2E5`, `#B9CBD0`, `#147B67` | `rgba(190,216,225,.075)`, `rgba(190,216,225,.13)`, `rgba(190,216,225,.24)`, `#58E1C2` |
| `--accent-default`, `--accent-hover`, `--accent-pressed`, `--accent-subtle`, `--accent-subtle-border` | `#087762`, `#066551`, `#064F42`, `#E0F4EF`, `#B9E3D8` | `#51D7B7`, `#83EFD2`, `#32AA8D`, `rgba(81,215,183,.12)`, `rgba(81,215,183,.28)` |
| `--status-info-{fg,bg,border}` | `#155F91`, `#E8F3FC`, `#C2DEF3` | `#94CEFF`, `rgba(69,154,242,.14)`, `rgba(105,177,247,.28)` |
| `--status-success-{fg,bg,border}` | `#176B4D`, `#E7F6EE`, `#BDE5CF` | `#83E4B1`, `rgba(47,182,117,.14)`, `rgba(90,209,147,.28)` |
| `--status-warning-{fg,bg,border}` | `#805307`, `#FFF5DE`, `#F1DCAA` | `#FFD17E`, `rgba(214,151,46,.15)`, `rgba(234,179,83,.30)` |
| `--status-danger-{fg,bg,border}` | `#9E343D`, `#FFF0F1`, `#F1C6CA` | `#FF929B`, `rgba(217,77,93,.15)`, `rgba(235,106,121,.29)` |
| `--status-neutral-{fg,bg,border}` | `#4C6269`, `#EEF2F3`, `#D8E1E3` | `#C0CDD2`, `rgba(191,210,215,.09)`, `rgba(191,210,215,.16)` |
| `--status-violet-{fg,bg,border}` | `#664298`, `#F3EDFB`, `#DDD0F3` | `#CFB1FF`, `rgba(151,111,219,.15)`, `rgba(172,133,235,.29)` |
| `--status-teal-{fg,bg,border}` | `#126C60`, `#E4F5F1`, `#B9E4DA` | `#75E0CE`, `rgba(54,191,166,.15)`, `rgba(75,210,183,.30)` |
| `--status-indigo-{fg,bg,border}` | `#424D9A`, `#EEF0FC`, `#D2D6F2` | `#B1BDFF`, `rgba(107,119,239,.17)`, `rgba(137,147,247,.30)` |
| `--text-sidebar-muted`, `--border-sidebar` | `#9EAFB5`, `rgba(232,245,246,.10)` | `#82929B`, `rgba(190,216,225,.09)` |
| `--space-1..10`, `--text-size-2xs..3xl`, `--line-height-*`, weights | Сохранить текущую 4px шкалу; применить текущие системные размеры/веса с плотной data-first иерархией | Те же значения, что Light |
| `--radius-xs..full` | `4px / 6px / 8px / 11px / 15px / 999px` | Те же значения, что Light |
| `--shadow-1..4` | `0 1px 2px rgba(25,53,59,.055)`; `0 4px 12px rgba(25,53,59,.075)`; `0 12px 32px rgba(25,53,59,.12)`; `0 24px 64px rgba(25,53,59,.16)` | `0 1px 2px rgba(0,0,0,.20)`; `0 4px 12px rgba(0,0,0,.24)`; `0 12px 32px rgba(0,0,0,.36)`; `0 24px 64px rgba(0,0,0,.48)` |
| `--duration-*`, `--easing-*` | Оставить существующие motion-токены; короткие переходы только transform/opacity и состояние контролов | То же |
| `--sidebar-width`, `--sidebar-width-collapsed`, `--row-height`, `--control-height-*` | `252px`, `76px`, `46px`, `37px / 30px` | Те же значения; compact: строка `36px`, контролы `32px / 26px` |

**Проверки контраста перед merge:** обычный текст — ≥4.5:1, крупный — ≥3:1; отдельная проверка цветных status-chip в обеих темах, focus-ring на поднятых поверхностях и контраста disabled-состояний. Не полагаться только на цвет: статус сохраняет текст и маркер.

## CSS-файлы production для restyle

Работать в пределах существующих компонентов и их CSS, без изменения бизнес-логики:

1. `frontend/src/design-system/tokens.css` — значения semantic-токенов обеих тем, density и типографическая шкала.
2. `frontend/src/design-system/global.css` — фон/текст приложения, focus-ring, общие переходы и фоновые слои.
3. `frontend/src/app-shell/workspace.css` — сайдбар, topbar, навигация, рабочая область, профиль и responsive-состояния.
4. `frontend/src/design-system/components/{button,field,modal,drawer,statusChip,tabs,toast,avatar,stateViews}.css` — соответствующие базовые паттерны и overlay.
5. `frontend/src/features/candidates/{candidates,drawer,kanban,attachments}.css` — список, drawer, статусы и канбан.
6. `frontend/src/features/analytics/analytics.css` — KPI, фильтры, графики и таблицы аналитики.
7. `frontend/src/features/{documents/documents,document-templates/documentTemplates,calendar/calendar,schedule/schedule,notifications/notifications,library/library}.css` — документы, календарь, график выхода, уведомления/напоминания и библиотека.
8. `frontend/src/features/{admin/admin,users/users,updates/updates,license/license}.css` и существующий блок очереди в `frontend/src/app-shell/workspace.css` — администрирование, пользователи, настройки и очередь. Отдельного `features/queue` в текущей структуре нет; функциональность остаётся без изменений.

## Риски и границы

- Не менять роутинг, запросы, формы, права, drag-and-drop, валидацию, таблицы и поведение уведомлений; это только визуальный слой.
- Не привязывать функциональный смысл к цвету или свечению; использовать текстовые статусы и значимые `aria-label`.
- Не добавлять CDN-шрифты в production и не вводить новые runtime-пакеты. Системный стек должен сохранять табличные цифры, контраст и компактность.
- В тёмной теме особенно проверить малый текст, статусы, placeholder, таблицы и focus-ring; в светлой — те же состояния на `surface-app` и `surface-sunken`.
- Не переносить градиенты/glass на обычные рабочие поверхности: градиент остаётся акцентом метрик, фростинг — только overlay.
- Мокап находится в `design-redesign-agent1/`; боевые файлы и `package.json` в рамках направления не менялись.

## Оценка

**Влезает в один PR:** да. Ориентир — 3–5 инженерных дней на токены, компонентный слой и экраны + 1 день на визуальную регрессию, контраст, responsive и полный прогон существующих тестов. Риск средний: экранов много, но при сохранении контрактов компонентов объём в основном ограничен CSS и проверкой обеих тем.
