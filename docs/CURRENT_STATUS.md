# Текущее состояние и handoff

Актуально после принятия **Phase 7 — backup, deployment и release**. Этот файл
— первая точка входа для нового агента; подробные требования следующей задачи
находятся в `prompts/PHASE_8_PROMPT.md`.

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

## Следующая задача: Phase 8

Реализовать **фундамент уведомлений и пилотный режим** строго по
`prompts/PHASE_8_PROMPT.md`. Ключевой scope:

1. внутренний notification center и личные напоминания без моков;
2. transactional outbox/очередь на PostgreSQL и отдельный worker;
3. lease/retry/dedup/cancel, неизменяемая история попыток и безопасная
   диагностика;
4. quiet hours, timezone/DST и повторяющиеся напоминания;
5. один явно назначенный пилотный пользователь с доказанным совмещённым
   доступом HR/manager/admin, без отключения RBAC/CSRF/audit;
6. простой идемпотентный bootstrap/мастер настройки и worker в Compose;
7. API, UI, миграции, unit/PostgreSQL integration/frontend/Compose тесты и
   отчёт Phase 8.

Не подключать в Phase 8 фиктивные SMTP/Telegram-отправки, Redis/RabbitMQ,
сообщения кандидатам, универсальный rule engine или новые микросервисы. Эти
функции зарезервированы за этапами 9–11.

## Как начать в новом чате

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
`prompts/PHASE_8_PROMPT.md`; изучить текущие RBAC, события, audit, миграции,
Compose и UI-колокольчик; выполнить baseline; создать ветку
`arena/phase-8-notifications`. Не менять `main` напрямую.

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