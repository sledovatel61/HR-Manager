# Промпт этапа 9 — Telegram и email-интеграции

Ты продолжаешь разработку репозитория `sledovatel61/HR-Manager` после принятого Phase 8. Работай как coding-agent: изучи репозиторий, внеси изменения, выполни проверки и открой PR. Не ограничивайся планом.

## Обязательный старт

1. Начни строго от актуального `origin/main`:

```bash
git fetch origin --prune
git switch main
git pull --ff-only origin main
git status --short --branch
git rev-parse HEAD
```

2. Прочитай полностью:

- `agents.md`;
- `PRODUCT_SPEC.md`;
- `ROADMAP.md`;
- `README.md`;
- `docs/CURRENT_STATUS.md`;
- `docs/ARCHITECTURE.md`;
- `docs/phase-8-report-agent2.md`;
- `prompts/PHASE_8_PROMPT.md`;
- этот файл.

3. Зафиксируй baseline SHA и результаты baseline-проверок. Работай только в отдельной ветке. Если AI Arena уже выдала тебе персональную ветку, сохрани её и используй именно её. Иначе создай `arena/phase-9-telegram-email-<короткий-суффикс-агента>`. Не используй общую ветку без суффикса: Phase 9 параллельно выполняют два независимых агента. Не работай напрямую в `main`, не изменяй ветку другого агента и не выполняй самостоятельный merge.

## Цель

Подключить реальные Telegram Bot API и SMTP-каналы поверх принятого Phase 8 transactional outbox/worker. Внутренние уведомления Phase 8 не ломать. Не добавляй Redis/RabbitMQ и не создавай новые микросервисы без доказанной необходимости.

## 1. Telegram

Реализуй production-ready адаптер Telegram Bot API:

- настройки токена и API endpoint только через environment/secret storage; секреты не попадают в git, БД, ответы API, логи, traces, metrics и отчёты;
- привязка пользователя только после добровольного открытия бота/подтверждения связи;
- одноразовый expiring linking token с безопасной энтропией, TTL и single-use защитой;
- хранение numeric `chat_id`, не username;
- возможность отвязки и повторной привязки с аудитом;
- проверка, что привязка принадлежит текущему пользователю и не позволяет IDOR;
- таймауты, ограничение размера ответа, обработка сетевых ошибок и HTTP-кодов;
- корректная обработка Telegram `429` с `retry_after`, bounded retry и без бесконечных циклов;
- различение временной ошибки, постоянной ошибки и отсутствующего/отозванного канала;
- provider `accepted` не считать прочтением человеком;
- адаптер не должен держать сетевой вызов внутри длинной транзакции.

Токен бота никогда не логировать. Не использовать username как идентификатор получателя.

## 2. SMTP/email

Реализуй универсальный SMTP-адаптер:

- host, port, encryption mode STARTTLS/TLS, username и secret только через environment/secret storage;
- app password/secret не хранить в открытом виде в БД и не выводить в лог;
- безопасные таймауты, ограничение размера сообщения и понятные классы ошибок;
- проверка конфигурации без отправки реального письма;
- отдельная явная тестовая отправка только по admin permission, CSRF и audit;
- провайдерский `accepted` не равен `delivered` и не равен прочтению;
- корректная кодировка русского текста, заголовков и display name без header injection;
- запрет произвольных SMTP-получателей из неразрешённого payload;
- Mailpit разрешён только в dev/test и не считается production delivery.

Не добавляй фиктивную отправку и не отмечай сообщение успешным без реального ответа адаптера.

## 3. Outbox/worker contract

Расширь принятый контракт Phase 8 аккуратно:

- `email` и `telegram` должны проходить через тот же PostgreSQL outbox и immutable delivery-attempt history;
- in-app поведение Phase 8 сохранить без регрессий;
- корректно сохранять provider message ID только если провайдер его вернул;
- `accepted` означает принятие провайдером, а не прочтение;
- retry/backoff bounded, lease recovery и idempotency сохранить;
- сетевые вызовы выполнять вне длинной транзакции, но финальный статус фиксировать атомарно и безопасно при гонках;
- отменённые, отозванные и просроченные задания не должны отправляться;
- duplicate delivery не допускается при параллельных worker;
- не маскировать FK/CHECK/schema/network ошибки под duplicate или `skipped`.

## 4. Preferences и consent

Расширь пользовательские настройки:

- opt-in/opt-out отдельно для Telegram и email;
- consent timestamp, источник и версия условий/политики;
- канал считается доступным только при наличии валидной привязки/конфигурации и согласия;
- изменение настроек и согласий аудируется;
- изменение email требует проверки права и безопасного подтверждения согласно существующей модели пользователей;
- отсутствие внешнего канала не ломает in-app уведомления;
- email/Telegram не включать молча для существующих пользователей.

## 5. API и UI

Добавь безопасные API и русскоязычный UI для:

- состояния интеграций без раскрытия секретов;
- запуска/завершения Telegram linking flow;
- отвязки Telegram;
- настройки email-канала и consent;
- admin-only проверки конфигурации и явной тестовой отправки;
- delivery history с provider status/message ID только в разрешённой области;
- понятных состояний: «не настроено», «требуется подтверждение», «работает», «временно недоступно», «отозвано»;
- loading/empty/error/retry и доступной клавиатурной навигации;
- отображения, что `accepted` не означает прочтение.

Все mutating endpoints защищены существующим CSRF и серверным RBAC. UI не является границей безопасности.

## 6. Безопасность и приватность

Обязательно проверить:

- IDOR для linking, delivery history и admin endpoints;
- CSRF, rate limits и anti-spam для linking/test-send/retry;
- отсутствие bot token, SMTP secret, session token, PII и полного email body в логах/метриках/диагностике;
- корректное экранирование HTML/headers и защита от header injection;
- аудит привязки/отвязки, consent, test-send, retry/cancel, ошибок конфигурации;
- отсутствие автоматической отправки чувствительных кадровых решений;
- retention и backup для credential metadata и истории согласно архитектуре Phase 8.

## 7. Миграции и совместимость

- Alembic upgrade/downgrade/re-upgrade на чистом PostgreSQL;
- существующие миграции `0007`/`0008` и Phase 8 API/worker не ломать;
- новые секреты не добавлять в миграции и не хранить plaintext;
- внешние каналы должны быть optional;
- dev Compose может использовать Mailpit/test double только явно и только для тестов, не выдавая его за production.

## 8. Тесты и Definition of Done

Добавь unit и PostgreSQL integration tests:

- Telegram linking: entropy, TTL, single-use, ownership, revoke/rebind;
- Telegram API success, timeout, `429 retry_after`, permanent errors;
- SMTP STARTTLS/TLS, timeout, encoding, header injection и test-send permission;
- opt-in/opt-out/consent и отсутствие канала;
- worker concurrent delivery, retry, lease recovery, idempotency и cancel race;
- provider `accepted` не превращается в `delivered/read`;
- отсутствие секретов/PII в логах и diagnostics;
- RBAC/IDOR/CSRF/rate-limit проверки;
- migrations upgrade/downgrade/re-upgrade;
- frontend lint/typecheck/tests/build/accessibility;
- Compose config и smoke с каналами отключёнными и с тестовыми адаптерами только в test/dev.

До PR выполни и укажи фактические результаты:

```bash
cd backend
ruff check app tests
ruff format --check app tests
mypy app tests
pytest -m "not integration" -q
pytest -m integration -q

cd ../frontend
npm ci
npm run lint
npm run typecheck
npm test -- --run
npm run build

cd ..
docker compose -f infra/docker-compose.yml config -q
docker compose -f infra/docker-compose.yml up --build -d --wait
git diff --check
```

Если команда недоступна, не называй её зелёной: зафиксируй причину и подтверди проверку соответствующим CI job. Не используй реальные Telegram-чаты, реальные SMTP-учётные данные или реальные персональные данные в тестах.

Phase 9 принимается только если:

1. реальные Telegram/SMTP адаптеры работают через outbox без фиктивного успеха;
2. секреты, consent и получатели защищены;
3. `accepted`, `delivered` и прочтение не смешаны;
4. in-app Phase 8 и backup/worker Compose работают без регрессий;
5. все backend/frontend/integration/migration/Compose/security проверки зелёные;
6. CI зелёный именно для final SHA ветки;
7. создан `docs/phase-9-report-<agent>.md` с baseline/final SHA, командами, числами passed/skipped, ограничениями и handoff.

В конце закоммить изменения, запушь ветку, открой PR в `main`, дождись checks для exact final SHA и в финальном ответе дай URL PR, SHA и только фактически подтверждённые результаты.
