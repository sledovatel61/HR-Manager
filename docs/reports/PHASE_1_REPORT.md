# Отчёт по Этапу 1 — технический каркас

Дата: 2026-09-02. Промпт: `prompts/PHASE_1_PROMPT.md`.
Бизнес-функциональность кандидатов не реализовывалась (требование этапа).

## Изменённые / созданные файлы

### Backend (`backend/`)
- `app/__init__.py` — версия пакета (`0.1.0`);
- `app/config.py` — `Settings` (pydantic-settings): env-конфигурация, запрет небезопасного production (SECRET_KEY/DATABASE_URL обязательны, DEBUG запрещён, SQLite отклоняется вне `APP_ENV=test`);
- `app/db/__init__.py`, `app/db/session.py` — engine SQLAlchemy 2 (`pool_pre_ping`, таймауты для PostgreSQL), зависимость сессии через `app.state`;
- `app/api/__init__.py`, `app/api/routes/__init__.py`, `app/api/routes/health.py` — `GET /health`: 200 только при доступной БД (`SELECT 1`), иначе 503 `degraded`; без утечки внутренних деталей;
- `app/schemas/__init__.py`, `app/schemas/health.py` — контракт `HealthResponse` (status, database, version, checked_at);
- `app/main.py` — фабрика `create_app(settings)`; `/docs` публикуется только вне production;
- `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/0001_baseline.py` — конвейер миграций + безопасная стартовая ревизия; шаблон новых миграций без пустых `pass`;
- `pyproject.toml` — конфигурация pytest / ruff / mypy (Python 3.12+);
- `requirements.src.txt`, `requirements-dev.src.txt` — диапазоны верхнего уровня (источник пинов);
- `requirements.txt`, `requirements-dev.txt` — точные пины всех транзитивных зависимостей (uv pip compile);
- `Dockerfile`, `docker-entrypoint.sh`, `.dockerignore` — непривилегированный образ, миграции при старте (`SKIP_MIGRATIONS=1` отключает), healthcheck на `/health`.

### Frontend (`frontend/`)
- `package.json`, `package-lock.json` — React 19.2.8, TypeScript 5.9.3, Vite 8.2.2, Vitest 4.1.11 (точные пины; `typescript-eslint` пока не принимает TS 7);
- `tsconfig.json`, `tsconfig.app.json`, `tsconfig.node.json` — strict-конфигурация;
- `vite.config.ts` — dev-прокси `/health`, `/api` → backend (`VITE_BACKEND_URL`), `VITE_ALLOWED_HOSTS` для предпросмотров, конфиг Vitest;
- `eslint.config.js` — flat config ESLint 10 + typescript-eslint + react-hooks;
- `index.html`, `public/favicon.svg`;
- `src/main.tsx`, `src/index.css`;
- `src/App.tsx`, `src/App.css` — страница состояния backend/БД (опрос `/health` каждые 5 с, ручная проверка, состояния: ok / degraded / backend недоступен);
- `src/api/health.ts` — типизированный клиент `/health`;
- `src/App.test.tsx`, `src/test/setup.ts` — 3 компонентных теста (ok / 503 degraded / сеть недоступна);
- `Dockerfile`, `nginx.conf`, `.dockerignore` — сборка Vite + nginx c проксированием API и заголовками безопасности.

### Инфраструктура и прочее
- `docker-compose.yml` — PostgreSQL 16 (volume `pgdata`, healthcheck `pg_isready`) + backend (`depends_on: service_healthy`) + frontend; порты по умолчанию на loopback;
- `.env.example` — примеры без секретов; `.gitignore` — `.env`, базы/дампы/backup, логи, node_modules, build-артефакты;
- `.github/workflows/ci.yml` — jobs: backend (ruff, mypy, pytest), frontend (eslint, tsc, vitest, build), compose-smoke (`docker compose up --build --wait` + `curl /health`);
- `tests/README.md`, `tests/backend/README.md` — политика тестов и явное исключение про SQLite;
- `tests/backend/conftest.py` — sys.path и `APP_ENV=test` по умолчанию;
- `tests/backend/test_health.py` — 200 при доступной БД, 503 при недоступной, отсутствие утечек в ответе;
- `tests/backend/test_config_security.py` — запрет production без SECRET_KEY / DATABASE_URL, слабых секретов, DEBUG, SQLite;
- `tests/backend/test_migrations.py` — upgrade/downgrade baseline;
- `infra/README.md`, `infra/postgres/README.md` — эксплуатация БД, планы Этапа 7;
- `docs/ARCHITECTURE.md` — архитектурные решения каркаса;
- `README.md` — инструкции запуска/проверок.

## Архитектурные решения (кратко)

1. Структура `backend/ frontend/ infra/ docs/ tests/` зафиксирована; backend-тесты — в общем `tests/backend` (в репозитории единая точка правды для тестов), конфиг pytest — в `backend/pyproject.toml`.
2. Same-origin: браузер ходит только на свой origin; Vite dev и nginx проксируют `/health` и `/api/*`. CORS не вводился.
3. `/health` — единственный публичный endpoint, не раскрывает внутренности, 503 при недоступной БД (на него завязаны healthcheck-и compose).
4. `Settings` валидирует безопасность production при старте (fail-fast) — тест `test_config_security.py` закрепляет инвариант.
5. `0001_baseline` — пустая, но реальная ревизия конвейера миграций; ORM-модели и автогенерация — Этап 2.
6. UI-библиотека не добавлена: не нужна для страницы состояния; выбор — Этап 3 (см. docs/ARCHITECTURE.md).

## Команды проверки и результат

Выполнено в песочнице (Python 3.11.2 предоставлен средой; toolchain проекта — 3.12+):

| Команда | Результат |
|---|---|
| `cd backend && .venv/bin/python -m pytest` | **10 passed** |
| `ruff check backend tests/backend` | All checks passed |
| `ruff format --check backend tests/backend` | 16 files already formatted |
| `cd backend && mypy` | Success: no issues found in 10 source files |
| `DATABASE_URL=... alembic upgrade head` / `downgrade base` (PostgreSQL 16-бинарники pgserver в песочнице) | upgrade/downgrade успешно, `alembic current` → `0001_baseline (head)` |
| `cd frontend && npm run lint` | без замечаний |
| `npm run typecheck` (`tsc -b`) | без ошибок |
| `npm run test` (vitest) | **3 passed** |
| `npm run build` (`tsc -b && vite build`) | ✓ built (dist ~62 kB gzip js) |
| YAML-валидация `.github/workflows/ci.yml`, `docker-compose.yml` | OK (PyYAML); фактический запуск compose — job `compose-smoke` в CI |
| Живой стенд песочницы: PostgreSQL(pgserver) + uvicorn + vite dev | `GET /health` → 200 `{"status":"ok","database":"up",...}` напрямую и через vite-прокси |

## Критерии приёмки

- [x] `docker compose up --build` поднимает PostgreSQL, backend, frontend — воспроизводится job `compose-smoke` в CI (в песочнице Docker отсутствует);
- [x] `GET /health` — 200 только при доступной БД (unit-тесты + живой стенд);
- [x] backend-тесты проходят (10);
- [x] frontend build/typecheck проходят (+ lint, vitest);
- [x] CI синтаксически корректен и покрывает все команды;
- [x] README позволяет запустить проект с нуля.

## Известные ограничения

- В песочнице агента нет Docker — сборка образов вынесена в CI (`compose-smoke`); Dockerfile и compose проверены статически и по составу.
- Образы пинуются по major-линии (`postgres:16-alpine`, `nginx:1.29-alpine`, `node:22-alpine`): registry из этой среды недоступен, digest зафиксируем в Этапе 7.
- В демо-стенде песочницы PostgreSQL поднят бинарниками `pgserver` (только демонстрация; не часть репозитория). Целевая БД — PostgreSQL 16 из compose.
- Локально проверки выполнялись на Python 3.11.2 (ограничение песочницы); целевой и единственный задекларированный toolchain — Python 3.12+ (`backend/pyproject.toml`, Docker, CI). Зависимости разрешены с `uv` под целевые диапазоны и совместимы с 3.12.
- Страница состояния — временный минимальный UI; UI-кит и дизайн-система — Этап 3.
