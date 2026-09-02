# Архитектура каркаса (Этап 1)

Документ фиксирует решения, принятые на Этапе 1 (см. `prompts/PHASE_1_PROMPT.md`).
Бизнес-функциональность кандидатов намеренно отсутствует — это технический каркас.

## Структура репозитория

```
backend/            FastAPI + SQLAlchemy 2 + Alembic (Python 3.12+)
  app/                код приложения (config, db, api/routes, schemas)
  alembic/            миграции; 0001_baseline — безопасная стартовая ревизия
  requirements*.txt   точные пины (генерируются из requirements*.src.txt)
frontend/           React 19 + TypeScript + Vite (Node 22)
  src/                страница состояния + API-клиент /health
  nginx.conf          раздача статики и проксирование API в Docker
infra/              эксплуатация: PostgreSQL, будущие backup/proxy (Этап 7)
tests/              tests/backend — pytest; tests/e2e — зарезервировано
docs/               этот документ и отчёты этапов
.github/workflows/  CI: backend / frontend / compose-smoke
docker-compose.yml  корневой, чтобы `docker compose up --build` работал без флагов
```

## Ключевые решения

1. **Same-origin вместо CORS.** Браузер никогда не ходит на backend напрямую:
   dev-сервер Vite и nginx в Docker проксируют `/health` и `/api/*`. CORS-
   middleware не добавляли — меньше поверхность атаки и конфигурации.
2. **Health-check честный.** `GET /health` возвращает 200 только когда
   выполняется `SELECT 1` к PostgreSQL; иначе 503 с `status: degraded`.
   Тексты ошибок и строки подключения наружу не отдаются. В compose healthcheck
   backend завязан на этот endpoint, frontend ждёт `service_healthy`.
3. **Конфигурация падает громко.** `Settings` (pydantic-settings) при
   `APP_ENV=production` отказывается стартовать без `SECRET_KEY`
   (мин. 32 символа, заглушки запрещены) и без явного `DATABASE_URL`;
   DEBUG запрещён. SQLite отклоняется везде, кроме `APP_ENV=test`
   (изолированные unit-тесты — см. `tests/backend/README.md`).
4. **Baseline-миграция.** `0001_baseline` не создаёт таблиц: бизнес-схема —
   Этапы 1–2 roadmap. Ревизия проверяет конвейер миграций и штампует
   `alembic_version`; её upgrade/downgrade покрыт тестом. Шаблон
   автогенерации (`script.py.mako`) по умолчанию поднимает
   `NotImplementedError` вместо пустого `pass`, чтобы пустая миграция
   не попала в базу незаметно.
5. **Без ORM-моделей пока.** `alembic/env.py` работает с
   `target_metadata = None`; модели и автогенерация появятся в Этапе 2.
6. **Без UI-библиотеки пока.** `agents.md` требует современную компонентную
   библиотеку для интерфейса; на каркасном этапе страница состояния — чистый
   CSS. Выбор библиотеки (MUI/Ant Design/…) — отдельное решение Этапа 3.
7. **Контейнеры непривилегированные и предсказуемые.** Backend — python:3.12-slim,
   user `app`, entrypoint применяет `alembic upgrade head`
   (`SKIP_MIGRATIONS=1` — для отдельного release-процесса). Образы пинуются
   по major-линии (`postgres:16-alpine`, `nginx:1.29-alpine`, `node:22-alpine`).

## Зависимости

- Python-пакеты: точные пины всех транзитивных зависимостей в
  `backend/requirements.txt` и `backend/requirements-dev.txt`, сгенерированы
  из `requirements*.src.txt` (`uv pip compile`). Туда же — диапазоны и причины.
- Node-пакеты: точные версии в `frontend/package.json` + `package-lock.json`
  (`npm ci` в CI и Docker).
- TestClient FastAPI использует `httpx2` (starlette≥1.5 объявила `httpx`
  в TestClient устаревшим).

## Порты и биндинги

| Сервис   | Внутри compose | Опубликован по умолчанию           |
|----------|----------------|-------------------------------------|
| db       | 5432           | `127.0.0.1:5432` (`DB_BIND/POSTGRES_PORT`) |
| backend  | 8000           | `127.0.0.1:8000` (`BACKEND_BIND/BACKEND_PORT`) |
| frontend | 80             | `127.0.0.1:8080` (`FRONTEND_BIND/FRONTEND_PORT`) |

## Что осознанно НЕ сделано (перенос на следующие этапы)

- Аутентификация, пользователи, роли, rate limiting — Этап 1 roadmap.
- ORM-модели кандидатов, аудит, бизнес-API — Этап 2+.
- Backup/restore, HTTPS-прокси, мониторинг — Этап 7.
- E2E-тесты — вместе с бизнес-функциональностью (`tests/e2e` зарезервировано).

## Известные ограничения каркаса

- Проверка `docker compose up --build` автоматизирована в CI (job
  `compose-smoke`): в локальной песочнице разработки без Docker стек
  собирается только в CI.
- Health-check БД ограничен `statement_timeout`/`connect_timeout` из
  настроек (по умолчанию 3 с).
