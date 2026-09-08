# Отчёт Phase 10 — Односторонние сообщения кандидатам (agent-2)

- **Ветка:** `arena/01a07f9a-hr-manager`
- **Baseline SHA (origin/main на старте):** `531de19473a995244c03c17ea346e7a097c92c75`
- **Final code SHA (реализация):** `941c0d3fdea8f33d6d416645a3ff243f6ae38c67`
  (коммит «feat(phase10): one-way candidate messages over the existing
  outbox/worker»; поверх идёт docs-коммит с этим отчётом — tip ветки =
  верхний из них, его SHA — в PR)
- **PR:** см. ответ агента / PR-комментарий (ветка `arena/01a07f9a-hr-manager` → `main`)
- **Merge в `main`:** не выполнялся (по правилам — владелец)

## Что сделано

Шесть типов односторонних русскоязычных сообщений кандидату —
«Собеседование назначено», «Напоминание о собеседовании»,
«Собеседование перенесено», «Собеседование отменено», «Запрос документов»,
«Напоминание о недостающих документах» — доставляются по email и Telegram
**только через существующий PostgreSQL outbox/worker фаз 8/9**. Новая
очередь/worker не создавались. Ответы, чат, входящая почта, загрузка
документов через каналы, SMS и рекламные рассылки — не входят (по ТЗ).

### Изменённые файлы (22)

Новые:

- `backend/app/candidate_messages.py` (614 строк) — шаблоны с безопасной
  подстановкой (`sanitize_inline`: вырезание управляющих символов,
  схлопывание пробелов, лимиты), согласия по каналам, состояния каналов,
  постановка в outbox, планирование сообщений жизненного цикла интервью
  (идемпотентно, cancel-wins), отмена ожидающих отправок по каналу;
- `backend/app/routers/candidate_messages.py` (1165) — API каналов и
  сообщений (см. «Архитектура»);
- `backend/alembic/versions/0010_candidate_communications.py` (335) —
  миграция (head `0009` → `0010`);
- `backend/tests/test_candidate_messages.py` (18 тестов),
  `backend/tests/test_worker_candidate.py` (17),
  `backend/tests/test_candidate_event_hooks.py` (6),
  `backend/tests/test_integration_candidate_messages.py` (5, PG);
- `frontend/src/features/candidates/MessagesTab.tsx` (+9 тестов в
  `MessagesTab.test.tsx`) — вкладка «Сообщения» в карточке кандидата.

Изменённые: `backend/app/{config,main,models,notification_service,schemas,worker}.py`,
`backend/app/routers/events.py`, `backend/tests/{conftest,test_migrations}.py`,
`frontend/src/{api,types}.ts`, `frontend/src/features/candidates/{CandidateDrawer.tsx,candidates.css}`.

## Архитектура

- **Согласия.** `candidate_channel_consents` — раздельно email/Telegram,
  с источником (`hr_recorded` / `telegram_start` — добровольный `/start`
  кандидата), версией политики `phase10-v1` и отзывом. Отзыв согласия
  (или unlink Telegram) немедленно переводит ожидающие строки канала в
  `cancelled`. Повторное включение — только новым явным решением.
- **Состояния канала.** `not_connected | pending | allowed | forbidden |
  temporarily_unavailable` (последнее — по классу временной ошибки
  провайдера на привязке за последний час, зеркало фазы 9).
- **Планирование.** Хук создания интервью ставит `scheduled` + `reminder`
  (за `CANDIDATE_INTERVIEW_REMINDER_HOURS`, по умолчанию 24 ч) на каждый
  разрешённый канал; перенос отменяет старые строки и ставит `rescheduled`
  + новый `reminder` (в тексте — старая и новая дата/время местной
  таймзоны); отмена события ставит `cancelled`-письмо. Идемпотентные
  ключи предотвращают дубли при повторном вызове.
- **Ручная отправка.** `POST /candidates/{id}/messages/send` (и `preview`
  без постановки): сервер сам рендерит точный текст и сам выбирает
  адресатов по текущим согласиям. Клиентский payload содержит только
  закрытый словарь (`message_type`, `event_id`, `documents`, `location`,
  `channel`) — получатель/текст/email/chat ID через клиент не передаются.
  Дубль такого же типа в ожидании → 409.
- **Доставка (worker).** `process_external_row`: вне HTTP, lease/retry,
  сеть вне длинных транзакций. Перед вызовом провайдера — fail-closed
  перепроверка: согласие, адрес/чат, состояние кандидата (не удалён),
  событие не перенесено/не отменено/не в прошлом (для reminder), строка
  не отменена. `accepted` = техническое принятие провайдером
  (`provider_message_id` хранится как вернул провайдер; plain SMTP без
  queue-id → `None`), ≠ доставлено ≠ прочитано. Отказ внешнего канала
  не ломает внутренние уведомления.
- **Безопасность.** RBAC: HR — своя область кандидатов, руководитель —
  своя; админ НЕ получает доступ к кандидатским сообщениям только из
  роли. CSRF на всех мутациях, rate limit на send/invite, защита от
  дублей. Аудит: согласия, отправка, отмена, ошибки/повторы (без
  PII/текста/адресов — проверено тестом на логи). Полный текст — только
  в неизменяемой истории доставки (`GET /candidates/{id}/messages`),
  терминальные статусы неизменяемы.
- **Миграция 0010.** `candidate_telegram_links` (chat_id, `last_sent_at`,
  классы ошибок, ревокация), `candidate_telegram_link_tokens`
  (одноразовые приглашения, в БД только SHA-256 хеш токена),
  `candidate_channel_consents`, колонки outbox
  (`recipient_candidate_id`, `consent_snapshot` и др.).
- **Frontend.** Вкладка «Сообщения» в карточке кандидата: карточки
  каналов с состояниями/согласием, поток приглашения Telegram
  (создать ссылку → кандидат жмёт Start → «Проверить подключение»),
  композер с предпросмотром серверного текста и выбором канала
  (недоступные явно помечены), история отправок с точным текстом,
  техническими статусами и отменой ожидающих.

## Команды и результаты (локально, песочница)

Backend (venv, из `backend/`):

```
ruff check app tests            → All checks passed!
ruff format --check app tests   → 78 files already formatted
mypy app tests                  → Success: no issues found in 78 source files
pytest -m "not integration" -q  → 444 passed, 81 deselected
TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55432/hr_manager_test \
pytest -m integration -q        → 81 passed, 444 deselected
```

PG-интеграционные тесты включают миграционный цикл
up → down → up и повторный upgrade (идемпотентность) на реальном
PostgreSQL; миграция `0010` применена на тестовой БД.

Фронтендовые сьюты фазы 10: `test_candidate_messages.py` 18 passed,
`test_worker_candidate.py` 17 passed, `test_candidate_event_hooks.py`
6 passed, `test_integration_candidate_messages.py` 5 passed (PG).
В integration-наборе: end-to-end ручной отправки по обоим каналам со
стабами SMTP/Telegram (точный текст, `provider_message_id` от Telegram),
жизненный цикл интервью, параллельные воркеры (ровно одна доставка),
отзыв согласия до воркера (cancel + подделанный `sending` → skip),
реальный poll `getUpdates` с `/start <token>` (в БД только хеш).

Frontend (Node 22, из `frontend/`):

```
npm test               → 18 files, 126 tests passed (из них 9 — MessagesTab)
npm run typecheck      → ok
npm run lint           → ok
npm run build          → ok (dist собран)
```

`git diff --check` — чисто. Регрессий фаз 0–9 нет: полный unit-набор
(444) и integration-набор (81) зелёные, `.github/workflows` не изменялись.

## Ограничения

- Plain SMTP-релей без queue-id не возвращает идентификатор сообщения —
  `provider_message_id` для email хранится как `None` (не выдумывается).
  Telegram возвращает `message_id`.
- Подключение Telegram требует ручного шага: HR создаёт приглашение,
  кандидат открывает ссылку и жмёт Start, затем HR подтверждает.
  Подтверждение опрашивает обновления бота тем же механизмом, что фаза 9.
- Compose stack-job в CI имеет предсуществующее падение на шаге владельца
  «Validate HTTPS proxy overlay configuration» (наследие фаз 8/9 —
  свежий shell без экспортов переменных; воспроизводится на принятом
  tip Phase 8/9). Функциональные backend/integration/frontend джобы
  ожидаются зелёными; фактический статус run — в ответе агента после
  завершения CI на итоговом SHA.
- `/start`-поллинг кандидатов и пользователей разделяет `telegram_poll_state`
  только для offset'ов; конфликтов chat_id (кандидат vs пользователь)
  обработан отказом подтверждения (409).

## Handoff

- Итоговый head миграций: `0010` (файл `0010_candidate_communications.py`).
- Новые настройки: `CANDIDATE_INTERVIEW_REMINDER_HOURS` (default 24),
  лимиты rate-limit кандидатских сообщений в `config.py`.
- Точки расширения: `app/candidate_messages.py::CANDIDATE_MESSAGE_TYPES`
  (закрытый словарь типов), `plan_candidate_interview_messages` (хуки
  событий в `routers/events.py`), `process_external_row` в `worker.py`
  (send-time перепроверки).
- Для владельца: merge PR в `main` после проверки CI на итоговом SHA
  (tip ветки = docs-коммит с этим отчётом поверх `941c0d3f`).
