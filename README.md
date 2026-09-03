# HR Manager

Сетевая система для командного подбора персонала: несколько HR-менеджеров
ведут кандидатов в единой PostgreSQL-базе, руководитель получает аналитику.

**Статус: этап 5 (события и календарь) завершён.** События, связанные
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
и серверная выдача ближайших/просроченных в workspace; внешняя доставка
(email/push) не реализована — в архитектуре нет фоновых worker, доставка
не имитируется (ограничение этапа, см. отчёт).

## Рабочий интерфейс HR (этап 4)

После входа доступны разделы по роли: HR — «Моя очередь», «Календарь»,
«Kanban», «Удалённые»; руководитель/администратор — «Кандидаты» (все
владельцы + фильтр по ответственному), «Календарь», «Kanban»,
«Удалённые». «Календарь» — недельная сетка (пн–пт, 8:00–17:00) с
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
```

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
infra/     docker-compose.yml, production overlay, preflight-скрипт
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
   (≥ 32 символов, `openssl rand -hex 32`), свой `POSTGRES_PASSWORD`.
2. Проверьте конфигурацию: `infra/scripts/check_env.sh`.
3. Проверьте итоговую конфигурацию:
   `docker compose -f infra/docker-compose.yml -f infra/compose.prod.yml config`.
4. Запуск: `docker compose -f infra/docker-compose.yml -f infra/compose.prod.yml up -d`.

Backend **откажется стартовать** в production с дефолтным ключом, dev-учёткой
БД, отсутствующим паролем или включённым debug. Preflight-скрипт отклоняет
dev-`SECRET_KEY` и dev-пароль PostgreSQL. Внешние порты production overlay не
публикует (`ports: !reset []`) — перед приложением ставится reverse proxy с
HTTPS (этап 7 роадмапа).

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
- Пользователи, роли, сессии, аудит и управление доступами реализованы на
  этапе 2 (см. раздел «Аутентификация и безопасность» выше). Функции
  кандидатов (единая база, статусы, очередь) появятся на следующем этапе по
  [`ROADMAP.md`](ROADMAP.md).

## Документация

- [`agents.md`](agents.md) — регламент для AI-агентов;
- [`PRODUCT_SPEC.md`](PRODUCT_SPEC.md) — актуальное ТЗ;
- [`ROADMAP.md`](ROADMAP.md) — этапы разработки;
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — решения этапа 1;
- [`prompts/PHASE_1_PROMPT.md`](prompts/PHASE_1_PROMPT.md) — промпт этапа 1;
- [`prompts/PHASE_2_PROMPT.md`](prompts/PHASE_2_PROMPT.md) — промпт этапа 2;
- [`prompts/PHASE_3_PROMPT.md`](prompts/PHASE_3_PROMPT.md) — промпт следующего
  этапа (единая база кандидатов);
- [`design/IMPLEMENTATION_GUIDE.md`](design/IMPLEMENTATION_GUIDE.md) — план
  переноса дизайна «Живая воронка» в production.
