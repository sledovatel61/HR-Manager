# HR Manager

Сетевая система для командного подбора персонала: несколько HR-менеджеров
ведут кандидатов в единой PostgreSQL-базе, руководитель получает аналитику.

**Статус: этап 2 (идентификация и безопасность) завершён.** Реализованы
пользователи с обязательным паролем, хеширование Argon2id, вход/выход,
короткоживущие серверные сессии (HttpOnly cookie + CSRF double-submit),
роли HR / руководитель / администратор, серверная проверка прав на каждом
запросе, rate limiting входа и блокировка аккаунта после серии неудачных
попыток, аудит безопасности и админ-управление пользователями. Фундамент
этапа 1 (FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL, React + TypeScript +
Vite, Docker Compose, health-check, тесты, CI) сохранён. Функции кандидатов
появятся на следующих этапах [`ROADMAP.md`](ROADMAP.md).

**Дизайн-трек «Живая воронка» принят** (документация `design/` и изолированный
прототип `design-prototype/`); production-код приложения не затронут.

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
| `/auth/login`, `/auth/logout`, `/auth/me` | POST/POST/GET | все (me — по сессии) |
| `/admin/users`, `/admin/users/{id}`, `/admin/users/{id}/unlock` | GET/POST/PATCH | только `admin` |
| `/admin/audit` | GET | только `admin` |

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
