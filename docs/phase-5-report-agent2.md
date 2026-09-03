# Отчёт по этапу 5 — события и календарь (агент 2)

- **Промпт:** `prompts/PHASE_5_PROMPT.md` (роадмап: этап 4 «События и
  календарь»).
- **Ветка:** `arena/phase-5-calendar` (опубликована, **без merge**).
- **PR для ревью:** https://github.com/sledovatel61/HR-Manager/pull/7
- **Коммиты ветки:**
  - реализация: `3ca3feb7371453c881bae611b8268d3a1f008c3e`;
  - отчёт: `790336de6fa424c5e2d051f21cd33fe48640773b` и последующие
    docs-only правки отчёта. Актуальный tip ветки проверяется командой
    `git ls-remote origin arena/phase-5-calendar`.
- **База ветки:** актуальный `origin/main` =
  `bbe7ca10900a0a3525e9223a32c4dcdb3d7048a1`, включающий принятый
  merge-коммит этапа 4 `649f6a606d6a89ba256ef4d899d821b84993b886`.
- **Дата:** 2026-09-03.

## Контракт (зафиксирован до миграции, см. `docs/ARCHITECTURE.md`)

1. **Типы и состояния:** `call | interview | reminder`; `scheduled |
   completed | postponed`. Закрытые enum-контракты (backend ↔
   `frontend/src/types.ts`), русские подписи — только представление.
   Словарь прототипа (planned/done/canceled/meeting) намеренно не
   переносится — источник правды промпт этапа.
2. **Переходы:** `scheduled → completed | postponed`;
   `postponed → scheduled | completed`; **`completed` — терминальный**
   (любой PATCH → 409). Переход в `postponed` без нового `starts_at` —
   422 (откладывание всегда переносит).
3. **Время и границы:** хранение UTC (`TIMESTAMPTZ` + CHECK порядка); API
   принимает ISO 8601 с offset/Z и нормализует вход в UTC (naive — как
   UTC); UI показывает локальное время браузера. `from`/`to` —
   полуинтервал `[from, to)` по пересечению с интервалом события
   `[starts_at, ends_at)`; `ends_at = NULL` — вырожденный интервал в
   точке `starts_at`; `from >= to` → 422.
4. **Просрочено:** `status = scheduled` и `starts_at < now` (now — UTC на
   момент запроса).
5. **Напоминание (reminder moment):** `remind_at` события (только для
   call/interview, `<= starts_at`; у reminder запрещён — 422) либо
   `starts_at` события типа reminder. Серверная выдача — фильтры
   `remind_from`/`remind_to` (задокументированное расширение параметров
   `GET /events`).
6. **Событие ≠ взаимодействие:** взаимодействие — неизменяемый факт
   прошлого (автор, тип, комментарий); событие — план с исполнителем,
   сроком, напоминанием и состоянием. Завершение события **не** меняет
   этап кандидата (нет второго источника правды воронки).
7. **Бизнес-история vs audit:** неизменяемая `event_history` (одна строка
   на мутацию, typed old/new для дат/статуса/исполнителя; title/note —
   только флаг изменения, содержимое не копируется). Audit log —
   security-журнал: детали только технические id и имена полей.
8. **Soft delete кандидата:** события удалённого кандидата скрыты от
   всех ролей через events-API (404, включая admin) — политика
   задокументирована; данные остаются в БД, admin видит факты только в
   audit log.

## Backend

- **Миграция `0005_events`** (после `0004_candidate_transfers`,
  обратимая): `events` (candidate_id FK CASCADE, author/assignee FK
  RESTRICT, type/status/title/note, starts/ends/remind/completed_at,
  `version`, created/updated) и `event_history` (kind, status/
  starts/ends/remind/assignee old+new, title_changed/note_changed).
  CHECK: словари type/status/kind, непустой title,
  `ends_at > starts_at`, `remind_at <= starts_at`, согласованность
  `completed_at` с состоянием. Физического удаления событий нет.
- **`GET /events`** — `from`, `to`, `owner_id`, `candidate_id`, `type`,
  `status`, `remind_from`, `remind_to`, `sort`
  (`starts_at|created_at|updated_at`), `direction`, `limit`, `offset`;
  стабильная сортировка (колонка + `id asc`); ответ пагинированный и
  достаточный для календаря, ближайших, просроченных и напоминаний.
- **`POST /events`** (201) — кандидат видимый и неудалённый (иначе 404);
  валидация типа, дат, исполнителя, обязательных полей.
- **`GET /events/{id}`** — в зоне видимости; чужое и событие удалённого
  кандидата — 404.
- **`PATCH /events/{id}`** — **обязательный `expected_version`**:
  проверка под row lock (`SELECT … FOR UPDATE` +
  `execution_options(populate_existing=True)` — кеш identity map обходится
  так же, как в контракте передачи), несовпадение — 409 без применения;
  переходы валидируются сервером; `completed` терминален.
- **`GET /events/{id}/history`** — пагинируемая бизнес-история (старые
  первыми) с видимостью события.
- **Атомарность:** мутация + строка истории + audit-событие — одна
  транзакция, один `db.commit()` (`_audit_event(..., commit=False)`).
  Аудит: `event_created/updated/rescheduled/completed/postponed/
  assignee_changed`, детали `candidate=<uuid> event=<uuid>
  fields=<имена>` — без PII, заголовков и заметок.
- **Права:** 401 без аутентификации; HR — события только своих
  кандидатов (чужие 404); manager/admin — все доступные + `owner_id`;
  новый исполнитель — только активный HR (HR назначает только себя —
  иначе 403); после передачи кандидата старый HR теряет доступ к
  событиям (история сохраняется), новый получает; CSRF на мутациях;
  все проверки — на сервере.

## Frontend

- **Раздел «Календарь»** (все роли; `features/calendar/`): недельная
  сетка пн–пт 8:00–17:00, навигация «Предыдущая/Следующая неделя» +
  «Сегодня» (кнопки с aria-label), фильтры тип/состояние/HR (HR-фильтр —
  только manager/admin), панели «Просроченные»/«Ближайшие»/
  «Напоминания» (серверные запросы с `to=now`/`from=now`/
  `remind_to`/`remind_from`), loading/empty/error+retry, быстрый переход
  «Выполнено» с текущей версией события.
- **EventFormModal** — создание (пикер кандидата через реальный
  `listCandidates` с debounce), редактирование, перенос, выполнение,
  откладывание; `datetime-local` ↔ ISO UTC (`time.ts`); напоминание
  недоступно для типа reminder; клиентская валидация; серверные ошибки и
  конфликт версий (409) без ложного успеха; вкладка истории изменений
  (kind-подписи, изменённые даты, исполнитель).
- **Карточка кандидата:** новая вкладка «События» — список
  (`GET /events?candidate_id=`), создание, быстрое «Выполнено».
- **Переход событие → карточка кандидата:** hash-навигация +
  `openCandidateId` в Workspace/CandidatesListPage.
- Переиспользована design-system (Modal/Field/StateViews/Toast и т.д.),
  focus trap/возврат фокуса, aria-labels, keyboard-альтернативы,
  `prefers-reduced-motion`; новых runtime-зависимостей нет; mockData не
  используется (моки — только в тестах).

## Изменённые файлы (21)

Backend: `alembic/versions/0005_events.py` (новый),
`app/models.py`, `app/schemas.py`, `app/routers/events.py` (новый),
`app/main.py`, `tests/conftest.py`, `tests/test_migrations.py`,
`tests/test_events.py` (новый), `tests/test_integration_events.py`
(новый). Frontend: `features/calendar/` (новый: CalendarPage,
EventFormModal, time.ts, calendar.css + 2 тест-файла), `api.ts`,
`types.ts`, `app-shell/Workspace.tsx`, `app-shell/useWorkspaceSection.ts`,
`features/candidates/CandidateDrawer.tsx(+тест)`,
`features/candidates/CandidatesListPage.tsx`,
`features/candidates/drawer.css`. Документация: `README.md`,
`docs/ARCHITECTURE.md`. `.github/` не изменялся.

## Фактические проверки

### Локально (песочница)

| Проверка | Результат |
|---|---|
| Backend `ruff check` / `ruff format --check` | ✅ |
| Backend `mypy app tests` | ✅ 36 файлов |
| Backend `pytest` (unit SQLite + integration) | ✅ **171 passed** |
| В т.ч. integration PostgreSQL **18.4** | ✅ 33 теста (7 новых событий) |
| Конкурентный PATCH (2 потока, barrier, одинаковый expected_version) | ✅ 3/3: ровно один 200 + один 409, версия 2, одна строка истории |
| Alembic `downgrade 0004 → upgrade head → current` | ✅ 0005 (head) |
| Frontend `tsc -b` / eslint | ✅ |
| Frontend vitest/RTL | ✅ **66 passed** (11 файлов) |
| Frontend production build | ✅ 217.3 kB / 67.0 kB gzip |
| `npm audit --audit-level=high` | ✅ 0 уязвимостей |
| `git diff --check` | ✅ |
| Compose статически: PyYAML `!reset` (dev+prod), `test_production_overlay.py` (6 passed), `make -n up` | ✅ |

### Live smoke (uvicorn + PostgreSQL 18.4 + Vite dev с прокси)

Health через Vite-прокси 200; создание события 201 (version 1); 403 при
попытке HR назначить другого HR; фильтр периода находит событие; перенос
(версия 2); stale `expected_version=1` → 409; postpone без даты → 422;
postpone с датой → 200; complete → 200 (completed_at выставлен);
терминальное изменение → 409; история `[created, rescheduled, postponed,
completed]`; аудит — 4 события `event_*` с деталями только из id/имён
полей (без ФИО, заголовка, телефона); фильтры remind/overdue; soft delete
кандидата → событие 404 у владельца и admin. **SMOKE OK.**

### GitHub Actions (реальное выполнение)

CI запускался дважды — на коммите реализации и на финальном tip ветки;
**все 4 джобы прошли в обоих прогонах**:

| Прогон | Ссылка | Результат |
|---|---|---|
| #1 (реализация) | https://github.com/sledovatel61/HR-Manager/actions/runs/33754180009 | ✅ 4/4 |
| #2 (tip с отчётом) | https://github.com/sledovatel61/HR-Manager/actions/runs/33754685000 | ✅ 4/4 |

Джобы: Backend checks, Backend integration tests (PostgreSQL 16,
`alembic upgrade head` + `pytest -m integration`), Frontend checks
(включая `npm audit --audit-level=high`), Compose stack smoke test. В
песочнице Docker недоступен — полный запуск стека выполнен джобой CI;
локально Compose валидирован статически.

## Ключевые решения

1. **Concurrency:** optimistic — обязательный `expected_version`,
   проверяемый под row lock с `populate_existing` (урок этапа 4); при
   конфликте 409 с указанием ожидаемой и актуальной версий; UI всегда
   шлёт текущую версию и не показывает ложного успеха.
2. **Kanban-подобная загрузка календаря:** недельная сетка = один
   ограниченный запрос за период (limit 100); панели — отдельные
   точечные запросы по 5; база не выгружается целиком.
3. **Напоминания без фоновой инфраструктуры:** хранение `remind_at` +
   серверные фильтры `remind_from/remind_to`; email/push-доставка явно
   объявлена ограничением (нет worker/cron в архитектуре, доставка не
   имитируется).
4. **`GET /candidates/{id}/events` не добавлен** — `GET
   /events?candidate_id=` покрывает сценарий без дублирующего контракта
   (промпт разрешает оба варианта; выбор задокументирован).
5. **Postpone требует новую дату** и всегда переносит; UI при переносе
   шлёт и `ends_at`, чтобы не нарушать CHECK порядка дат.

## Известные ограничения

- Внешняя доставка напоминаний (email/push/browser) не реализована
  (см. решение 3).
- Состояние «отменено» отсутствует — словарь из трёх состояний по
  промпту; отмена выражается завершением/откладыванием. Расширение
  словаря возможно только отдельным обоснованным контрактом.
- Календарь — рабочая неделя (пн–пт); выходные и почасовое
  позиционирование событий внутри дня вне scope (событие привязывается к
  часу начала).
- При создании события из календаря кандидат выбирается поиском (до 8
  совпадений); быстрый выбор из списка очереди доступен через карточку
  кандидата.
- Аналитика/KPI, экспорт, saved views, шаблоны, импорт/экспорт — вне
  scope этапа.
