# Отчёт Phase 9 — Telegram и email-интеграции (agent-2)

- **Ветка:** `arena/01a07ba5-hr-manager`
- **Baseline SHA (origin/main на старте):** `fc33d9d3f11341b5f91646da6bd8e76190c6ccce`
- **Final SHA (реализация):** `32e81546abcc4c455c26e57613409df2a0b91899`
  (коммит «feat(phase9): real Telegram Bot API and SMTP channels over
  outbox/worker»; поверх идёт только docs-коммит с этим отчётом —
  tip ветки = верхний из них)
- **PR:** https://github.com/sledovatel61/HR-Manager/pull/12
- **Fix SHA (ruff format):** `2796550` («fix(phase9): format migration
  0009» — CI проверяет `ruff format --check .` из `backend/`, а не
  только `app tests`)
- **Fix SHA (mailpit tag):** `2904662` («fix(phase9): pin existing
  mailpit image tag v1.31» — апстрим не публикует тег `v1`, только
  `latest`/`vX.Y.Z`/`vX.Y`; проверено по build-docker.yml апстрима)
- **CI:** run 34123075933 (head `2904662`): Backend checks — pass
  (1m41s), Backend integration tests — pass (1m43s), Frontend checks —
  pass (48s); stack-job зелёный вплоть до шага владельца «Validate
  HTTPS proxy overlay configuration» — то же предсуществующее падение,
  что и на принятом tip Phase 8 (см. «Ограничения»). Tip-прогон
  run 34123534981 (head `34f01b2`, docs поверх того же кода):
  backend/integration/frontend — pass, stack — та же сигнатура
  (всё зелёное до шага владельца включительно «/health 200»).
  Плюс этот docs-коммит поверх (tip ветки = верхний из них).
- **Доработка (ревью, 8 пунктов):** база `a0973fd`, code-final SHA
  `366e95f26ccaba7a39494a3ec6697f7662aa7ab7` (9 коммитов: 8 rework +
  1 type-fix, только эта ветка, без кода агента №1); tip ветки =
  docs-коммит с разделом «Доработка» поверх. Rework CI:
  run 34130007674 (head `7b53e22`): Backend integration tests —
  success, Frontend checks — success, Backend checks — failure
  ТОЛЬКО на шаге Mypy (1 ошибка типов в тестовой фикстуре,
  исправлена коммитом `366e95f`); stack-job — skipped (зависит от
  backend). Новый run на head `366e95f` на момент отчёта выполняется.
  `.github/workflows` не тронут, merge в `main` не делался.

## Доработка по ревью (agent-2, 8 пунктов ревьюера)

База — `a0973fd` (docs-tip реализации выше). Всё сделано 8 коммитами
в этой же ветке поверх, без переписывания Phase 9 и без переноса кода
агента №1: его ветка (`arena/01a07b9b-hr-manager`) осмотрена только
чтением (`git show FETCH_HEAD`) — его webhook с сравнением секрета
через `!=` не копировался; у нас polling-only by design, что теперь
зафиксировано 404-тестом на `/integrations/telegram/webhook`.

Коммиты (все — поверх `a0973fd`, в прямом порядке):

| # | SHA | Содержание |
|---|---|---|
| 1 | `cebe8791f6f31f8dcc1743f56c7642a81b3f5900` | `consent_granted`-колонки, partial unique index активного `chat_id`, BigInteger, `has_channel_consent()` в fan-out и worker |
| 2 | `234ae22c8b5b456e337c31a627dcb4e237dda7cf` | Строгий consent-контракт (strict-equality 422), worker-гейты, UI-чекбокс согласия |
| 3 | `f5a0f1f0988eae02a2a6aeb7528e9fdcf378e595` | Linking: explicit ownership, fail-closed 409 чужого чата, race backstop |
| 4 | `f2f2ff3607a8f746e7dbc16068b782834c367c47` | Test-send: получатели серверные, RBAC+CSRF+rate-limit тесты |
| 5 | `6553a77537913e93dc424be452d55fcdd9579985` | `external_recipient`: контракт queue-time + revalidation в worker |
| 6 | `af39cfa6534384f61f2bb806d5142c36c615a4c0` | Worker-честность и гигиена логов: `internal_error`, `request_id`, без PII исключений |
| 7 | `d7932286706dfe18c94aaad71447600bd2601e67` | Polling-закрытие: 503/404/409 доказательства, diagnostics без PII |
| 8 | `7b53e2249bb281540c1abcad81dbb69296f836ba` | Фикстура: unowned delivery-строка строится напрямую, не через `schedule()` |
| 9 | `366e95f26ccaba7a39494a3ec6697f7662aa7ab7` | Type-fix: узкий temporary в фикстуре (зелёный mypy `--no-incremental`) |

Покрытие пунктов ревьюера:

1. **Webhook закрыт по умолчанию** — webhook-получателя нет by design
   (только исходящий polling через `getUpdates`); пустой/выключенный
   конфиг даёт 503 до любых мутаций (`link-code`, `confirm`,
   `telegram/test`, `set_email`, admin checks); секреты сравниваются
   только через `secrets.compare_digest` по хэшам (email-код) или
   поиском по хэшу (link-токены) — plaintext-сравнений нет. Тесты:
   503 без сайд-эффектов, webhook-путь 404 для GET и POST, чужой
   токен в инбоксе → 409 с гидом + токен активен + offset продвинут.
2. **Запрет silent consent** — контракт `ConsentUpdate{opt_in,
   consent_granted}`: оба обязательны, несовпадение →
   422 до любых мутаций (и без audit-строки); новые колонки
   `telegram/email_consent_granted` (NOT NULL, default false);
   гейт везде — `opt_in is True and consent_granted is True` через
   `has_channel_consent()` (fan-out, worker, status/confirm/unlink);
   `consent_at`/source/version ставятся при каждом решении.
   UI: кнопка включения заблокирована, пока не отмечен явный
   чекбокс согласия; выключение — без чекбокса (безопасное
   направление). Тесты: reject missing/contradictory grant,
   opt-out/re-enable, opt_in без гранта → skip без сети,
   no-resend после opt-out, fan-out игнорирует `enabled_channels`
   без гранта.
3. **Test-send только себе** — оба endpoint'а не принимают получателя
   вообще; цель выводится сервером (свой привязанный чат / свой
   подтверждённый адрес). Тесты: подделанные `recipient` в body
   игнорируются (`external_recipient` остаётся NULL), admin endpoint —
   403 для не-админа и без CSRF, 429 + Retry-After при исчерпании
   бюджета, на всех reject-путях очередь пуста.
4. **Нет PII/секретов в логах/audit/diagnostics** — crash-логи
   адаптеров и worker'а несут только тип исключения (`exc_info` и
   `str(exc)` удалены: сообщения/трейсбэки могут эхать адрес,
   URL с токеном бота или данные строки); SMTP/Telegram warnings —
   код/класс + `request_id` (id outbox-строки); diagnostics —
   счётчики/статусы/heartbeat (доказано маркерными строками).
   Аудит конфликтов и тестов — без идентификаторов чатов/адресов.
5. **Linking** — explicit `token.user_id != user.id` → 403 до
   polling/мутаций; чужой активный чат → 409 + audit
   `TELEGRAM_LINK_CONFLICT` (без chat id), холдер не тронут,
   start-event удалён, токен активен (повтор тем же кодом после
   отвязки); DB-уровень — partial unique index
   `uq_telegram_links_chat_id_active` + конвертация duplicate-key
   `IntegrityError` в тот же 409 (backstop same-chat гонки);
   inactive → 401 на confirm (зависимость гейтит хендлер).
   Тесты: fail-closed unit, inactive 401, PG two-thread race
   «один чат — два аккаунта» → ровно один 200, одна активная
   привязка, токен проигравшего активен, один audit (5/5 стабильно).
6. **`external_recipient` не обходит consent/authorization** —
   `schedule()` принимает его ТОЛЬКО для verification-письма
   (EMAIL + `email_verification` + валидный mailbox), иначе
   ValueError и ничего не queued; worker перепроверяет адрес в
   момент отправки (кованые строки → skip без сети);
   не-верификационные шаблоны всегда резолвят получателя из
   собственной подтверждённой привязки и игнорируют stored
   external. Тесты: misuse-матрица `schedule()` + forged rows
   с взрывающимся sender-double.
7. **Worker-контракт** — accepted/delivered/lease/retry/cancel-wins
   сохранены; новое: инфраструктурный краш `process_row` пишется
   как `internal_error` (стирает stale provider-вердикт прошлой
   попытки, включая `error_code`), в лог — только тип; phase-B
   backstop — `transport_error` + bounded retry (лимит попыток;
   бесконечных loop'ов нет), в лог — тип + `request_id`.
   External-строки никогда не `delivered`. Тесты: stale-класс
   заменён, секреты сообщений отсутствуют в `caplog`, корреляция
   по `request_id` присутствует.
8. **Миграция 0009** — правки: индекс активного `chat_id`,
   2 `consent_granted`-колонки, downgrade в обратном порядке;
   модели `chat_id`/`last_update_id` Integer→BigInteger (миграция
   уже была BigInteger). Верификация на реальном PG (pgserver):
   upgrade head → downgrade 0008 (таблицы 0009 исчезли, users целы)
   → re-upgrade head (`current = 0009 (head)`); индекс эффективен
   (`WHERE revoked_at IS NULL AND chat_id IS NOT NULL`, дубликат
   активного чата отклонён `IntegrityError`); `chat_id` — bigint;
   гранты — boolean default false; hash-only токены, каналы
   выключены по умолчанию, секретов в миграции нет.

Инварианты безопасности (все зафиксированы тестами):

- без пары `opt_in=true + consent_granted=true` внешняя отправка
  невозможна ни планированием, ни worker'ом, ни relink'ом;
- один активный Telegram-чат — один пользователь (индекс + 409);
- тестовая отправка не принимает получателя от клиента;
- `external_recipient` существует только внутри verification-письма;
- в логах/audit/diagnostics/API-ответах нет токенов, паролей,
  chat id, адресов и текстов сообщений (только маски/классы/коды);
- `accepted ≠ delivered ≠ read`; infra-ошибки не маскируются под
  provider (`internal_error`/`transport_error`, bounded retry).

Числа доработки (песочница, локальный прогон):

```bash
# backend: ruff check . / ruff format --check . (из backend/) — чисто, 82 файла
# mypy app tests — чисто, 72 файла
pytest -m "not integration" -q   # 403 passed, 76 deselected (было 376/75; +27)
TEST_DATABASE_URL=... pytest -m integration -q
  # 64 passed, 11 skipped (нет pg_dump в песочнице), 403 deselected;
  # +1 новый тест (same-chat race, файл 11→12);
  # 1 env-only фейл test_health_degrades_against_stopped_postgresql:
  # его URL-regex не матчит socket-DSN песочницы (доказано отдельно),
  # на TCP-DSN CI сработает как раньше (этот тест в CI зелёный)
# frontend: eslint чисто, tsc -b чисто, vitest 117 passed, vite build ✓
# docker compose config — не запускался (нет Docker в песочнице;
#   compose-файлы доработкой не тронуты); YAML-валидация — за CI stack-job
# git diff --check — чисто
```

Ограничения rework-CI: run 34130007674 (head `7b53e22`, до type-fix)
дал integration+frontend success и единственное падение — шаг Mypy
в Backend checks (моя ошибка в тестовой фикстуре; локальный
инкрементальный `mypy` её скрыл, `--no-incremental` поймал;
исправлено в `366e95f`, локально `mypy app tests --no-incremental`
чисто). Новый run на head `366e95f` на момент отчёта выполняется —
заявлять его зелёным рано. Ожидаемая итоговая сигнатура — как у всех
голов этой ветки: backend/integration/frontend success +
предсуществующее падение stack-job на шаге владельца «Validate HTTPS
proxy overlay configuration» (нет экспортов секретов во fresh shell;
чинить `.github/workflows/ci.yml` из этой среды запрещено сервером —
нет `workflows` permission; фикс — 3 export'а владельца).
В тестах доработки — только doubles/стабы, без реальных чатов,
SMTP-учёток и PII.

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

- `stack` CI-job: dev/prod Compose-валидации, сборка и запуск всего стека
  (включая Mailpit `axllent/mailpit:v1.31`) и `/health` 200 — зелёные;
  job останавливается на шаге владельца «Validate HTTPS proxy overlay
  configuration», как и на принятом tip Phase 8 (run 34096234548).
  Причина предсуществующая и не связана с Phase 9: шаг не экспортирует
  `SECRET_KEY`/`POSTGRES_PASSWORD`/`BOOTSTRAP_ADMIN_PASSWORD`, а каждый
  step — fresh shell, поэтому `config` с `${VAR:?}` падает до проверок.
  Фикс тривиален (3 export по образцу соседнего шага), но применить его
  из этой среды невозможно: push workflow-файлов отклонён сервером —
  `refusing to allow a GitHub App to create or update workflow ...
  without 'workflows' permission` (ограничение задокументировано в
  `docs/ARCHITECTURE.md` ещё с этапа 1; по той же причине шаг не чинил
  и Phase 8). Владельцу достаточно добавить 3 строки в `ci.yml`.
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
