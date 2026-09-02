# Информационная архитектура — HR Manager

**Концепция:** Signal Desk
**Дата:** 2026-09-02

## 1. Карта разделов

```
HR Manager
├── Вход (public)
├── Главная / Рабочий стол          [все роли; контент role-aware]
├── Моя очередь                     [HR primary; manager optional]
├── Кандидаты
│   ├── Таблица
│   ├── Kanban
│   └── Карточка /:id
│       ├── Timeline
│       ├── Данные
│       ├── События
│       └── Документы (заглушка этапа 6)
├── Календарь
├── Аналитика                       [manager+; HR — personal subset]
├── Шаблоны                         [этап 6; nav visible, content placeholder]
├── Пользователи                    [admin]
│   └── Создание / редактирование
├── Журнал аудита                   [admin; manager read optional]
└── Настройки
    ├── Профиль
    ├── Плотность / тема
    └── Сессия / выход
```

## 2. Глобальная навигация

### Left rail (primary)
| Item | Route (mock) | HR | Manager | Admin |
|---|---|---|---|---|
| Главная | `#/home` | ✓ | ✓ | ✓ |
| Моя очередь | `#/queue` | ✓ | ✓ | — |
| Кандидаты | `#/candidates` | ✓ | ✓ | ✓ |
| Календарь | `#/calendar` | ✓ | ✓ | ✓ |
| Аналитика | `#/analytics` | personal | full | full |
| Шаблоны | `#/templates` | ✓ | ✓ | ✓ |
| Пользователи | `#/users` | — | — | ✓ |
| Аудит | `#/audit` | — | read? | ✓ |
| Настройки | `#/settings` | ✓ | ✓ | ✓ |

- Collapsed rail: icons + tooltips (`aria-label`).
- Active item: brand tint + indicator bar.
- Badge on Queue: count due today.

### Top bar
- Product mark + env badge (demo)
- Search button (opens palette) showing shortcut
- Density toggle
- Notifications (mock)
- User menu: role chip, profile, logout

## 3. Локальная навигация

- **Кандидаты:** segmented Table | Kanban + saved views + filters
- **Карточка:** tabs
- **Аналитика:** period tabs + sub-sections (Обзор / Воронка / По HR / Источники)
- **Users:** list | create form overlay

## 4. Breadcrumbs

Используются на:
- Карточка: `Кандидаты / Иванов П.С.`
- User create: `Пользователи / Новый`
- Nested settings

Не на top-level pages (избыточны при rail).

## 5. Глобальный поиск и Command palette

**Единый overlay `⌘K` / `Ctrl+K`**, также кнопка «Поиск».

Группы результатов:
1. Кандидаты (name, phone masked, vacancy)
2. Команды (Создать взаимодействие, Перейти в Kanban…)
3. Разделы навигации
4. Недавние

Query modes:
- plain text → fuzzy candidates
- `>` prefix → commands only
- `@` → users (transfer target)

## 6. Быстрые и контекстные действия

| Контекст | Actions |
|---|---|
| Table row | Open, Change status, Transfer, Add note |
| Kanban card | Same + Move to column |
| Candidate header | Call log, Schedule, Transfer, More |
| Bulk (future) | Assign, Export — disabled stub |

Primary max 2 visible; rest in menu.

## 7. Фильтры и saved views

- Filter bar collapsible; active filters as removable chips
- Saved views: «Мои активные», «Без касания 7д», «Офферы» (mock)
- Switching view resets/applies filter set; dirty indicator if modified

## 8. Уведомления

- Bell dropdown: event reminders, transfers to me, system
- Toast for immediate action feedback
- No email in prototype

## 9. Table ↔ Kanban

- Shared filter model and selection candidate id
- Toggle does not remount filters
- URL hash: `#/candidates?view=table|kanban`

## 10. Role-dependent home

| Role | Default route |
|---|---|
| HR | `#/queue` |
| Руководитель | `#/home` (ops summary + analytics teaser) |
| Admin | `#/home` or last; quick links to Users/Audit |

## 11. Keyboard shortcuts (discoverable via `?`)

| Shortcut | Action |
|---|---|
| `Ctrl/⌘ K` | Command palette |
| `/` | Focus search (when not in input) |
| `G` then `H/Q/C/K/A/U` | Go Home/Queue/Candidates/Calendar/Analytics/Users |
| `Esc` | Close overlay |
| `?` | Shortcuts help |
| `N` | New interaction (on card) |
| `T` | Transfer (on card) |
| `S` | Status menu (on card) |
| `V` | Toggle table/kanban on candidates |

## 12. Responsive breakpoints

| Name | Width | Behavior |
|---|---|---|
| Desktop+ | ≥1440 | Full rail + optional split preview |
| Desktop | 1280–1439 | Full rail, denser |
| Compact desktop | 1024–1279 | Collapsible rail default collapsed; filters sheet |
| Narrow | <1024 | Rail → drawer; tables horizontal scroll; critical flows work |

Mobile not primary; login, view candidate, approve transfer must not break.

## 13. Overlay stack (z-index)

1. Base app
2. Sticky header/rail
3. Drawer/preview
4. Modal/dialog
5. Command palette
6. Toast
7. Session-expired blocking

## 14. Empty / loading / error placement

Each primary page owns:
- skeleton matching layout
- empty with CTA
- error with retry
- permission-denied variant when role blocks
