# Этап 1 — технический каркас: архитектурные решения

Дата: 2026-09-02 · Ветка: `main` (Этап 1). Статус: принято.

## Цель этапа

Запускаемый технический скелет сетевого приложения HR Manager без
бизнес-функциональности: FastAPI + SQLAlchemy 2 + Alembic + PostgreSQL,
React + TypeScript + Vite, Docker Compose, health-check, тесты, CI.

## Принятые решения

| Решение | Обоснование |
| --- | --- |
| Монолит `backend/` + `frontend/` + `infra/` + `docs/` | ТЗ (раздел 10): сначала надёжный монолит; микросервисы не нужны. |
| `src/`-layout Python-пакета (`hr_manager`) | Чистые импорты, editable-установка, единая точка входа `hr_manager.main:app`. |
| PostgreSQL 16 — единственная БД приложения | agents.md/PRODUCT_SPEC. SQLite — только in-memory в изолированных unit-тестах (conftest), задокументировано. |
| URL БД и секреты — только из окружения (`DATABASE_URL`, `SECRET_KEY`, `APP_ENV`) | Правила безопасности agents.md: секреты не в git; `.env.example` без секретов. |
| Валидация конфига на этапе загрузки (pydantic-settings) | `APP_ENV=production` без длинного `SECRET_KEY`, с dev-паролем или не-PostgreSQL URL — отказ при старте; защищено тестами. |
| `GET /health` возвращает 200 только при доступной БД | Критерий приёмки этапа; 503 с причиной при сбое. Health не содержит бизнес-данных. |
| Alembic: metadata приложения — source of truth; `compare_type=True`; первая ревизия `0001` — пустая и безопасная | Любая будущая смена схемы — только миграцией; autogenerate сверяет БД с `Base.metadata`. |
| Frontend ходит на backend по относительным путям; dev-прокси Vite (`/health` → backend) | Прямые обращения браузера к localhost/другим хостам исключены; работает и в compose, и в проде за reverse-proxy. |
| Страница состояния: poll `/health` каждые 10 с, состояния checking/ok/degraded/unreachable | Понятное состояние backend/database — требование этапа. |
| Docker Compose: `db` + `backend` + `frontend`, healthcheck'и, `depends_on: service_healthy`, named volumes | `docker compose up --build` запускает стек; `--wait` дожидается здоровья в CI. |
| CI (GitHub Actions): jobs backend/frontend/compose | Линт+typecheck+тесты+сборка+интеграция с PostgreSQL+smoke всего стека. |

## Какой структуры избегаем

- Старый PyQt6/SQLite-прототип не переносится (решение из README).
- «Фиктивные» endpoint'ы, заглушки бизнес-логики, hard delete — не добавляются.
- Секреты/`.env`/дампы/backup в репозитории — запрещены (.gitignore).

## Что появится на следующих этапах

Этап 2 — пользователи и безопасность; этап 3 — кандидаты; далее по
`ROADMAP.md`. Модели добавляются импортом в `hr_manager.db.schema` —
autogenerate и интеграционный тест синхронизации схемы начнут
контролировать их соответствие миграциям автоматически.
