# HR Manager

Сетевая система для командного подбора персонала: несколько HR-менеджеров
ведут кандидатов в единой PostgreSQL-базе, руководитель получает аналитику.

**Статус: этап 1 завершён — запускаемый технический каркас** (FastAPI +
SQLAlchemy 2 + Alembic + PostgreSQL, React + TypeScript + Vite, Docker
Compose, health-check, тесты, CI). Бизнес-функциональность появится на
следующих этапах по [`ROADMAP.md`](ROADMAP.md).

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
- Функции кандидатов, пользователей и ролей на этом этапе нет — они появятся
  по [`ROADMAP.md`](ROADMAP.md).

## Документация

- [`agents.md`](agents.md) — регламент для AI-агентов;
- [`PRODUCT_SPEC.md`](PRODUCT_SPEC.md) — актуальное ТЗ;
- [`ROADMAP.md`](ROADMAP.md) — этапы разработки;
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — решения этапа 1;
- [`prompts/PHASE_1_PROMPT.md`](prompts/PHASE_1_PROMPT.md) — промпт этапа 1.
