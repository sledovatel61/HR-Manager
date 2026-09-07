# Отчёт по реализации Phase 9 — Интеграции доставки: Telegram Bot API и SMTP

**Агент:** №1  
**Ветка:** `arena/01a07b9b-hr-manager`  
**Базовый коммит:** `fc33d9d3f11341b5f91646da6bd8e76190c6ccce` (`main`)  
**Дата:** 2026-09-07  

---

## 1. Краткое резюме

В рамках этапа **Phase 9** в систему `HR-Manager` внедрены промышленные адаптеры внешних каналов доставки уведомлений: **Telegram Bot API** и **Universal SMTP**, работающие поверх transactional outbox на PostgreSQL и фонового worker-процесса.

### Ключевые достижения:
1. **Telegram Bot API адаптер (`app/telegram_adapter.py`)**:
   - Безопасная отправка сообщений с экранированием HTML (`html.escape`).
   - Автоматическое маскирование секретных токенов бота (`_redact_url`) во всех логах, URL и текстах исключений.
   - Обработка `HTTP 429 Too Many Requests` с разбором `parameters.retry_after` и откладыванием повтора.
   - Разделение постоянных ошибок (400 Bad Request, 403 Bot Blocked, 404 Not Found) и временных сбоев (5xx, network timeouts).
   - Диагностический зонд `getMe` без раскрытия секретов.
2. **Безопасное связывание Telegram аккаунтов**:
   - Одноразовые криптографически стойкие токены с временем жизни 15 минут.
   - В БД сохраняется только **SHA-256 хеш** токена (открытый токен не сохраняется).
   - Привязка осуществляется строго по **числовому `chat_id`** (`BigInteger`), исключая уязвимости подмены и кражи username.
   - Поддержка deep-link ссылки `t.me/<bot>?start=<token>`, ручного ввода и webhook/polling обработки.
   - Возможность отвязки аккаунта с аудитом и отзывом активных токенов.
3. **Universal SMTP адаптер (`app/smtp_adapter.py`)**:
   - Поддержка защищённых протоколов TLS (порт 465) и STARTTLS (порт 587).
   - Защита от **Header Injection** (проверка и очистка `\r` и `\n` в заголовках темы, отправителя и получателя).
   - Корректное кодирование русских тем и отправителей по RFC 2047 (UTF-8) и MIME UTF-8 тел сообщений (RFC 5322).
   - Генерация и отслеживание RFC 5322 `Message-ID` как `provider_message_id`.
   - Диагностический зонд `NOOP`/`EHLO` для проверки доступности сервера.
4. **Контракт Transactional Outbox и Worker (`app/worker.py`, `app/notification_service.py`)**:
   - Чёткое разделение статусов: **`accepted` (принято шлюзом провайдера) != `delivered` (подтверждённая доставка) != human read**.
   - Неизменяемый журнал попыток (`notification_delivery_attempts`) с сохранением `provider_message_id`, времени ответа и кодов ошибок.
   - Сетевые вызовы вынесены за пределы долгоживущих транзакций БД; захват и фиксация статусов выполняются атомарно с lease recovery.
   - Bounded backoff: экспоненциальное нарастание задержки, предельное число попыток, переход в `failed` с алертом в метриках.
5. **Управление согласиями (Consent Management) и предпочтениями**:
   - Явный opt-in / opt-out для каждого канала.
   - Фиксация версии согласия (`consent_version`), времени (`consent_at`) и источника (`consent_source`).
   - Запрет неявной активации каналов (silent activation).
6. **Alembic миграция `0009_telegram_email_integrations`**:
   - Создана таблица `telegram_link_tokens` и добавлены колонки интеграций и согласий в `notification_preferences`.
   - Полная обратимость: успешно проверен цикл `upgrade head → downgrade -1 → re-upgrade`.
7. **Frontend (React + TypeScript + Design System)**:
   - Экран `PreferencesPage`: модальное окно привязки Telegram с deep-link и QR-кодом, настройка Email, чекбоксы согласия, подтверждение отвязки.
   - Экран `AdminQueuePage`: статус интеграционных каналов, зонды `getMe` и `NOOP`, форма тестовой отправки администратора.
   - Экран `NotificationCenterPage`: отображение Message-ID провайдера и времени подтверждения шлюзом.
   - Доступность: семантическая разметка, ARIA-атрибуты, `role="dialog"`, `role="alert"`, `aria-live="polite"`, фокус-трап, поддержка клавиатурной навигации, контрастность. Полностью на русском языке.

---

## 2. Архитектурные детали и модель данных

### 2.1. Схема базы данных (миграция `0009_telegram_email_integrations`)

```sql
-- Таблица одноразовых токенов связывания Telegram
CREATE TABLE telegram_link_tokens (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    expires_at TIMESTAMPTZ NOT NULL,
    confirmed_at TIMESTAMPTZ,
    telegram_chat_id BIGINT,
    telegram_username VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_telegram_link_tokens_status
        CHECK (status IN ('pending', 'confirmed', 'expired', 'cancelled'))
);

CREATE INDEX ix_telegram_link_tokens_user_id ON telegram_link_tokens (user_id);
CREATE INDEX ix_telegram_link_tokens_expires_at ON telegram_link_tokens (expires_at);

-- Расширение notification_preferences
ALTER TABLE notification_preferences
    ADD COLUMN telegram_chat_id BIGINT,
    ADD COLUMN telegram_username VARCHAR(255),
    ADD COLUMN telegram_opt_in BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN telegram_linked_at TIMESTAMPTZ,
    ADD COLUMN telegram_consent_version VARCHAR(32),
    ADD COLUMN telegram_consent_at TIMESTAMPTZ,
    ADD COLUMN telegram_consent_source VARCHAR(64),
    ADD COLUMN email_address VARCHAR(255),
    ADD COLUMN email_opt_in BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN email_consent_version VARCHAR(32),
    ADD COLUMN email_consent_at TIMESTAMPTZ,
    ADD COLUMN email_consent_source VARCHAR(64);
```

### 2.2. Жизненный цикл доставки (Outbox Contract)

1. **Создание задания**: при генерации события формируется строка в `notification_outbox` со статусом `queued` и `scheduled_at_effective` (с учётом тихих часов и рабочей недели).
2. **Захват worker-ом**: worker выбирает готовые задания пакетами с `FOR UPDATE SKIP LOCKED`, переводит их в статус `sending` и устанавливает `lease_expires_at = now + 120s`.
3. **Сетевой вызов адаптера**:
   - `in_app`: атомарно создаёт запись в `notifications` и переходит в `delivered`.
   - `telegram` / `email`: выполняет вызов внешнего шлюза. При ответе шлюза об успешном приёме (Telegram HTTP 200 `ok=true` или SMTP `250 OK`) переходит в **`accepted`** (но **не** в `delivered`), фиксирует `provider_message_id` и `accepted_at`.
4. **Обработка сбоев**:
   - **Rate limit (429)**: извлекается `retry_after` (по умолчанию 30с), вычисляется `next_attempt_at = now + retry_after + jitter`, статус возвращается в `queued`.
   - **Transient error**: сетевой таймаут / 5xx / ошибка подключения → планируется повтор с экспоненциальным backoff.
   - **Permanent error**: 400 / 403 (бот заблокирован) / 404 / 550 (почтовый ящик не существует) → статус переводится в `failed`, lease сбрасывается.
   - **Terminal exhaustion**: по достижении максимального числа попыток (`WORKER_MAX_ATTEMPTS = 5`) статус переходит в `failed`.
5. **Журнал попыток**: каждая попытка append-only сохраняется в `notification_delivery_attempts` с длительностью, кодами ошибок и `provider_message_id`.

---

## 3. Безопасность и соответствие стандартам

- **Защита от Header Injection**: строгая валидация и удаление символов `\r` и `\n` в SMTP адаптере.
- **Хранение секретов**: bot token передаётся только через переменные окружения (`TELEGRAM_BOT_TOKEN`, `SMTP_PASSWORD`); в БД сохраняются только SHA-256 хеши токенов связывания.
- **Маскирование в логах и API**:
  - Токены бота маскируются регулярным выражением (`bot<token>/...` → `bot***REDACTED***/...`).
  - Email-адреса маскируются в ответах API (`j***n@c***y.test`).
  - Диагностика очереди не раскрывает тексты сообщений, имена кандидатов или персональные данные.
- **IDOR защита**: управление привязкой Telegram и настройками уведомлений доступно только аутентифицированному владельцу (`current_user.id`).
- **CSRF защита**: double-submit cookie на всех мутирующих эндпоинтах.
- **RBAC**: эндпоинты тестовой отправки (`/admin/integrations/test-send`) и зондов (`/admin/integrations/test-telegram`, `/admin/integrations/test-smtp`) доступны только роли `admin`.
- **Аудит**: все действия привязки, отвязки, изменения согласий и тестовых отправок фиксируются в `audit_log` без раскрытия секретов.

---

## 4. Результаты тестирования и верификации

### 4.1. Backend Unit Tests (SQLite in-memory)
```bash
pytest -m "not postgres_integration" -q
```
**Результат:** `325 passed, 67 skipped, 20 warnings in 73.24s`.

### 4.2. Backend Integration Tests (PostgreSQL 16.2)
```bash
TEST_DATABASE_URL="postgresql+psycopg://postgres@127.0.0.1:5432/hr_manager_test" \
BACKUP_PGDUMP_BIN="/home/user/venv/lib/python3.11/site-packages/pgserver/pginstall/bin/pg_dump" \
BACKUP_RESTORE_BIN="/home/user/venv/lib/python3.11/site-packages/pgserver/pginstall/bin/pg_restore" \
pytest -m "postgres_integration or not postgres_integration" tests/test_integration_*.py tests/test_migrations.py tests/test_health.py -q
```
**Результат:** `69 passed, 5 warnings in 51.04s`.

### 4.3. Backend Typecheck и Linter
```bash
mypy app
ruff check .
ruff format --check .
```
**Результат:**
- `mypy app`: `Success: no issues found in 37 source files` (0 ошибок).
- `ruff check .`: `All checks passed!` (0 предупреждений).
- `ruff format --check .`: `83 files already formatted` (0 диффов).

### 4.4. Frontend Linter, Typecheck, Unit/Integration Tests & Build
```bash
cd frontend
npm run lint
npm run typecheck
npm run test
npm run build
```
**Результат:**
- `eslint .`: 0 ошибок, 0 предупреждений.
- `tsc -b`: 0 ошибок.
- `vitest run`: **18 test files, 119 passed** (100% green).
- `vite build`: `dist/index.html 0.40 kB, dist/assets/index.js 278.91 kB` — успешная сборка.

---

## 5. Переменные окружения (Инструкция для операторов)

Для настройки интеграций в `.env` добавлены параметры:

```bash
# --- Telegram Bot API ---
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ_dev
TELEGRAM_BOT_USERNAME=HrManagerBot
TELEGRAM_API_BASE_URL=https://api.telegram.org
TELEGRAM_TIMEOUT_SECONDS=10
TELEGRAM_LINK_TTL_MINUTES=15
TELEGRAM_WEBHOOK_SECRET=

# --- Universal SMTP / Email ---
SMTP_HOST=smtp.company.test
SMTP_PORT=587
SMTP_USERNAME=notifications@company.test
SMTP_PASSWORD=secret-smtp-password
SMTP_USE_TLS=false
SMTP_USE_STARTTLS=true
SMTP_FROM_EMAIL=notifications@company.test
SMTP_FROM_NAME=HR Manager
SMTP_TIMEOUT_SECONDS=10
```

---

## 6. Заключение и Handoff

Все требования **Phase 9** выполнены в полном объёме:
- Реализованы реальные production-адаптеры Telegram Bot API и SMTP.
- Проведено полное сквозное тестирование на PostgreSQL 16.
- Обеспечена безопасность секретов, PII, CSRF и IDOR.
- Обновлён и протестирован русскоязычный интерфейс настроек и администрирования.
- Ветка `arena/01a07b9b-hr-manager` подготовлена к открытию Pull Request в ветку `main`.
