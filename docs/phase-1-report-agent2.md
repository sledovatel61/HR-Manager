# Отчёт по Этапу 1 — технический каркас (агент №2, независимая реализация)

Дата: 2026-09-02 · Ветка: `arena/01a060e3-hr-manager` (локальный коммит
`eff8163` и последующие)

> ⚠️ На удалённой ветке уже опубликована реализация агента №1 (коммиты
> `f9f11ac`, `09452ef`). Этот отчёт описывает **независимую** реализацию
> агента №2, собранную с нуля и проверенную сквозными тестами. Решение о том,
> какой вариант принять за основной, принимает оркестратор; работа агента №1
> не перезаписывалась (см. `docs/ARCHITECTURE.md` этого варианта).

## Изменённые файлы

```
backend/
  app/__init__.py            версия приложения
  app/config.py              настройки из env; production-guard (fail-fast)
  app/db.py                  engine + probe БД (SELECT 1, без исключений наружу)
  app/main.py                фабрика приложения (инъекция settings/engine)
  app/routers/health.py      GET /health: 200 только при доступной БД, иначе 503
  app/schemas.py             Pydantic-схемы HealthResponse/DatabaseHealth
  alembic.ini, alembic/env.py, alembic/script.py.mako
  alembic/versions/0001_baseline.py   CREATE EXTENSION IF NOT EXISTS pgcrypto
  tests/conftest.py          in-memory SQLite только для unit-тестов (StaticPool)
  tests/test_health.py       unit + integration (реальный PostgreSQL)
  tests/test_config.py       запрет небезопасной production-конфигурации
  requirements.txt, requirements-dev.txt   pinned-зависимости (==)
  pyproject.toml             метаданные, ruff/mypy/pytest-конфигурация
  Dockerfile, .dockerignore  образ: alembic upgrade head + uvicorn
frontend/
  src/App.tsx, src/api.ts, src/types.ts, src/styles.css, src/main.tsx
  src/App.test.tsx, src/setupTests.ts     Vitest + Testing Library (4 теста)
  package.json, package-lock.json, tsconfig*.json, vite.config.ts
  eslint.config.js (flat config)
  Dockerfile, nginx.conf, .dockerignore, .env.example
infra/
  docker-compose.yml         db(PG16+volume+healthcheck) + backend + frontend
  compose.prod.yml           production overlay: секреты только из env, без портов
  scripts/check_env.sh       preflight production-секретов
docs/ARCHITECTURE.md         решения, стратегия тестов, ограничения
tests/README.md              карта тестов (требуемый каталог tests/)
.github/workflows/ci.yml     4 job: backend, frontend, integration, stack
.env.example, .gitignore, Makefile, README.md (переписан)
```

## Архитектурные решения

1. **Монолит**: `backend/` (FastAPI + SQLAlchemy 2 + Alembic + Pydantic v2,
   Python 3.12+) + `frontend/` (React 18 + TypeScript strict + Vite).
2. **PostgreSQL — единственная БД**; конфигурация запрещает SQLite вне
   `APP_ENV=test` (только изолированные unit-тесты, задокументировано).
3. **Секреты только из переменных окружения** (`.env` не читается автоматически).
   В `APP_ENV=production` приложение **отказывается стартовать**, если:
   `SECRET_KEY` отсутствует/короче 32 символов/дефолтный, `DATABASE_URL`
   без пароля или с dev-учёткой, включён `APP_DEBUG`. Отдельный тест это
   фиксирует.
4. **`GET /health`** проверяет БД (`SELECT 1`, таймаут 3 с): `200
   {"status":"ok"}` только при доступной БД, иначе `503 {"status":"degraded"}`
   — без краха и без утечки деталей подключения.
5. **Первая миграция — безопасная заготовка**: включение `pgcrypto`
   (trusted-extension, idempotent, reversible). Схемы пользователей/кандидатов
   появятся на своих этапах вместе с кодом.
6. **Единый origin для frontend**: nginx отдаёт SPA и проксирует `/api/*` →
   backend (в dev — Vite-прокси). Будущие same-origin сессии работают без CORS.
7. **CI — 4 независимые задачи**: backend (ruff/mypy/pytest), frontend
   (eslint/tsc/vitest/build), integration (pytest против PostgreSQL 16 в
   service container), stack (полный `docker compose up --build`, health
   200, прокси, деградация 503 при остановке БД).

## Команды проверки и полный результат

### Backend (Python 3.11 venv в песочнице; в CI/Docker — 3.12, pinned-пакеты установлены в чистый venv без ошибок)

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt
ruff check .                       # All checks passed!
ruff format --check app tests alembic   # 12 files already formatted
mypy app tests                     # Success: no issues found in 10 source files
pytest -q                          # 12 passed
TEST_DATABASE_URL=postgresql+psycopg://hr_manager:hr_manager_dev_password@127.0.0.1:5432/hr_manager_test \
  pytest -m integration -v         # 2 passed (против реального PostgreSQL 18.4)
```

Интеграционные проверки выполнены против **реального PostgreSQL** (18.4,
поднят из бинарников в песочнице). Дополнительно вручную проверено:
- `alembic upgrade head` → ревизия 0001, расширение `pgcrypto` создано;
- `alembic downgrade base` → `upgrade head` — миграция обратима;
- живой запуск `uvicorn app.main:app`:
  - БД доступна → `GET /health` = **HTTP 200**, `{"status":"ok", ... "latency_ms":8}`;
  - БД остановлена → **HTTP 503**, `{"status":"degraded", ...}` — приложение не падает;
  - БД снова запущена → снова 200.
- импорт приложения с небезопасной production-конфигурацией падает с явной
  ошибкой (fail-fast), с полной конфигурацией — стартует.

### Frontend (Node 22 в песочнице; в CI — Node 20)

```bash
cd frontend && npm ci
npm run lint         # ok (flat eslint, typescript-eslint)
npm run typecheck    # ok (tsc -b, strict)
npm run test         # Test Files 1 passed; Tests 4 passed
npm run build        # ok (vite build → dist: 145.64 kB JS, 1.97 kB CSS)
```

Живой запуск: Vite dev-сервер (0.0.0.0:5173) + прокси `/api` → uvicorn
(0.0.0.0:8000) → PostgreSQL: страница отдаётся, `/api/health` возвращает
`{"status":"ok",...}` — статусная страница показывает «Система работает».

### Инфраструктура и CI

- `infra/docker-compose.yml`, `infra/compose.prod.yml`, `.github/workflows/ci.yml`
  — синтаксически корректный YAML (проверено парсером; `!override` — штатный
  compose-тег);
- `infra/scripts/check_env.sh` (bash -n ok): без окружения — ошибка (exit 1),
  с полной production-конфигурацией — `ok` (exit 0);
- `make up/down/logs/ps/check/prod-preflight` — dry-run корректен;
- `docker compose up --build` в песочнице не выполнялся (Docker недоступен):
  эквивалентная связка проверена вживую (см. выше), полный подъём стека
  автоматически проверяет job `stack` в CI на GitHub-раннерах.

## Известные ограничения

- Docker в песочнице недоступен — `docker compose up --build` проверяется
  задачей `stack` в CI (и локально на машине с Docker по README).
- В песочнице Python 3.11: код написан под 3.12+ (CI и Docker используют
  3.12); тесты прогнаны на 3.11 — синтаксис совместим.
- Предупреждение Starlette о deprecation `httpx` для TestClient исходит из
  самой библиотеки (starlette 1.6) и на результат не влияет.
- Этап 1 не содержит бизнес-функций, авторизации и rate limiting — по
  определению этапа (см. ROADMAP).
