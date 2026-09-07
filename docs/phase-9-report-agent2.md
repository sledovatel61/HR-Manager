# Отчёт Phase 9 — Telegram и email-интеграции (agent-2)

- **Ветка:** `arena/01a07ba5-hr-manager`
- **Baseline SHA (origin/main на старте):** `fc33d9d3f11341b5f91646da6bd8e76190c6ccce`
- **Final SHA (реализация):** `32e81546abcc4c455c26e57613409df2a0b91899`
  (коммит «feat(phase9): real Telegram Bot API and SMTP channels over
  outbox/worker»; поверх идёт только docs-коммит с этим отчётом —
  tip ветки = верхний из них)
- **PR:** https://github.com/sledovatel61/HR-Manager/pull/12
- **CI:** ожидается прогон для exact final SHA (см. checks PR #12)

## Резюме

Подключены реальные Telegram Bot API и SMTP-каналы поверх принятого
Phase 8 transactional outbox/worker. In-app поведение, RBAC, CSRF, audit,
backup и Compose 0–8 сохранены без регрессий. Новых брокеров/микросервисов
нет: оба адаптера — stdlib (urllib/smtplib) внутри существующего worker-а.

Ключевые свойства:

- привязка Telegram — только добровольным Start у бота, одноразовый
  expiring токен (сырой показан один раз, хранится SHA-256), numeric
  `chat_id` (username не используется), отвязка/повторная привязка с
  аудитом, IDOR-защита, Telegram `429` с `retry_after`;
- SMTP — режимы `none|starttls|tls`, секреты только env, проверка
  конфигурации без отправки, admin-only тест на собственный адрес,
  русские тексты через RFC 2047, запрет header injection и произвольных
  получателей;
- worker — сеть вне транзакций, атомарная финализация, cancel-wins при
  гонках, advisory-lock single-flight параллельных worker-ов, bounded
  retry, lease recovery, `accepted ≠ delivered ≠ read`;
- consent — раздельный opt-in Telegram/email (метка времени, источник,
  версия политики `phase9-v1`), без согласия внешние строки не
  создаются; существующим пользователям ничего не включается молча;
- UI «Интеграции» на русском: wizard привязки, email-формы, тумблеры
  согласия, admin-проверки, дисклеймер о честности доставки;
- миграция `0009`, Mailpit в dev Compose как явный локальный sink,
  prod overlay + `check_env.sh` preflight для каналов.

## Архитектурные решения

- **Outbox — тот же контракт:** `schedule_fan_out` создаёт in-app строку
  с phase-8 ключом + внешние строки с ключами `{key}:{channel}` и
  snapshot согласия. In-app row → `notifications`; внешние строки идут
  тем же claim/lease/retry и append-only историей попыток.
- **Сеть вне транзакций:** external-доставка — фаза A (re-lock,
  re-validate привязки/конфига/согласия, продление lease, commit) →
  фаза B (провайдер без открытой транзакции, bounded таймауты) → фаза C
  (re-lock, атомарная финализация). Admin-cancel, выигравший гонку, не
  перезаписывается; отозванные/просроченные/отменённые не отправляются.
- **Single-flight:** PostgreSQL advisory lock на ключ строки на все три
  фазы (пинованное соединение сессии; смерть worker-а отпускает lock
  разрывом соединения; lease recovery — backstop). Интеграционный тест
  с двумя потоками доказал: ровно один вызов провайдера, ровно одна
  попытка. SQLite-юниты lock пропускают (однопоточны).
- **Честность статусов:** `provider_message_id` сохраняется только если
  провайдер его вернул; адаптеры никогда не выставляют `delivered`;
  `delivered_at` внешних строк всегда NULL (зафиксировано тестами).
- **Ошибки не маскируются:** `IntegrityError` вне точного duplicate-ключа
  пробрасывается (не «duplicate»/«skipped»); коды ошибок SMTP/Telegram —
  фиксированные токены без текста сервера, PII и секретов.
- **Приватность:** токен бота, SMTP-пароль, chat id, тексты и адреса —
  вне логов (тесты `caplog`), метрик, диагностики и API-ответов
  (маскирование); в БД секретов нет.

## Покрытие ТЗ (по разделам промпта)

1. **Telegram** — адаптер `app/telegram.py` (таймауты, кап ответа,
   классы ошибок), linking flow (§ «Архитектура» выше), `429` →
   `next_attempt_at = now + retry_after` (capped), blocked-bot →
   авто-отзыв, токен только env.
2. **SMTP** — адаптер `app/smtp.py` (STARTTLS/TLS, таймауты, лимит
   размера), `check` без отправки, `test-send` admin-only + CSRF +
   audit + rate-limit на собственный подтверждённый адрес, RFC 2047,
   CR/LF → 422, получатели только из allow-list конструкции.
3. **Outbox/worker** — тот же PG outbox и immutable history; in-app без
   регрессий (все phase-8 тесты зелёные); provider id по факту; bounded
   backoff (60 c → 3600 c, лимит попыток); lease recovery; cancel-wins;
   advisory single-flight; без маскировки ошибок.
4. **Preferences/consent** — раздельный opt-in + timestamp/источник/
   версия, audit изменений, email меняется только с подтверждением
   токеном из письма (bounded попытки), in-app не зависит от внешних
   каналов, silent enable нет (тест: без consent — ровно одна строка).
5. **API/UI** — `/integrations/status` (маски, 5 честных состояний),
   link-code/confirm/unlink, email set/confirm/remove, consent endpoints,
   admin checks/test, `GET /notifications/deliveries{,/{outbox_id}}`
   (своя история с provider id, чужое — 404), UI «Интеграции» с
   loading/error/retry и доступностью через design-system. Все мутации —
   CSRF + серверный RBAC.
6. **Безопасность** — IDOR/ CSRF/rate-limit тесты; caplog-тесты на
   отсутствие секретов/PII; header-injection тесты; audit link/unlink/
   consent/test-send/retry/cancel/config-check; новых чувствительных
   типов уведомлений не добавлено (внешне уходит только то, на что
   пользователь явно подписался); backup покрывает новые таблицы без
   изменения формата (только хэши/маски).
7. **Миграции** — `0009` upgrade/downgrade/re-upgrade на чистом PG
   проверены (см. «Команды»); `0007`/`0008` и Phase 8 API/worker целы;
   секретов в миграциях нет; каналы optional; Mailpit только dev/test.
8. **Тесты/DoD** — unit + PG integration (см. «Числа»); Compose config
   валидируется в CI `stack`-job (в песочнице нет Docker — YAML
   проверен парсером + overlay unit-тестами без Docker).

## Числа

| Контур | Baseline (`main` @ phase-8 report) | Final (эта ветка) |
|---|---|---|
| Backend unit (`-m "not integration"`) | 284 passed | **376 passed**, 75 deselected |
| Backend integration (real PG) | 64 всего (53 passed + 11 skipped без pg_dump) | **64 passed, 11 skipped** (те же pg_dump-скипы песочницы; в CI с client-16 будет 75 passed), 373 deselected |
| Frontend (Vitest) | 111 | **117 passed** (17 файлов) |
| Ruff check / format / mypy | — | чисто, 72 файла |
| Frontend lint / typecheck / build | — | чисто, `vite build` OK |

Новые тесты (+92 unit, +11 integration, +6 frontend):

- `test_telegram_adapter.py` + `test_smtp_adapter.py` — 48: успех,
  таймауты, сеть, `429 retry_after`, permanent/temporary классы,
  STARTTLS/TLS handshake, кодировка русских тем, header injection,
  caplog «без секретов/PII», границы размеров;
- `test_worker_external.py` — 18: accepted+provider-id, bounded retry,
  revoked/expired/cancelled не отправляются, cancel-race, backstop,
  fan-out контракт, silent-enable нет;
- `test_integrations_api.py` — 20: entropy/hash/supersede, numeric
  chat_id, IDOR, single-use, unlink+relink, consent `phase9-v1`, email
  set/confirm/remove, admin без секретов, CSRF 403, rate-limit
  429+Retry-After;
- `test_notifications_api.py` — 3: `deliveries/{outbox_id}` для внешней
  строки с попытками, чужое/unowned — 404, список с provider id;
- `test_production_overlay.py` — 3: dev SMTP→Mailpit/бот выключен,
  prod disabled-by-default + сброс портов Mailpit, `check_env.sh`
  фейлит incomplete-enabled и `SMTP_ENCRYPTION=none`;
- `test_integration_external.py` — 11 (real PG + stub HTTP Telegram +
  stub TCP SMTP): end-to-end доставка обоих каналов, 429→успех,
  confirm end-to-end, confirm-гонка двух потоков (200+409),
  concurrent delivery без дубля (advisory lock), lease recovery,
  cancel-race, SMTP 550 → permanent, admin check без отправки, fan-out
  3 строки + dedupe;
- `IntegrationsPage.test.tsx` — 6: not-configured, linking flow,
  consent-тумблер, email flow, скрытие admin-блока, admin checks.

## Команды и фактические результаты

```bash
# backend (venv песочницы; PG на 127.0.0.1:55433)
ruff check app tests                    # All checks passed!
ruff format --check app tests           # 72 files already formatted
mypy app tests                          # Success: no issues found in 72 source files
pytest -m "not integration" -q          # 376 passed, 75 deselected
TEST_DATABASE_URL=... pytest -m integration -q
                                        # 64 passed, 11 skipped (нет pg_dump в песочнице)

# frontend
npm run lint                            # чисто
npm run typecheck                       # чисто
npx vitest run                          # 117 passed (17 files)
npm run build                           # ✓ built

# миграции на чистой БД hr_manager_mig
alembic upgrade head                    # ... 0008 -> 0009 OK
alembic downgrade 0008                  # downgrade OK, phase-9 таблиц: 0
alembic upgrade head                    # re-upgrade OK, таблиц: 5, current = 0009 (head)

# compose/окружение
git diff --check                        # чисто
bash -n infra/scripts/check_env.sh      # OK + поведенческие кейсы (см. overlay-тесты)
python -c yaml.safe_load(dev compose)   # services: backend backup db frontend mailpit worker
python -c yaml(+!reset) prod overlay    # backend/worker: по 18 phase9-переменных; mailpit ports reset
```

Не выполнялось локально (нет Docker в песочнице, честно делегировано CI):
`docker compose config`, `up --build --wait`, полный `stack`-job.
Взамен: YAML-парсинг обоих файлов, overlay unit-тесты без Docker,
`check_env.sh` behavior-тесты. В тестах не использовались реальные
Telegram-чаты, реальные SMTP-учётные данные и реальные персональные
данные (только stub-серверы и `example.com`).

## Ограничения и известные риски

- `stack` CI-job (dev/prod/proxy Compose + Mailpit образ `axllent/mailpit:v1.31`
  с Docker Hub) подтвердится только прогоном CI для final SHA.
- STARTTLS-live покрыт fake-based unit-тестами и общим кодовым путём;
  интеграционный SMTP-stub — plaintext (как Mailpit); ручная проверка
  против реального провайдера не выполнялась (запрещена ТЗ в тестах).
- `GET /notifications/deliveries` отдаёт попытки пустым списком
  (детализация — в `/deliveries/{outbox_id}`); пагинация/фильтры по
  каналу не добавлялись (вне scope).
- Прод-оператору: канали `TELEGRAM_*`/`SMTP_*` — только env;
  `--scale mailpit=0` для `up` в production при желании.

## Handoff следующему этапу

- Ветка `arena/01a07ba5-hr-manager`, PR ветка → `main`; после merge
  обновить `docs/CURRENT_STATUS.md` (блок «Phase 9 — принято», SHA
  merge-коммита) — до приёмки файл не трогал намеренно.
- Архитектура каналов зафиксирована в `docs/ARCHITECTURE.md`
  (раздел «Telegram и email (этап 9)»), env — в `.env.example`.
- Точка входа в код: `backend/app/telegram.py`, `backend/app/smtp.py`,
  `backend/app/worker.py` (`process_external_row`),
  `backend/app/routers/integrations.py`, миграция `0009`,
  `frontend/src/features/notifications/IntegrationsPage.tsx`.
- Окружение песочницы (`/tmp` venv, PG 55433) вне снапшотов и в репозиторий
  не входит.

## Файлы (инвентарь ветки)

Новые: `backend/alembic/versions/0009_external_channels.py`,
`backend/app/{telegram,smtp}.py`, `backend/app/routers/integrations.py`,
`backend/tests/test_{telegram,smtp}_adapter.py`,
`test_worker_external.py`, `test_integrations_api.py`,
`test_integration_external.py`,
`frontend/src/features/notifications/IntegrationsPage{.tsx,.test.tsx}`,
`docs/phase-9-report-agent2.md`.

Изменены: `backend/app/{config,main,models,notification_service,worker}.py`,
`routers/{candidates,events,notifications,setup}.py`, `schemas.py`,
`tests/{conftest,test_migrations,test_notifications_api,test_production_overlay}.py`,
`frontend/src/{api,types}.ts`, `app-shell/{Workspace,useWorkspaceSection}.ts*`,
`features/notifications/{PreferencesPage,notifications.css}`,
`.env.example`, `infra/{docker-compose,compose.prod}.yml`,
`infra/scripts/check_env.sh`, `docs/ARCHITECTURE.md`.
