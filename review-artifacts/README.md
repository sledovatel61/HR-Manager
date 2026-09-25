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

## Phase 14 — pilot readiness (агент Arena, ветка `arena/01a08ff0-hr-manager`)

| Файл | Назначение |
|---|---|
| `ci.phase14.yml` | полный `.github/workflows/ci.yml` после Phase 14: drill в jobs backend/integration/windows-installer |
| `ci.phase14.patch` | патч (применяется `git apply -p1` из корня репозитория) |
| `update-channel.phase14.yml` | полный `.github/workflows/update-channel.yml` после Phase 14: две независимые подписи, production fail-closed, environment `update-channel-signing` |
| `update-channel.phase14.patch` | патч для того же файла |

Причина: GitHub App `arena-ai-coding-agent[bot]`, публикующий ветки `arena/*`,
не имеет разрешения `workflows`, поэтому проверенный контент workflow доставляется
как review-artifact (см. «Статус workflow на GitHub» выше).

Перенос (owner action):

```bash
git checkout -b apply/phase14-workflows <merge-коммит PR>
git apply -p1 review-artifacts/ci.phase14.patch
git apply -p1 review-artifacts/update-channel.phase14.patch
git diff --stat   # ожидаются только .github/workflows/ci.yml и update-channel.yml
git commit -m "Phase 14: enable pilot drill in CI and release fail-closed policy"
```

Либо простым копированием полных файлов:

```bash
cp review-artifacts/ci.phase14.yml .github/workflows/ci.yml
cp review-artifacts/update-channel.phase14.yml .github/workflows/update-channel.yml
```

Контроль целостности (SHA256 полных файлов на момент публикации ветки):

* `ci.phase14.yml` — `c76b9288bd4093a4d251ef33f0959da7b3ad2734ab4171584222d76d1d3335e8`
* `update-channel.phase14.yml` — `594626ec55e1e648113bc18511c1c837c885e8551889a96dc6b55ab375af5a2b`

Пока файлы не перенесены, GitHub Actions по ветке работает со старым набором
jobs: новых drill-шагов и production-политики релиза в CI ещё нет (сам код
политики протестирован в backend-наборе тестов).

## Этап 15 — офлайн-лицензия: цепочка открытого ключа (PR #34)

Примечание: в этой ветке изменения `.github/workflows/ci.yml` **пушатся и
исполняются** (см. run 35963793099 / 35964596589), поэтому отдельных копий
workflow для этапа 15 нет — заметка про отсутствие разрешения `workflows`
выше относится к более ранним веткам.

| Файл | Назначение |
|---|---|
| `final-verdict.md` | итоговый вердикт: fix-коммит, run CI, результаты jobs, PASS/FAIL/BLOCKED/NOT RUN |
| `gen_license_chain_evidence.py` | генератор `license-chain-evidence.*`: все проверки **вычисляются** из реальных файлов репозитория; статусы Compose/runtime берутся **только** из импортированного артефакта CI, иначе `NOT RUN`. Запуск: `python review-artifacts/gen_license_chain_evidence.py` |
| `license-chain-evidence.json` / `.md` | результат генератора (только отпечатки SHA-256, без ключей) |
| `compose-pilot-license-chain.ci.json` | дословная копия отчёта `infra/scripts/compose_pilot_license_chain.py`, напечатанного CI-шагом «Print pilot license-chain report» между маркерами `BEGIN/END COMPOSE-LICENSE-CHAIN JSON` (job 107521357971, run 35964596589; тот же контент — в step summary и артефакте `compose-pilot-license-chain` id 10794046262) |
| `ci-run-status.json` | run id, head SHA, результаты всех jobs, id jobs/артефактов, способ получения отчёта |
| `windows-issuer-bundle-check.md` | чек-лист ручной проверки офлайн-issuer на чистой Windows (BLOCKED без VM владельца) |

Тест `backend/tests/test_license_middleware_comprehensive.py::test_public_key_chain_evidence_is_computed_not_declared`
перезапускает `compute_checks()` генератора и проверяет, что evidence
не содержит неотредактированного base64 и что декларативный
`license-public-key-chain.json` (раньше писался тестом с захардкоженными `True`) не вернулся.

## Этап 16 — release-перепроверка PR #34 на HEAD `e59aa5b` (ветка `arena/01a0d255-hr-manager`)

Ветка сессии не содержит кода PR #34 (она основана на `main`), поэтому все проверки выполнялись **по
извлечённому дереву ревьюируемого коммита** (`git archive e59aa5b7a3df49b61a8b7c599601bfbd4e9b2784`), а не по
рабочему каталогу. Изменения этой перепроверки затрагивают **только** `review-artifacts/`: backend, Compose,
infra, frontend и `tools/` не менялись, тесты не переписывались, merge/tag/release/production workflow не
запускались.

| Файл | Назначение |
|---|---|
| `issuer-offline-evidence.json` / `.md` | **новое**: результат автономных проверок issuer'а на Linux (30 PASS / 0 FAIL / 8 GAP / 8 NOT RUN). Что реально запускалось: `cli.py gen-keypair → issue → verify` под harness'ом, запрещающим сокеты (0 сетевых вызовов); offline-HTML в JS VM с заглушкой DOM (пути WebCrypto и TweetNaCl, 0 сетевых попыток); backend-верификация выпущенных лицензий настоящим `parse_and_verify_license_text`; негативные контроли; статические проверки лаунчеров. Что **не** запускалось: `run-gui.bat`, `run-html.bat`, GUI Tkinter, `build.ps1` — отмечено `NOT RUN`. |
| `gen_issuer_offline_evidence.py` | **новое**: генератор предыдущего файла. Требует интерпретатор с `cryptography` (+ зависимости backend для верификации) и `node`; всё сгенерированное сырьё живёт во временном каталоге вне репозитория, артефакты содержат только отпечатки/длины/булевы значения (есть assert, что ни один созданный секрет не попал в файл). Запуск: `python3 review-artifacts/gen_issuer_offline_evidence.py --python .venv/bin/python` |
| `final-verdict.md` | **обновлён**: вердикт для HEAD `e59aa5b` и run `35965657324` (все шесть jobs `success`, три шага license-chain `success`, digest артефакта), список PASS/BLOCKED/NOT RUN, находки G1–G6, подтверждение отсутствия ключей/PII, и раздел про перенос артефактов в PR-ветку |
| `windows-issuer-bundle-check.md` | **обновлён**: статус остался **BLOCKED** (в среде ревью нет Windows: нет `/dev/kvm`, нет `vmx`/`svm`, нет qemu/wine/pwsh). Добавлены результаты статических/Linux-проверок, находки по лаунчерам и подробный чек-лист владельца с правилами редактирования доказательств |
| `ci-run-status.json` | **обновлён**: head-run `35965657324` @ `e59aa5b` (id jobs, conclusions, id и `digest` артефакта) + история прогонов `35964596589`, `35963793099`. `digest` из Artifacts API совпал с ранее вписанными вручную SHA-256 zip — независимая проверка той записи. Отдельный блок `chain_report` фиксирует, что тело отчёта импортировано из прогона `35964596589` (логи/артефакты нового прогона из sandbox недоступны: blob storage отдаёт EOF) |
| `license-chain-evidence.json` / `.md` | перегенерированы для `e59aa5b`: значения `checks_read_from_real_files` совпали с версией, сгенерированной на PR-ветке, — подтверждение, что дерево ревьюируемого коммита соответствует протестированному. Добавлено поле `reviewed_tree` |
| `gen_license_chain_evidence.py` | добавлена поддержка `HRM_EVIDENCE_REF=<sha>`: читает дерево указанного коммита (через `git archive`), поэтому evidence можно перегенерировать из любой ветки; блок `ci_import` теперь различает прогон-источник отчёта и head-run PR |

Перенос в PR #34 (ветка сессии не может пушить в `arena/01a0ccb9-hr-manager`):

```bash
git checkout arena/01a0ccb9-hr-manager
git checkout arena/01a0d255-hr-manager -- review-artifacts/
git diff --stat   # ожидаются только файлы review-artifacts/
```

Вердикт остаётся **NO-GO**: без реального прогона `run-gui.bat` / `run-html.bat` на чистой Windows 10/11 `GO`
не выпускается.

## Этап 17 — правки issuer'а по замечаниям ревью PR #34 (ветка `arena/01a0d255-hr-manager`, draft PR #35)

Все правки — только owner-side тулинг; backend/Compose/infra/frontend не менялись, тесты не переписывались,
merge/tag/release/production workflow не запускались. Ветка сессии основана на `main` и не содержит кода PR #34,
поэтому результат доставлен патчем и как отдельный draft-PR #35 (обязательно **не** для merge в таком виде).

| Изменение | Проверка «до/после» (baseline = PR head `e59aa5b`) |
|---|---|
| `run-html.bat`: `python -m http.server %PORT% -b 127.0.0.1 --directory <bundle>`, страница `http://127.0.0.1:8765/...` | `run_html_bat_binds_loopback_only`: FAIL → **PASS**; измерено: с `-b` процесс слушает только `127.0.0.1`, без `-b` — `0.0.0.0` |
| `run-html.bat`: убран тихий fallback на системный `python`, теперь fail-closed | `run_html_bat_fails_closed_without_system_python`: FAIL → **PASS** |
| `run-gui.bat`: убран совет ставить/использовать системный Python | `run_gui_bat_no_system_python_advice`: FAIL → **PASS** |
| smoke-тест сборки: `gen-keypair --out-dir <temp под GetTempPath()>` вне рабочего дерева git, удаление каталога в `finally`, билд падает при попадании ключа в лог | `smoke_test_runs_outside_the_repository`, `smoke_test_never_prints_key_material`: FAIL → **PASS** |
| `.gitignore`: `keys/`, `private_key.hex`, `public_key.b64`, `*.hrmlicense`, `license-issuer-dist.zip` | `gitignore_blocks_key_and_license_material`: FAIL → **PASS**; `git ls-files -ci --exclude-standard` пуст |

Файлы этапа: `license-issuer-fixes.patch` (применяется к PR head, проверено `git apply --check -p1`),
`issuer-offline-evidence.{json,md}` (39 PASS / 0 FAIL / 3 GAP / 8 NOT RUN, before/after-таблица вычисляется
генератором), обновлённые `final-verdict.md`, `ci-run-status.json`, `windows-issuer-bundle-check.md`.

**Windows-проверка по-прежнему NOT RUN.** Среда ревью не может запустить Windows: нет `/dev/kvm`, нет флагов
`vmx`/`svm`, нет qemu/wine/pwsh, установка пакетов и скачивание ISO невозможны (все внешние запросы падают).
`build.ps1` не запускался, поэтому `license-issuer-dist.zip` не собран; `run-gui.bat` и `run-html.bat` ни разу не
запускались, лицензия на Windows не выпускалась. Вердикт остаётся **NO-GO**, merge не рекомендуется.

CI: PR head `e59aa5b` — run 35965657324 (6/6 success, три шага license-chain success); fix-commit `c515a49` —
run 35973708182 (6/6 success; license-chain шагов в нём нет, т.к. ветка основана на `main`).

## Этап 18 — перенос исправлений в ветку PR #34 (merge-коммит `f85a362`, PR #36)

Сессия закреплена за веткой `arena/01a0d255-hr-manager` и не может пушить в `arena/01a0ccb9-hr-manager`,
поэтому перенос сделан так: в ветку сессии влита ветка PR #34 (`git merge origin/arena/01a0ccb9-hr-manager`),
конфликты add/add разрешены в пользу исправленных файлов, и открыт PR **#36** с базой `arena/01a0ccb9-hr-manager`.
Merge PR #36 и есть перенос исправлений в PR #34 — это решение владельца, и не раньше, чем появится Windows-доказательство.

Диф PR #36 относительно ветки PR #34 — ровно:

```
 .gitignore                     |   8 +
 tools/license-issuer/build.ps1 |  57 +--
 review-artifacts/*             | (доказательства, генераторы, вердикт)
```

`backend/`, `infra/`, Compose и `frontend/` не изменены; тесты не переписывались; ничего не смержено.

| Артефакт | Содержимое |
|---|---|
| `issuer-offline-evidence.json` / `.md` | **39 PASS · 0 FAIL · 3 INFO · 3 GAP · 8 NOT RUN** для ревизии `f85a362`; таблица «до/после» (baseline — `e59aa5b`), где каждая исправляемая проверка падает до и проходит после; измеренный loopback; хэши лаунчеров |
| `ci-run-status.json` | схема 2: PR-голова `e59aa5b`, фикс-коммит `c515a49`, **PR #36 и его CI-прогон `35977017563`** (id job'ов, conclusions, шаги license-chain, digest'ы артефактов), провенанс импортированного отчёта цепочки |
| `license-chain-evidence.{json,md}` | перегенерированы для `HEAD`; все `checks_read_from_real_files` совпадают со значениями ветки PR |
| `license-issuer-fixes.patch` | тот же двухфайловый фикс патчем (`git apply --check` по `e59aa5b` — ок) |
| `final-verdict.md`, `windows-issuer-bundle-check.md` | вердикт (**NO-GO**) и чек-лист владельца; Windows-проверка остаётся **NOT RUN** |

CI нового HEAD: прогон `35977017563` — 6/6 jobs success, и, поскольку ветка теперь содержит workflow из PR #34,
в нём **выполнены три шага license-chain** (artifact `compose-pilot-license-chain` id 10798503119,
`sha256:0f4772f9b81c9ddd…`). Отдельно отмечено: артефакты и логи этого прогона из sandbox недоступны (EOF), поэтому
step conclusions и digest взяты из REST API, а дословное тело отчёта цепочки осталось импортированным из прогона
35964596589.

**Windows runtime validation — NOT RUN**, пока владелец не предоставит реальные доказательства с чистой Windows
10/11 (страница HTTP 200, «Подпись корректна» для обоих issuer'ов, отсутствие приватного ключа в
`%TEMP%`/`%APPDATA%`/бандле/загрузках, ноль исходящих соединений). До этого `GO` не выпускается, merge PR #36 и
PR #34 не рекомендуется.
