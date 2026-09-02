# Review artifacts — реализации AI-агентов

## Статус workflow на GitHub

GitHub App (`arena-ai-coding-agent[bot]`), через который публикуются ветки
`arena/*`, **не имеет разрешения `workflows`**: GitHub отклоняет любой push,
содержащий изменения файлов в `.github/workflows/`. Поэтому опубликованные
ветки агентов не содержат изменений CI, и GitHub Actions по этим веткам
**не запускается с обновлённым workflow**. Это ограничение платформы, а не
кода репозитория.

**Важно:** файлы в `review-artifacts` НЕ исполняются GitHub Actions — это
точные копии рабочих workflow, которые должен перенести владелец
репозитория.

## Агент №2 (этап 1)

| Файл | Назначение |
|---|---|
| `ci.agent-2.yml` | полная CI-конфигурация этапа 1 (4 job: backend, frontend, integration, stack) |
| `ci.agent-2.patch` | git-патч (format-patch), добавляющий `.github/workflows/ci.yml` |

Инструкция по переносу — в истории этого файла (вариант A: `git am`,
вариант B: копирование файла).

## Агент №4 (этап 2 — идентификация и безопасность)

| Файл | Назначение |
|---|---|
| `ci.agent-4.yml` | полная CI-конфигурация для этапа 2 — то же, что `.github/workflows/ci.yml` должен содержать после переноса |
| `ci.agent-4.patch` | патч-диф относительно состояния `main` (коммит `7f8c18c`); применяется `git apply` |

Что меняет workflow для этапа 2:

- **Integration job: отдельный шаг `Apply Alembic migrations` (`alembic upgrade head`)
  перед integration-тестами.** Без него на чистом GitHub-раннере PostgreSQL пуст,
  и integration-тесты падают на отсутствующих таблицах. Шаг запускается с
  `DATABASE_URL`, указывающим на тот же сервисный PostgreSQL, что и тесты
  (`...@localhost:5432/hr_manager_test`).
- backend preflight теперь требует `BOOTSTRAP_ADMIN_PASSWORD` (проверки
  «отсутствие/дефолтный пароль отклоняются» и «полная конфигурация принимается»).
- stack-валидация production-оверлея экспортирует `BOOTSTRAP_ADMIN_PASSWORD`.

Перенос владельцем (однократно; делает учётка с правом записи workflows):

```bash
git fetch origin
git checkout -b arena/phase-2-agent-4-workflow origin/arena/01a061ab-hr-manager

# Проверка, что патч применяется чисто (без внесения изменений):
git apply --check review-artifacts/ci.agent-4.patch

# Вариант A — применить патч:
git apply review-artifacts/ci.agent-4.patch
# Вариант B (эквивалентно) — просто скопировать готовый файл:
# cp review-artifacts/ci.agent-4.yml .github/workflows/ci.yml

# Обязательная проверка эквивалентности:
cmp review-artifacts/ci.agent-4.yml .github/workflows/ci.yml && echo "workflow matches artifact"

git add .github/workflows/ci.yml
git commit -m "ci: publish agent-4 workflow (identity phase)"
git push -u origin arena/phase-2-agent-4-workflow
```

После этого открыть/обновить Pull Request, чтобы GitHub Actions запустился,
и дождаться зелёного выполнения **всех** jobs:

- **Backend checks** (ruff, format, mypy, unit-тесты, production preflight);
- **Frontend checks** (ESLint, typecheck, Vitest, build, npm audit);
- **Backend integration tests (PostgreSQL)** — миграции (`alembic upgrade head`),
  затем pytest против PostgreSQL 16;
- **Compose stack smoke test** (dev + production overlay).

Проверка чистоты патча уже выполнена: `git apply --check review-artifacts/ci.agent-4.patch`
на базовом `.github/workflows/ci.yml` (коммит `7f8c18c`) проходит без ошибок,
а применение даёт байт-идентичный `ci.agent-4.yml`.
