# Стартовый промпт для нового чата — Phase 9

Скопируй текст ниже целиком в новый чат с coding-agent.

---

Продолжи разработку репозитория `sledovatel61/HR-Manager` и полностью выполни **Phase 9 — реальные интеграции Telegram и email**.

Работай как самостоятельный coding-agent: изучи существующий проект, реализуй весь scope, добавь миграции и тесты, выполни проверки, опубликуй отдельную ветку и открой PR в `main`. Не ограничивайся анализом, планом, макетами или рекомендациями.

## Важный контекст

Этапы 0–8 уже приняты. Phase 8 влита в `main` на SHA `2cb6ab63f1276f259146d477cd1cc98462f066ec`; после неё в `main` опубликован handoff Phase 9. В проекте уже есть внутренние уведомления, PostgreSQL transactional outbox, отдельный worker, lease/retry/dedup, quiet hours, preferences, immutable delivery attempts, пилотный режим, RBAC, CSRF, audit, backup и Docker Compose. Не создавай эти механизмы заново и не заменяй их упрощёнными версиями — расширяй принятую архитектуру.

Эту задачу параллельно выполняют два независимых агента. Не используй ветку другого агента и не делай merge в `main`. Качество обеих реализаций будет независимо проверено оркестратором; будет принята только лучшая полностью рабочая версия.

## Обязательный старт

1. Синхронизируй репозиторий строго с актуальным `origin/main`:

```bash
git fetch origin --prune
git switch main
git pull --ff-only origin main
git status --short --branch
git rev-parse HEAD
```

2. До любых изменений полностью прочитай:

- `agents.md`;
- `PRODUCT_SPEC.md`;
- `ROADMAP.md`;
- `README.md`;
- `docs/CURRENT_STATUS.md`;
- `docs/ARCHITECTURE.md`;
- `docs/phase-8-report-agent2.md`;
- `prompts/PHASE_8_PROMPT.md`;
- `prompts/PHASE_9_PROMPT.md`.

3. Полным и приоритетным техническим заданием является файл `prompts/PHASE_9_PROMPT.md` из актуального `main`. Выполни его целиком, включая безопасность, миграции, frontend, тесты, Compose, отчёт и Definition of Done. Не сокращай scope по своему усмотрению.

4. Зафиксируй baseline SHA и результаты исходных проверок. Если AI Arena уже выдала персональную ветку, оставайся в ней. Иначе создай ветку `arena/phase-9-telegram-email-<короткий-суффикс-агента>`. Не работай напрямую в `main`, не используй общую ветку без суффикса и не выполняй merge.

## Критические требования

- Реализуй настоящие адаптеры Telegram Bot API и универсального SMTP поверх существующего PostgreSQL outbox/worker.
- Telegram-привязка выполняется через безопасный одноразовый expiring single-use token; хранится numeric `chat_id`, а не username.
- Учитывай Telegram `429 retry_after`, временные и постоянные ошибки, таймауты и bounded retry.
- SMTP должен поддерживать STARTTLS/TLS, русскую кодировку, безопасные заголовки и защиту от header injection.
- Provider `accepted` не означает `delivered` или прочтение человеком.
- Не отмечай Telegram/email доставленными без реального ответа адаптера.
- Не включай внешние каналы существующим пользователям молча: нужны валидная настройка и явное consent/opt-in.
- Секреты должны поступать только через environment/secret storage. Bot token, SMTP password, session tokens, PII и полный текст email нельзя писать в git, БД открытым текстом, логи, traces, metrics, diagnostics или отчёт.
- Сохрани работоспособность in-app уведомлений, RBAC, CSRF, IDOR-защиты, audit, backup, миграций и Compose этапов 0–8.
- Не добавляй Redis/RabbitMQ и новые микросервисы без доказанной необходимости.
- Mailpit/test doubles допустимы только в dev/test и не считаются реальной production-доставкой.
- Коммуникации с кандидатами относятся к Phase 10: не расширяй Phase 9 до массовых или автоматических сообщений кандидатам.

## Проверка и завершение

Выполни все проверки из `prompts/PHASE_9_PROMPT.md`, включая backend unit и PostgreSQL integration, миграции upgrade/downgrade/re-upgrade, frontend lint/typecheck/tests/build, Compose config/smoke, security/PII и `git diff --check`.

Не заявляй непроведённую проверку успешной. Если инструмент недоступен локально, явно укажи причину и подтверди результат соответствующим GitHub CI job. Не используй реальные Telegram-чаты, реальные SMTP-учётные данные или реальные персональные данные в тестах.

Создай `docs/phase-9-report-<agent>.md`, где укажи:

- baseline и exact final SHA;
- архитектурные решения;
- миграции и изменённые файлы;
- точные команды и фактические результаты тестов с числом passed/skipped;
- результаты PostgreSQL и Compose;
- security/PII проверки;
- известные ограничения без сокрытия проблем;
- handoff для Phase 10.

Затем закоммить изменения, запушь только свою ветку, открой отдельный PR в `main` и дождись GitHub checks именно для final SHA. При падении проверок изучи логи, исправь причину и повтори проверки. В финальном ответе предоставь URL PR, имя ветки, baseline SHA, final SHA, краткий список реализованного и только фактически подтверждённые результаты.

Начинай с синхронизации актуального `main`, чтения полного Phase 9 prompt и проверки baseline.

---
