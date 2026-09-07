# Отчёт Phase 8 — фундамент уведомлений и пилотный режим (agent-2)

- **Ветка:** `arena/01a060e3-hr-manager`
- **Baseline SHA (origin/main на старте):** `d18ec9efc7973d8c86c9ecedf0d3e2f122c7ad2b`
- **Final SHA:** `e175ab46cc9b6215a69d07e4b4cfca13c2b2bd92`
- **PR:** https://github.com/sledovatel61/HR-Manager/pull/10
- **CI:** run 34090710477 (PR #10, head e175ab4); run 34089737761 (первый пуш bcb4176)

## Резюме

Реализован полный принятый scope этапа: внутренний центр уведомлений без
моков, личные напоминания, transactional outbox на PostgreSQL и отдельный
worker, тихие часы (пересечение полночи, DST, рабочие дни), неизменяемая
история попыток доставки, явно назначенный пилотный пользователь с полным
совмещённым доступом, идемпотентный мастер настройки, админ-диагностика
очереди без PII, UI на русском и worker в dev/prod Compose. Миграции
`0007`/`0008`, 340 backend-тестов и 111 frontend-тестов, все локальные
проверки зелёные; CI: backend/frontend/integration — зелёные, stack-job
останавливается на шаге владельца «Validate HTTPS proxy overlay
configuration» (см. «Ограничения»).

## Архитектурные решения

- **Outbox — тот же commit:** планирование уведомлений вызывается внутри
  сессии роутера до `db.commit()`, строка outbox коммитится в одной
  транзакции с бизнес-операцией (событие/передача/напоминание). Падение
  процесса не теряет уведомление; откат бизнес-транзакции откатывает и
  outbox (покрыто тестом `test_outbox_transactionality_rollback`).
- **Захват:** `SELECT … FOR UPDATE SKIP LOCKED` пакетами (PG-only путь;
  SQLite-юниты используют ту же логику на уровне state machine). Два
  worker-а никогда не получают одну строку (`test_claim_skip_locked_no_overlap`).
- **Lease:** статус `sending` + `lease_expires_at`; просроченные lease
  возвращаются в `queued`, а прерванная попытка фиксируется в
  append-only истории с `error_class=lease_expired` (краш-тест в
  `test_lease_recovery_and_reprocessing_after_crash`).
- **Retry:** bounded exponential backoff (база 60 c, потолок 3600 c,
  лимит 5 попыток из настроек), терминальный `failed` порождает
  системное уведомление пилоту. Retry/cancel админом — только через
  API с permission и аудитом; повторная отправка создаёт НОВУЮ строку
  (история принятых записей не редактируется).
- **Dedup:** `idempotency_key` уникален на outbox; `dedupe_key` уникален
  (partial unique index) на notifications — повторная обработка события
  не дублирует; напоминания дедуплицируются по `reminder_id:occurrence`
  (счётчик срабатываний монотонен, параллельные worker-ы не дублируют).
- **Каналы:** `in_app` доставляется атомарным созданием `notifications`
  (INSERT в той же короткой транзакции финализации); `email`/`telegram`
  зарезервированы контрактом и помечаются `skipped`
  (`error_class=channel_not_configured`) — фиктивного `accepted/delivered` нет.
- **Тихие часы:** границы — локальное время зоны получателя; функция
  `next_allowed_time()` сканирует минуты через zoneinfo (DST-корректно,
  полночь-корректно, детерминировано для замороженного времени).
  Исходное время хранится в `scheduled_at`, фактическое — в
  `scheduled_at_effective` (аудит). Уже просроченное сообщение в
  разрешённый момент доставляется сразу, в тихий — переносится.
  Срочный ручной override — `bypass_quiet_hours` с подтверждением,
  audit-записью `notification_urgent_override` и отдельным admin-permission.
- **Пилот:** аудит ролей этапов 2–7 показал, что `admin` фактически
  проходит все HR/manager-ветки кандидатов, событий, передач и аналитики
  и имеет администрирование → введённая модель: пилот = роль `admin` +
  **явный** грант `pilot_full_access` (уникальный активный, выдача/отзыв
  аудируются, endpoint-тесты доказывают полный доступ пилота и 403/404
  обычного HR на те же операции). Ни один существующий RBAC/CSRF/audit
  путь не отключён. Bootstrap-админ этапов 2–7 не тронут.
- **События:** добавлен терминальный статус `cancelled` (CHECK + история
  + `cancelled_at`); переходы этапа 5 сохранены; отменённое/завершённое
  событие не редактируется.

## Миграции

- `0007_notifications_foundation` — таблицы `notification_preferences`,
  `notifications`, `reminders`, `notification_outbox`,
  `notification_delivery_attempts`, `worker_heartbeat`, `access_grants`;
  CHECK-ограничения словарей; частичные уникальные индексы dedupe/гранта;
  `ON DELETE` политики (история уведомлений живёт со строкой-получателем).
- `0008_event_cancelled_status` — `events.cancelled_at`, расширенный CHECK
  статусов, CHECK согласованности `cancelled_at`, расширение словаря
  `event_history.kind`. Downgrade отказывает при наличии
  `status='cancelled'` строк (безопасный явный откат схемы).
- Обе ревизии reversible: `alembic downgrade 0006` → `upgrade head` —
  без ошибок на PostgreSQL 16 (локально) и на CI.

## Изменённые файлы

- Backend: `app/config.py` (настройки контура), `app/models.py` (модели +
  enum-ы + `cancelled`), `app/quiet_hours.py`, `app/notification_service.py`,
  `app/worker.py` (новые), `app/cli.py` (`worker`, `worker-check`),
  `app/schemas.py`, `app/main.py` (роутеры), `app/routers/{notifications,
  reminders,preferences,setup}.py` (новые), `app/routers/{events,candidates,
  ops}.py` (хуки планирования, cancelled, админ-очередь, сигнал /ops/status),
  `alembic/versions/{0007,0008}_*.py` (новые).
- Тесты: 9 новых модулей (`test_quiet_hours`, `test_notification_service`,
  `test_worker_logic`, `test_notifications_api`, `test_reminders_api`,
  `test_preferences_api`, `test_setup_api`, `test_event_notifications`,
  `test_integration_worker`) + правки `test_migrations`,
  `test_integration_analytics`, `test_ops_api`, `test_production_overlay`,
  `conftest.py`.
- Frontend: `src/types.ts`, `src/api.ts` (+ функции), новый каталог
  `src/features/notifications/` (bell, центр, напоминания, настройки,
  админ-очередь, мастер, CSS, 2 тест-файла), `src/app-shell/Workspace.tsx`
  и `useWorkspaceSection.ts` (секции/колокольчик).
- Infra: `infra/docker-compose.yml` (+worker), `infra/compose.prod.yml`
  (+worker), `.env.example`, `Makefile` (worker-logs/worker-status).
- Docs: `docs/ARCHITECTURE.md` (решения/границы/угрозы),
  `docs/CURRENT_STATUS.md`, `README.md`,
  `docs/phase-8-report-agent2.md` (этот файл), `review-artifacts/`
  (`ci.agent-2.phase8.yml/.patch`, README).

## Проверки — команды и результаты

Локально (песочница, Python 3.11 venv, PostgreSQL 16.2 из pgserver
16.2-бинарников; Docker недоступен — Compose проверяется статически +
CI-джобой):

```bash
# backend
cd backend
ruff check .                                    # All checks passed
ruff format --check .                           # 73 files already formatted
mypy app tests                                  # Success: no issues found in 64 source files
pytest -m "not integration" -q                  # 277 passed, 63 deselected
TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/hr_manager_test \
BACKUP_PGDUMP_BIN=…/pg_dump BACKUP_RESTORE_BIN=…/pg_restore \
  pytest -q                                     # 340 passed (277 unit + 63 integration)
# миграции: alembic downgrade 0006 && alembic upgrade head — OK (ревизия 0008)

# frontend
cd frontend
npm ci                                          # found 0 vulnerabilities
npm run lint                                    # OK
npm run typecheck                               # OK
npm run test                                    # 16 files, 111 tests passed
npm run build                                   # OK (dist)

# статический Compose (Docker недоступен): overlay-тесты
cd backend && pytest tests/test_production_overlay.py -q   # 23 passed
# PyYAML(!reset)-разбор dev/prod/proxy — валиден; git diff --check — чисто
```

CI (GitHub Actions, Linux/Python 3.12/PG 16):

- run 34089737761 (bcb4176) и 34090710477 (e175ab4): **Backend checks** ✓
  (ruff/format/mypy/pytest + preflight), **Frontend checks** ✓ (lint/
  typecheck/test/build/audit), **Backend integration tests (PostgreSQL)** ✓
  (весь integration-сьют включая новые 7), **Compose stack smoke**:
  ✓ validate dev config, ✓ validate production overlay, ✓ полный запуск
  стека с worker-ом (`up --build --wait` — worker поднялся здоровым по
  `worker-check`), ✓ `/health` 200, ✗ «Validate HTTPS proxy overlay
  configuration» (шаг владельца — см. «Ограничения»).

## Security/PII review

- В логах, метриках и диагностике нет заголовков/тел сообщений и PII:
  `/admin/ops/notifications/queue` возвращает только счётчики/статусы/
  worker-liveness (тест `test_admin_queue_diagnostics_has_no_pii`);
  `/ops/status.notifications` — то же; метрики не содержат query-строк.
- Уведомления не хранят имена/контакты кандидатов: только id в
  `object_type/object_id` + PII-free metadata; `resolve` повторно
  проверяет актуальные права перед навигацией (тест на отзыв доступа).
- Все списки — только свои (чужой id — 404), recipient/owner из payload
  не подменяется; bulk-операции ограничены 100 id; CSRF — существующий
  double-submit механизм на всех мутациях.
- Пароль пилота хешируется Argon2id, никогда не возвращается и не
  логируется; повторный запуск мастера не сбрасывает пароль (тест).
- Тексты истории доставки неизменяемы; попытка/исправление — новая
  строка. Ретention технических попыток и бизнес-истории не разделён
  отдельным механизмом удаления — история не удаляется обычным CRUD
  (явный handoff для этапа 11).
- Секретов, дампов и тестовых персональных данных в репозитории нет;
  dev-учётки помечены DEVELOPMENT ONLY и отвергаются production-охранником.

## Чистая установка (обычный пользователь)

1. `docker compose -f infra/docker-compose.yml up --build -d` (или
   `make up`) — Postgres + backend + frontend + backup + **worker**.
2. Открыть `http://127.0.0.1:8080`, войти `admin` / `AdminAdmin123`
   (dev-значения; production — `check_env.sh` + реальные секреты).
3. Мастер первой настройки предложит часовую зону и тихие часы
   (можно пропустить).
4. Раздел «Администрирование» → «Создать пилота» (имя + пароль ≥ 12
   символов). Пилот получает полный совмещённый доступ одним логином.
5. Колокольчик, «Уведомления», «Напоминания», «Настройки уведомлений»
   работают сразу; Telegram/email честно показаны «не настроено».

## Ограничения и handoff

1. **CI (требует переноса владельцем):** шаг «Validate HTTPS proxy overlay
   configuration» в перенесённом владельцем `ci.yml` (коммит `784f388`)
   не экспортирует `${SECRET_KEY:?}`/`${POSTGRES_PASSWORD:?}`/
   `${BOOTSTRAP_ADMIN_PASSWORD:?}` (изолированные shell-ы шагов) — шаг
   падает на любом PR. Исправление готово:
   `review-artifacts/ci.agent-2.phase8.yml/.patch` + инструкция в
   `review-artifacts/README.md`. `?`-охраны `compose.prod.yml` намеренно
   не ослаблены. До переноса stack-job красный на этом шаге; остальные
   три job зелёные.
2. Docker в песочнице недоступен: полный Compose-запуск подтверждён
   CI-джобой stack (включая health worker-контейнера); локально — только
   статические проверки (PyYAML + overlay-тесты).
3. `email`/`telegram` — зарезервированный контракт (outbox-столбцы,
   `skipped`-семантика, consent_snapshot, external_recipient); реальные
   адаптеры — этап 9 (там же 429 retry_after, Telegram chat_id привязка,
   SMTP STARTTLS/TLS, Mailpit только для локальной проверки).
4. Сообщения кандидатам и история коммуникаций — этап 10; правила
   автоматизации и ретention-политики истории — этап 11. Событийный
   набор v1 покрывает только перечисленные в промпте триггеры.
5. Напоминания: повторение «по рабочим дням» использует workdays
   владельца (зона самого напоминания); смена настроек после постановки
   учитывается при следующем срабатывании — исходное время сохраняется.
6. Срочная отправка вне тихих часов реализована для внутреннего канала
   (общая механика `bypass_quiet_hours` переносится на будущие каналы).
