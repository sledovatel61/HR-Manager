# HR Manager

Сетевая система для командного подбора персонала.

## Цель

Несколько HR-менеджеров работают с единой базой кандидатов через браузер. Каждый видит свою рабочую очередь, но при необходимости может найти и принять кандидата коллеги. Руководитель видит общую картину, историю действий и показатели каждого сотрудника.

## Статус

**Этап 1 завершён**: запускаемый технический каркас — FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL 16, React + TypeScript + Vite, Docker Compose, health-check, тесты и CI. Бизнес-функциональности кандидатов пока нет (см. [ROADMAP.md](ROADMAP.md)).

Архитектурные решения — в [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), отчёт об этапе — в [docs/reports/PHASE_1_REPORT.md](docs/reports/PHASE_1_REPORT.md).

## Быстрый старт (Docker)

Требования: Docker 24+ с Compose v2.

```bash
cp .env.example .env      # примеры значений для локальной разработки, без секретов
docker compose up --build
```

- Frontend: http://localhost:8080 — страница состояния backend и БД;
- Backend API: http://localhost:8000 — `/health`, интерактивная схема `/docs`;
- PostgreSQL: `127.0.0.1:5432` (`docker compose exec db psql -U hr_manager -d hr_manager`).

Остановка: `docker compose down`. Полный сброс вместе с данными: `docker compose down -v`.

## Локальная разработка без Docker

Требования: Python 3.12+, Node 22+, PostgreSQL 16 (например, `docker compose up db`).

### Backend

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements-dev.txt
cd backend
.venv/bin/alembic upgrade head            # применить миграции (нужна доступная БД)
.venv/bin/uvicorn app.main:app --reload   # http://127.0.0.1:8000
```

URL базы и прочее — через `.env` в каталоге `backend/` или переменные окружения (см. `.env.example`). По умолчанию (development) используется `postgresql+psycopg://hr_manager@localhost:5432/hr_manager` без пароля.

### Frontend

```bash
cd frontend
npm ci
npm run dev     # http://127.0.0.1:5173, /health и /api/* проксируются на backend (VITE_BACKEND_URL, по умолчанию http://localhost:8000)
```

## Команды проверки

Используются в CI (`.github/workflows/ci.yml`) и локально.

### Backend (Python)

```bash
cd backend
.venv/bin/python -m pytest        # тесты (tests/backend)
.venv/bin/ruff check ..           # lint (backend + tests/backend из корня: ruff check backend tests/backend)
.venv/bin/ruff format --check .   # формат
.venv/bin/mypy                    # typecheck
```

Из корня репозитория: `backend/.venv/bin/ruff check backend tests/backend`.

### Frontend (Node)

```bash
cd frontend
npm run lint         # ESLint (flat config)
npm run typecheck    # tsc -b (strict)
npm run test         # Vitest (jsdom)
npm run build        # tsc + production-сборка Vite
```

### Миграции

```bash
cd backend
DATABASE_URL=... .venv/bin/alembic upgrade head     # применить
DATABASE_URL=... .venv/bin/alembic downgrade -1     # откатить последнюю
DATABASE_URL=... .venv/bin/alembic revision -m "..."  # новая миграция (заполните upgrade/downgrade)
```

В Docker миграции применяет entrypoint backend при старте (`SKIP_MIGRATIONS=1` отключает).

## Зависимости

- Python: пины транзитивных зависимостей в `backend/requirements.txt` / `requirements-dev.txt`. Источники диапазонов — `requirements*.src.txt`; пересборка: `uv pip compile backend/requirements.src.txt --python-version 3.12 -o backend/requirements.txt`.
- Node: точные версии в `frontend/package.json`, lock-файл `frontend/package-lock.json`.

## Безопасность

- В репозитории нет секретов и персональных данных: `.env`, дампы, backup и логи в `.gitignore`.
- Production не стартует без явного `SECRET_KEY` (мин. 32 символа) и `DATABASE_URL`; `DEBUG` запрещён; SQLite отклоняется везде, кроме изолированных unit-тестов — проверяется тестами (`tests/backend/test_config_security.py`).
- В production нельзя использовать общую ссылку на SQLite-файл или хранить пароли в открытом виде. Production-развёртывание должно использовать HTTPS, PostgreSQL, хеширование паролей, роли, аудит, ограничение сессий и проверенные backup/restore-процедуры.
- `/health` публичен и не раскрывает внутренние детали (только status, database, version, checked_at).

## Документы проекта

- [`agents.md`](agents.md) — главный регламент для AI-агентов;
- [`PRODUCT_SPEC.md`](PRODUCT_SPEC.md) — актуальное ТЗ;
- [`ROADMAP.md`](ROADMAP.md) — этапы разработки;
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — решения каркаса;
- [`tests/README.md`](tests/README.md) — политика тестов (в т.ч. про SQLite);
- [`prompts/PHASE_1_PROMPT.md`](prompts/PHASE_1_PROMPT.md) — промпт первого этапа.
