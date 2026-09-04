# Архитектура HR Manager — этап 1 (технический каркас)

Этот документ фиксирует структуру репозитория и ключевые решения, принятые на
этапе 1. Бизнес-функциональность (кандидаты, пользователи, аналитика) на этом
этапе намеренно не реализована.

## Структура репозитория

```
HR-Manager/
├── backend/                  # FastAPI + SQLAlchemy 2 + Alembic (Python 3.12+)
│   ├── app/
│   │   ├── config.py         # настройки из переменных окружения + production-guard
│   │   ├── db.py             # engine + проверка доступности БД
│   │   ├── main.py           # фабрика приложения, lifespan, GET /health
│   │   ├── models.py         # User/сессии/аудит + кандидаты/взаимодействия/передачи
│   │   ├── schemas.py        # Pydantic-схемы запросов/ответов
│   │   └── routers/          # health, auth, users(+справочник HR),
│   │                         # audit, candidates(+передачи)
│   ├── alembic/              # миграции БД (0001–0004, головная — передачи)
│   ├── tests/                # unit (SQLite in-memory) + integration (PostgreSQL)
│   ├── requirements*.txt     # зафиксированные зависимости (lock-файлы)
│   └── Dockerfile            # образ: миграции + uvicorn
├── frontend/                 # React + TypeScript + Vite
│   ├── src/
│   │   ├── design-system/    # токены + общие UI-примитивы (из дизайн-трека)
│   │   ├── app-shell/        # Workspace: навигация, пользователь, выход
│   │   ├── features/candidates/  # таблица, Kanban, карточка, передача, дубли
│   │   ├── api.ts / types.ts # API-клиент и общие контракты
│   │   └── App.tsx           # вход/сессия + гейт на workspace
│   ├── nginx.conf            # SPA fallback + прокси /api → backend
│   └── Dockerfile            # сборка Node → nginx
├── infra/
│   ├── docker-compose.yml    # локальная среда (db + backend + frontend)
│   ├── compose.prod.yml      # production overlay без dev-значений
│   └── scripts/check_env.sh  # preflight проверка production-секретов
├── docs/                     # проектная документация
├── .github/workflows/ci.yml  # CI: lint, typecheck, тесты, build
├── prompts/                  # промпты этапов (вход для агентов)
├── agents.md / PRODUCT_SPEC.md / ROADMAP.md
└── README.md
```

## Ключевые решения

### Backend

- **Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2** — целевой стек
  из `agents.md`. DRF-стиля нет: доступ к БД — через SQLAlchemy напрямую.
- **Конфигурация только из переменных окружения** (`app/config.py`). Файл
  `.env` автоматически не читается: секреты поставляет среда запуска
  (Docker Compose / CI / process supervisor), что исключает случайный коммит.
- **Production guard в конфигурации.** При `APP_ENV=production` приложение
  отказывается стартовать, если `SECRET_KEY` отсутствует/короткий/дефолтный,
  `DATABASE_URL` использует dev-учётку или не содержит пароля, либо включён
  debug. Тест на это поведение — `backend/tests/test_config.py`.
- **`GET /health`** проверяет связность с БД (`SELECT 1`) и возвращает
  `200 {"status": "ok"}` только при доступной БД; иначе `503
  {"status": "degraded"}` с детализацией по компоненту. В теле ответа нет
  учётных данных и деталей подключения.

### База данных

- **PostgreSQL 16** — единственная production-БД, как требует ТЗ.
- **Первая миграция — безопасная заготовка**: `CREATE EXTENSION IF NOT EXISTS
  pgcrypto` (idempotent, reversible, trusted-extension, superuser не нужен).
  UUID-генерация для пользователей/кандидатов на следующих этапах будет
  использовать `gen_random_uuid()`. Никаких таблиц этапа 1 не создаётся —
  «пустая» миграция-заготовка под реальную схему появилась бы только вместе
  с кодом, который её использует.
- **SQLite** допускается **только** для изолированных unit-тестов
  (`APP_ENV=test`, in-memory, без файлов). Конфигурация запрещает SQLite во
  всех остальных средах. Это задокументировано здесь и в README.

### Frontend

- **React 18 + TypeScript (strict) + Vite**. Отдельная страница показывает
  понятное состояние «backend / база данных» с ручной перепроверкой
  (`GET /health` через `/api`).
- **Единый origin**: nginx отдаёт SPA и проксирует `/api/*` на backend —
  браузер не обращается к backend напрямую, CORS не требуется, а будущие
  same-origin cookies для сессий будут работать без настройки.
- **nginx работает от non-root пользователя**: используется образ
  `nginxinc/nginx-unprivileged` (слушает порт 8080 внутри контейнера).
  nginx добавляет базовые security headers: `X-Content-Type-Options`,
  `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy` и
  `Content-Security-Policy`.
- Тесты компонента на Vitest + Testing Library (счастливый путь, отказ БД,
  недоступность backend, ручная перепроверка).

### Инфраструктура и CI

- **`infra/docker-compose.yml`** — локальная среда: PostgreSQL 16 c volume и
  healthcheck, backend (ждёт healthy БД, сам имеет healthcheck), frontend
  (ждёт healthy backend). Все dev-порты привязаны к `127.0.0.1`; учётные
  данные явно помечены как development-only. **`build.context` разрешается
  относительно расположения Compose-файла**, поэтому пути заданы как
  `../backend` и `../frontend`.
- **`infra/compose.prod.yml`** — production overlay: секреты только из
  переменных окружения (`${VAR:?}` — ошибка при отсутствии), порты наружу
  не публикуются (`ports: !reset []`; требуется Docker Compose v2.24+),
  HTTPS/балансировщик — на этапе 7 (deployment).
- **CI (`.github/workflows/ci.yml`)** — четыре задачи: backend (ruff,
  mypy, pytest, preflight-проверки), frontend (eslint, tsc, vitest, build,
  npm audit), интеграционные тесты против реального PostgreSQL 16
  (включая конвейер миграций), compose smoke-тест стека (валидация dev- и
  prod-конфигураций, `up --build --wait`, health 200, деградация 503,
  очистка `if: always()`).

### Безопасность уже на этапе 1

- Нет секретов в репозитории: только шаблон `.env.example` и dev-значения,
  явно помеченные как development-only.
- Backend не стартует в production с небезопасной конфигурацией (см. выше).
- Логи не пишут персональные данные (на этапе 1 их просто нет); при появлении
  кандидатов маскирование будет обязательным.
- Минимальные права БД и шифрованные backup — этапы 2 и 7 роадмапа.

## База кандидатов (этап 3)

### Модель данных

Миграция `0003_candidates` создаёт таблицы `candidates` и
`candidate_interactions` и добавляет `audit_log.candidate_id` (FK `SET NULL`)
для связи аудита с кандидатом:

- **candidates**: `full_name` (+ `full_name_normalized` — Python-casefold
  для корректного поиска по кириллице на обеих БД), `phone`/
  `phone_normalized`, `email`/`email_normalized`, `source`, `position`,
  `owner_user_id` (FK на `users`, `RESTRICT` — владельца нельзя удалить,
  пока у него есть кандидаты), `stage` + `stage_position` (порядок воронки
  для серверной сортировки), `created_at`/`updated_at`,
  `deleted_at`/`deleted_by_user_id` (мягкое удаление), CHECK-ограничения на
  стадию и источник, индексы на владельца, стадию, нормализованные ФИО/
  телефон/email, `deleted_at`, `updated_at`.
- **candidate_interactions**: `type` (call/email/meeting/note/status_change),
  `comment`, `author_user_id` (FK `RESTRICT`), `candidate_id`
  (FK `CASCADE` — удаляется вместе с кандидатом), CHECK на тип.

Нормализация в Python (`app/utils.py`), а не в SQL: `normalize_phone`
оставляет только цифры и приводит 11-значные номера с `8` к `7`
(«8-900-…» ≡ «+7-900-…»); `normalize_email` — trim + lower;
`normalize_full_name` — trim + casefold. Значения хранятся в колонках,
потому что SQLite `lower()` не сворачивает кириллицу, а поведение должно
совпадать на обеих БД. Нормализованные телефон/email никогда не попадают
в логи и не возвращаются API.

### Права доступа

- **HR** видит и изменяет только своих кандидатов; чужие (включая мягко
  удалённые) возвращают 404, чтобы не раскрывать существование.
  `owner_id` в запросах HR игнорируется; создание на другого владельца — 403.
- **manager/admin** видят все кандидаты и могут фильтровать по владельцу;
  могут создавать кандидатов на любого активного пользователя. Мягко
  удалённые кандидаты видны только через restore.
- Права проверяются на сервере в каждой операции (общий `_get_candidate`
  со scope-фильтром); CSRF double-submit — на всех мутирующих методах, как
  и в этапе 2.

### Дубликаты

При создании и при смене телефона/email проверяются нормализованные
значения среди не-удалённых кандидатов, видимых текущему пользователю.
При совпадении API отвечает **409** с телом
`{message, duplicates: [CandidateOut, …]}`; повторный запрос с
`confirm_duplicate: true` создаёт точную копию (аудит-действие
`duplicate_candidate_created`).

### Аудит

События `candidate_created`, `candidate_updated`, `candidate_stage_changed`,
`candidate_deleted`, `candidate_restored`, `duplicate_candidate_created`
пишутся в существующую `audit_log` с `candidate_id`; в `details` — только
идентификаторы, стадии и источники, **без** персональных данных.

## Рабочий интерфейс HR (этап 4)

### Контракт передачи ответственности

- **`POST /candidates/{id}/transfer`**, тело `{new_owner_user_id, reason}`.
  В одной транзакции: проверка видимости (чужой/удалённый — 404, как
  принято), блокировка строки кандидата `SELECT … FOR UPDATE` с повторным
  чтением (`populate_existing`) и повторной проверкой видимости/владельца —
  конкурентная передача не может перезаписать чужое изменение (409 или
  404 в зависимости от момента), атомарная смена `owner_user_id`, запись
  неизменяемой строки в `candidate_transfers` (инициатор, from, to, причина,
  время) и аудит-событие `candidate_transferred` (только id, без PII и без
  текста причины). Новый владелец — только другой активный пользователь
  роли `hr`; пустая причина отклоняется на уровне Pydantic и CHECK-ограничения
  `ck_candidate_transfers_reason_not_blank`. HR передаёт только своего
  кандидата (иначе 404/403), manager/admin — любого видимого. Ответ —
  `{transfer, candidate}`: UI обновляется без повторной загрузки.
- **`GET /candidates/{id}/transfers`** — пагинируемая история передач
  (старые записи первыми) с правилами видимости карточки: после передачи
  бывший HR не читает ни карточку, ни историю через прямой URL.
- **`GET /admin/users/hr`** — минимальный справочник активных HR
  (id/username/full_name/role/is_active) для выбора ответственного;
  административные поля не отдаются.
- **`GET /candidates?include_deleted=true`** — серверный список мягко
  удалённых (видимость та же, что у обычного списка); используется экраном
  «Удалённые».

Бизнес-история передач хранится только в `candidate_transfers`; audit log
остаётся журналом безопасности и не является историей кандидата.

### Frontend-структура (production)

```
frontend/src/
  design-system/        # перенесённые из дизайн-прототипа токены и примитивы
    tokens.css          # semantic-токены (единственное место «сырых» цветов)
    global.css          # базовые стили, focus-ring, reduced-motion, skip-link
    icons/Icon.tsx      # единый inline-SVG набор (ноль зависимостей)
    components/         # Button, Field, Modal, Drawer, ConfirmDialog, Tabs,
                        # StatusChip, StateViews, Toast (+Context), useFocusTrap
  app-shell/            # Workspace (sidebar/topbar/выход) + hash-навигация
  features/candidates/  # таблица, Kanban, drawer карточки, форма создания,
                        # диалог передачи, дубль-подтверждение, deleted-экран
```

- Навигация — лёгкий hash-роутер (`useWorkspaceSection`), без React Router;
  глобальное состояние — локальные хуки (React state), без внешних
  state-библиотек; DnD в Kanban — нативный HTML5 + обязательная
  keyboard-альтернатива (select этапа на каждой карточке).
- Kanban-стратегия загрузки (задокументированное решение): каждая из 11
  колонок постранично запрашивает свою ленту
  `GET /candidates?stage=…&limit=20&offset=…` и растёт кнопкой «Показать
  ещё»; доска никогда не запрашивает всю базу разом.
- Оптимистичная смена этапа: мгновенное перемещение + блокировка повтора,
  серверное подтверждение и жёсткий откат при ошибке (таблица — только
  серверные данные, без оптимизма).
- Сессии: любой `401` вне `/auth/login` через `api.onUnauthorized`
  возвращает приложение на экран входа; `403` отображается как состояние
  недостаточных прав; CSRF-заголовок добавляется в API-клиенте как и раньше.
- Моки API используются только в тестах; production-экраны без
  `mockData` и без client-only операций.

## События и календарь (этап 5)

### Контракт (зафиксирован до миграции)

- **Типы:** `call | interview | reminder`; **состояния:** `scheduled |
  completed | postponed`. Закрытые словари-контракты (backend `EventType`/
  `EventStatus` ↔ `frontend/src/types.ts`); русские подписи — только
  представление. Словарь дизайн-прототипа (planned/done/canceled/meeting)
  намеренно не переносится — источник правды промпт этапа.
- **Переходы:** `scheduled → completed | postponed`;
  `postponed → scheduled | completed`; `completed` — терминальное состояние
  (любое изменение → 409). Откладывание всегда переносит: переход в
  `postponed` без нового `starts_at` отклоняется (422).
- **Время:** хранение — UTC (`TIMESTAMPTZ`, CHECK-ограничения порядка);
  API — ISO 8601 с offset/Z, вход нормализуется в UTC (naive трактуется как
  UTC); UI показывает локальное время браузера.
- **Границы периода:** `from`/`to` — полуинтервал `[from, to)` по
  пересечению с интервалом события `[starts_at, ends_at)`; `ends_at = NULL`
  — вырожденный интервал в точке `starts_at`; `from >= to` → 422.
- **Просроченное событие:** `status = scheduled` и `starts_at < now`.
- **Напоминание (reminder moment):** `remind_at` события (call/interview;
  только `<= starts_at`) либо `starts_at` события типа `reminder` (у него
  `remind_at` запрещён — 422). Серверные фильтры `remind_from`/`remind_to`
  — задокументированное расширение набора параметров `GET /events`.
- **Событие ≠ взаимодействие:** взаимодействие — неизменяемый факт
  прошлого (автор, тип, комментарий); событие — план с исполнителем,
  сроком, напоминанием и жизненным циклом. Завершение события не меняет
  этап кандидата (нет второго источника правды для воронки).
- **Бизнес-история:** неизменяемая `event_history` — одна строка на
  мутацию, typed old/new для дат, статуса и исполнителя; `title`/`note`
  фиксируются только флагом изменения (содержимое не копируется). Audit
  log остаётся security-журналом: детали — только технические id и имена
  полей, без PII, заголовков и заметок.
- **Soft delete кандидата:** события удалённого кандидата не видны в
  обычных списках и по прямой ссылке **ни для кого** через events-API
  (404), включая admin — задокументированная политика; данные остаются в
  БД, admin видит их только в audit log.
- **Сортировка:** стабильная — колонка (`starts_at|created_at|updated_at`,
  `asc|desc`) + `id asc`.

### Модель и миграция

`0005_events`: таблицы `events` (candidate_id FK CASCADE, author/assignee
FK RESTRICT, type/status/title, starts_at/ends_at/remind_at/completed_at,
`version` — счётчик optimistic concurrency, created/updated) и
`event_history` (kind, status/starts/ends/remind/assignee old+new,
title_changed/note_changed). CHECK: словари type/status/kind, непустой
title, `ends_at > starts_at`, `remind_at <= starts_at`, согласованность
`completed_at` с состоянием. Физическое удаление события отсутствует
(строка живёт до удаления кандидата, каскад).

### API

- `GET /events` — `from`, `to`, `owner_id`, `candidate_id`, `type`,
  `status`, `remind_from`, `remind_to`, `sort`, `direction`, `limit`,
  `offset`; пагинированный ответ, достаточный для календаря, ближайших,
  просроченных и напоминаний без выгрузки базы.
- `POST /events` — событие для видимого неудалённого кандидата;
  исполнитель по ролям (HR — только себя; manager/admin — любой активный
  HR); 201.
- `GET /events/{id}` — в зоне видимости (чужое/удалённого кандидата — 404).
- `PATCH /events/{id}` — **обязательный** `expected_version`; проверка под
  row lock (`SELECT … FOR UPDATE` + `populate_existing`); несовпадение —
  409 без применения (нельзя молча затереть новую версию). Переходы
  состояний валидируются сервером; завершённое событие не редактируется.
  **Семантика null:** omission («поле не передано») отличается от явного
  `null` («очистить») через `model_fields_set` — очищаются `note`,
  `ends_at`, `remind_at`; `starts_at` не nullable (явный `null` → 422);
  `assignee_user_id` очистить нельзя (исполнитель обязателен).
- `GET /events/{id}/history` — пагинируемая неизменяемая бизнес-история
  (старые записи первыми), видимость как у события.
- Мутация события + строка бизнес-истории + audit event — **одна
  транзакция, один `db.commit()`** (регрессионный тест отката на
  искусственном сбое записи аудита).

### Права

401 без аутентификации; HR — события только своих кандидатов (чужие 404);
manager/admin — все доступные + фильтр `owner_id`; новый исполнитель —
только активный HR, причём manager/admin **обязаны явно указать**
исполнителя (нет валидного значения «я сам» для не-HR-роли; без
`assignee_user_id` — 422); после передачи кандидата старый HR теряет
доступ к событиям (история сохраняется), новый получает; CSRF на
мутациях; проверки — только на сервере.

### Напоминания

Фоновых worker/cron в архитектуре нет — доставка email/push намеренно не
имитируется (ограничение этапа). Реализовано хранение `remind_at` и
серверная выдача ближайших/просроченных напоминаний через
`remind_from`/`remind_to` + панели в workspace; API не блокируется
фоновыми задачами.

### Frontend-модуль

`frontend/src/features/calendar/`: `CalendarPage` (недельная сетка пн–пт
8–17 + панели «Просроченные»/«Ближайшие»/«Напоминания», серверные фильтры
тип/состояние/HR, навигация по неделям с keyboard-кнопками), `EventFormModal`
(создание/редактирование/перенос/выполнение/откладывание, datetime-local ↔
ISO UTC, история изменений, версионные конфликты 409), `time.ts` (локальное
время). Вкладка «События» в карточке кандидата + переход из события в
карточку (hash-навигация + `openCandidateId`).

## Аналитика и отчёты (этап 6)

### Контракт (зафиксирован до миграции)

- **Источник правды — append-only журнал фактов** `analytics_facts`.
  Никакая метрика не считается по текущим снимкам стадий: `dismissed` —
  исторический переход на `rejected` в периоде, `terminated` — только
  записи увольнений, «уволен» по статусу без даты не засчитывается.
- **Период:** полуинтервал `[from, to)` (from включая, to исключая) по
  UTC-инстантам; `timezone` — IANA (по умолчанию `UTC`), валидируется и
  возвращается; сервер никогда не использует машинное локальное время.
  422: `from >= to`, период > 366 дней, неизвестная таймзона, `hr_id`
  не-HR. Пресеты считает клиент в выбранной таймзоне и шлёт явные
  `from`/`to` — воспроизводимость ответов.
- **Метрики (факты, не снимки):** `created_candidates` — создания в
  периоде (включая позже soft-deleted — исторический факт);
  `processed_candidates` — уникальные кандидаты с активностью
  (interaction/stage change/transfer/event created/completed);
  `calls` — взаимодействия типа call; `reached` — переходы на `reached`;
  `interviews_scheduled`/`interviews_done` — уникальные события-интервью
  (создано/завершено); `offers` — уникальные кандидаты с переходом на
  `offer`; `hired` — переходы на `hired`/`started`; `dismissed` —
  переходы на `rejected`; `terminated` — записи увольнений.
- **Воронка:** фиксированный порядок из `CANDIDATE_STAGE_ORDER` без
  терминальных `fired`/`rejected` (они меряются dismissed/terminated);
  достижение `new` = факт создания, прочие этапы = факты `stage_changed`
  со `stage_to`; повторные переходы не удваивают кандидата (DISTINCT).
- **Конверсии — когортные A→B:** знаменатель = уникальные кандидаты,
  достигшие A в периоде; числитель = те же, кто достиг B **после** A
  в том же периоде (`fact_at(B) > fact_at(A)`); `rate` = `null` при
  знаменателе 0 (никогда 0), иначе 0..100 с округлением до 2 знаков.
- **Разрезы:** `by_source` — источник на момент факта (снимок, не
  текущее значение); `by_hr` — ответственный HR на момент факта
  (передачи не переписывают историю). Строки — только по реальным
  данным периода; несуществующие источники/HR не фабрикуются.
- **Роли:** `/analytics/*` — только manager/admin; аутентифицированный HR
  получает 403 (никогда «тихая» фильтрация до своих данных); аноним —
  401. Фильтры `hr_id`/`source` — manager/admin.
- **Увольнение:** отдельная бизнес-сущность `candidate_terminations`
  (`terminated_at` + непустая безопасная причина) — не выводится из
  audit log и не из статуса `fired`. `POST /candidates/{id}/termination`
  (201), `GET /candidates/{id}/terminations` (новые первыми); причина не
  попадает в аудит (только id записи).
- **Экспорт CSV:** `GET /analytics/export?format=csv` (format обязателен,
  иное → 422) с теми же параметрами; `text/csv; charset=utf-8`, UTF-8
  BOM, `Content-Disposition: attachment; filename="analytics-<от>-<до>.csv"`
  (даты в выбранной таймзоне); секции KPI/конверсии/воронка/разрезы с
  фиксированным порядком колонок (зафиксирован тестами); экранирование
  `,` `;` `"` и переносов; нейтрализация формул (`=`, `+`, `-`, `@`
  в начале поля); PII не выгружается; аудит `analytics_exported` без
  содержимого; ошибки — JSON в общем формате, частичный файл не
  создаётся.

### Модель и миграция

`0006_analytics_ledger_and_terminations`: `analytics_facts` (append-only:
FK на кандидата/владельца/бизнес-строки, CHECK словаря типов, индексы
`(fact_at)`, `(fact_at, owner_user_id)`, `(fact_at, source)`, частичные
уникальные индексы по бизнес-строкам = идемпотентность;
`(event_id, fact_type, fact_at)` — легитимное повторное завершение
события не блокируется) и `candidate_terminations` (CHECK непустой
причины, индексы по кандидату и `terminated_at`). **Бэкфил** в миграции
восстанавливает факты из реальной истории: создания (из `candidates`),
взаимодействия (`candidate_interactions`), передачи
(`candidate_transfers`), события (`events`, создано/завершено) и
переходы этапов **только** из `audit_log`
(`candidate_stage_changed`, `details = "old -> new"`); текущая стадия
кандидата как «переход» никогда не выдумывается. Ответственный на
момент факта для старых строк аппроксимируется последней передачей до
факта (fallback — текущий владелец); источник для старых фактов —
текущее значение кандидата. Ограничения бэкфила (зафиксированы):
переходы этапов восстанавливаются только из аудита; история старше
аудит-журнала невосстановима; источник до миграции — снимок «как
сейчас».

### Запись фактов (транзакционность)

Факт пишется в **той же транзакции**, что и бизнес-операция
(создание/изменение кандидата со сменой этапа, взаимодействие,
передача, создание/завершение события, увольнение) — один
`db.commit()`; сбой записи факта или аудита откатывает операцию
целиком (регрессионные тесты на искусственном сбое). Частичные
уникальные индексы блокируют дубликаты фактов на уровне БД
(тест конкурентной вставки одного факта двумя потоками: одна строка).

### API

- `GET /analytics/kpi?from&to&timezone&hr_id&source` → `{period: {from, to,
  timezone}, filters: {hr_id, source}, scope: "team", kpis: {10 метрик},
  conversions: [{from_stage, to_stage, numerator, denominator, rate}],
  by_source: [{source, created, hired, dismissed, terminated}], by_hr:
  [{hr_id, username, created, processed, hired, dismissed, terminated}]}`.
- `GET /analytics/funnel` (те же параметры) → `{period, filters, stages:
  [{stage, reached}], conversions}`.
- `GET /analytics/export?format=csv` (те же параметры) → CSV (см.
  контракт). Все агрегации — SQL (COUNT(DISTINCT …) FILTER …), таблицы
  целиком в Python не загружаются; порядок конверсий/этапов — из
  `CANDIDATE_STAGE_ORDER`, разрезы отсортированы (source; lower(username)).
- OpenAPI-схемы (`AnalyticsKpiResponse`, `AnalyticsFunnelResponse`,
  `AnalyticsConversionOut`, `AnalyticsKpisOut`, …) описаны и зафиксированы
  тестом `/openapi.json`.

### Frontend-модуль

`frontend/src/features/analytics/`: `AnalyticsPage` (раздел только у
manager/admin; hash `#/analytics`), `time.ts` (пресеты в выбранной
IANA-таймзоне через `Intl` wall-clock-итерации, DST-безопасно, включая
дни 23/25 часов), `api.ts` (`fetchAnalyticsKpi`/`fetchAnalyticsFunnel`/
`exportAnalyticsCsv` — параметры в точности передаются в API, экспорт
разбирает `Content-Disposition`). UI: пресеты день/неделя/месяц/квартал
+ произвольный период и таймзона, фильтры HR/источник (только
manager/admin), KPI-полоса с определениями (tooltip + `dl/dt/dd`),
табличная воронка и конверсии с `N/A` при `null`-ставке, блок
отказов/увольнений, разрезы источник/HR, экспорт по текущим параметрам
(успех — только после 2xx), состояния загрузки/пустоты/ошибки/повтора/
403, guard от устаревших ответов (requestId), фильтры/период не
сбрасываются при переключении видов. Исключения этапа: без сохранённых
представлений, конструктора отчётов, сравнения периодов, графиков,
интеграций, расписаний/email и импорта.

## Backup, deployment и release (этап 7)

### Контур резервного копирования

Полное описание — `docs/backup-and-restore.md` (формат, имена, timezone,
retention, ротация ключей, RPO/RTO, алерты). Кратко:

- `app/backup.py` — формат `HRMBCK1`: AES-256-GCM (1 МиБ-записи), JSON-заголовок
  в AAD, отсоединённый SHA-256, атомарная публикация, retention с нижней
  границей `BACKUP_MIN_COPIES`, state-файл.
- `app/backup_runner.py` — оркестрация: `flock` (без параллельных запусков),
  0600 staging без umask-окна, `pg_dump -Fc` → шифрование → обратная
  проверка → публикация; restore drill в отдельную БД с миграциями,
  проверкой ключевых таблиц и `/health`; exit-коды 0/2/3/4/5/6/7/8/9/10/11.
- `app/cli.py` (`backup-now/check/drill/list/prune`; `--actor` — серверная
  авторизация администратора по паролю, `--as-scheduler` — сервисная
  идентичность), `app/routers/ops.py` (`POST /admin/ops/backup` 202/409/403,
  фоновый поток, аудит без PII).
- Композ-сервис `backup` (образ `backend/Dockerfile.backup`: pg_dump/pg_restore
  16.15 собраны из исходников с pinned SHA-256, не-root процесс, отдельный
  volume `backups`); планировщик `infra/scripts/backup_scheduler.sh`
  (UTC, retry/backoff, маркер healthcheck).
- Backup — секретный актив: не попадает в git/образы/артефакты/логи/volume
  приложения; ключи только через environment.

### Deployment и release

- Образы: `backend` (python:3.12-slim), `frontend` (nginx-unprivileged
  1.27-alpine), `backup` — все non-root, без секретов; версии закреплены
  (стратегия обновления: ручной подъём пинов с диффом и CI).
- production-оверлей публикует **ноль портов**; миграции НЕ выполняются при
  старте контейнера — только `infra/scripts/migrate.sh up` до переключения
  трафика (one-shot, concurrency guard: `pg_advisory_xact_lock(767147072)`
  в `alembic/env.py` — второй запуск ждёт и видит уже применённый head).
- `infra/scripts/deploy.sh`: preflight (`check_env.sh`) → build + теги
  `release-<sha>`/`release-current`/`release-prev` → миграции → переключение
  с readiness-гейтом (`compose up --wait`) → smoke (`/health` + `release_sha`
  из `/ops/status`) → **автоматический rollback** на предыдущий релиз при
  провале readiness/smoke; `--failure-drill` доказывает откат в тестовом
  контуре (сломанный релиз → откат → smoke). Предыдущие образы не удаляются.
- CI/CD: обновлённый `ci.yml` и новый `release.yml` (деплой только с тега
  `release-*` после зелёного CI для точного SHA) лежат в `review-artifacts/`
  (ci.agent-2.phase7.*, release.agent-2.*) — публикующая App не имеет
  права `workflows`, переносит владелец (инструкция в
  `review-artifacts/README.md`). Без `DEPLOY_HOST` release-пайплайн
  исполняется на CI-раннере как локальный тестовый контур.
- Release notes: SHA, миграции, изменения конфигурации, известные
  ограничения (`/tmp/release-notes-<sha>.md`, артефакт в CI).

### HTTPS

- `infra/docker-compose.proxy.yml` + `infra/nginx/default.conf.template`:
  TLS-терминация (1.2/1.3), HTTP→HTTPS redirect (политика документирована,
  замена на reject — одна строка), `Strict-Transport-Security`,
  `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
  `Permissions-Policy`, CSP (зеркало `frontend/nginx.conf`, совместима с UI),
  `client_max_body_size 10m`, `server_tokens off`. Сертификаты/DNS не
  поставляются и не предполагаются существующими — шаги оператора и
  проверка срока действия в `infra/nginx/README.md`. Ключ TLS недоступен
  приложению (mount только в proxy).

### Наблюдаемость

- `/health` — liveness + readiness БД (семантика этапов 1–6 сохранена: 200
  только при доступной БД, иначе 503; backup-сигналы сюда не подмешаны,
  чтобы не менять контракт).
- `/ops/status` — `release_sha`, `database`, `migrations`
  (current/expected revision), `backup` (available/ok/size/age,
  `last_drill_ok`).
- `/ops/backup-health` — 200 только при свежем (≤ `BACKUP_MAX_AGE_HOURS`) и
  целостном backup, иначе 503 (без «фальшивых 200»).
- `/ops/metrics` — Prometheus text: счётчики/латентность по шаблону
  маршрута; query/cookies/authorization/тела не логируются.
- Алерты (severity/dedup/cooldown/действия) — таблица в
  `docs/backup-and-restore.md`.

### Миграции этапа 7

Новых Alembic-ревизий не потребовалось: audit-действия — строковый
`Enum(native_enum=False)` без CHECK-констрейнта (новые значения
`backup_*`/`deploy_recorded`/`release_recorded` валидны на head `0006`).
Стратегия: каждая миграция backward-compatible с предыдущей версией кода
(двухшаговый деплой, если нет); колонки/таблицы не удаляются, пока старый
код их использует; rollback кода ≠ rollback схемы (downgrade только по
явному безопасному плану, иначе restore-forward); автоматический downgrade
production запрещён (`migrate.sh` его не имеет).

## Стратегия тестирования

| Уровень | Что проверяет | Где исполняется |
|---|---|---|
| Unit (pytest) | /health (ok и degraded-ветка), production-guard конфигурации (включая backup-ключи/retention), ops endpoints (RBAC, audit, 202/409/503), backup-формат (шифрование, tamper, ротация, retention, state), lifecycle | in-memory SQLite, CI |
| Integration (pytest) | /health против настоящего PostgreSQL 16, конвейер миграций; **этап 7**: реальный зашифрованный backup (pg_dump → HRMBCK1) и restore drill в отдельную БД (pg_restore + миграции + ключевые таблицы + /health + cleanup), повреждённый ciphertext → failure, lock от параллельных запусков, слабые ключи/недоступная БД → безопасная ошибка, CLI end-to-end, advisory-lock миграций | `TEST_DATABASE_URL`, CI service container (+ pg_dump/pg_restore 16 из PGDG после переноса workflow) |
| Frontend (Vitest) | отображение всех состояний статусной страницы | jsdom, CI |
| Compose smoke (CI) | валидация dev/prod/proxy конфигураций, полный запуск стека, health 200, прокси, деградация 503, реальный зашифрованный backup в volume | GitHub Actions, Docker на раннере |
| Оверлей-тесты (pytest) | статическая проверка compose-файлов/Dockerfile/nginx/скриптов (порты, секреты, non-root, UTC, retry, запрет downgrade, check_env.sh-семантика) | везде, без Docker |

Запуск интеграционных тестов локально:
`TEST_DATABASE_URL=postgresql+psycopg://hr_manager:hr_manager_dev_password@localhost:5432/hr_manager pytest -m integration -v`

## Политика миграций

- Все изменения схемы — только через Alembic; ревизии пишутся вручную
  (metadata-модели появятся вместе с бизнес-сущностями).
- Каждая миграция должна быть idempotent и reversible, где это возможно.
- Головная ревизия — `0006` (аналитика: журнал фактов + увольнения).
  `alembic upgrade/downgrade/upgrade` и повторное применение
  (`upgrade head` дважды) покрыты интеграционными тестами.
- Применение в dev/staging: автоматически при старте backend-контейнера.
  В production — отдельный контролируемый процесс `infra/scripts/migrate.sh up`
  (one-shot, до переключения трафика, advisory lock); `alembic upgrade` в CMD
  production-контейнера не используется.

## Известные ограничения этапа 1

- Нет авторизации, кандидатов и аналитики — по определению этапа.
- Нет rate limiting и audit log — они требуют схемы пользователей (этап 1
  роадмапа).
- Healthcheck frontend-контейнера использует wget из alpine-образа;
  для прод-мониторинга на этапе 7 будет отдельный exporter/agent.
- Compose-проверки (config, запуск стека, деградация) выполняются задачей
  `stack` в CI на GitHub-раннерах; в песочницах без Docker они недоступны.
- Файл `.github/workflows/ci.yml` присутствует в репозитории, однако
  публикующая GitHub App не имеет разрешения `workflows`, поэтому в ветке
  `arena/phase-1-agent-2` workflow не может быть запушен и GitHub Actions
  по ней не исполняется; копия и git-патч для владельца лежат в
  `review-artifacts/` (см. `review-artifacts/README.md`).
- Node-версии синхронизированы: `frontend/package.json` (engines
  `>=22.22.2`), `frontend/Dockerfile` (`node:22.22.3-alpine`) и
  `.github/workflows/ci.yml` (`NODE_VERSION: 22.22.3`) — `npm ci` без
  EBADENGINE-предупреждений.
