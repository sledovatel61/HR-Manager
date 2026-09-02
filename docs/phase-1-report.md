# Отчёт по Этапу 1 — технический каркас HR Manager

Дата: 2026-09-02 · Ветка: `arena/01a060e3-hr-manager`

## Изменённые файлы

Полный список файлов этапа — см. `git ls-files` (корень репозитория). Ключевое:

- `compose.yaml` — стек: `db` (PostgreSQL 16, volume + healthcheck), `backend`
  (FastAPI, миграции при старте), `frontend` (Vite). Всё — `docker compose up --build`.
- `.env.example`, `.gitignore` — шаблон окружения без секретов, ignore-правила
  (`.env`, базы, backup, логи, node_modules, build artifacts и т.д.).
- `backend/` — FastAPI + SQLAlchemy 2 + Alembic (src-layout, пакет `hr_manager`):
  `api/health.py` (GET /health), `core/` (config, db engine, session, logging),
  `db/` (Base с naming convention, точка регистрации будущих моделей),
  `alembic/versions/0001_initial_empty.py` — безопасная стартовая миграция;
  `pyproject.toml` (зависимости, ruff, mypy, pytest с маркером `database`);
  `tests/` — unit-тесты health и безопасности конфигурации + интеграционные
  тесты с настоящим PostgreSQL.
- `frontend/` — React + TypeScript + Vite: страница состояния
  «Backend API / База данных (PostgreSQL)» с опросом `/health` каждые 10 с,
  состояниями checking/ok/degraded/unreachable, dev-прокси `/health` → backend;
  vitest + Testing Library, eslint, tsc.
- `.github/workflows/ci.yml` — три job: backend (ruff/mypy/pytest с Postgres-сервисом),
  frontend (tsc/eslint/vitest/build), compose smoke (`up --build` + проверка `/health` и прокси).
- `docs/` (`phase-1-architecture.md`, этот отчёт), `infra/` и `tests/` — структура каталогов.
- `README.md` — переписан: запуск, команды проверки, переменные окружения, ограничения.

## Архитектурные решения

1. **Монолит-каркас** `backend/` + `frontend/` + `infra/` (+ `docs/`, `tests/`) без микросервисов.
2. **PostgreSQL — единственная БД.** SQLite допускается только in-memory в
   изолированных unit-тестах (`backend/tests/conftest.py` — задокументировано);
   интеграционные проверки идут на настоящий PostgreSQL.
3. **Секреты только через окружение**; `production`-режим валидируется при загрузке
   конфига: обязателен `SECRET_KEY` ≥ 32 символов, запрещён dev-пароль из compose
   в `DATABASE_URL`, запрещён не-PostgreSQL backend БД.
4. **Metadata приложения — source of truth схемы**; Alembic `compare_type=True`,
   миграции применяются автоматически при старте backend в compose.
5. **Frontend без знания адресов backend**: относительный путь `/health`, проксируется
   Vite в dev, reverse-proxy в production.
6. **GET /health → 200 только при доступной БД**, иначе 503 с причиной; никаких
   фиктивных эндпоинтов и hard delete.
7. **CI делится на независимые проверки** (backend, frontend, compose), что позволяет
   быстро локализовать поломку.

## Команды проверки и полный результат

### Backend (локально, Python 3.11 venv)

```bash
cd backend
pip install -e ".[dev]"
ruff check src tests        # Все проверки пройдены
mypy                        # Success: no issues found in 17 source files
pytest -m "not database"    # 10 passed   (unit: health, безопасность конфигурации)
```

Интеграционные тесты с **настоящим PostgreSQL 16** были выполнены локально:
PostgreSQL 16.2 поднят в песочнице (pgserver), подключение через unix-socket:

```bash
APP_ENV=test DATABASE_URL='postgresql+psycopg://postgres@/postgres?host=/tmp/hr_pgdata' \
    pytest -m database       # 2 passed (диалект PG; alembic upgrade/downgrade, синхронизация схемы)

pytest                        # 12 passed суммарно
```

### Frontend (локально)

```bash
cd frontend && npm install
npm run typecheck   # ok
npm run lint        # ok
npm test            # 7 passed (classifyHealth, fetchHealth, состояние страницы)
npm run build       # ok (tsc && vite build → dist)
```

### Живой запуск стека в песочнице (без Docker)

Docker в песочнице недоступен, поэтому полный стек поднят эквивалентно:
PostgreSQL 16 (pgserver) + `uvicorn hr_manager.main:app` + `vite dev` с прокси:

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok","app":"HR Manager API","version":"0.1.0","environment":"development","database":"ok"}
curl http://127.0.0.1:5173/health   # через dev-прокси frontend — тот же 200
# OpenAPI содержит только "/health" (фиктивных эндпоинтов нет)
# alembic current → 0001 (head)
```

### Docker Compose

Синтаксис `compose.yaml` и CI-файла проверен (YAML валиден). Сам `docker compose up
--build` в песочнице выполнить нельзя (нет docker); этот сценарий заложен в
CI-джобу `compose` и должен выполниться на GitHub Actions, когда файл workflow
будет запушен (см. ограничения).

## Известные ограничения

1. **CI-файл не запушен на GitHub**: GitHub App `arena-ai-coding-agent[bot]` не имеет
   разрешения **Workflows**, поэтому push, содержащий создание
   `.github/workflows/ci.yml`, отклоняется. Файл закоммичен локально поверх
   запушенной ветки и лежит в рабочем дереве; после выдачи разрешения его нужно
   запушить одной командой `git push`. Тогда CI (включая docker-compose smoke)
   выполнится автоматически.
2. Docker Compose-сценарий не выполнен в этой песочнице (нет docker); проверен
   синтаксис файла, healthcheck-логика и состав сервисов; исполнение гарантируется
   CI-джобой `compose` после п.1.
3. Бизнес-функциональность (пользователи, кандидаты, роли, аудит) намеренно не
   реализована — Этапы 2+ по `ROADMAP.md`.
4. В compose используются dev-образы (editable backend, Vite dev-server);
   production-образы и reverse-proxy — Этап 7.
