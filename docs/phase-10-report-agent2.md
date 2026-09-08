# Отчёт Phase 10 — коммуникации с кандидатами (agent-2)

## Исходные данные

- Ветка: `arena/01a07f99-hr-manager`
- Исходный SHA (точка старта, «phase 10 handoff»):
  `531de19473a995244c03c17ea346e7a097c92c75`
- Итоговый SHA реализации (коммит кода):
  `12d954241cb1e08108c506cfa23d7647a0f2550b`
- Коммит отчёта — голова ветки на момент открытия PR (точный SHA головы
  указан в описании PR и в финальном сообщении агента; CI-проверки зелёные
  именно для него).
- Задание: `prompts/PHASE_10_PROMPT.md`; продукты-правила: `agents.md`,
  `PRODUCT_SPEC.md`, `ROADMAP.md`.

## Что вошло

Односторонние русскоязычные сообщения кандидатам поверх принятых событий,
очереди PostgreSQL и worker-а фаз 8/9. Реализованы backend (API, планировщик,
worker, миграция) и **фронтенд UI в карточке кандидата**.

### Backend

- Шесть бизнес-типов сообщений: `interview_scheduled`, `interview_reminder`,
  `interview_rescheduled`, `interview_cancelled`, `documents_request`,
  `documents_reminder` (+ внутренний `consent_invite` для double-opt-in письма).
- Новые модели (`backend/app/models.py`) и миграция
  `backend/alembic/versions/0010_candidate_communications.py`:
  `candidate_contact_channels` (согласие/адрес/чат, серверный получатель),
  `candidate_messages` (неизменяемая история: канал, тип, точный текст,
  источник `manual|event|rule|system`, снапшот согласия, idempotency key,
  статусы `queued|sending|accepted|delivered|failed|cancelled|skipped`,
  provider id только если реально вернулся, безопасный `error_class`),
  `candidate_message_attempts`, `candidate_channel_tokens` (hash-only,
  single-use, expiring). `accepted` ≠ доставка/прочтение.
- `backend/app/candidate_communications.py` — сервисный слой: честные пять
  состояний канала (`not_connected | pending_confirmation | allowed | denied |
  temporarily_unavailable`), серверная композиция текста с экранированием
  (`sanitize_single_line`, безопасная подстановка имени/времени/места/документов),
  one-shot токены, тихие часы для кандидатских отправок, планирование строк
  в единой транзакции.
- API-роутер `backend/app/routers/candidate_communications.py`
  (`/candidates/{id}/communications/*`): каналы, запрос email-согласия,
  Telegram link/confirm, revoke, предпросмотр (серверный текст), ручная
  отправка (только тип/канал/событие/документы — получатель и текст всегда
  серверные), история с пагинацией, отмена ожидающей отправки; публичные
  страницы `/public/candidates/consent/{token}` и
  `/public/candidates/revoke/{candidate}/{channel}` (HMAC-подпись).
  Mutating-эндпоинты: CSRF + per-user rate limit + idempotency.
- Worker (`backend/app/worker.py`): `claim_candidate_batch` /
  `process_candidate_message` / `recover_stale_candidate_leases` —
  SKIP LOCKED + advisory locks + lease recovery, bounded retries/backoff,
  повторная проверка согласия/получателя/события/токена непосредственно
  перед отправкой, cancel-wins над гонкой, «честный» skip недоступного канала,
  алерт пилоту при терминальной ошибке без PII/текста. События-интервью
  автоматически ставят письма в той же транзакции, что и изменение события
  (`backend/app/routers/events.py`), с отменой устаревших queued-строк.
- Настройки `CANDIDATE_CONSENT_TOKEN_TTL_HOURS`,
  `CANDIDATE_TELEGRAM_TOKEN_TTL_MINUTES`, rate limit ручной отправки
  (`config.py`, `.env.example`).
- Исправлен баг: PATCH события с изменённым `location` падал с KeyError в
  аудите — `_SAFE_FIELD_NAMES` дополнен `"location"` (events.py).

### Frontend (phase 10 UI в существующей карточке)

- `frontend/src/types.ts` — типы/словари каналов, состояний, типов, статусов
  сообщений (сообщение «accepted ≠ доставлено» продублировано в подсказках UI);
  статус события `cancelled` добавлен в `CalendarEventStatus`/labels
  (backend уже отдавал его с фазы 8 — это устранённый дрейф типа).
- `frontend/src/api.ts` — типизированный клиент коммуникаций (channels,
  consent, telegram link/confirm, revoke, preview, send, history, cancel).
- `frontend/src/features/candidates/CandidateCommunicationsTab.tsx` — вкладка
  «Сообщения» в `CandidateDrawer.tsx`:
  - карточки каналов Email/Telegram с пятью состояниями и действиями
    (запросить письмо-согласие, повторная отправка, Telegram deep-link +
    «Проверить подключение», отзыв согласия с ConfirmDialog);
  - «Новое сообщение»: выбор разрешённого канала, шести типов, события-
    собеседования (для интервью-типов) или списка документов (для
    документных), серверный предпросмотр, подтверждение тихих часов,
    постановка в очередь с идемпотентностью;
  - «История сообщений» с пагинацией, статусами, точным текстом (details),
    отменой queued-отправок;
  - состояния загрузки/пусто/ошибка/повтор, deleted-candidate guard,
    честное предупреждение про «принято провайдером ≠ доставлено/прочитано».
  UI никогда не передаёт получателя/текст/владельца — только серверный
  контекст.
- `frontend/src/features/candidates/candidate-communications.css`, иконки
  `copy|link|send` в `Icon.tsx`.

## Тесты

- Backend unit (SQLite) и PostgreSQL integration:
  - `backend/tests/test_candidate_communications.py` — сервисный слой
    (15 тестов): состояния каналов, согласия, токены, планирование/доки.
  - `backend/tests/test_candidate_communications_api.py` — API (13 тестов):
    права/IDOR, CSRF, rate limit, preview/send/cancel, telegram/email
    consent-флоу, аудит.
  - `backend/tests/test_candidate_worker.py` — worker (13 тестов): accept,
    bounded retries, skip недоступного канала, re-validation (consent,
    recipient, event version/status), quiet hours, cancel-wins, lease recovery,
    алерт без PII.
  - `backend/tests/test_integration_candidate_communications.py`
    (4 теста, `pytestmark = pytest.mark.integration`): SKIP LOCKED disjoint
    claims между сессиями, доставка `accepted` на PostgreSQL, событие →
    авто-очередь/reschedule письма в транзакции, consent/revoke + аудит.
  - Миграции: `test_migrations.py` переведён на `HEAD_REVISION = "0010"`;
    upgrade/downgrade/re-upgrade проверяются существующим миграционным тестом.
- Frontend:
  - `api.test.ts` +8 тестов клиента коммуникаций;
  - `CandidateCommunicationsTab.test.tsx` (11 тестов): состояния каналов,
    preview→send (без передачи PII в контракт), тихие часы, email consent,
    revoke через диалог, Telegram link+confirm, «нет собеседований»,
    выбор события, cancel из истории, retry, deleted-карточка;
  - `CandidateDrawer.test.tsx` +1 тест вкладки «Сообщения».

## Числа (команды и фактические результаты)

```text
# backend (venv песочницы; PostgreSQL на 127.0.0.1:55433)
ruff check .            -> All checks passed!
ruff format --check .   -> 89 files already formatted
mypy app tests          -> Success: no issues found in 78 source files
pytest -m "not integration" -q
                        -> 444 passed, 80 deselected
TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55433/hr_manager_test \
  pytest -m integration -q
                        -> 69 passed, 11 skipped, 444 deselected
                        (skips — pg_dump/pg_restore не установлены локально;
                        CI устанавливает postgresql-client-16)
git diff --check        -> чисто

# frontend
npm run lint            -> чисто
npm run typecheck       -> чисто
npm test -- --run       -> 18 files passed, 137 tests passed
npm run build           -> ✓ built (vite), tsc -b чист
```

## Проверки Compose / stack

В песочнице нет Docker, поэтому локально `docker compose ...` не запускался
(то же ограничение, что в отчёте фазы 9). Эквивалентная проверка встроена в
CI `.github/workflows/ci.yml` (job «Compose stack smoke test»): валидация dev и
prod overlay конфигураций, `up --build --wait`, `/health` = 200, HTTPS-proxy
overlay, появление зашифрованного backup, frontend через nginx с прокси на API,
degraded health 503 при остановленной БД. Результат этого job для итогового
SHA — в статусе PR.

## Ограничения и честные оговорки

- Реальная отправка через SMTP/Telegram не выполнялась (нет внешних
  провайдеров); сетевой слой заменён фейковыми сендерами в тестах — как и в
  фазах 8/9. Worker-путь, статусы и `accepted`-семантика проверены на
  PostgreSQL-интеграции.
- Telegram-привязка использует существующий poll `/start`-механизм; live-бот не
  подключался.
- Backup/restore-drill интеграционные тесты пропущены локально из-за отсутствия
  pg_dump; в CI они выполняются.
- Письма и сообщения кандидатам — строго односторонние; ответы, чат, входящая
  почта, документы через каналы, SMS и массовые рассылки не входят (см.
  задание и ROADMAP).

## Handoff следующему этапу

- Этап 11 по ROADMAP — списки документов и правила автоматизации (источник
  `rule` уже зарезервирован в `CandidateMessageSource`, тип
  `documents_reminder` готов к правилам).
- Рекомендации на будущее: при добавлении статуса в backend-словари синхронно
  расширять `frontend/src/types.ts` (пример исправленного в этой фазе
  дрейфа `cancelled`); `docs/CURRENT_STATUS.md` обновляет владелец после merge.
- Ветка не мержит `main` самостоятельно: открыт PR, merge — за владельцем.

## Файлы (инвентарь ветки)

Изменено: `.env.example`, `backend/app/config.py`, `backend/app/main.py`,
`backend/app/models.py`, `backend/app/routers/events.py`,
`backend/app/schemas.py`, `backend/app/worker.py`, `backend/tests/conftest.py`,
`backend/tests/test_migrations.py`, `frontend/src/api.ts`, `frontend/src/api.test.ts`,
`frontend/src/types.ts`, `frontend/src/design-system/icons/Icon.tsx`,
`frontend/src/features/candidates/CandidateDrawer.tsx`,
`frontend/src/features/candidates/CandidateDrawer.test.tsx`.

Добавлено: `backend/alembic/versions/0010_candidate_communications.py`,
`backend/app/candidate_communications.py`,
`backend/app/routers/candidate_communications.py`,
`backend/tests/test_candidate_communications.py`,
`backend/tests/test_candidate_communications_api.py`,
`backend/tests/test_candidate_worker.py`,
`backend/tests/test_integration_candidate_communications.py`,
`frontend/src/features/candidates/CandidateCommunicationsTab.tsx`,
`frontend/src/features/candidates/CandidateCommunicationsTab.test.tsx`,
`frontend/src/features/candidates/candidate-communications.css`,
`docs/phase-10-report-agent2.md` (этот файл).
