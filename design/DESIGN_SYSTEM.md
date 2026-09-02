# DESIGN_SYSTEM.md — дизайн-система HR Manager («Живая воронка»)

Полный исходник токенов: `design-prototype/src/styles/tokens.css`.
Полный исходник компонентов: `design-prototype/src/components/ui/`.
Здесь — компактное описание с обоснованием и антипаттернами.

## 1. Foundations

### 1.1 Цветовые токены (semantic, не raw)

Компоненты используют **только** semantic-переменные (`--surface-*`,
`--text-*`, `--border-*`, `--status-*`, `--accent-*`), никогда raw-палитру
(`--palette-*`) напрямую. Это единственный способ, которым тёмная тема
переключается без переписывания компонентов.

| Токен | Назначение |
|---|---|
| `--surface-canvas` | Фон всего приложения (за карточками) |
| `--surface-app` / `--surface-raised` | Фон карточек, модалок, таблиц |
| `--surface-sunken` | Фон вложенных панелей (фильтры, quick-grid) |
| `--surface-hover` / `--surface-pressed` | Интерактивные состояния |
| `--surface-selected` | Активная строка/пункт |
| `--text-primary/secondary/tertiary/disabled` | Иерархия текста |
| `--border-subtle/default/strong/focus` | Иерархия границ |
| `--accent-default/hover/pressed/subtle` | Единственный бренд-акцент (indigo) |
| `--status-{info,success,warning,danger,neutral,violet,teal,indigo}-{fg,bg,border}` | Семантические статусы для чипов/бейджей |

### 1.2 Фоновые уровни

canvas (0) → raised/app (1) → sunken (вложенный, -1 визуально) → overlay
(модальный слой). Явно 4 уровня, не больше — это специально ограничено,
чтобы не создавать "тортовую" iOS-подобную многослойность.

### 1.3 Текстовые цвета
primary (заголовки, значения) → secondary (лейблы, описания) → tertiary
(метаданные, таймстемпы) → disabled.

### 1.4 Spacing scale (4px база)
`--space-0` … `--space-10` = 0, 4, 8, 12, 16, 20, 24, 32, 40, 48, 64px.

### 1.5 Radius scale
`--radius-xs` 4px, `sm` 6px, `md` 8px (базовый для кнопок/инпутов/карточек),
`lg` 12px (панели/модалки), `xl` 16px (login-карточка), `full` (чипы, аватары).
**Антипаттерн, которого мы избегаем:** не у каждого элемента `radius-full`/
избыточное скругление — только там, где это несёт смысл (пилюли статусов,
аватары).

### 1.6 Shadows / elevation
`--shadow-1` (лёгкая, hover карточки) → `--shadow-4` (модалки/палитра).
Тени **не декоративны** — они однозначно привязаны к слою (canvas vs
overlay vs floating), не используются "для красоты" на плоских элементах.

### 1.7 Typography scale
`--text-size-2xs` 11px … `--text-size-3xl` 32px. Основной текст интерфейса —
`md` (14px). Числа в таблицах/KPI используют `font-variant-numeric:
tabular-nums`, чтобы колонки цифр не "прыгали".

### 1.8 Размеры иконок
`--icon-size-sm` 14px (внутри чипов/кнопок sm), `md` 16px (по умолчанию),
`lg` 20px (иконки в state-views). Единая толщина линии (`stroke-width: 1.8`)
для всех иконок набора — см. `design-prototype/src/icons/Icon.tsx`.

### 1.9 Сетка и breakpoints
Контент ограничен `--content-max-width: 1440px`, центрирован. Проверенные
контрольные точки: 1440×900 (основная), 1280×800 (`max-width: 1279px`),
1024×768 (`max-width: 1024px` для входа; таблицы получают горизонтальный
скролл естественным образом при любой ширине уже, а не через отдельный
брейкпоинт).

### 1.10 Motion durations и easing
`--duration-instant` 80ms (микро-обратная связь), `fast` 130ms (hover),
`base` 180ms (открытие popover/панелей), `slow` 260ms (drawer/палитра).
Easing: `standard` для большинства переходов, `decelerate` для
входа элементов, `accelerate` для выхода. **Все анимации** обёрнуты в
`@media (prefers-reduced-motion: reduce)` на глобальном и локальном уровне
(см. `styles/global.css` и каждый `*.css` компонента с `@keyframes`).

### 1.11 Focus ring
Единый `--focus-ring: 0 0 0 2px surface, 0 0 0 4px indigo`, применяется
**только** через `:focus-visible` (не `:focus`) — мышиные клики не показывают
рамку, клавиатурная навигация — показывает всегда. Действует одинаково на
кнопках, полях, строках таблицы, вкладках, пунктах Kanban.

### 1.12 Z-index layers
`base(0) < sticky(10) < dropdown(20) < drawer(30) < modal-overlay(40) <
modal(41) < toast(50) < tooltip(60)` — строго документированная лестница,
исключающая "магические" числа в компонентах.

### 1.13 Density modes
`comfortable` (по умолчанию, 48px строка) / `compact` (36px строка) —
управляется атрибутом `data-density` на `<html>`, переключается из topbar
или Settings. Это единственный "визуальный density switch", запрошенный как
wow-фича в промпте.

## 2. Компоненты

Ниже — обязательный список из промпта со ссылкой на файл реализации и
покрытые состояния.

| Компонент | Файл | Состояния |
|---|---|---|
| Button / Icon Button | `ui/Button.tsx` | default, hover, active, focus-visible, disabled, loading (spinner) |
| Text Input / Password Input | `ui/Field.tsx` (`TextInput`, `type="password"`) | default, hover, focus-visible, disabled, invalid (aria-invalid) |
| Select / Combobox (базовый select) | `ui/Field.tsx` (`SelectInput`) | default, hover, focus-visible, disabled |
| Date/Time picker | нативные `<input type="date"/"time">` в `ScheduleEventForm.tsx` | использует нативную a11y ОС, без переизобретения виджета |
| Checkbox | нативный `<input type="checkbox">` в `CandidateTable.tsx` (выбор строки) | нативные состояния браузера + focus-ring через `:focus-visible` |
| Badge / Status chip | `ui/StatusChip.tsx` | 8 semantic-тонов, всегда иконка+текст |
| Avatar | `ui/Avatar.tsx` | только инициалы, без внешних изображений |
| Tooltip | `ui/Tooltip.tsx` | hover и focus (не только hover) |
| Dropdown (меню пользователя, уведомления) | `shell/Topbar.tsx`, `shell/NotificationsPopover.tsx` | open/closed, click-away, Escape |
| Tabs | `ui/Tabs.tsx` | roving tabindex, active, стрелки навигации |
| Segmented control (view toggle, density) | `pageHeader.css` `.view-toggle` + `IconButton active` | active/inactive через `aria-pressed` |
| Command palette | `command/CommandPalette.tsx` | open/closed, фильтрация, активный пункт через `aria-activedescendant` |
| Toast | `ui/Toast.tsx` | success/info/danger, автозакрытие + ручное закрытие, `aria-live="polite"` |
| Modal | `ui/Modal.tsx` | dialog/alertdialog, focus trap, Escape, click-outside |
| Confirmation dialog | `ui/ConfirmDialog.tsx` | построен над Modal, danger-вариант |
| Drawer | `ui/Drawer.tsx` | focus trap, Escape, click-outside |
| Table | `features/candidates/CandidateTable.tsx`, переиспользуется в `UsersPage` | default row, hover, selected, row-actions на hover/focus-within |
| Pagination | *не реализована как отдельный визуальный компонент в этой итерации* — таблицы прототипа рендерят полный мок-набор без серверной пагинации; см. `IMPLEMENTATION_GUIDE.md` (реальная пагинация требует backend API этапа 2) |
| Filters | `features/candidates/FilterBar.tsx` | панель open/closed, активные значения, сброс |
| Saved views | `FilterBar.tsx` (select) | см. ограничение в `PERSONAS_AND_FLOWS.md` |
| Timeline / Activity item | `features/candidates/Timeline.tsx` | иконка по типу события, автор, дата |
| Kanban card | `features/candidates/KanbanBoard.tsx` | draggable, drag-over колонки, keyboard-alternative select |
| Calendar event | `pages/CalendarPage.tsx` (`.calendar-event-*`) | 4 типа событий, разные тона, клик → toast с деталями |
| KPI visualization | `features/analytics/KpiStrip.tsx` | не решётка одинаковых плиток — акцентирован первый показатель |
| Charts (funnel, source breakdown) | `features/analytics/FunnelChart.tsx`, `SourceBreakdown.tsx` | значение всегда продублировано текстом, не только длиной полосы/цветом |
| Empty state | `ui/StateViews.tsx` (`EmptyState`) | заменяет список целиком |
| Skeleton | `ui/StateViews.tsx` (`SkeletonRows`, `SkeletonCards`) | `aria-hidden`, `prefers-reduced-motion`-safe |
| Error state | `ui/StateViews.tsx` (`ErrorState`) | `role="alert"`, кнопка повтора |
| Permission-denied state | `ui/StateViews.tsx` (`PermissionDeniedState`) | используется на `AnalyticsPage`/`UsersPage`/`AuditPage`/`QueuePage` при нехватке роли |
| Session-expired state | `ui/StateViews.tsx` (`SessionExpiredState`) | полноэкранный, единственная доступная кнопка — "Войти снова" |

### Анти-паттерны (чего в системе нет намеренно)
- Нет "плиточного" дашборда, где каждая метрика — отдельная одинаковая
  карточка с иконкой и тенью (см. `KpiStrip` — единая лента, не грид карточек).
- Нет глобального glassmorphism/blur — единственное использование
  прозрачности — оверлей модалок (`--surface-overlay`), не декоративный.
- Нет неоновых градиентов; единственный градиент — фон login-экрана
  (`login-visual`), и тот приглушённый (slate→indigo-900), не кислотный.
- Радиус скругления не унифицирован "под one-size" — кнопки/инпуты 8px,
  панели 12px, пилюли-статусы 999px осознанно, а не случайно разные.
- Анимации не длиннее 260ms и не используются для сообщения состояния без
  дублирующего текстового/иконочного признака.

## 3. Темы

### Светлая (основная)
Реализована полностью, это тема по умолчанию и единственная, на которой
гарантированно проверялись все экраны.

### Тёмная (дополнительная, не блокирует прототип)
Собрана на **тех же semantic-токенах** (`[data-theme="dark"]` переопределяет
только значения переменных, не структуру компонентов) — переключатель есть в
topbar и Settings. Явно помечена как secondary: она не проходила отдельный
контраст-аудит построчно для каждого компонента (см. `ACCESSIBILITY.md`,
статус "требует отдельного аудита" для тёмной темы).

## 4. Примеры использования (текстовое описание, без Figma)

- **Смена статуса кандидата:** `CandidateDrawer` → select "Изменить этап" →
  состояние обновляется мгновенно (optimistic, т.к. это мок) → запись
  добавляется в Timeline → toast подтверждает действие. В production это
  должно быть: optimistic UI + серверное подтверждение + откат при ошибке
  (см. `IMPLEMENTATION_GUIDE.md`).
- **Передача кандидата:** `TransferDialog` — двухшаговый паттерн (выбор →
  подтверждение) специально разделён на 2 модалки, чтобы случайный клик по
  primary-кнопке не отправлял необратимое действие сразу.
