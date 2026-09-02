# Тесты HR Manager

Единого каталога для всех тестов нет намеренно: тесты живут рядом с кодом,
который они проверяют.

| Где | Что |
|---|---|
| `backend/tests/` | pytest: unit-тесты `/health` и production-guard конфигурации (in-memory SQLite, только для изолированных тестов) + интеграционные тесты против настоящего PostgreSQL (маркер `integration`, переменная `TEST_DATABASE_URL`) |
| `frontend/src/*.test.tsx` | Vitest + Testing Library: состояния статусной страницы |

## Запуск

```bash
# backend
cd backend
pytest -v                                              # unit
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/db \
  pytest -m integration -v                             # против PostgreSQL

# frontend
cd frontend
npm run test
```

Полная инструкция проверок — в корневом `README.md`.
