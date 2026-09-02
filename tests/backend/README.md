# tests/backend — модульные тесты backend

Запуск: `cd backend && python -m pytest` (конфигурация —
`backend/pyproject.toml`).

## Явно задокументированное исключение про SQLite

Регламент (`agents.md`, `prompts/PHASE_1_PROMPT.md`) запрещает SQLite как
БД приложения. Здесь SQLite используется **только** как стенд для двух
изолированных unit-тестов, когда само приложение запущено с
`APP_ENV=test`:

1. `test_health.py` — ветка «БД доступна»: in-memory SQLite подменяет
   живую БД, потому что `SELECT 1` не требует схемы.
2. `test_migrations.py` — проверка применения/отката baseline-ревизии
   на временном файле, чтобы тест не требовал запущенного PostgreSQL.

Валидация поведения против настоящего PostgreSQL выполняется
smoke-тестом Docker Compose в CI (`.github/workflows/ci.yml`) и через
`docker compose up` локально. Дополнительную защиту даёт само
приложение: `Settings` отклоняет `sqlite`-URL при любом `APP_ENV`,
кроме `test` (см. `test_config_security.py`).
