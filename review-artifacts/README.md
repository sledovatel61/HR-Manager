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

Примечание про `git diff --check`: его не следует запускать против самого
текстового файла `.patch` — в валидном unified diff пустая контекстная строка
обязана начинаться с лидирующего пробела (это структурный маркер формата).
Проверять пробелы нужно на **применённом** изменении, например:

```bash
git apply review-artifacts/ci.agent-4.patch
git diff --check                                   # worktree после применения — clean
grep -nP ' +$' .github/workflows/ci.yml            # вывод пуст: trailing whitespace нет
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"  # YAML валиден
```

Патч генерируется как `git diff` между `.github/workflows/ci.yml` из
`origin/main` и `review-artifacts/ci.agent-4.yml`, поэтому его blank-context
строки содержат штатный лидирующий пробел, и `git apply` его принимает.

## Агент №2 (этап 7 — backup, deployment и release)

| Файл | Назначение |
|---|---|
| `ci.agent-2.phase7.yml` | полная CI-конфигурация этапа 7 (обновлённые job'ы: integration получает PostgreSQL client tools и переменные backup-контура; preflight проверяет backup-секреты; stack валидирует proxy-оверлей и ждёт реальный зашифрованный backup от сервиса) |
| `ci.agent-2.phase7.patch` | git-патч относительно текущего `.github/workflows/ci.yml` на `main` |
| `release.agent-2.yml` | новый workflow `.github/workflows/release.yml`: деплой-конвейер (tag `release-*`/manual dispatch) — обязательный зелёный CI для коммита, preflight, build+tag образов, one-shot миграции с advisory lock, переключение трафика с readiness-гейтом, smoke по `/health` и `release_sha`, автоматический rollback, failure-drill и release notes артефактом |
| `release.agent-2.patch` | git-патч, создающий `.github/workflows/release.yml` |

Что меняют/добавляют workflow для этапа 7:

- **integration job**: установка `postgresql-client-16` (PGDG) на раннер и
  переменные `BACKUP_DRILL_ADMIN_URL`/`BACKUP_PGDUMP_BIN`/`BACKUP_RESTORE_BIN`
  — без этого backup/restore-drill интеграционные тесты корректно
  скипаются (они сами пропускаются при отсутствии `pg_dump`).
- **preflight шаг**: негативные проверки backup-секретов
  (`BACKUP_ENABLED=true` требует реальный 32-байтовый ключ, dev-ключ
  отклоняется) + позитивная проверка с настоящим ключом.
- **stack job**: рендер и валидация HTTPS proxy-оверлея (публикуются только
  80/443, без dev-credentials) и проверка, что backup-сервис реально
  опубликовал зашифрованный `*.pgdump.enc` в dedicated volume.
- **release.yml** (новый файл): деплой только после зелёного CI для точного
  SHA; секреты только из GitHub Secrets (`DEPLOY_HOST`,
  `DEPLOY_SSH_USER`, `DEPLOY_SSH_KEY`, `SECRET_KEY`, `POSTGRES_PASSWORD`,
  `BOOTSTRAP_ADMIN_PASSWORD`, `BACKUP_KEY_ID`, `BACKUP_ENC_KEY`); без
  `DEPLOY_HOST` конвейер выполняется на CI-раннере как локальный тестовый
  контур (реальные образы, реальная БД, реальный smoke и failure-drill
  rollback) — production-хост не затрагивается.

Перенос владельцем (однократно; делает учётка с правом записи workflows):

```bash
git fetch origin
git checkout -b arena/phase-7-agent-2-workflow origin/arena/phase-7-release

# 1) обновление CI:
git apply --check review-artifacts/ci.agent-2.phase7.patch
git apply review-artifacts/ci.agent-2.phase7.patch
cmp review-artifacts/ci.agent-2.phase7.yml .github/workflows/ci.yml \
  && echo "ci.yml matches artifact"

# 2) новый workflow релиза:
git apply --check review-artifacts/release.agent-2.patch
git apply review-artifacts/release.agent-2.patch
cmp review-artifacts/release.agent-2.yml .github/workflows/release.yml \
  && echo "release.yml matches artifact"

# 3) настройка репозитория (settings):
#    - tag protection rule для `release-*` (деплой только с защищённого тега);
#    - GitHub Secrets из таблицы выше (для production-деплоя на свой хост).

git commit -m "workflows: phase 7 CI + release pipeline (owner transfer)"
git push
```

Без переноса владельцем GitHub Actions выполняет **старую** версию CI:
backup-интеграционные тесты честно скипаются (нет `pg_dump` на раннере),
новые шаги preflight/stack/release не запускаются. Это ожидаемое поведение
до переноса — см. ограничение App в начале файла.

## Обновление этапа 8 (2026-09-07)

Workflows перенесены владельцем (коммит `784f388` на `main`: обновлённый
`ci.yml` + `release.yml`). В перенесённом `ci.yml` шаг
`Validate HTTPS proxy overlay configuration` ссылается на
`${SECRET_KEY:?}`/`${POSTGRES_PASSWORD:?}`/`${BOOTSTRAP_ADMIN_PASSWORD:?}`
production-оверлея, но не экспортирует эти переменные (каждый CI-шаг —
изолированный shell, экспорт соседнего шага не наследуется). Шаг падает
на любом PR (зафиксировано на PR #10, run 34089737761: остальные шаги
stack-job — dev/prod config, полный запуск стека с worker-ом, /health,
backup — зелёные).

Артефакты исправления:

- `ci.agent-2.phase8.yml` — полный `ci.yml` с исправленным шагом
  (экспорт эфемерных `APP_ENV/SECRET_KEY/POSTGRES_PASSWORD/
  BOOTSTRAP_ADMIN_PASSWORD` перед рендером proxy-оверлея);
- `ci.agent-2.phase8.patch` — минимальный diff от текущего
  `.github/workflows/ci.yml`.

Перенос владельцем выполнен при финальной приёмке. Воспроизводимая команда для
аудита или восстановления:

```bash
git fetch origin
git checkout -b arena/phase-8-agent-2-workflow origin/main

git apply --check review-artifacts/ci.agent-2.phase8.patch
git apply review-artifacts/ci.agent-2.phase8.patch
cmp review-artifacts/ci.agent-2.phase8.yml .github/workflows/ci.yml \
  && echo "ci.yml matches artifact"

git commit -m "workflows: export required env in the proxy overlay CI step"
git push
```

До переноса stack-job каждого PR останавливается на этом шаге (остальные
три job — backend, frontend, integration — зелёные и не зависят от
переноса). Семантика `:?`-охран в `compose.prod.yml` намеренно НЕ
ослабляется: они — защита «fail fast» production-конфигурации.

## Phase 13 rework (2026-09-11): update-channel release workflow

Для доработки Phase 13 (PR #23) подготовлен исполняемый release workflow
`.github/workflows/update-channel.yml`. Пуш этого файла через GitHub App
сессии отклонён сервером — точная ошибка:

```
! [remote rejected] arena/01a084e4-hr-manager -> arena/01a084e4-hr-manager
  (refusing to allow a GitHub App to create or update workflow
   `.github/workflows/update-channel.yml` without `workflows` permission)
```

Артефакты:

| Файл | Назначение |
|---|---|
| `update-channel.yml` | полный workflow (точная копия того, что должно лечь в `.github/workflows/`) |
| `update-channel.patch` | полноценный unified git patch с `@@`-hunk header (создаёт `.github/workflows/update-channel.yml`); применение проверено исполняемым тестом `test_workflow_patch_applies_byte_exact` — реальный `git apply` в отдельном каталоге даёт byte-identical файл (`read_bytes()` совпадает с артефактом; одного `git apply --check` недостаточно) |

Перенос владельцем (однократно; учётка с правом записи workflows):

```bash
git fetch origin
git checkout -b arena/phase-13-update-channel-workflow origin/arena/01a084e4-hr-manager
git apply --check review-artifacts/update-channel.patch
git apply review-artifacts/update-channel.patch
cmp review-artifacts/update-channel.yml .github/workflows/update-channel.yml \
  && echo "workflow matches artifact"
git commit -m "ci: phase 13 update channel release workflow"
git push
```

Перед production-выпуском создать environment `update-channel-signing` с protection
rules (ветки main; НЕ разрешать PR) и секретами
`UPDATE_CHANNEL_SIGNING_KEY` (64 hex Ed25519), `UPDATE_CHANNEL_KEY_ID`,
`UPDATE_CHANNEL_PUBLIC_KEYS` (тот же JSON trust store, что у сервера), а
также tag protection rules на `v*` (SemVer). Семантика fail-closed и
fixture-тесты — в `infra/release/publish_channel.py` и
`backend/tests/test_release_pipeline.py` (исполняются в существующем CI
без production secret).

Замечания оркестратора к dispatch учтены в этой версии workflow:
`workflow_dispatch` больше не может собрать код одного коммита под
release_sha другого — при ручном запуске `release_sha` валидируется
(40 hex), проверяется его существование в репозитории (`git cat-file -e`),
checkout выполняется по нему, и сборка стартует только при
HEAD == release_sha; тег/релиз создаётся `--target` на тот же SHA. Все
значения dispatch-пользователя попадают в shell-скрипты ТОЛЬКО через
env (никаких inline-expressions в run-блоках — shell injection
исключён). Перед записью в `$GITHUB_OUTPUT` version/release_sha/notes_ru
отклоняются fail closed при наличии CR/LF — многострочный input не может
подмешать поддельные строки-выводы (`sha=…`, `tag=…`). Эти инварианты
закреплены тестами `test_release_pipeline.py`, включая ТРИ исполняемых
теста: два запускают resolve-скрипт из workflow в bash с поддельным
`$GITHUB_OUTPUT` (атака CR/LF отклоняется до записи; валидная
однострочная русская заметка сохраняется без изменений), третий —
`test_workflow_patch_applies_byte_exact` — реально применяет
`update-channel.patch` командой `git apply` в отдельном временном
каталоге и сравнивает `read_bytes()` установленного
`.github/workflows/update-channel.yml` с артефактом (патч содержит
`@@`-hunk header; применение без hunk'а создавало бы пустой файл).
