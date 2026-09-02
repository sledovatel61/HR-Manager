# Архитектура HR Manager — этап 1 (технический каркас)

Этот документ фиксирует структуру репозитория и ключевые решения, принятые на
этапе 1. Бизнес-функциональность (кандидаты, пользователи, аналитика) на этом
этапе намеренно не реализована.

## Структура репозитория

```
HR-Manager/
├── backend/                  # FastAPI + SQLAlchemy 2 + Alembic (Python 3.12+)
│   ├── app/
│   │   ├── config.py         # настройки из переменных окружения + production-guard
│   │   ├── db.py             # engine + проверка доступности БД
│   │   ├── main.py           # фабрика приложения, lifespan, GET /health
│   │   ├── schemas.py        # Pydantic-схемы ответов
│   │   └── routers/health.py # health endpoint
│   ├── alembic/              # миграции БД (заготовка: включение pgcrypto)
│   ├── tests/                # unit (SQLite in-memory) + integration (PostgreSQL)
│   ├── requirements*.txt     # зафиксированные зависимости (lock-файлы)
│   └── Dockerfile            # образ: миграции + uvicorn
├── frontend/                 # React + TypeScript + Vite
│   ├── src/                  # статусная страница этапа 1
│   ├── nginx.conf            # SPA fallback + прокси /api → backend
│   └── Dockerfile            # сборка Node → nginx
├── infra/
│   ├── docker-compose.yml    # локальная среда (db + backend + frontend)
│   ├── compose.prod.yml      # production overlay без dev-значений
│   └── scripts/check_env.sh  # preflight проверка production-секретов
├── docs/                     # проектная документация
├── .github/workflows/ci.yml  # CI: lint, typecheck, тесты, build
├── prompts/                  # промпты этапов (вход для агентов)
├── agents.md / PRODUCT_SPEC.md / ROADMAP.md
└── README.md
```

## Ключевые решения

### Backend

- **Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2** — целевой стек
  из `agents.md`. DRF-стиля нет: доступ к БД — через SQLAlchemy напрямую.
- **Конфигурация только из переменных окружения** (`app/config.py`). Файл
  `.env` автоматически не читается: секреты поставляет среда запуска
  (Docker Compose / CI / process supervisor), что исключает случайный коммит.
- **Production guard в конфигурации.** При `APP_ENV=production` приложение
  отказывается стартовать, если `SECRET_KEY` отсутствует/короткий/дефолтный,
  `DATABASE_URL` использует dev-учётку или не содержит пароля, либо включён
  debug. Тест на это поведение — `backend/tests/test_config.py`.
- **`GET /health`** проверяет связность с БД (`SELECT 1`) и возвращает
  `200 {"status": "ok"}` только при доступной БД; иначе `503
  {"status": "degraded"}` с детализацией по компоненту. В теле ответа нет
  учётных данных и деталей подключения.

### База данных

- **PostgreSQL 16** — единственная production-БД, как требует ТЗ.
- **Первая миграция — безопасная заготовка**: `CREATE EXTENSION IF NOT EXISTS
  pgcrypto` (idempotent, reversible, trusted-extension, superuser не нужен).
  UUID-генерация для пользователей/кандидатов на следующих этапах будет
  использовать `gen_random_uuid()`. Никаких таблиц этапа 1 не создаётся —
  «пустая» миграция-заготовка под реальную схему появилась бы только вместе
  с кодом, который её использует.
- **SQLite** допускается **только** для изолированных unit-тестов
  (`APP_ENV=test`, in-memory, без файлов). Конфигурация запрещает SQLite во
  всех остальных средах. Это задокументировано здесь и в README.

### Frontend

- **React 18 + TypeScript (strict) + Vite**. Отдельная страница показывает
  понятное состояние «backend / база данных» с ручной перепроверкой
  (`GET /health` через `/api`).
- **Единый origin**: nginx отдаёт SPA и проксирует `/api/*` на backend —
  браузер не обращается к backend напрямую, CORS не требуется, а будущие
  same-origin cookies для сессий будут работать без настройки.
- **nginx работает от non-root пользователя**: используется образ
  `nginxinc/nginx-unprivileged` (слушает порт 8080 внутри контейнера).
  nginx добавляет базовые security headers: `X-Content-Type-Options`,
  `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy` и
  `Content-Security-Policy`.
- Тесты компонента на Vitest + Testing Library (счастливый путь, отказ БД,
  недоступность backend, ручная перепроверка).

### Инфраструктура и CI

- **`infra/docker-compose.yml`** — локальная среда: PostgreSQL 16 c volume и
  healthcheck, backend (ждёт healthy БД, сам имеет healthcheck), frontend
  (ждёт healthy backend). Все dev-порты привязаны к `127.0.0.1`; учётные
  данные явно помечены как development-only. **`build.context` разрешается
  относительно расположения Compose-файла**, поэтому пути заданы как
  `../backend` и `../frontend`.
- **`infra/compose.prod.yml`** — production overlay: секреты только из
  переменных окружения (`${VAR:?}` — ошибка при отсутствии), порты наружу
  не публикуются (`ports: !reset []`; требуется Docker Compose v2.24+),
  HTTPS/балансировщик — на этапе 7 (deployment).
- **CI (`.github/workflows/ci.yml`)** — четыре задачи: backend (ruff,
  mypy, pytest, preflight-проверки), frontend (eslint, tsc, vitest, build,
  npm audit), интеграционные тесты против реального PostgreSQL 16
  (включая конвейер миграций), compose smoke-тест стека (валидация dev- и
  prod-конфигураций, `up --build --wait`, health 200, деградация 503,
  очистка `if: always()`).

### Безопасность уже на этапе 1

- Нет секретов в репозитории: только шаблон `.env.example` и dev-значения,
  явно помеченные как development-only.
- Backend не стартует в production с небезопасной конфигурацией (см. выше).
- Логи не пишут персональные данные (на этапе 1 их просто нет); при появлении
  кандидатов маскирование будет обязательным.
- Минимальные права БД и шифрованные backup — этапы 2 и 7 роадмапа.

## Стратегия тестирования

| Уровень | Что проверяет | Где исполняется |
|---|---|---|
| Unit (pytest) | /health (ok и degraded-ветка), production-guard конфигурации, lifecycle (dispose engine при завершении) | in-memory SQLite, CI |
| Integration (pytest) | /health против настоящего PostgreSQL 16, деградация при недоступной БД, конвейер миграций (upgrade/downgrade/upgrade + идемпотентность) | `TEST_DATABASE_URL`, CI service container |
| Frontend (Vitest) | отображение всех состояний статусной страницы | jsdom, CI |
| Compose smoke (CI) | валидация dev/prod конфигураций, полный запуск стека, health 200, прокси, деградация 503 | GitHub Actions, Docker на раннере |

Запуск интеграционных тестов локально:
`TEST_DATABASE_URL=postgresql+psycopg://hr_manager:hr_manager_dev_password@localhost:5432/hr_manager pytest -m integration -v`

## Политика миграций

- Все изменения схемы — только через Alembic; ревизии пишутся вручную
  (metadata-модели появятся вместе с бизнес-сущностями).
- Каждая миграция должна быть idempotent и reversible, где это возможно.
- Применение в dev/staging: автоматически при старте backend-контейнера.
  В production — отдельный контролируемый процесс (этап 7), `alembic upgrade`
  в CMD контейнера для production не используется.

## Известные ограничения этапа 1

- Нет авторизации, кандидатов и аналитики — по определению этапа.
- Нет rate limiting и audit log — они требуют схемы пользователей (этап 1
  роадмапа).
- Healthcheck frontend-контейнера использует wget из alpine-образа;
  для прод-мониторинга на этапе 7 будет отдельный exporter/agent.
- Compose-проверки (config, запуск стека, деградация) выполняются задачей
  `stack` в CI на GitHub-раннерах; в песочницах без Docker они недоступны.
- Файл `.github/workflows/ci.yml` присутствует в репозитории, однако
  публикующая GitHub App не имеет разрешения `workflows`, поэтому в ветке
  `arena/phase-1-agent-2` workflow не может быть запушен; копия лежит в
  `review-artifacts/ci.agent-2.yml` (см. `review-artifacts/README.md`).
