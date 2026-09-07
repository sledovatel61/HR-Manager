# IMPLEMENTATION_GUIDE.md — план переноса дизайна в production

Этот документ описывает **будущий** план; в рамках текущей задачи внедрение
в `frontend/src` не выполнялось (запрещено промптом).

## 1. Порядок внедрения (предлагаемый)

1. **Foundations first.** Перенести `design-prototype/src/styles/tokens.css`
   в `frontend/src` как самостоятельный CSS-слой (или CSS-in-JS эквивалент,
   если команда решит сменить подход) — без изменения существующих
   компонентов на этом шаге. Сложность: **S**.
2. **Общие примитивы (`ui/`).** Перенести `Button`, `IconButton`, `Avatar`,
   `StatusChip/Badge`, `Field/TextInput/SelectInput`, `Tabs`, `Modal`,
   `ConfirmDialog`, `Drawer`, `Toast`, `StateViews` как переиспользуемый
   пакет `frontend/src/components/ui/`. На этом шаге production `LoginForm`/
   `Dashboard` можно начать использовать новые `Button`/`Field` без смены
   бизнес-логики. Сложность: **M**.
3. **Shell (Sidebar/Topbar/CommandPalette).** Требует финальной
   информационной архитектуры и, главное, **реального списка разделов**,
   разрешённых текущим API/ролям — зависит от прогресса этапов 2–6
   роадмапа. Command Palette на этом шаге можно подключить только к
   реальной навигации (без мок-поиска кандидатов, пока нет API поиска).
   Сложность: **M**.
4. **Экраны кандидатов (таблица/Kanban/карточка).** Только после появления
   API этапа 2 (`GET /candidates`, статусы, владелец, soft delete) —
   иначе это будет повторная "заглушечная" реализация. На этом шаге
   `CandidateTable`/`KanbanBoard`/`CandidateDrawer` из прототипа становятся
   основой, но данные должны идти из API, а не из `mockData.ts`. Сложность:
   **L** (включает пагинацию, серверные фильтры, обработку ошибок сети).
5. **Календарь и аналитика.** Зависят от API этапов 4 и 5 соответственно.
   Визуальные компоненты (`FunnelChart`, `KpiStrip`, `CalendarPage`)
   переносимы почти как есть, но требуют контракта на агрегированные данные
   (см. раздел "API-контракты" ниже). Сложность: **M** каждый.
6. **Шаблоны, пользователи, audit log.** `UsersPage`/`AuditPage`/
   `TemplatesPage` подключаются к уже существующему (после этапа
   безопасности) `GET /users`, `GET /audit` — это единственная область,
   которую **можно начать частично внедрять уже сейчас**, после принятия
   этапа безопасности (см. раздел 6). Сложность: **M**.

## 2. Предлагаемая структура компонентов (production)

```
frontend/src/
  design-system/        # токены + примитивы (перенос из design-prototype/src/styles и ui/)
    tokens.css
    components/
      Button.tsx, Field.tsx, Modal.tsx, Drawer.tsx, Toast.tsx, StatusChip.tsx, ...
  app-shell/             # Sidebar, Topbar, CommandPalette — зависят от auth context
  features/
    candidates/          # таблица, kanban, drawer, timeline, transfer — зависят от API этапа 2/3
    calendar/             # этап 4
    analytics/            # этап 5
    templates/             # этап 6
    users/                  # уже частично возможно (этап 1 principals)
    audit/                   # уже частично возможно (этап 1 principals)
  pages/                 # тонкие page-компоненты, аналог design-prototype/src/pages
```

`design-system/` должен быть единственным местом с "сырыми" визуальными
деталями — features/pages используют только его примитивы, не пишут новый
CSS напрямую (кроме layout-специфичных мелочей).

## 3. Какие компоненты должны быть общими

- `Button`, `IconButton`, `Avatar`, `Badge/StatusChip`, `Field/TextInput/
  SelectInput`, `Modal`, `ConfirmDialog`, `Drawer`, `Toast`, `Tabs`,
  `StateViews` (Empty/Skeleton/Error/PermissionDenied/SessionExpired) —
  используются во всех фичах без вариаций "под фичу".
- `useFocusTrap` — общий хук, не дублировать реализацию фокус-трапа в
  каждом диалоге отдельно.
- Иконочный набор (`Icon.tsx`) — либо перенести как есть (inline SVG, ноль
  зависимостей), либо заменить на согласованную с командой icon-библиотеку,
  но **не смешивать два источника иконок** в одном интерфейсе.

## 4. Где нужны API-контракты (ещё не существуют)

| Экран | Нужный контракт | Комментарий |
|---|---|---|
| Таблица/Kanban кандидатов | `GET /candidates?query=&stage=&owner_id=&source=&sort=&limit=&offset=` с пагинацией | Этап 2 роадмапа |
| Карточка кандидата | `GET /candidates/{id}`, `GET /candidates/{id}/interactions`, `POST /candidates/{id}/interactions` | Этап 2/3 |
| Передача кандидата | `POST /candidates/{id}/transfer {new_owner_id, reason}` → должен создавать audit-запись атомарно | Этап 3, тесно связан с audit log этапа 1 |
| Календарь/события | `GET /events?from=&to=&owner_id=`, `POST /events`, `PATCH /events/{id}` (done/postponed) | Этап 4 |
| Аналитика | `GET /analytics/kpi?scope=&period=`, `GET /analytics/funnel?period=` — **важно**: единое серверное определение конверсии (не пересчитывать на клиенте из сырых событий) | Этап 5 |
| Saved views | `GET/POST /saved-views` — сохранение фильтра+сортировки набором | Не описан явно в текущем `PRODUCT_SPEC.md`; требует отдельного мини-дизайна перед реализацией |
| Шаблоны/контент | `GET /templates`, `POST /templates` (версии, draft/published/archived) | Этап 6 |

## 5. Что зависит от будущих этапов backend

- Все экраны, кроме `Users`/`Audit`/`Settings`/`Login`, требуют backend
  этапов 2–6, которых сейчас нет в `main` (только каркас + этап
  безопасности в параллельной ветке).
- Soft delete/restore кандидата — есть один мок-пример
  (`CANDIDATES[30].isDeleted`), но UI для просмотра "Корзины" и
  восстановления **не спроектирован** в этой итерации — рекомендуется
  отдельный проход дизайна одновременно с API soft-delete этапа 2.
- Защита от дублей кандидатов по телефону/email (`PRODUCT_SPEC.md`, п.4) —
  не реализована ни в прототипе, ни в этом гайде: требует UX-решения
  (модалка "похожий кандидат найден") вместе с backend-логикой поиска
  дублей.

## 6. Что можно внедрить уже после принятия этапа безопасности

Если ветка `arena/01a061ab-hr-manager` (идентификация и безопасность) будет
принята в `main`, следующее можно внедрять **немедленно**, без ожидания
этапов 2–6:
- design tokens + общие UI-примитивы (раздел 1, шаги 1–2);
- обновлённый `LoginPage`-стиль поверх существующего `LoginForm.tsx`
  (без изменения логики авторизации, только визуал/токены);
- `UsersPage`/`CreateUserModal`, подключённые к существующим `GET /users`,
  `POST /users` (эндпоинты уже есть в ветке безопасности, см.
  `backend/app/routers/users.py` в `origin/arena/01a061ab-hr-manager`);
- `AuditPage`, подключённая к существующему `GET /audit` (см.
  `backend/app/routers/audit.py` в той же ветке).

## 7. Риски

- **Риск рассинхронизации токенов**, если design-system переносится
  частями, а не единым PR — рекомендуется отдельный PR только с
  токенами+примитивами перед любыми фичами.
- **Риск "двух источников правды"** для терминов статусов/этапов — уже
  сейчас стоит зафиксировать `STAGE_ORDER`/`STAGE_LABELS` как общий
  словарь между frontend и backend enum (см. `PRODUCT_SPEC.md` §5), иначе
  дизайн и backend разойдутся в формулировках.
- **Риск избыточной анимации при неаккуратном переносе** — при интеграции с
  реальными данными легко забыть `prefers-reduced-motion` на новых местах;
  рекомендуется code-review чеклист (см. `REVIEW_CHECKLIST.md`).
- **Риск потери контекста в Kanban drag-and-drop** при реальном API (сетевая
  задержка) — нужен явный optimistic-update + rollback-паттерн, не
  реализованный в мок-версии (там всё синхронно).

## 8. Оценка сложности (сводно)

| Блок | Сложность |
|---|---|
| Tokens + primitives | S |
| App shell (sidebar/topbar/palette) | M |
| Candidates (table/kanban/card/transfer) | L |
| Calendar | M |
| Analytics | M |
| Templates | M |
| Users/Audit (частично готово) | M |

## 9. Стратегия постепенного внедрения (без big-bang rewrite)

1. Внедрять **параллельно** с текущим приложением: design-system как новый
   пакет, который может использоваться point-by-point в уже существующих
   `App.tsx`/`Dashboard.tsx`, не требуя переписывания всего разом.
2. Каждая фича переносится только вместе со своим backend-этапом — не
   раньше, чтобы не плодить моки в production-коде.
3. Feature-flag или отдельная ветка для каждого крупного экрана (кандидаты,
   календарь, аналитика), мержится в `main` только после прохождения тех же
   тестовых требований, что и остальной backend (см. `agents.md` Definition
   of Done).
4. Дизайн-токены версионируются как "контракт" — изменения токенов проходят
   отдельный review, т.к. затрагивают все уже перенесённые экраны разом.
