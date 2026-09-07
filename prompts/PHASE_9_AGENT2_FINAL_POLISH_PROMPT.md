# Phase 9 — финальная адресная проверка и устранение оставшегося CI-дефекта

## Контекст

Ты — агент №2, работаешь в ветке `arena/01a07ba5-hr-manager` и PR #12.

Текущий принятый review-кандидат:

- baseline: `fc33d9d3f11341b5f91646da6bd8e76190c6ccce`
- exact tip: `7e055a76c67e2ecb0197155116d65579c1234bad`
- PR: `https://github.com/sledovatel61/HR-Manager/pull/12`

Phase 9 и адресная доработка уже прошли независимое review по функциональности и безопасности. **Не переписывай Phase 9, не используй код агента №1 и не меняй контракт приложения без необходимости.**

Цель этой работы — только финальная проверка exact tip и, если возможно, устранение оставшегося CI/workflow-дефекта.

## Обязательная работа

### 1. Проверить текущий exact HEAD

В начале проверь:

```text
git branch --show-current
git rev-parse HEAD
git status --short
git log --oneline --decorate -12
```

Убедись, что HEAD — актуальный tip ветки `arena/01a07ba5-hr-manager`, а не старый `a0973fd` или промежуточный commit.

### 2. Проверить CI run на exact SHA

Проверь GitHub Actions для exact SHA `7e055a76c67e2ecb0197155116d65579c1234bad`.

Ожидаемая картина:

- Backend checks — success;
- PostgreSQL integration — success;
- Frontend checks — success;
- Compose stack до проверки proxy overlay — success;
- `/health` — HTTP 200;
- красным остаётся только `Validate HTTPS proxy overlay configuration`, если workflow всё ещё не передаёт обязательные production-переменные в отдельный shell.

В отчёте нельзя называть такой CI полностью зелёным.

### 3. Исправить proxy-overlay workflow только при наличии разрешения

Проверь `.github/workflows/ci.yml`. В job `stack` отдельный шаг `Validate HTTPS proxy overlay configuration` использует `infra/compose.prod.yml`, где есть обязательные `${VAR:?}` переменные. Каждый GitHub Actions `run`-step получает новый shell, поэтому экспортированные в предыдущем шаге значения не сохраняются.

Если сервер разрешает изменять workflow:

- добавь в proxy-overlay step необходимые безопасные тестовые значения `APP_ENV`, `SECRET_KEY`, `POSTGRES_PASSWORD`, `BOOTSTRAP_ADMIN_PASSWORD` перед `docker compose ... config -q`;
- значения должны генерироваться внутри step через `python3 -c 'import secrets; ...'` либо задаваться только как заведомо тестовые non-production значения;
- не добавляй секреты в репозиторий, `.env`, YAML secrets или логи;
- не ослабляй проверки отсутствия dev credentials и отсутствия лишних published ports;
- сохрани проверку HTTPS target `443` и proxy overlay.

Если push workflow запрещён (`without 'workflows' permission`):

- не обходи ограничение;
- не меняй workflow косвенно;
- оставь `.github/workflows/ci.yml` без изменений;
- зафиксируй ограничение в отчёте и PR;
- не заявляй полный CI green.

### 4. Повторно проверить функциональные инварианты

Не добавляй новые крупные функции. Убедись, что после любых изменений сохраняются:

- strict consent: `opt_in=true` работает только вместе с явным `consent_granted=true`;
- webhook отсутствует/закрыт fail-closed, используется polling;
- один активный Telegram `chat_id` принадлежит не более чем одному пользователю;
- link tokens хранятся только как хеши, single-use и с TTL;
- foreign token/chat не приводит к мутации чужой привязки;
- admin test-send отправляет только на собственный разрешённый канал;
- `external_recipient` допустим только для email verification;
- worker повторно проверяет binding, verified email, consent и глобальную конфигурацию;
- `accepted` не превращается в `delivered` без подтверждения провайдера;
- cancel-wins, advisory single-flight, lease recovery, bounded retry и append-only attempts сохранены;
- в логах, audit, metrics и diagnostics нет raw secrets, token, полного email, полного `chat_id`, текста сообщения или provider payload.

### 5. Запустить проверки

Из корня проекта выполни доступные проверки:

```text
cd backend
ruff check .
ruff format --check .
mypy app tests --no-incremental
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

Для PostgreSQL дополнительно проверь migration:

```text
alembic upgrade head
alembic downgrade 0008
alembic upgrade head
```

Если Docker, PostgreSQL, Docker registry или отдельные backup tools недоступны, укажи это точно. Не выдавай пропущенную проверку за выполненную.

### 6. Проверить diff hygiene

```text
git diff --check fc33d9d3f11341b5f91646da6bd8e76190c6ccce..HEAD
git status --short
git diff --stat fc33d9d3f11341b5f91646da6bd8e76190c6ccce..HEAD
```

Не оставляй trailing whitespace.

### 7. Отчёт и публикация

Обнови `docs/phase-9-report-agent2.md` отдельным commit после code/workflow commits.

В отчёте укажи:

- исходный tip `7e055a7`;
- новые commit SHA, если они появились;
- exact final SHA;
- фактические результаты CI по final SHA;
- отдельно — какие jobs success/failure/skipped;
- был ли разрешён push workflow;
- какие проверки запускались локально;
- что функциональный код Phase 9 не переписывался.

Запушь изменения в `arena/01a07ba5-hr-manager` и обнови PR #12. **Merge в `main` не выполняй.**

## Критерий завершения

Работа завершена, если выполнено одно из двух:

1. Workflow разрешено менять, proxy-overlay step исправлен, новый CI run на exact final SHA подтверждает backend/integration/frontend и все доступные stack checks, кроме реально независимых внешних ограничений; либо
2. Workflow менять запрещено, код Phase 9 повторно проверен, diff чистый, отчёт честно фиксирует единственный оставшийся CI-дефект и ограничение permission.

Финальное сообщение должно содержать exact final SHA и фактические результаты проверок. Не заявляй `CI green`, если required job красный или skipped.
