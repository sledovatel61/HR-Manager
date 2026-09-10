# HR Manager

Сетевая система для командного подбора персонала: несколько HR-менеджеров
ведут кандидатов в единой PostgreSQL-базе, руководитель получает аналитику.

**Статус: этапы 0–11 завершены; следующий — этап 12 после согласования
продуктового контракта.** Phase 11 принята в PR #18 и влита в `main`
merge-коммитом `a6ac73cb797919383b80ce65b1614cd6e4dad47f`. Актуальный handoff находится в
[`docs/CURRENT_STATUS.md`](docs/CURRENT_STATUS.md), отчёт принятой реализации — в
[`docs/phase-11-report-arena.md`](docs/phase-11-report-arena.md). Зашифрованные
backup (AES-256-GCM) с retention ≥ 7 дней и restore drill в отдельную БД,
deploy-скрипт с автоматическим rollback, HTTPS reverse proxy и
observability-сигналы — см. [`docs/backup-and-restore.md`](docs/backup-and-restore.md).
Ранее (этап 5) добавлены события, связанные
с кандидатом (звонки, собеседования, напоминания), с исполнителем,
сроком, напоминанием, состояниями «запланировано/выполнено/отложено»,
неизменяемой бизнес-историей изменений и аудитом; календарное недельное
представление, панели просроченных/ближайших событий и напоминаний,
серверные фильтры по периоду/типу/состоянию/ответственному, создание и
редактирование событий из календаря и карточки кандидата, optimistic
concurrency через обязательный `expected_version`. Этап 4 (рабочий
интерфейс HR) сохраняется: Над базой кандидатов
этапа 3 построен полноценный production-интерфейс на реальном API:
application shell с разделами «Моя очередь» / «Кандидаты» / «Kanban» /
«Удалённые» (по ролям), таблица с серверными поиском, фильтрами,
сортировкой и пагинацией, Kanban с постраничной загрузкой колонок и
keyboard-альтернативой смены этапа (оптимистичное обновление с откатом),
карточка кандидата (drawer со вкладками «Сведения» / «Взаимодействия» /
«События» / «Передачи», редактирование, история с пагинацией),
создание/редактирование с UX подтверждения дубликатов, мягкое удаление и
отдельный экран удалённых с восстановлением, двухшаговая передача
кандидата с обязательной причиной и неизменяемой историей. Добавлен backend-контракт передачи
`POST /candidates/{id}/transfer` (одна транзакция, блокировка строки,
только активный HR-получатель, аудит без PII и текста причины) и
`GET /candidates/{id}/transfers`. Фундамент этапов 1–3 (FastAPI +
SQLAlchemy 2 + Alembic + PostgreSQL, React + TypeScript + Vite,
сессии/CSRF, роли, аудит, health-check, Docker Compose, тесты, CI)
сохранён.

**Дизайн-трек «Живая воронка» принят** (документация `design/` и изолированный
прототип `design-prototype/`); на этапе 4 его токены и UI-примитивы перенесены
в production-структуру `frontend/src/design-system/` (см.
`docs/ARCHITECTURE.md`).

## Аутентификация и безопасность (этап 2)

- **Пользователи и роли.** Три роли: `hr`, `manager` (руководитель),
  `admin` (администратор). Пароль обязателен при создании пользователя и
  хранится только как хеш **Argon2id** (`argon2-cffi`, память 64 МиБ,
  3 итерации, 4 потока); пароль задаётся по политике (минимум 12 символов,
  буквы и цифры, не совпадает с логином).
- **Сессии.** Серверные короткоживущие сессии (по умолчанию TTL 30 минут,
  скользящее продление). В браузере — `HttpOnly`, `SameSite=Lax` cookie
  `hrm_session` (значение — UUID сессии) и JS-читаемый cookie `hrm_csrf`.
  Выход и истечение отзывают сессию на сервере немедленно.
- **CSRF.** Double-submit токен: мутирующие запросы требуют заголовок
  `X-CSRF-Token`, совпадающий с cookie и токеном сессии.
- **Защита от перебора.** Два уровня: per-IP sliding-window rate limit на
  `/auth/login` (429) и блокировка аккаунта после `LOGIN_MAX_FAILURES`
  неудачных входов (423, по умолчанию 5 попыток / 15 минут).
- **Права на сервере.** Все `/admin/*` endpoint'ы проверяют роль `admin`
  на сервере (`Depends`); скрытие кнопок во frontend защитой не является.
- **Аудит.** В таблицу `audit_log` пишутся входы/выходы, неудачные входы,
  блокировки, создание/изменение/деактивация пользователей, смена ролей,
  разблокировки — с IP и User-Agent, без паролей и секретов.
- **Начальный администратор.** При пустой таблице пользователей на старте
  создаётся администратор: в dev — `admin` / `AdminAdmin123` (выводится в
  лог, помечен development-only); в production пароль обязателен через
  `BOOTSTRAP_ADMIN_PASSWORD` (иначе приложение не стартует; администратора
  также можно создать командой `python -m app.cli create-admin`).

API-доступы:

| Endpoint | Метод | Доступ |
|---|---|---|
| `/health` | GET | без аутентификации |
| `/auth/login`, `/auth/logout`, `/auth/me` | POST/POST/GET | все (me — по сессии) |
| `/admin/users`, `/admin/users/{id}`, `/admin/users/{id}/unlock` | GET/POST/PATCH | только `admin` |
| `/admin/audit` | GET | только `admin` |
| `/candidates` | GET/POST | HR — только свои; manager/admin — все |
| `/candidates/{id}` | GET/PATCH/DELETE | HR — только свои (чужие 404); manager/admin — все |
| `/candidates/{id}/restore` | POST | HR — только свои; manager/admin — все |
| `/candidates/{id}/interactions` | GET/POST | HR — только свои; manager/admin — все |
| `/candidates/{id}/transfer` | POST | HR — только своего; manager/admin — любого видимого |
| `/candidates/{id}/transfers` | GET | как у карточки (после передачи бывший HR получает 404) |
| `/admin/users/hr` | GET | любой авторизованный (минимальные поля активных HR) |
| `/events` | GET/POST | HR — события своих кандидатов; manager/admin — все + фильтр `owner_id` |
| `/events/{id}` | GET/PATCH | как у списка; `PATCH` требует `expected_version` (409 при конфликте) |
| `/events/{id}/history` | GET | как у события (неизменяемая бизнес-история) |
| `/candidates/{id}/termination` | POST | HR — только своего кандидата; manager/admin — любого видимого |
| `/candidates/{id}/terminations` | GET | как у карточки кандидата |
| `/analytics/kpi` | GET | только manager/admin (HR — 403 даже при валидной сессии) |
| `/analytics/funnel` | GET | только manager/admin |
| `/analytics/export` | GET | только manager/admin; обязателен `format=csv`, ответ `text/csv; charset=utf-8` (attachment) |

`GET /candidates` поддерживает `query` (ФИО/телефон/email), `stage`,
`source`, `owner_id`, `include_deleted` (список только мягко удалённых),
`sort` (`created_at`/`updated_at`/`full_name`/`stage`), `direction`
(`asc`/`desc`), `limit` (≤100), `offset`; ответ — пагинированный
`{items, total, limit, offset}`. Стадия — закрытый словарь из 11 значений
(`new`, `contacted`, `reached`, `interview_scheduled`, `interview_done`,
`offer`, `hired`, `started`, `probation`, `fired`, `rejected`).
При дубликате телефона/email API отвечает 409 с найденными кандидатами;
повторный запрос с `confirm_duplicate: true` создаёт точную копию.
Передача: `POST /candidates/{id}/transfer` с телом
`{new_owner_user_id, reason}` (причина обязательна, непустая); операция
атомарна, пишет неизменяемую историю в `candidate_transfers` и аудит-событие
без PII и текста причины.

### События и календарь (этап 5)

Типы `call | interview | reminder`, состояния `scheduled | completed |
postponed` (`completed` — терминальное; откладывание требует новую дату).
Все времена хранятся в UTC, API принимает/отдаёт ISO 8601 с timezone, UI
показывает локальное время браузера. `GET /events` поддерживает `from`/`to`
(полуинтервал `[from, to)`), `owner_id`, `candidate_id`, `type`, `status`,
`remind_from`/`remind_to` (момент напоминания), `sort`, `direction`,
`limit`, `offset`. Права: HR — события только своих кандидатов (чужие и
события soft-deleted кандидатов — 404), manager/admin — все + фильтр по
HR; исполнитель — только активный HR (HR назначает себя; manager/admin
обязаны явно выбрать активного HR — без исполнителя 422). `PATCH
/events/{id}` требует `expected_version` — при конкурентном изменении 409
без потери данных; явный `null` очищает `note`/`ends_at`/`remind_at`
(отличается от «поле не передано»). Мутация события, строка бизнес-истории
(`event_history`) и audit-событие фиксируются одной транзакцией; в аудите
и логах нет PII, заголовков и заметок. Напоминания: хранение `remind_at`
и серверная выдача ближайших/просроченных в workspace. В этапах 8–10 поверх
этого добавлены PostgreSQL outbox/worker, email/Telegram и безопасные
односторонние сообщения кандидатам.

### Аналитика и отчёты (этап 6)

Единый источник правды метрик — append-only журнал фактов
`analytics_facts`: по строке на бизнес-факт (`candidate_created`,
`interaction_added`, `stage_changed`, `transfer`, `event_created`,
`event_completed`, `terminated`) с `fact_at` (UTC), снимком источника
и ответственного HR **на момент факта** (передачи не переписывают
историю). Факт пишется в **той же транзакции**, что и бизнес-операция:
сбой записи журнала или аудита откатывает операцию целиком. Частичные
уникальные индексы дают идемпотентность; миграция `0006` создаёт журнал
и бэкфилит существующую историю (создания, взаимодействия, передачи,
события и переходы этапов из `audit_log`). Увольнение — отдельная
бизнес-сущность `candidate_terminations` (`terminated_at` + непустая
причина), а не текущий статус `fired`: статус без даты не доказывает
увольнение и в метрику не попадает.

Период — полуинтервал `[from, to)`: `from` включая, `to` исключая,
сравнение по UTC-инстантам; `timezone` — IANA (по умолчанию `UTC`),
валидируется и возвращается в ответе; сервер никогда не использует
машинное локальное время. 422 при `from ≥ to`, периоде > 366 дней,
неизвестной таймзоне или `hr_id`, не указывающем на HR. Пресеты
(день/неделя/месяц/квартал) считает **клиент** в выбранной таймзоне и
шлёт явные `from`/`to` — ответы воспроизводимы. Метрики (SQL-агрегации,
без загрузки таблиц в Python): `created_candidates` (созданные в периоде,
включая позже удалённых), `processed_candidates` (уникальные кандидаты
с активностью: взаимодействие, смена этапа, передача, событие),
`calls`, `reached`, `interviews_scheduled`/`interviews_done` (уникальные
события-интервью), `offers`/`hired` (уникальные кандидаты, перешедшие на
`offer`/`hired` или `started`), `dismissed` (переход на `rejected`
в периоде), `terminated` (записи увольнений). Воронка — фиксированный
порядок этапов (`new … probation`; терминальные `fired`/`rejected`
в воронку не входят и меряются `dismissed`/`terminated`). Конверсии —
когортные A→B: знаменатель — уникальные кандидаты, достигшие A в
периоде; числитель — те же, кто достиг B **после** A в том же периоде;
`rate` — `null` (не 0) при нулевом знаменателе, иначе 0..100 с ≤2
знаками; повторные переходы не удваивают кандидата. Разрезы
`by_source`/`by_hr` используют снимки источника/владельца на момент
факта; строки только по реально существующим данным (без выдуманных
источников/HR).

Экспорт: `GET /analytics/export?format=csv` с теми же фильтрами;
`text/csv; charset=utf-8`, UTF-8 BOM, attachment
`analytics-<от>-<до>.csv` (даты в выбранной таймзоне); фиксированные
секции KPI / конверсии / воронка / разрезы (порядок колонок зафиксирован
тестами); экранирование запятых/точек с запятой/кавычек/переносов и
нейтрализация формул (`=`, `+`, `-`, `@` в начале поля); PII не
выгружается; экспорт аудируется (`analytics_exported`, без содержимого
отчёта); ошибки возвращаются в обычном JSON-формате — частичный
«успешный» файл не создаётся. Права: 401 без сессии; аутентифицированный
HR получает **403** на всех `/analytics/*` (никогда не «тихая» фильтрация
до своих данных). В UI раздел «Аналитика» виден только
руководителю/администратору: пресеты + произвольный период и таймзона,
фильтры HR/источник, KPI-полоса с определениями (tooltip), табличная
воронка с процентами и `N/A`, блок отказов/увольнений, разрезы,
экспорт по текущим параметрам (успех — только после ответа 2xx);
состояния загрузки/пустоты/ошибки/повтора/403, фильтры и период не
сбрасываются при переключении видов. Ограничения этапа: без сохранённых
представлений, конструктора отчётов, сравнения периодов, графиков,
расписаний/email-рассылок и импорта данных.


### Уведомления и пилотный режим (этап 8)

Фундамент доставки: реальные внутренние уведомления, личные напоминания,
PostgreSQL transactional outbox + отдельный worker, тихие часы и явно
назначенный пилотный пользователь с полным совмещённым доступом.

- **Модель** (миграции `0007`/`0008`): `notifications` (снимок
  заголовка/текста без PII кандидата, `dedupe_key`), `reminders`
  (owner/assignee, повторение из закрытого набора, optimistic concurrency),
  `notification_preferences` (IANA timezone, тихие часы с пересечением
  полночи, рабочие дни), `notification_outbox` (канал, статусы
  `queued→sending→accepted/delivered` + `failed/cancelled/skipped`,
  `idempotency_key`, попытки/backoff/lease, исходное vs фактическое время
  планирования), `notification_delivery_attempts` (неизменяемая история),
  `worker_heartbeat`, `access_grants`. События получили терминальный
  статус `cancelled` (словарь этапа 5 расширен задокументированно).
- **Worker** — отдельный процесс того же образа
  (`python -m app.cli worker`, сервис `worker` в dev/prod Compose):
  `FOR UPDATE SKIP LOCKED`, параллельные экземпляры без дублей,
  восстановление просроченных lease, bounded exponential backoff,
  идемпотентная in_app-доставка, корректный SIGTERM, healthcheck
  (`worker-check`), метрики/статус без PII. `email`/`telegram`
  зарезервированы (этап 9) и честно помечаются `skipped` — фиктивного
  `delivered` нет.
- **Бизнес-события** (назначение, приближение собеседования/звонка с
  настраиваемыми смещениями, просрочка, перенос, отмена, передача
  кандидата, личное напоминание, критическая ошибка очереди) пишут
  outbox-строки в **одной транзакции** с операцией; повторная обработка
  не дублирует; перенос/завершение/отмена отменяет устаревший план.
- **Тихие часы** считает backend: локальное время зоны получателя,
  DST через zoneinfo, перенос на первое разрешённое время; срочный
  ручной override — только с подтверждением, permission и аудитом.
- **API**: пагинированный центр уведомлений (+unread count), mark
  read/dismiss (лимиты массовых операций), `/notifications/{id}/resolve`
  повторно проверяет права перед переходом к объекту, история доставки;
  CRUD собственных напоминаний; собственные настройки; админ-диагностика
  очереди (только счётчики/статусы), retry/cancel с аудитом.
- **Пилот**: один аккаунт (роль `admin` — подтверждённый superset этапов
  2–7 — плюс **явный** грант `pilot_full_access`), идемпотентный мастер
  создания без сброса паролей и без молчаливого расширения прав; гранты
  выдаются/отзываются админом и аудируются. RBAC/CSRF/audit не
  отключаются. После пилота HR и руководители добавляются обычным путём
  без смены архитектуры.
- **UI (русский)**: колокольчик с unread-badge и popover, центр
  уведомлений с фильтрами/пагинацией/aria-live, «Мои напоминания»
  (компактная форма без знания cron), «Настройки уведомлений»,
  админ-экран очереди и мастер первой настройки (timezone + тихие часы,
  можно пропустить). Telegram/email честно показаны «не настроено» и не
  блокируют приложение.

## Рабочий интерфейс HR (этап 4)

После входа доступны разделы по роли: HR — «Моя очередь», «Календарь»,
«Kanban», «Удалённые»; руководитель/администратор — «Кандидаты» (все
владельцы + фильтр по ответственному), «Календарь», «Kanban»,
«Удалённые», «Аналитика» (KPI, воронка, разрезы, экспорт CSV). «Календарь» — недельная сетка (пн–пт, 8:00–17:00) с
серверными фильтрами и панелями просроченных/ближайших событий и
напоминаний; события создаются из календаря и из вкладки «События»
карточки кандидата; из события можно перейти в карточку кандидата. Все экраны получают
данные только из backend API (поиск/фильтры/сортировка/пагинация — на
сервере); ответ `401` возвращает на экран входа, `403` показывает состояние
недостаточных прав. Kanban грузится постранично внутри колонок; смена этапа
— drag-and-drop или выбор в карточке (клавиатура), с оптимистичным
обновлением и откатом при ошибке. Карточка кандидата: редактирование
полей, история взаимодействий (с добавлением без перезагрузки), история
передач и двухшаговая передача с обязательной причиной и подтверждением.
Запуск UI: `cd frontend && npm ci && npm run dev` (прокси `/api` →
backend на `localhost:8000`), продакшен-сборка — `npm run build`
(раздаётся nginx, см. `frontend/nginx.conf`).

После входа `POST /auth/login` возвращает пользователя и `csrf_token`;
далее все POST/PATCH/DELETE шлют заголовок `X-CSRF-Token: <csrf_token>`.

## Стек

- **Backend:** Python 3.12+, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2
- **Frontend:** React 18, TypeScript (strict), Vite
- **Database:** PostgreSQL 16
- **Запуск:** Docker Compose; **CI:** GitHub Actions

## Быстрый старт (Docker Compose)

Требуется Docker с плагином Compose.

```bash
git clone https://github.com/sledovatel61/HR-Manager.git
cd HR-Manager

docker compose -f infra/docker-compose.yml config   # проверка конфигурации
docker compose -f infra/docker-compose.yml up --build -d
```

После запуска:

| Что | Где |
|---|---|
| Frontend (статусная страница) | http://localhost:8080 |
| Backend API (Swagger UI) | http://localhost:8000/docs |
| Health check | http://localhost:8000/health |

Порты dev-стека привязаны к `127.0.0.1` (localhost) и не публикуются в
локальную сеть. Все учётные данные в dev-файле помечены как development-only;
production использует только переменные окружения (см. ниже).

`GET /health` возвращает `200` только когда PostgreSQL доступен; при
недоступной БД — `503` с телом `{"status": "degraded", ...}`.

Остановка (данные в volume сохраняются):

```bash
docker compose -f infra/docker-compose.yml down
# полная очистка вместе с данными: docker compose -f infra/docker-compose.yml down -v
# этап 8: `up` поднимает и worker уведомлений (сервис worker, healthcheck
# по heartbeat); при первом входе появится мастер быстрой настройки
# (timezone + тихие часы), пилотного пользователя создаёт администратор
# в разделе «Администрирование».
```

## Локальный пилот Windows (этап 12)

Обычный пользователь Windows 10/11 x64 устанавливает HR Manager в несколько
кликов: скачать `HR Manager Setup.exe` (артефакт CI), запустить, выбрать
роль (`HR | Руководитель | Администратор`), ввести фамилию, нажать
«Установить». Дальше мастер сам собирает образы, генерирует секреты,
поднимает Postgres/бэкенд/фронтенд/worker/бэкапы в Docker Desktop и
открывает браузер на `http://127.0.0.1:8080` — там создаётся единственная
учётная запись пилота с паролем, который пользователь задаёт сам.
Никаких команд Docker/Postgres/Alembic, `.env` или PowerShell в основном
сценарии нет.

- [`installer/README.md`](installer/README.md) — сборка установщика,
  закреплённый инструмент (Inno Setup 6.7.3, SHA256), манифест хешей,
  честный статус кодовой подписи;
- [`infra/windows/README.md`](infra/windows/README.md) — движок
  `hr-manager.ps1` (install/start/stop/status/open/update/diagnostics/
  uninstall/resume), секреты, доверенная граница обновления, состояния
  диагностики, неинтерактивный режим и тесты;
- [`infra/compose.pilot.yml`](infra/compose.pilot.yml) — пилотный overlay
  (проект `hr-manager-pilot`, только `127.0.0.1`, тома `pilot_pgdata`/
  `pilot_backups`, внешние SMTP/Telegram выключены; production-контур
  `compose.prod.yml` не ослаблен).

Docker Desktop пользователь ставит сам с официального docker.com —
установщик лицензии за него не принимает.

## Локальная разработка без Docker

### Backend (Python 3.12+, PostgreSQL 16 на localhost:5432)

```bash
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# БД из docker-compose (или своя): создайте базу/пользователя из .env.example
alembic upgrade head                # применить миграции
uvicorn app.main:app --reload       # http://localhost:8000
```

Переменные окружения задаются явно, например:

```bash
export DATABASE_URL=postgresql+psycopg://hr_manager:hr_manager_dev_password@localhost:5432/hr_manager
```

### Frontend (Node.js 20+)

```bash
cd frontend
npm ci
npm run dev      # http://localhost:5173, проксирует /api на localhost:8000
```

### Проверки backend

```bash
cd backend
ruff check .            # линтер
ruff format --check .   # форматирование
mypy app                # типы
pytest -v               # unit-тесты (in-memory SQLite, только для тестов)
```

Интеграционные тесты — против реального PostgreSQL (например, поднятого
через `infra/docker-compose.yml`):

```bash
cd backend
TEST_DATABASE_URL=postgresql+psycopg://hr_manager:hr_manager_dev_password@localhost:5432/hr_manager \
  pytest -m integration -v
```

### Проверки frontend

```bash
cd frontend
npm run lint          # ESLint
npm run typecheck     # TypeScript
npm run test          # Vitest
npm run build         # production build
npm audit --audit-level=high   # аудит уязвимостей
```

Или всё сразу из корня: `make check`.

## Структура репозитория

```
backend/   FastAPI + SQLAlchemy 2 + Alembic, тесты, Dockerfile
frontend/  React + TypeScript + Vite, тесты, Dockerfile + nginx
design/    UX/UI-концепция «Живая воронка»: дизайн-система, гайд переноса
design-prototype/  изолированный интерактивный прототип (не production-код)
infra/     docker-compose.yml, production/pilot overlays, preflight-скрипт
          windows/ — движок установки/обновления пилота (phase 12)
installer/ исходники Inno Setup мастера HR Manager Setup.exe (phase 12)
docs/      ARCHITECTURE.md — решения и ограничения этапа
prompts/   промпты этапов разработки
```

## Переменные окружения

Скопируйте шаблон и заполните при необходимости:

```bash
cp .env.example .env    # .env игнорируется git'ом
```

| Переменная | Назначение | Где используется |
|---|---|---|
| `APP_ENV` | `development` / `test` / `production` | backend |
| `APP_DEBUG` | отладочный режим (в production запрещён) | backend |
| `SECRET_KEY` | ключ подписи (в production ≥ 32 симв., не dev-значение) | backend |
| `DATABASE_URL` | строка подключения PostgreSQL | backend, alembic |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | учётные данные БД | docker compose |
| `VITE_API_BASE_URL` | базовый URL API для браузера (по умолч. `/api`) | frontend build |
| `TEST_DATABASE_URL` | PostgreSQL для интеграционных тестов | pytest |
| `SESSION_TTL_MINUTES` | время жизни неактивной сессии (по умолч. 30) | backend |
| `SESSION_COOKIE_SECURE` | флаг Secure cookie (авто-`true` в production) | backend |
| `LOGIN_RATE_LIMIT` / `LOGIN_RATE_WINDOW_SECONDS` | лимит попыток входа на IP / окно | backend |
| `LOGIN_MAX_FAILURES` / `LOGIN_LOCK_MINUTES` | порог и срок блокировки аккаунта | backend |
| `BOOTSTRAP_ADMIN_USERNAME` / `BOOTSTRAP_ADMIN_PASSWORD` / `BOOTSTRAP_ADMIN_FULL_NAME` | начальный администратор (в production пароль обязателен) | backend |

## Production

Требуется Docker Compose **v2.24+** (тег `!reset` в overlay).

1. Задайте секреты в окружении: `APP_ENV=production`, сильный `SECRET_KEY`
   (≥ 32 символов, `openssl rand -hex 32`), свой `POSTGRES_PASSWORD`,
   `BOOTSTRAP_ADMIN_PASSWORD`. Для контура backup (этап 7) —
   `BACKUP_ENABLED=true`, `BACKUP_KEY_ID` и
   `BACKUP_ENC_KEY="$(openssl rand -base64 32)"`.
2. Проверьте конфигурацию: `infra/scripts/check_env.sh`.
3. Миграции до запуска (one-shot, advisory lock): `infra/scripts/migrate.sh up`.
4. Запуск: `docker compose -f infra/docker-compose.yml -f infra/compose.prod.yml up -d`.
5. HTTPS: добавьте оверлей proxy (`infra/docker-compose.proxy.yml`) — шаги
   оператора в [`infra/nginx/README.md`](infra/nginx/README.md).
6. Деплой релизов — [`infra/scripts/deploy.sh`](infra/scripts/deploy.sh)
   (build → миграции → переключение с readiness-гейтом → smoke →
   автоматический rollback), в CI — workflow `release.yml` из
   `review-artifacts/` (переносит владелец).

Backend **откажется стартовать** в production с дефолтным ключом, dev-учёткой
БД, отсутствующим паролем или включённым debug. Preflight-скрипт отклоняет
dev-`SECRET_KEY`, dev-пароль PostgreSQL и (при `BACKUP_ENABLED=true`)
отсутствующие/dev/слабые ключи шифрования backup. Внешние порты production
overlay не публикует (`ports: !reset []`) — перед приложением ставится
reverse proxy с HTTPS. Подробнее про backup/restore, RPO/RTO, ротацию
ключей и алерты — [`docs/backup-and-restore.md`](docs/backup-and-restore.md).

## CI

GitHub Actions (`push` в `main`, pull requests): ruff (check + format) + mypy
+ pytest для backend (включая проверки production preflight), ESLint +
typecheck + Vitest + production build + `npm audit` для frontend,
интеграционные тесты против PostgreSQL 16 (включая конвейер миграций
upgrade/downgrade), а также compose smoke-тест полного стека: валидация
dev- и production-конфигураций, `up --build --wait`, `/health` → 200,
frontend и `/api/health`, остановка БД → `/health` 503, гарантированная
очистка через `if: always()`.

## Важные ограничения

- **SQLite запрещён как production-БД.** Он используется только в
  изолированных unit-тестах (in-memory, `APP_ENV=test`) — см.
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
- В репозитории нет секретов и персональных данных; `.env`, дампы и backup
  игнорируются git'ом.
- Пользователи, роли, сессии, аудит, кандидаты, события, аналитика,
  эксплуатационный контур, коммуникации, версионируемые списки документов,
  ограниченные личные правила и локальный Windows-пилот реализованы. Следующий
  этап — Phase 13: безопасный канал доставки обновлений.

## Документация

- [`agents.md`](agents.md) — регламент для AI-агентов;
- [`PRODUCT_SPEC.md`](PRODUCT_SPEC.md) — актуальное ТЗ;
- [`ROADMAP.md`](ROADMAP.md) — этапы разработки;
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — архитектурные решения и ограничения;
- [`docs/CURRENT_STATUS.md`](docs/CURRENT_STATUS.md) — актуальный handoff;
- [`prompts/PHASE_1_PROMPT.md`](prompts/PHASE_1_PROMPT.md) — промпт этапа 1;
- [`prompts/PHASE_2_PROMPT.md`](prompts/PHASE_2_PROMPT.md) — промпт этапа 2;
- [`prompts/PHASE_3_PROMPT.md`](prompts/PHASE_3_PROMPT.md) — исторический промпт базы кандидатов;
- [`prompts/PHASE_11_PROMPT.md`](prompts/PHASE_11_PROMPT.md) — историческое задание этапа 11;
- [`docs/phase-12-report-arena.md`](docs/phase-12-report-arena.md) — отчёт этапа 12 (Windows-пилот);
- [`docs/phase-13-report-arena.md`](docs/phase-13-report-arena.md) — отчёт этапа 13 (канал обновлений);
- [`docs/phase-12-local-acceptance.md`](docs/phase-12-local-acceptance.md) — итоговая локальная приёмка Windows;
- [`prompts/PHASE_13_PROMPT.md`](prompts/PHASE_13_PROMPT.md) — задание этапа 13;
- [`design/IMPLEMENTATION_GUIDE.md`](design/IMPLEMENTATION_GUIDE.md) — план
  переноса дизайна «Живая воронка» в production.

## Phase 11 — принято

В PR #18 добавлены «Списки документов», вкладка «Документы» в карточке
и «Мои правила». Опубликованная версия неизменяема; новая публикация не меняет
старые снимки кандидатов. Учитывается только факт получения, без файлов/номеров.

Управление списками — admin или `document_lists_manage`; расширенная область
кандидатских документов — подтверждённый `candidate_documents_all` (пилотный
grant включает оба). Согласия email/Telegram подключаются прежним способом.

Запрос документов теперь отправляется по серверному `document_set_id`, а не
по свободно введённым названиям. Ручные и автоматические отправки используют
существующий worker, consent, lease/retry и тихие часы. Правила ограничены
переходом этапа/сроком, списком, каналом и действием; произвольного кода нет.

Подробные API/миграционные решения, проверки, границы совместимости и handoff:
[`docs/phase-11-report-arena.md`](docs/phase-11-report-arena.md).

## Phase 13 — принято (в ветке-базе Phase 12)

Безопасный канал доставки обновлений Windows-пилота: подписанный release
manifest (detached Ed25519 над каноническим payload), проверка публичным
ключом на сервере и на host, HTTPS-загрузка со staging через
bind-mounted каталог, защита от downgrade/подмены/Zip Slip, серверные
состояния и админ-раздел «Обновления», повторное использование Phase 12
backup/rollback/resume. Детали: [`docs/phase-13-report-arena.md`](docs/phase-13-report-arena.md),
[`infra/release/README.md`](infra/release/README.md).

## Phase 12 — принято

Windows-пилот: графический установщик `HR Manager Setup.exe` (Inno Setup
6.7.3, сборка в CI, манифест хешей), движок `infra/windows/hr-manager.ps1`
(9 действий, секреты только в защищённом каталоге состояния, честный
префлайт, обновление с бэкап-воротами и откатом без даунгрейда БД,
агрегированная диагностика с редакцией секретов), одноразовый loopback-обмен
первого запуска (единственный владелец: роль `admin` + грант
`pilot_full_access`, режим работы — поле профиля), пилотный Compose-оверлей
с публикацией только на `127.0.0.1` и миграция `0013`. Отчёт:
[`docs/phase-12-report-arena.md`](docs/phase-12-report-arena.md).

Финальная локальная приёмка на реальной Windows подтвердила trusted update,
автоматический rollback намеренно сломанного обновления и uninstall с
сохранением StateDir, PostgreSQL/backup volumes и зашифрованных backup-файлов.
Подробности и SHA256 установщика:
[`docs/phase-12-local-acceptance.md`](docs/phase-12-local-acceptance.md).

## Phase 13 — реализовано

Подписанный manifest канала обновлений (Ed25519), безопасная
загрузка/staging, административный UI «Обновления» и release pipeline
поверх уже принятого update/rollback Phase 12. Задание:
[`prompts/PHASE_13_PROMPT.md`](prompts/PHASE_13_PROMPT.md); отчёт:
[`docs/phase-13-report-arena.md`](docs/phase-13-report-arena.md).
