# Отчёт Phase 8 — фундамент уведомлений и пилотный режим (agent-2)

- **Ветка:** `arena/01a060e3-hr-manager`
- **Baseline SHA (origin/main на старте):** `d18ec9efc7973d8c86c9ecedf0d3e2f122c7ad2b`
- **Final SHA (реализация):** `bb1dfaa54ecc31e4d5ccdb6914ecca1df628e1d5`
  (верхний коммит ветки — docs-фикс с этим же SHA внутри отчёта)
- **Fix SHA (ответ на ревью оркестратора, текущий tip):**
  `4b1c8c5` (коммит «fix(phase8): address review blockers on notifications
  foundation» поверх `b0c4ab3`)
- **PR:** https://github.com/sledovatel61/HR-Manager/pull/10
- **CI:** run 34096234548 (PR #10, head 4b1c8c5) — прогон fix-tip;
  run 34090939408 (head bb1dfaa) — прогон реализации;
  run 34091223809 (head b0c4ab3) — прогон, отклонённый ревью;
  run 34089737761 (первый пуш bcb4176), run 34090710477 (e175ab4) — промежуточные

## Резюме

Реализован полный принятый scope этапа: внутренний центр уведомлений без
моков, личные напоминания, transactional outbox на PostgreSQL и отдельный
worker, тихие часы (пересечение полночи, DST, рабочие дни), неизменяемая
история попыток доставки, явно назначенный пилотный пользователь с полным
совмещённым доступом, идемпотентный мастер настройки, админ-диагностика
очереди без PII, UI на русском и worker в dev/prod Compose. Миграции
`0007`/`0008`, 348 backend-тестов (284 unit + 64 integration) и 111
frontend-тестов, все локальные проверки зелёные; CI fix-tip `4b1c8c5`
(run 34096234548): backend/frontend/integration — зелёные, stack-job
останавливается на шаге владельца «Validate HTTPS proxy overlay
configuration» (см. «Ограничения»). Ревью оркестратора (b0c4ab3) с тремя
блокирующими замечаниями закрыто коммитом `4b1c8c5` — см. раздел
«Ответ на ревью оркестратора».

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

## Ответ на ревью оркестратора (b0c4ab3 → 4b1c8c5)

Три блокирующих замечания закрыты отдельным коммитом `4b1c8c5`; каждый
пункт имеет regression-тесты, закрепляющие контракт.

| # | Замечание | Исправление | Тесты |
|---|-----------|-------------|-------|
| 1 | `list_reminders` отдавал только `assignee_user_id`, владелец не видел делегированные напоминания | Фильтр заменён на `or_(Reminder.assignee_user_id == user.id, Reminder.owner_user_id == user.id)`; docstring фиксирует owner-or-assignee | `test_reminders_api`: assert «владелец видит делегированное» в существующем тесте делегирования + новый `test_list_scope_is_owner_or_assignee` (owner видит 2, assignee — 1, посторонний — пусто/404) |
| 2 | `schedule`/`deliver_in_app` ловили любой `Exception` и делали `db.rollback()` — маскировали FK/CHECK/схему и ломали внешнюю транзакцию | `_flush_inside_savepoint()`: INSERT внутри `db.begin_nested()`; дубликат = ТОЛЬКО unique-violation нужного класса (`psycopg.errors.UniqueViolation` на PG, `sqlite3.IntegrityError` с текстом «UNIQUE constraint failed» в юнитах) → откат только сейвпоинта, возврат None/False, транзакция вызывающего цела. Любое другое исключение пробрасывается | `test_notification_service`: dedupe-keeps-outer-transaction-intact, propagates CHECK violation, deliver-returns-False-only-for-duplicate; `test_integration_worker` (PostgreSQL): propagates FK violation + отсутствие строки |
| 3 | `backup_scheduler.sh` ломался на Windows-checkout (CRLF shebang, `env: 'bash\r'`), backup unhealthy | `.gitattributes`: `*.sh text eol=lf`, `infra/scripts/*.sh`, Dockerfile-ы, compose-файлы, nginx-шаблон — eol=lf; `Dockerfile.backup`: `sed -i 's/\r$//'` перед `chmod` (страховка для репозиториев, склонированных до атрибута) | `test_production_overlay::test_backup_scheduler_line_endings_protected` (атрибут, sed-слой, отсутствие CR в коммитнутом скрипте, точный shebang). Локальная симуляция: CRLF-копия падает (`set: pipefail\r: invalid option name`), после sed-нормализации — «backup scheduler started» (exit 0) |

Контракт дедупликации теперь явно прописан в docstrings `schedule()` и
`deliver_in_app()`: возврат None/False — только при дубликате ключа;
никакая другая ошибка не маскируется и не откатывает чужую транзакцию.

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
- Fix-коммит `4b1c8c5`: `backend/app/notification_service.py` (savepoint
  dedupe, `is_duplicate_key_error`), `backend/app/routers/reminders.py`
  (owner-or-assignee фильтр), `.gitattributes` (eol=lf),
  `backend/Dockerfile.backup` (sed-нормализация CR),
  `backend/tests/{test_notification_service,test_reminders_api,
  test_integration_worker,test_production_overlay}.py` (regression-тесты).

## Проверки — команды и результаты

Локально (песочница, Python 3.11 venv, PostgreSQL 16.2 из pgserver
16.2-бинарников; Docker недоступен — Compose проверяется статически +
CI-джобой). Числа ниже — для fix-tip `4b1c8c5` (финальный прогон):

```bash
# backend
cd backend
ruff check app tests                           # All checks passed
ruff format --check app tests                  # 64 files already formatted
mypy app tests                                 # Success: no issues found in 64 source files
pytest -m "not integration" -q                 # 284 passed, 64 deselected (SQLite-юниты)
TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/hr_manager_backup_test \
BACKUP_PGDUMP_BIN=…/pg_dump BACKUP_RESTORE_BIN=…/pg_restore \
  pytest -m integration -q                     # 64 passed, 0 skipped (PostgreSQL 16.2)
# итого backend: 348 passed (284 unit + 64 integration)
# миграции на чистой БД: alembic upgrade head (8) → downgrade base (8) →
#   upgrade head (8); alembic current = 0008 (head)
# overlay: pytest tests/test_production_overlay.py -q   # 24 passed

# frontend
cd frontend
npm run lint                                    # OK
npm run typecheck                               # OK
npm test -- --run                               # 111 tests passed
npm run build                                   # OK (dist)

# статический Compose (Docker недоступен): PyYAML(!reset)-разбор
# dev/prod/proxy — валиден; git diff --check — чисто
```

CI (GitHub Actions, Linux/Python 3.12/PG 16), прогон fix-tip
run 34096234548 (head 4b1c8c5):

- **Backend checks** ✓ (ruff/format/mypy/pytest + preflight);
- **Frontend checks** ✓ (lint/typecheck/test/build/npm audit);
- **Backend integration tests (PostgreSQL)** ✓ (весь сьют);
- **Compose stack smoke** — все шаги ✓ (validate dev config, validate
  production overlay, полный `up --build --wait`: backend/worker/backup
  healthy, `/health` 200), кроме ✗ «Validate HTTPS proxy overlay
  configuration» — шаг владельца, см. «Ограничения» (шаги «Verify the
  backup service produced an encrypted backup» и далее не запускаются,
  т. к. идут после упавшего шага). Подпись падения идентична прогону
  b0c4ab3 — баг не в ветке, перенос патча за владельцем.

CI run 34090939408 (head bb1dfaa, реализация): Backend ✓, Frontend ✓,
Integration ✓, stack — то же единственное ✗. Run 34091223809 (b0c4ab3,
отклонён ревью) — та же подпись.

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
