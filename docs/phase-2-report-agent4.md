# Отчёт по Этапу 2 — идентификация и безопасность (агент №4)

Дата: 2026-09-02 · Ветка: `arena/01a061ab-hr-manager` (сессионная ветка
Arena; запрошенная `arena/phase-2-agent-4` платформно заменена на неё — см.
«Известные ограничения»). Merge в `main` не выполнялся.

> Нумерация этапов: в `ROADMAP.md` «Этап 1 — идентификация и безопасность»
> (1-based). В коде/промптах фаза безопасности именуется «phase 2»
> (`prompts/PHASE_2_PROMPT.md`). Этот отчёт описывает этап идентификации и
> безопасности по `ROADMAP.md`.

## Цель (по `prompts/PHASE_2_PROMPT.md`)

Пользователи с обязательным паролем, Argon2id-хеширование, вход/выход,
короткоживущие сессии, роли HR / руководитель / администратор, серверная
проверка прав, rate limiting и временная блокировка после неудачных входов,
audit log входа/выхода/блокировки/админ-изменений, админ-управление
пользователями и ролями, минимальный frontend входа/выхода и текущего
пользователя. Функции кандидатов не реализовывались.

## Изменённые и добавленные файлы

```
backend/
  app/config.py              + настройки сессий, rate limit, bootstrap-админа; production-guard расширён
  app/db.py                  + фабрика ORM-сессий (SessionLocal), зависимость get_db, bind_session_factory
  app/models.py              (новое) User, UserSession, AuditEvent + UserRole/AuditAction
  app/security.py            (новое) Argon2id PasswordHasher, политика паролей, генерация CSRF
  app/deps.py                (новое) зависимости FastAPI: сессия-из-cookie, CSRF double-submit, require_roles, cookies
  app/rate_limiting.py       (новое) потокобезопасный sliding-window limiter (per-IP)
  app/audit.py               (новое) запись событий в audit_log (без секретов/ПДн)
  app/bootstrap.py           (новое) создание начального администратора на пустой БД
  app/cli.py                 (новое) `python -m app.cli create-admin | list-users`
  app/utils.py               (новое) utc_now/ensure_aware, client_ip, user_agent
  app/main.py                + подключение роутеров auth/users/audit, middleware security-заголовков, bootstrap в lifespan
  app/schemas.py             + LoginRequest, CurrentUserOut, UserCreate/Update/Out/List, AuditEventOut/List
  app/routers/auth.py        (новое) POST /auth/login, POST /auth/logout, GET /auth/me
  app/routers/users.py       (новое) GET/POST /admin/users, GET/PATCH /admin/users/{id}, POST .../unlock
  app/routers/audit.py       (новое) GET /admin/audit (фильтры action/username, пагинация)
  alembic/env.py             target_metadata = Base.metadata
  alembic/versions/0001_baseline.py  pgcrypto теперь best-effort (PG13+ имеет gen_random_uuid встроенным)
  alembic/versions/0002_identity_security.py (новое) users / user_sessions / audit_log (UUID, timestamptz, FK, индексы)
  requirements.txt           + argon2-cffi==25.1.0
  pyproject.toml             + зависимость argon2-cffi; ruff ignore B008 (FastAPI Depends-идиома) и
                               per-file ignore RUF00x (русский текст); mypy explicit_package_bases
  tests/conftest.py          + схема из моделей, фикстуры db_session/make_user + PostgreSQL pg_*
  tests/test_auth.py         (новое, 25 тестов) вход, выход, сессии, CSRF, блокировка, rate limit, аудит
  tests/test_users_admin.py  (новое, 17 тестов) RBAC для HR/manager/admin, CRUD пользователей, пароли
  tests/test_security.py     (новое, 16 тестов) Argon2id, политика паролей, bootstrap
  tests/test_integration_identity.py (новое, 7 integration) те же сценарии на реальном PostgreSQL
  tests/test_migrations.py   расширено на ревизию 0002 и пошаговый downgrade/upgrade
  tests/test_config.py       + проверки bootstrap-пароля, SESSION_COOKIE_SECURE
  tests/test_health.py       порт stopped-PG больше не хардкодит :5432
frontend/
  src/types.ts               + UserRole, User, CurrentUser, AuditEvent, пагинация
  src/api.ts                 + login/logout/fetchCurrentUser/listUsers/listAuditEvents, credentials, CSRF-заголовок
  src/App.tsx                + восстановление сессии, гейт входа, состояние loading/anonymous/authenticated
  src/components/LoginForm.tsx (новое) форма входа с ошибкой и состоянием отправки
  src/components/Dashboard.tsx (новое) текущий пользователь, выход, список пользователей (admin)
  src/styles.css             + стили формы/панелей/таблицы
  src/App.auth.test.tsx      (новое, 5 тестов) вход/ошибка/восстановление сессии/loading
  src/api.test.ts            (новое, 5 тестов) клиент API: credentials, CSRF, ошибки
  src/App.test.tsx           smoke оболочки
infra/
  compose.prod.yml           + проброс BOOTSTRAP_ADMIN_* и SESSION_COOKIE_SECURE
  scripts/check_env.sh       + проверка BOOTSTRAP_ADMIN_PASSWORD
.github/workflows/ci.yml      + preflight без/с BOOTSTRAP_ADMIN_PASSWORD, переменная в stack-валидации
docs/phase-2-report-agent4.md (этот отчёт)
tests/README.md              карта тестов расширена
.env.example                 + SESSION_*, LOGIN_*, BOOTSTRAP_ADMIN_*
README.md                    раздел «Аутентификация и безопасность», таблицы API/env, статус
```

## Архитектурные решения

1. **Серверные сессии.** В БД (`user_sessions`) хранится сессия с CSRF-токеном,
   сроком, отзывом, IP/User-Agent. Cookie `hrm_session` — только UUID сессии
   (`HttpOnly`, `SameSite=Lax`, `Secure` в production). Выход и истечение
   отзывают сессию мгновенно. Скользящее продление (30 мин простоя, запись в
   БД не чаще раза в минуту).
2. **CSRF — double-submit.** Cookie `hrm_csrf` (JS-читаемый) + заголовок
   `X-CSRF-Token`; оба должны совпадать с токеном, привязанным к сессии.
   Сравнение через `secrets.compare_digest`. Login не требует CSRF, logout —
   требует.
3. **Пароли — Argon2id** (`argon2-cffi`: память 64 МиБ, t=3, p=4, 32-байтный
   хеш, 16-байтная соль). Политика: ≥12 символов, буква и цифра, не равен
   логину. Хеш автоматически перехешируется (`check_needs_rehash`) при успехе
   входа, если параметры устарели.
4. **Два слоя защиты входа.** Per-IP sliding-window rate limiter в памяти
   процесса (20 попыток / 5 мин → 429 + `Retry-After`; потокобезопасный,
   сброс в тестах) и персистентная блокировка аккаунта (5 неудач → 423 на
   15 минут, переживает рестарт). Сообщения об ошибке «неверный логин или
   пароль» одинаковы для неизвестного пользователя и неверного пароля (нет
   user enumeration).
5. **Роли на сервере.** `require_roles(...)` (`Depends`) закрывает все
   `/admin/*`. HR и руководитель получают 403, аноним — 401. Безопасность не
   зависит от UI.
6. **Аудит — append-only** (`audit_log`): входы/выходы, неудачи, блокировки,
   создание/изменение/деактивация/реактивация/разблокировка пользователей,
   смена роли; с IP, User-Agent и деталями. Пароли и секреты не пишутся.
7. **Начальный администратор.** На пустой БД в dev создаётся
   `admin/AdminAdmin123` (лог в стартовом выводе, development-only). В
   production `BOOTSTRAP_ADMIN_PASSWORD` обязателен — иначе приложение не
   стартует (config guard) и bootstrap не создаёт слабую учётку. Плюс CLI
   `python -m app.cli create-admin`.
8. **Миграции.** Ручная ревизия `0002` с нативными для PostgreSQL UUID
   (`gen_random_uuid()`) и `TIMESTAMPTZ`, внешними ключами (CASCADE/SET NULL)
   и функциональным unique-индексом `lower(username)`. Полностью обратима.
   Baseline-миграция включает `pgcrypto` best-effort (в PostgreSQL 13+
   `gen_random_uuid()` встроен). Модели используют БД-агностичные типы, поэтому
   изолированные unit-тесты идут на in-memory SQLite (`APP_ENV=test`), а
   integration — на реальном PostgreSQL.
9. **Security-заголовки** (`X-Content-Type-Options`, `X-Frame-Options`,
   `Referrer-Policy`, `Permissions-Policy`, `Cache-Control: no-store`) на всех
   ответах API.

## Команды проверки и результаты

### Backend (venv, Python 3.11 в песочнице; в CI/Docker — Python 3.12)

```bash
cd backend
ruff check .                     # All checks passed!
ruff format --check app tests    # 27 files already formatted
mypy app tests                   # Success: no issues found in 27 source files
pytest -m "not integration" -q   # 77 passed
```

Интеграция против **реального PostgreSQL 16.2** (порт 55432, бинарный пакет
`pgserver` — системного PostgreSQL/Docker в песочнице нет):

```bash
TEST_DATABASE_URL=postgresql+psycopg://hr_manager@127.0.0.1:55432/hr_manager_test \
  sh -c 'alembic upgrade head && pytest -m integration -v'
# 11 passed (identity 7, health 2, migrations 2)
# Полный прогон: 88 passed
```

Миграции вживую на PostgreSQL: `alembic upgrade head` (создаются
users/user_sessions/audit_log с UUID и timestamptz, индекс `lower(username)`),
`downgrade base` (таблицы удаляются), повторный `upgrade head` — ревизия
`0002`, пошаговый `downgrade -1` + `upgrade head` — успешно.

CLI проверен вживую: `list-users`, `create-admin` (слабый пароль отклонён,
сильный создан, дубль отклонён), вход созданным администратором — 200.

Сквозной прогон вживую (uvicorn + PostgreSQL + Vite-прокси): bootstrap-admin
на пустой БД; вход 200 (cookie `hrm_session`/`hrm_csrf`); `/auth/me`; создание
HR без CSRF → 403, с CSRF → 201; список пользователей; аудит; 5 неверных
паролей → 423 (верный пароль во время блокировки → 423); 25 попыток → 429;
выход 200 → `/auth/me` 401. Через Vite-прокси (`/api/*`) — те же 200/403,
health `ok`.

### Frontend (Node 22)

```bash
cd frontend
npm ci
npm run lint       # ok
npm run typecheck  # tsc -b, ok
npm run test       # Test Files 3 passed; Tests 11 passed
npm run build      # vite build ok (148.47 kB JS / 3.65 kB CSS)
npm audit --audit-level=high   # 0 vulnerabilities
```

### Инфраструктура

- `infra/scripts/check_env.sh`: без `BOOTSTRAP_ADMIN_PASSWORD` — отказ
  (exit 1); полная production-конфигурация — `ok` (exit 0).
- Статические проверки Compose-оверлея (`pytest tests/test_production_overlay.py`)
  — 6 passed.
- `docker compose up --build` в песочнице не запускался (Docker недоступен);
  полный стек проверяется job `stack` в CI, а эквивалентная связка
  (PostgreSQL + uvicorn + Vite-прокси) проверена вживую выше.

## Известные ограничения

- **Ветка.** Рабочая ветка сессии — `arena/01a061ab-hr-manager` (Arena
  жёстко привязывает сессию к ней и не дала бы отследить работу в
  `arena/phase-2-agent-4`). Merge в `main` не выполнялся; изменения не
  запушены в указанную ветку. При необходимости коммиты можно перенести на
  ветку с нужным именем.
- **Rate limiter пер-IP — в памяти одного процесса.** Для этапа (один
  API-процесс за reverse proxy) достаточно; горизонтальному масштабированию
  понадобится общий стор (Redis) — выходит за рамки этапа.
- **Смена пароля самим пользователем** (self-service) не входила в перечень
  этапа: администратор сбрасывает пароль через `/admin/users/{id}`. Добавится
  вместе с личными настройками в будущих этапах.
- **Песочница:** Python 3.11 (код и CI — 3.12), без Docker; интеграция
  выполнена против реального PostgreSQL 16.2 из pip-пакета `pgserver`
  (в нём нет contrib-расширения `pgcrypto`, поэтому baseline ставит его
  best-effort, а миграции используют встроенный в PG13+ `gen_random_uuid()`).
- Мелкие предупреждения Starlette о `httpx`/`httpx2` TestClient — из самой
  библиотеки, на результат не влияют.
