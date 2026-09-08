# Текущее состояние и handoff

> Фазы 0–9 приняты. Актуальный `main` — SHA `a747f35ab3e8fc0907190595684249933a413190`. Для следующего этапа использовать `prompts/PHASE_10_PROMPT.md`.

Актуально после **Phase 9 — Telegram/email и исправление backup smoke**.
Phase 8 принята после независимого review и влита в `main` с fast-forward до
SHA `2cb6ab63f1276f259146d477cd1cc98462f066ec` (PR #10). Предыдущий принятый
этап — **Phase 7 — backup, deployment и release**. Этот файл — первая точка
входа для нового агента. Полное техническое задание Phase 8 находится в
`prompts/PHASE_8_PROMPT.md`, а стартовый промпт следующего этапа — в
`prompts/PHASE_9_PROMPT.md`.

## Что принято

- Этапы 0–6 продукта и подготовительная документация Phase 8 находятся в
  `main`.
- Phase 7 прошёл review и влит в `main` с сохранением истории ветки
  `arena/phase-7-release`; финальный SHA реализации агента —
  `8670195b9e1a0799c14cffaff687e9d84befa198`, исходный PR — #9.
- Реализованы AES-256-GCM backup PostgreSQL, retention, integrity check и
  restore drill в отдельной БД; ручной admin-trigger и audit; операционные
  status/metrics endpoints; production Compose overlay, HTTPS reverse proxy,
  preflight, блокируемые Alembic-миграции, deploy/smoke/rollback scripts.
- `/health` намеренно остаётся чистым liveness/readiness endpoint с проверкой
  БД. Backup freshness, restore drill, release SHA и метрики доступны через
  `/ops/status`, `/ops/backup-health` и `/ops/metrics`.
- Workflow Phase 7 перенесён из `review-artifacts` в исполняемые
  `.github/workflows/ci.yml` и `.github/workflows/release.yml`. Для реального
  production deploy владелец всё ещё должен настроить GitHub Secrets,
  защищённые `release-*` tags и operator-owned host согласно
  `docs/backup-and-restore.md`; наличие внешнего production-хоста, DNS и TLS
  сертификата репозиторий не имитирует.

Подробный технический отчёт и результаты проверок Phase 7:
`docs/phase-7-report-agent2.md`.

## Phase 8 — принято

Ветка `arena/01a060e3-hr-manager`, PR #10, final SHA
`2cb6ab63f1276f259146d477cd1cc98462f066ec`. Что вошло:

1. внутренний notification center и личные напоминания без моков;
2. transactional outbox на PostgreSQL + отдельный worker (SKIP LOCKED,
   lease recovery, bounded backoff, dedup, неизменяемая история);
3. тихие часы с пересечением полночи, DST/zoneinfo, рабочие дни,
   исходное vs фактическое время планирования;
4. один явно назначенный пилотный пользователь (admin + грант
   `pilot_full_access`) с доказанным полным доступом, идемпотентный
   мастер настройки, worker в dev/prod Compose;
5. UI: колокольчик, центр уведомлений, напоминания, настройки,
   админ-экран очереди, мастер первой настройки (всё на русском);
6. миграции `0007`/`0008`, unit + PostgreSQL integration + frontend +
   Compose overlay тесты, отчёт `docs/phase-8-report-agent2.md`.

Не подключались (зарезервированы за этапами 9–11): фиктивные
SMTP/Telegram-отправки, Redis/RabbitMQ, сообщения кандидатам,
универсальный rule engine, новые микросервисы. Каналы `email`/`telegram`
в outbox-контракте зарезервированы и честно помечаются `skipped`.

## Известная проблема CI (переносится владельцем)

В перенесённом владельцем `ci.yml` (коммит `784f388`) шаг
`Validate HTTPS proxy overlay configuration` рендерит production-оверлей
без обязательных `${SECRET_KEY:?}`/`${POSTGRES_PASSWORD:?}`/
`${BOOTSTRAP_ADMIN_PASSWORD:?}` — шаг падает на любом PR (впервые
зафиксировано на PR #10, run 34089737761; остальные шаги stack-job
зелёные). Исправление — `review-artifacts/ci.agent-2.phase8.yml/.patch`
+ инструкция переноса в `review-artifacts/README.md`. Семантика
`?`-охран `compose.prod.yml` намеренно не ослабляется.

## Phase 9 — принято

Phase 9 реализована и принята в `main`. Финальный SHA реализации и backup smoke fix:
`a747f35ab3e8fc0907190595684249933a413190`. CI run `34191549759` завершился
успешно: backend, PostgreSQL integration, frontend и Compose stack smoke — зелёные.

В фазу вошли реальные Telegram Bot API и SMTP через существующий PostgreSQL outbox,
одноразовая привязка Telegram, согласия каналов, безопасная тестовая отправка,
retry/lease/idempotency и русскоязычный UI. Дополнительно устранена гонка запуска
backup: backup ждёт healthy backend перед стартовой копией, а scheduler сохраняет
правильный код ошибки CLI. Зашифрованный `.pgdump.enc` подтверждён локально и CI.
Подробности: `docs/phase-9-report-agent2.md` и `docs/phase-9-backup-smoke-report.md`.

## Phase 10 — сообщения кандидатам (выполнена; идёт доработка PR #14)

Полное задание находится в `prompts/PHASE_10_PROMPT.md`, доработка — в
`prompts/PHASE_10_PR14_REWORK_PROMPT.md`. Scope Phase 10: односторонние
русскоязычные сообщения кандидатам о назначении, напоминании, переносе и
отмене собеседований, а также запросы и напоминания о документах.

Доработка PR #14 (по результатам ревью): настоящий email double opt-in
(одноразовый токен с TTL, в БД только хеш, письмо через общий outbox,
публичная ссылка подтверждения, отзыв fail-closed), идемпотентность HTTP
retry по клиентскому `idempotency_key` (unique constraint + replay
результата, 409 при другом payload, конкурентные запросы дают одну
логическую отправку), сервер — единственный источник содержания (клиент не
передаёт location/получателя/текст, событие перепроверяется на HTTP-этапе и
перед provider call), честная история (русские статусы, инициатор, точный
сохранённый текст, provider message ID), RBAC/CSRF/rate limiting на всех
endpoint-ах, включая публичное подтверждение. Миграция `0011` (обратимая,
PostgreSQL). Ответы, чат, входящая почта, загрузка документов через каналы,
SMS и рекламные рассылки не входят.

Вторая итерация доработки (по независимой проверке): атомарный one-shot
claim подтверждения (условный UPDATE, rowcount решает; PG race-тест двух
параллельных кликов), сериализация инициации по candidate advisory lock +
частичный unique index «не более одного активного токена» (PG race-тест
двух параллельных инициаций), raw-токен нигде не персистится — это HMAC
секрет-ключ+id строки, письмо хранит плейсхолдер, worker подставляет ссылку
в памяти перед отправкой (скан всех текстовых колонок PG в тесте), история
маскирует ссылку; worker держит оба advisory-лока (строка + кандидат) на
выделенном соединении через provider call, consent/revoke/confirm/
initiation сериализованы тем же ключом, повторная валидация непосредственно
перед вызовом провайдера (модель отзыва задокументирована честно: до
отправки — останавливает, во время — применяется сразу после).

## Как начать в новом чате

Скопировать в новый чат содержимое `prompts/PHASE_10_PROMPT.md`. Ниже
приведены те же ключевые команды синхронизации для быстрой проверки.

Перед изменениями агент должен:

```bash
git fetch origin --prune
git switch main
git pull --ff-only origin main
git status --short --branch
git rev-parse HEAD
```

Затем прочитать `agents.md`, `PRODUCT_SPEC.md`, `ROADMAP.md`, `README.md`,
`docs/ARCHITECTURE.md`, этот handoff, отчёты этапов и полностью
`prompts/PHASE_10_PROMPT.md`; изучить текущие RBAC, события, audit, миграции,
Compose, outbox/worker, Telegram/email и UI; выполнить baseline; создать ветку
`arena/phase-10-candidate-communications-<короткий-суффикс>`. Не менять `main` напрямую.

Минимальный baseline (с учётом доступности Docker/PostgreSQL):

```bash
cd backend
ruff check .
ruff format --check .
mypy app tests
pytest -m "not integration" -v

cd ../frontend
npm ci
npm run lint
npm run typecheck
npm run test
npm run build

cd ..
docker compose -f infra/docker-compose.yml config -q
git diff --check
```

CI на `main` является окончательной проверкой Linux/PostgreSQL/Compose. Последний
успешный CI — run `34191549759` для SHA `a747f35ab3e8fc0907190595684249933a413190`.
Нельзя заявлять локально не выполненную проверку как успешную: причину недоступности
нужно явно записать в отчёт и подтвердить соответствующим GitHub job.