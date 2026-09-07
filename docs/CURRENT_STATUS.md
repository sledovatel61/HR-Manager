# Текущее состояние и handoff

> Phase 8 принята и влита в `main` на SHA `2cb6ab63f1276f259146d477cd1cc98462f066ec`. Для следующего этапа использовать `prompts/PHASE_9_PROMPT.md`.

Актуально после **Phase 8 — фундамент уведомлений и пилотный режим**.
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

## Phase 9 — подготовлено к разработке

Полное задание находится в `prompts/PHASE_9_PROMPT.md`. Этап должен начать
coding-agent строго от актуального `origin/main`, выполнить обязательный аудит
Phase 8 и создать отдельную ветку `arena/phase-9-telegram-email`. Scope Phase 9:
реальные Telegram Bot API и SMTP-адаптеры через существующий PostgreSQL outbox,
без фиктивной доставки, с одноразовой привязкой Telegram, consent, таймаутами,
retry/backoff, безопасными секретами, RBAC/CSRF/IDOR-защитой и русскоязычным UI.

## Как начать в новом чате

Скопировать в новый чат содержимое `prompts/PHASE_9_PROMPT.md`. Ниже
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
`prompts/PHASE_9_PROMPT.md`; изучить текущие RBAC, события, audit, миграции,
Compose, outbox/worker и UI; выполнить baseline; создать ветку
`arena/phase-9-telegram-email`. Не менять `main` напрямую.

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

CI на `main` является окончательной проверкой Linux/PostgreSQL/Compose. Нельзя
заявлять локально не выполненную проверку как успешную: причину недоступности
нужно явно записать в отчёт и подтвердить соответствующим GitHub job.