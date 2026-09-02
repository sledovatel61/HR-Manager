# HR Manager

Сетевая система для командного подбора персонала: несколько HR-менеджеров
работают с единой базой кандидатов в браузере.

> **Статус: Этап 1 — технический каркас.** Реализован запускаемый скелет
> сетевого приложения (FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL,
> React + TypeScript + Vite, Docker Compose, health-check, тесты, CI).
> Бизнес-функциональность кандидатов ещё не реализована.

## Архитектура и структура репозитория

```
├── backend/                 # FastAPI-приложение (Python 3.12+)
│   ├── src/hr_manager/
│   │   ├── api/health.py    #   GET /health — проверка приложения и БД
│   │   ├── core/            #   конфиг (env), engine, сессии, логирование
│   │   └── db/              #   SQLAlchemy 2 Base, metadata, схема
│   ├── alembic/             # миграции Alembic (первая безопасная — пустая)
│   ├── tests/               # pytest: health, безопасность конфига, интеграция
│   ├── alembic.ini
│   ├── pyproject.toml       # зависимости, ruff, mypy, pytest
│   └── Dockerfile
├── frontend/                # React + TypeScript + Vite (SPA)
│   ├── src/                 # страница состояния backend/database
│   ├── tests vitest         # unit-тесты (vitest + Testing Library)
│   ├── vite.config.ts       # dev-прокси /health → backend
│   ├── package.json
│   └── Dockerfile
├── infra/                   # (зарезервировано) production-развёртывание
├── docs/                    # (зарезервировано) документация
├── compose.yaml             # локальный dev-стек: postgres + backend + frontend
├── .env.example             # шаблон окружения без секретов
└── .github/workflows/ci.yml # CI: backend, frontend, docker compose smoke
```

Принципиальные решения Этапа 1:

- **Единый источник схемы** — `Base.metadata` (SQLAlchemy 2, naming
  convention). Alembic сравнивает БД с metadata (`compare_type=True`),
  поэтому autogenerate выдаёт чистые миграции.
- **PostgreSQL 16** — единственная БД приложения. SQLite допускается
  только в изолированных unit-тестах (in-memory, см. `backend/tests/conftest.py`);
  интеграционные проверки всегда идут на настоящий PostgreSQL (CI).
- **Секреты — только через окружение.** Никаких паролей в коде: dev-значения
  живут в `.env` (не коммитится) и в compose по умолчанию. При
  `APP_ENV=production` приложение **отказывается стартовать** без длинного
  `SECRET_KEY`, с dev-паролем в `DATABASE_URL` или с не-PostgreSQL URL
  (проверяется тестами `backend/tests/test_security_config.py`).
- **Frontend не знает адресов backend** — все запросы идут по относительным
  путям; в dev их проксирует Vite (`/health` → backend), в production —
  reverse-proxy.
- **Health** (`GET /health`) возвращает `200` только когда приложение живо
  И PostgreSQL отвечает; иначе `503` с причиной.

## Быстрый старт (Docker Compose — рекомендуемый путь)

Требования: Docker с плагином Compose v2 (или `docker-compose`).

```bash
cp .env.example .env      # при необходимости поменяйте порты/пароль
docker compose up --build
```

Что произойдёт:

1. поднимется `db` — PostgreSQL 16 с volume `pgdata` и healthcheck;
2. `backend` дождётся здоровой БД, применит миграции (`alembic upgrade head`)
   и запустит FastAPI на http://localhost:8000;
3. `frontend` запустит Vite dev-сервер на http://localhost:5173.

Проверка:

```bash
curl http://localhost:8000/health
# {"status":"ok","app":"HR Manager API","version":"0.1.0","environment":"development","database":"ok"}

curl -s http://localhost:5173/health   # frontend-прокси до backend
```

Откройте http://localhost:5173 — страница показывает состояние backend и БД
(зелёные индикаторы) и обновляется каждые 10 секунд. Документация API:
http://localhost:8000/docs.

Остановка: `docker compose down` (данные останутся в volume `pgdata`);
полная очистка: `docker compose down -v`.

## Локальная разработка без Docker (backend)

Требования: Python 3.11+, работающий PostgreSQL 16, Node.js 20+.

```bash
# 1) База данных: создайте пользователя и БД, либо используйте docker:
#    docker compose up -d db

# 2) Backend
cd backend
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

export APP_ENV=development
export DATABASE_URL=postgresql+psycopg://hr_manager:change-me-local-only@localhost:5432/hr_manager
alembic upgrade head                 # применить миграции
uvicorn hr_manager.main:app --reload # http://localhost:8000

# 3) Frontend (в другом терминале)
cd frontend
npm install
npm run dev                          # http://localhost:5173 (прокси на localhost:8000)
```

## Команды проверки

### Backend (`backend/`, после `pip install -e ".[dev]"`)

```bash
ruff check src tests                 # lint
mypy                                 # typecheck
pytest -m "not database"             # unit-тесты (без внешней БД)
pytest -m database                   # интеграционные тесты (нужен PostgreSQL + DATABASE_URL)
pytest                               # всё сразу
```

### Frontend (`frontend/`, после `npm install`)

```bash
npm run typecheck   # tsc
npm run lint        # eslint src
npm test            # vitest run
npm run build       # tsc && vite build → frontend/dist/
```

### Docker Compose

```bash
docker compose config --quiet        # валидность файла
docker compose up -d --build --wait  # поднять стек и дождаться healthcheck'ов
docker compose ps
docker compose logs -f backend       # логи
docker compose down -v               # остановить и удалить volume'ы
```

CI повторяет всё перечисленное в `.github/workflows/ci.yml` (jobs: `backend`,
`frontend`, `compose`) — включая интеграционные тесты с сервисным PostgreSQL
и полный smoke-запуск `docker compose` с проверкой `/health` и frontend-прокси.

## Переменные окружения

Все переменные документированы в [`.env.example`](.env.example). Ключевые:

| Переменная | Назначение |
| --- | --- |
| `APP_ENV` | `development` / `test` / `production` |
| `DATABASE_URL` | URL PostgreSQL для SQLAlchemy (`postgresql+psycopg://…`) |
| `SECRET_KEY` | секрет приложения; обязателен в `production` (≥ 32 символа) |
| `POSTGRES_USER/PASSWORD/DB` | учётные данные для сервиса `db` в compose |
| `POSTGRES_PORT/BACKEND_PORT/FRONTEND_PORT` | наружные порты (только compose) |
| `VITE_PROXY_TARGET` | адрес backend для dev-прокси Vite (только compose) |

`.env` никогда не коммитится (см. `.gitignore`). Для production используйте
секретное хранилище/менеджер секретов, не файл `.env` в репозитории.

## Известные ограничения Этапа 1

- Нет пользователей, авторизации и бизнес-моделей — это Этапы 2+ (см. `ROADMAP.md`).
- В compose используются dev-образы (editable-установка backend, Vite dev-server
  во frontend) — для production нужны отдельные оптимизированные образы и
  reverse-proxy с HTTPS (Этап 7).
- Интеграционные тесты PostgreSQL (`pytest -m database`) требуют живого
  PostgreSQL и не входят в обычный локальный прогон `pytest`.
- SQLite применяется только внутри изолированных unit-тестов (in-memory)
  и явно документирован в `backend/tests/conftest.py`.

## Документы проекта

- [`agents.md`](agents.md) — главный регламент для AI-агентов
- [`PRODUCT_SPEC.md`](PRODUCT_SPEC.md) — актуальное ТЗ
- [`ROADMAP.md`](ROADMAP.md) — этапы разработки
- [`prompts/PHASE_1_PROMPT.md`](prompts/PHASE_1_PROMPT.md) — промпт этапа 1
- [`docs/`](docs/) — архитектурные заметки этапа (см. `docs/phase-1-architecture.md`)
