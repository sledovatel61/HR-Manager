# Phase 9 — адресная доработка реализации агента 2

## Контекст

Ты — агент №2. Твоя реализация Phase 9 находится в ветке `arena/01a07ba5-hr-manager`, PR #12.

- Baseline: `fc33d9d3f11341b5f91646da6bd8e76190c6ccce`
- Текущий заявленный tip: `a0973fde3e4078e32f5433cf095700a1d64066f9`
- Реализация Phase 9 уже выполнена. **Не переписывай Phase 9 с нуля и не переноси код агента №1.**
- Нужно адресно устранить замечания независимого review и подготовить новый exact final SHA.

Перед началом:

1. Проверь текущий HEAD ветки и убедись, что работаешь именно в `arena/01a07ba5-hr-manager`.
2. Сравни его с baseline `fc33d9d3f11341b5f91646da6bd8e76190c6ccce`.
3. Изучи исходный Phase 9 prompt и собственный отчёт `docs/phase-9-report-agent2.md`.
4. Не меняй уже принятые инварианты Phase 8 без необходимости.

## Цель

Сделать реализацию Phase 9 готовой к независимому принятию: безопасной, согласованной с контрактом, покрытой тестами и проходящей локальные проверки. Никаких реальных Telegram-чатов, SMTP-учётных данных или персональных данных в тестах.

## Обязательные исправления

### 1. Telegram webhook должен быть закрыт по умолчанию

Проверь endpoint webhook и его конфигурацию.

Требования:

- Если webhook secret не настроен, webhook не должен принимать внешние обновления молча.
- При пустом секрете endpoint должен либо возвращать `503`/`500` как misconfigured, либо использовать другой явно безопасный механизм, предусмотренный архитектурой.
- При настроенном секрете сравнение должно быть constant-time (`secrets.compare_digest`), без логирования значения заголовка или секрета.
- Некорректный секрет — `403`.
- Добавь тесты для пустого секрета, неверного секрета и корректного секрета.
- Не раскрывай raw link token, bot token, SMTP password или полные PII в ответах и логах.

### 2. Запретить silent activation consent

Для Telegram и email opt-in должен включаться только при явном `*_consent_granted=True`.

Требования:

- `email_opt_in=true` при `email_consent_granted` отсутствующем или `false` не включает канал и возвращает понятную валидационную ошибку либо безопасно оставляет opt-in выключенным — выбери и задокументируй единый контракт.
- Аналогично для Telegram.
- Нельзя считать `None` согласием.
- Нельзя активировать внешний канал только из `enabled_channels`.
- При opt-out доставка должна прекращаться для новых отправок.
- Consent должен сохранять `timestamp`, `source` и policy/terms version; отзыв не должен приводить к отправке.
- Добавь API-тесты на отсутствие поля consent, `false`, `true`, opt-out и повторное включение.

### 3. Ограничить admin test-send получателями

Проверь контракт PR: test-send — admin-only, но произвольный получатель из payload запрещён.

Требования:

- Telegram test-send разрешён только на заранее привязанный/подтверждённый `chat_id` администратора либо на явно разрешённый серверный allowlist. Нельзя принимать произвольный numeric `chat_id` из payload.
- Email test-send разрешён только на собственный подтверждённый email администратора либо на явно разрешённый серверный allowlist. Нельзя отправлять на произвольный адрес из payload.
- Если переданный `recipient` не совпадает с разрешённым получателем — `403` или `422`, без сетевого вызова.
- Сохрани RBAC, CSRF и rate limit.
- Добавь тесты: admin на разрешённый адрес, admin на чужой/неподтверждённый адрес, обычный пользователь, отсутствие recipient.
- Не сохраняй полный email или полный chat ID в audit/logs; используй маскирование или безопасный идентификатор.

### 4. Устранить PII и секреты из логов, audit и diagnostics

Проведи поиск по всей Phase 9 реализации.

Запрещено логировать или возвращать:

- bot token;
- link token в открытом виде;
- SMTP password;
- полный email получателя;
- полный Telegram `chat_id`;
- тело/тему уведомления в диагностике;
- необработанные тексты исключений, если в них могут быть URL, credentials, provider payload или PII.

Требования:

- SMTP warning должен содержать безопасный код ошибки и технический correlation/request id, но не email-адрес.
- Список refused recipients не должен попадать в лог целиком.
- Audit details для test-send и linking должны быть PII-minimal: channel, outcome, безопасный masked identifier или hash.
- Пользовательский API status может показывать только необходимый masked status.
- Admin diagnostics не должны отдавать пароль, bot token, raw provider response или внутренние traceback details.
- Добавь тесты, перехватывающие логи и проверяющие отсутствие секретов/PII.

### 5. Telegram linking: ownership, IDOR и уникальность

Проверь полный lifecycle linking/relink/unlink.

Требования:

- Проверка принадлежности link token текущему пользователю должна выполняться до любой необратимой мутации состояния. Нельзя сначала помечать token использованным, менять preferences, а потом надеяться на rollback.
- IDOR-тест должен проверять не только HTTP `403`, но и неизменность token, binding, consent и preferences.
- Один numeric Telegram `chat_id` не может быть одновременно привязан к нескольким активным пользователям. Обеспечь database-level unique partial index/constraint либо эквивалентную транзакционно безопасную защиту.
- Релинк существующего chat ID должен иметь однозначную безопасную политику: отказ, предварительный revoke или явное подтверждение. Не допускай тихого перехвата чужой привязки.
- Одновременное подтверждение одного token должно приводить максимум к одной успешной привязке.
- Деактивированный пользователь не может подтвердить token.
- Добавь PostgreSQL integration tests на duplicate chat ID, concurrent/single-use confirm, IDOR rollback/immutability, unlink/relink и inactive user.

### 6. External recipient не должен обходить consent и authorization

Проверь все места создания `external_recipient` и обработку в worker.

Требования:

- Обычный пользовательский flow не должен позволять создать outbox с произвольным внешним recipient в обход ownership/consent.
- Если `external_recipient` нужен для административного или системного сценария, зафиксируй серверный allowlist/authorization и добавь audit.
- Worker не должен отправлять на внешний recipient только потому, что строка существует в outbox.
- Некорректный, неподтверждённый или неразрешённый recipient должен завершаться безопасным `skipped`/`failed` без сетевого вызова.
- Добавь regression tests на попытку обойти consent через `external_recipient`.

### 7. Worker и ошибки

Сохрани контракт:

- `accepted` означает только принятие провайдером;
- `delivered` не выставляется для Telegram/SMTP без подтверждения provider webhook;
- lease и retry остаются bounded;
- cancel-wins race сохраняется;
- network-вызов выполняется вне долгой DB-транзакции;
- каждая попытка append-only.

Проверь, что неожиданные DB/schema/FK/CHECK ошибки не маскируются как обычная ошибка внешнего провайдера. Ожидаемые сетевые ошибки можно retry-ить, но инфраструктурные ошибки должны быть заметны и не приводить к бесконечному повтору.

Добавь/обнови тесты на:

- Telegram 429 с `retry_after`;
- Telegram 403 с автоматическим revoke/без повторной отправки;
- SMTP 4xx/5xx/550;
- provider accepted с сохранением provider message id;
- cancel перед сетевым вызовом;
- lease recovery;
- max attempts;
- отсутствие повторной отправки после opt-out.

### 8. Миграция и инварианты БД

Проверь migration `0009` на чистом PostgreSQL и базе с данными.

Требования:

- `upgrade head`;
- `downgrade 0008`;
- повторный `upgrade head`;
- downgrade с существующими linking/preferences/outbox данными;
- проверка FK и `ON DELETE CASCADE`;
- unique protection для активного Telegram `chat_id`;
- hash-only storage для link tokens;
- внешние каналы по умолчанию выключены;
- отсутствие хранения секретов.

Если структура migration уже корректна, не создавай дублирующие таблицы или параллельную модель без необходимости. Обнови модели и миграцию согласованно.

## CI/workflow

Не изменяй `.github/workflows/ci.yml`, если сервер не предоставляет разрешение на изменение workflow. В отчёте честно зафиксируй ограничение.

Известная проблема CI: job stack проходит сборку, запуск полного Compose-стека и проверку `/health`, но затем падает на шаге `Validate HTTPS proxy overlay configuration`, потому что этот шаг запускается в новом shell и не получает обязательные `${VAR:?}` production-переменные. Это не нужно маскировать как зелёный CI.

Если у тебя есть разрешение безопасно исправить workflow — можно исправить только передачу необходимых переменных в отдельный proxy step, не ослабляя проверки и не добавляя секреты в репозиторий. Если разрешения нет — не обходи защиту и не редактируй workflow косвенно.

## Требования к тестам и проверкам

Перед финальным commit выполни и сохрани фактические результаты:

```text
cd backend
ruff check .
ruff format --check .
mypy app tests
pytest -m "not integration" -q
pytest -m integration -q

cd ../frontend
npm run lint
npm run typecheck
npm run test -- --run
npm run build

cd ..
docker compose -f infra/docker-compose.yml config
```

Если Docker или PostgreSQL недоступны, не выдавай проверки за выполненные: укажи точную команду и причину пропуска. Для migration и integration используй реальный PostgreSQL в CI/локальном контейнере, а для внешних провайдеров — контролируемые test doubles.

Проверь также:

```text
git diff --check fc33d9d3f11341b5f91646da6bd8e76190c6ccce..HEAD
git status --short
```

## Отчёт и результат

После исправлений:

1. Создай/обнови `docs/phase-9-report-agent2.md`.
2. В отчёте укажи:
   - baseline SHA;
   - каждый commit и его назначение;
   - финальный exact SHA;
   - фактические команды и результаты тестов;
   - известные ограничения CI/окружения;
   - какие security-инварианты проверены.
3. Не заявляй `CI green`, если хотя бы один required job красный или skipped.
4. Сделай небольшие тематические commits, запушь ветку и обнови PR #12.
5. В финальном сообщении укажи exact tip SHA, список изменённых файлов, тестовые результаты и нерешённые ограничения.

## Критерий готовности

Работа считается готовой для повторного независимого review только если:

- все обязательные исправления выше реализованы;
- добавлены regression tests;
- локальные доступные проверки зелёные;
- migration upgrade/downgrade/re-upgrade подтверждены;
- нет raw secrets/PII в logs, audit или diagnostics;
- нет silent consent activation;
- нет arbitrary test recipient;
- нет duplicate active Telegram binding;
- exact final SHA опубликован в PR #12;
- отчёт точно отражает фактические результаты, включая красные/skipped CI jobs.

**Не выполняй merge в `main`. Решение о принятии и merge будет принято после нового независимого review exact final SHA.**