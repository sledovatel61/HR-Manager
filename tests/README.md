# Тесты HR Manager

Единого каталога для всех тестов нет намеренно: тесты живут рядом с кодом,
который они проверяют.

| Где | Что |
|---|---|
| `backend/tests/test_health.py` | health 200/503: unit + integration (реальный PostgreSQL) |
| `backend/tests/test_config.py` | production-guard: приём полной конфигурации, отказ небезопасной |
| `backend/tests/test_lifecycle.py` | lifespan закрывает пул соединений |
| `backend/tests/test_production_overlay.py` | статические проверки Compose dev/prod (порты, секреты) |
| `backend/tests/test_migrations.py` | integration: Alembic upgrade→downgrade→upgrade, идемпотентность |
| `backend/tests/test_auth.py` | unit: вход/выход, сессии, CSRF, блокировка, rate limit, аудит |
| `backend/tests/test_users_admin.py` | unit: роли/RBAC, админ-управление пользователями, аудит-фильтры |
| `backend/tests/test_security.py` | unit: Argon2id, политика паролей, bootstrap администратора |
| `backend/tests/test_integration_identity.py` | integration (PostgreSQL): вход, RBAC, блокировка, UUID/timestamptz |
| `frontend/src/App.test.tsx` | smoke: оболочка приложения и заголовок |
| `frontend/src/App.auth.test.tsx` | Vitest + Testing Library: экран входа, успешный вход, ошибка, сессия |
| `frontend/src/api.test.ts` | API-клиент: cookies/credentials, CSRF-заголовок, обработка ошибок |

## Запуск

```bash
# backend unit (in-memory SQLite, APP_ENV=test; PostgreSQL в dev/prod)
cd backend
pytest -v

# backend integration против настоящего PostgreSQL (схема должна быть применена)
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/db \
  sh -c 'alembic upgrade head && pytest -m integration -v'

# frontend
cd frontend && npm run test
```

Полная инструкция проверок — в корневом `README.md` (и `make check`).
