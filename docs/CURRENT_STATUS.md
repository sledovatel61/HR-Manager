# Текущее состояние и handoff

> Phase 9 (Telegram Bot API и SMTP адаптеры, transactional outbox routing, linking flow, consent management) реализована агентом №1.

Актуально после **Phase 9 — интеграции доставки: Telegram Bot API и SMTP**.
Phase 9 реализована строго в ветке `arena/01a07b9b-hr-manager`.

## Что сделано в Phase 9

1. **Telegram Bot API адаптер (`backend/app/telegram_adapter.py`)**:
   - Production-ready адаптер для отправки сообщений, верификации токена (`getMe`), проверки вебхука.
   - Маскирование bot token во всех логах, URL и ошибках.
   - Экранирование HTML-тегов (`html.escape`), форматирование сообщений.
   - Одноразовые токены привязки (`telegram_link_tokens`) с SHA-256 хешированием, TTL 15 минут, связывание только с числовым `chat_id` (защита от кражи юзернеймов).
   - Обработка `429 Too Many Requests` с `retry_after`, классификация permanent (400, 403, 404) vs transient ошибок.

2. **Universal SMTP адаптер (`backend/app/smtp_adapter.py`)**:
   - Поддержка TLS (порт 465) и STARTTLS (порт 587).
   - Защита от header injection (`\r`, `\n`).
   - RFC 2047 и MIME UTF-8 кодирование для русских заголовков и текстов.
   - RFC 5322 Message-ID генерация и возврат `provider_message_id`.
   - Диагностика подключения через NOOP/EHLO.

3. **Transactional Outbox Worker & Routing (`backend/app/worker.py`, `backend/app/notification_service.py`)**:
   - Маршрутизация заданий по каналам `in_app`, `telegram`, `email`.
   - Чёткое разделение статусов: `accepted` (принято шлюзом провайдера) != `delivered` (подтверждённая доставка) != human read.
   - Запись попыток в `notification_delivery_attempts` с `provider_message_id` и временем ответа.
   - Проверка явного согласия (opt-in + consent snapshot) перед отправкой.
   - Bounded exponential backoff и recovery просроченных lease.

4. **API и безопасность**:
   - Эндпоинты `/preferences` (настройки каналов, opt-in/opt-out, email, consent snapshot).
   - Эндпоинты `/integrations/telegram/link/initiate`, `/confirm`, `/unlink`.
   - Эндпоинты `/integrations/status`, `/integrations/telegram/webhook`.
   - Admin-only эндпоинты `/admin/integrations/test-send`, `/admin/integrations/test-telegram`, `/admin/integrations/test-smtp`.
   - Полный аудит всех действий связывания, отвязки, смены согласий и тестовых отправок.
   - Строгая CSRF double-submit и IDOR защита.

5. **Alembic миграция `0009_telegram_email_integrations`**:
   - Таблица `telegram_link_tokens` и новые поля в `notification_preferences`.
   - Проверены и подтверждены `alembic upgrade head`, `downgrade -1`, `re-upgrade`.

6. **Frontend (React + TypeScript + Design System)**:
   - Обновлены типы и API-клиент.
   - Экран `PreferencesPage`: модальное окно привязки Telegram с deep-link и QR-кодом, настройка Email, чекбоксы согласия, отвязка с подтверждением.
   - Экран `AdminQueuePage`: статус каналов, зонды `getMe` и `NOOP`, форма тестовой отправки администратора.
   - Экран `NotificationCenterPage`: отображение Message-ID провайдера и времени приёма шлюзом.
   - Доступность (ARIA, фокус-трап, screen readers, контраст, клавиатурная навигация), все тексты на русском языке.

7. **Тестирование**:
   - Backend: 325 unit-тестов (SQLite) + 69 интеграционных тестов (PostgreSQL).
   - Frontend: ESLint (0 ошибок), TypeScript (0 ошибок), 119 vitest тестов, Vite production build (успешно).

## Как продолжить (Phase 10)

Следующий этап — **Phase 10 — коммуникации с кандидатами и шаблоны сообщений**.
В Phase 10 будут разработаны шаблоны писем кандидатам, приглашения на интервью, автоматические статусные уведомления соискателям на базе созданных адаптеров Telegram и SMTP.