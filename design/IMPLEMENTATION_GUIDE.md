# Implementation Guide — перенос дизайна в production

**Не выполнять внедрение в этом этапе.**
**Дата:** 2026-09-02

## 1. Порядок внедрения

| Шаг | Что | Сложность | Зависимость |
|---|---|---|---|
| 0 | Design tokens CSS variables + theme provider | S | Нет |
| 1 | Layout shell: AppShell, Rail, Topbar, SkipLink | M | Auth session (этап security) |
| 2 | Primitives: Button, Input, Select, Badge, Avatar, Toast, Modal | M | Tokens |
| 3 | Login + session expired + forbidden pages | S | Auth API |
| 4 | Command palette shell (local nav + stub search) | M | Candidates API later |
| 5 | Candidates table + filters + pagination | L | Candidates API (этап 2–3) |
| 6 | Candidate drawer/page + timeline | L | Interactions API |
| 7 | Kanban board | L | Status update API |
| 8 | Transfer flow + confirm | M | Transfer API + audit |
| 9 | Queue home for HR | M | Owner filter |
| 10 | Calendar | L | Events API (этап 4) |
| 11 | Analytics | L | Metrics API (этап 5) |
| 12 | Users admin + audit UI | M | Security phase APIs |
| 13 | Templates | M | Этап 6 |
| 14 | Density + dark theme polish | S | Tokens |
| 15 | A11y audit + visual QA | M | All |

## 2. Предлагаемая структура frontend

```
frontend/src/
  app/                 # routes, providers
  shared/
    ui/                # design system primitives
    lib/               # a11y helpers, cn, dates
    config/            # roles, status maps
  features/
    auth/
    candidates/
    queue/
    calendar/
    analytics/
    users/
    audit/
    search/            # command palette
  styles/
    tokens.css
    global.css
```

## 3. Общие компоненты

Обязательно shared: Button, IconButton, Input, PasswordInput, Textarea, Select, Combobox, Checkbox, Switch, Badge, StatusChip, Avatar, Tooltip, DropdownMenu, Tabs, SegmentedControl, Modal, ConfirmDialog, Drawer, ToastProvider, Table, Pagination, EmptyState, Skeleton, ErrorState, ForbiddenState, SessionExpired, CommandPalette, FilterBar, SavedViews, Timeline, KpiStat, PageHeader, AppShell.

## 4. API-контракты (нужны от backend)

- Auth: login, logout, me, session errors codes
- Users CRUD + role change
- Audit list + filters
- Candidates CRUD, search, filter, soft delete
- Transfer endpoint (reason required)
- Interactions / timeline
- Events / calendar
- Analytics aggregations with metric definitions
- Templates metadata

UI не должен «угадывать» права — опираться на scopes/permissions в `/me`.

## 5. После этапа security можно сразу

- AppShell + role-based nav hiding (не security boundary)
- Login visual redesign matching Signal Desk
- Users & Audit screens wired to real API
- Session expired / locked / forbidden states
- Toast + Modal primitives
- Command palette for **navigation only**

Нельзя без candidates API: table data, transfer, kanban.

## 6. Стратегия без big rewrite

1. Ввести tokens параллельно со старыми styles.
2. Новые экраны только на shared/ui.
3. Постепенно заменять legacy.
4. Feature flags per route.
5. Prototype (`design-prototype/`) остаётся reference, не monorepo package unless extracted carefully.

## 7. Риски

| Риск | Митигация |
|---|---|
| Conflict with security agent | Не трогать frontend/src до merge security; design isolated |
| Scope creep wow features | Palette + density first; charts later |
| A11y debt custom widgets | Prefer headless a11y libs |
| Performance large tables | Virtualize >100 rows; server pagination |
| Metric definition drift | Single source definitions from backend |

## 8. Оценка wow-элементов

| Элемент | Prod complexity | Priority |
|---|---|---|
| Command palette | M | P0 |
| Candidate preview drawer | M | P0 |
| Density switch | S | P1 |
| Funnel viz | M | P1 |
| Timeline-first card | M | P0 |
| Saved views | M | P1 |
| Keyboard G+nav | S | P1 |
| Transfer confirm pattern | S | P0 |
