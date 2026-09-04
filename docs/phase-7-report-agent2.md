# Phase 7 report — backup, deployment и release (agent 2)

- Ветка: `arena/phase-7-release`
- Базовый коммит: `f2fe9ee308c7ba426b807f33787fde27622602eb` (`docs: add phase 7 release prompt`), содержит merge Phase 6 (`19fd3f4a4c81cd2ab4c83c9b34fce472eccdcec4`).
- Коммиты этапа (по порядку):
  1. `1003885` — infra: backup-сервис, deploy/migrate-скрипты, HTTPS proxy
  2. `e28fd4d` — backend: движок шифрования, restore drill, ops API, метрики
  3. `72476fd` — docs + review-artifacts (workflow для переноса владельцем)
  4. `0a20e66` — фикс кавычек в compose-дефолте `BACKUP_SCHEDULE_UTC`
  5. `8b2a368` — фикс маскирования паролей в производных URL + резолвинг
     dump-тулинга для CI (см. «Что поймал CI»)
- Pull request: https://github.com/sledovatel61/HR-Manager/pull/9
- CI: https://github.com/sledovatel61/HR-Manager/actions/runs/33860309810 (см. ниже)
- **Merge в `main` не выполнялся** — это действие владельца.

## Что сделано

### Backup/restore

- Формат `HRMBCK1` (`backend/app/backup.py`): магическая сигнатура + JSON-заголовок
  (key id, версия формата, метаданные) + записи AES-256-GCM по 1 МиБ; заголовок
  входит в AAD каждой записи (подмена заголовка = ошибка аутентификации);
  отсоединённый SHA-256 в `<file>.sha256`.
- `backend/app/backup_runner.py`: `flock` (нет параллельных запусков), staging
  0600 через `os.open` (без umask-окна), `pg_dump -Fc` → шифрование → обратная
  аутентифицированная проверка → атомарная публикация (`os.replace`);
  retention ≥ 7 дней с нижней границей `BACKUP_MIN_COPIES` (при провале запуска
  копии не удаляются); restore drill в отдельную БД; exit-коды 0/2/3/4/5/6/7/8/9/10/11.
- CLI `backend/app/cli.py`: `backup-now/check/drill/list/prune`;
  `--actor` проверяет пароль администратора против хеша в БД
  (`BACKUP_ADMIN_PASSWORD` или интерактивный ввод), `--as-scheduler` — явная
  сервисная идентичность.
- API `backend/app/routers/ops.py`: `POST /admin/ops/backup` (202 + фоновый
  поток, 409 при дубликате request-id, 403 без роли admin), `/admin/ops/backups`,
  `/admin/ops/releases`, `/ops/status`, `/ops/backup-health` (503 при
  просрочке/нецелостности — без фальшивых 200), `/ops/metrics` (Prometheus,
  route-шаблоны, без query/cookie/заголовков).
- Аудит: `backup_started/succeeded/failed`, `backup_retention_cleaned`,
  `backup_restore_drill_started/succeeded/failed`, `backup_verify_failed`,
  `deploy_recorded`, `release_recorded` — без PII, строк подключения, ключей
  и содержимого дампов.
- Compose-сервис `backup` (dev + prod): образ `backend/Dockerfile.backup`
  (pg_dump/pg_restore 16.15 собраны из исходников с pinned SHA-256, non-root,
  `TZ=UTC`), планировщик `infra/scripts/backup_scheduler.sh` (расписание UTC,
  retry ×3 с backoff 300/600/900 с, drill каждые 168 ч, маркер healthcheck),
  отдельный volume `backups` (backup никогда не лежит в volume приложения).
- `infra/scripts/check_env.sh`: при `BACKUP_ENABLED=true` отсутствующие/
  dev-значения/короткие ключи шифрования — жёсткая ошибка; иначе warning.
- Production-guard в `app/config.py`: dev-ключ и ключи неверной длины
  отклоняются в production; `BACKUP_RETENTION_DAYS < 7` отклоняется везде.

### Deployment/release

- `infra/scripts/deploy.sh`: preflight (`check_env.sh`) → build + теги
  `release-<sha>`/`release-current`/`release-prev` → one-shot миграции
  (`migrate.sh up`, advisory lock) → переключение трафика с readiness-гейтом
  (`compose up --wait`) → smoke (`/health` 200 + `release_sha` из `/ops/status`)
  → автоматический rollback при провале; `--failure-drill` доказывает откат
  в тестовом контуре; release notes (`/tmp/release-notes-<sha>.md`, артефакт CI).
  Предыдущие образы не удаляются.
- Production-оверлей: публикует 0 портов; CMD backend без auto-migrate;
  `RELEASE_SHA` пробрасывается в `/ops/status`; `migrate.sh` не имеет
  downgrade (rollback схемы — только по явному безопасному плану).
- HTTPS: `infra/docker-compose.proxy.yml` + `infra/nginx/default.conf.template`
  (TLS 1.2/1.3, HTTP→HTTPS redirect с документированной политикой,
  HSTS/X-Content-Type-Options/X-Frame-Options/Referrer-Policy/
  Permissions-Policy/CSP, `client_max_body_size 10m`, без секретов;
  сертификат монтируется из `TLS_CERT_DIR`) + `infra/nginx/README.md`
  (шаги оператора, проверка срока действия, явные non-claims).

### CI (только review-artifacts — App без права `workflows`)

- `review-artifacts/ci.agent-2.phase7.yml/.patch` — обновлённый `ci.yml`:
  установка `postgresql-client-16` и переменные backup-контура в integration;
  негативные/позитивные preflight-проверки backup-секретов; валидация
  proxy-оверлея и ожидание реального зашифрованного `*.pgdump.enc` в стеке.
- `review-artifacts/release.agent-2.yml/.patch` — новый workflow `release.yml`:
  деплой только с тега `release-*` после зелёного CI для точного SHA; секреты
  только из GitHub Secrets; без `DEPLOY_HOST` — локальный тестовый контур на
  CI-раннере. Инструкция переноса — в `review-artifacts/README.md`.

## Что поймал CI (реальные баги, найденные GitHub Actions)

Первый прогон CI на PR поймал три реальные проблемы, которые локальная
песочница не могла воспроизвести (встроенный PostgreSQL в песочнице —
trust-аутентификация, пароль игнорируется):

1. **Маскирование пароля в производных URL (production-баг).** SQLAlchemy
   2.x в `str(URL)` заменяет пароль на `***`. `_admin_url()` в интеграционных
   тестах и — главное — построение drill-URL в `backup_runner.py`
   (`str(make_url(...).set(database=...))`) отдавали подключение с паролем
   `***`; при scram-аутентификации (как в CI и в любом production) restore
   drill падал бы с «password authentication failed». Исправлено:
   `render_as_string(hide_password=False)` в обоих местах + regression-тест
   `test_sqlalchemy_url_stringification_hides_password_trap`.
   **Верификация фикса**: поднят отдельный локальный кластер с
   `--auth=scram-sha-256` и UTF8 (точный аналог CI-контура) — все 11
   backup-интеграционных тестов прошли под парольной аутентификацией.
2. **Backup-интеграционные тесты не скипались в CI** (на ubuntu-24.04
   раннере уже есть pg_dump/pg_restore 16), но использовали путь тулинга
   песочницы `/tmp/pginst`. Теперь тулинг резолвится по приоритету:
   `BACKUP_PGDUMP_BIN`/`BACKUP_RESTORE_BIN` → бинарник песочницы → `PATH`;
   скип только при полном отсутствии обоих.
3. **npm registry увёл legacy quick-audit endpoint** (`/-/npm/v1/security/
   audits/quick`): npm < 11 шлёт запрос туда и получает 400 «Invalid package
   tree»/зависание; npm 11 использует новый bulk-advisory endpoint.
   Локально подтверждено: `npm audit` с npm@11 → `found 0 vulnerabilities`
   (npm@10.9.4 зависает). Шаг аудита исправлен в передаваемом CI
   (`review-artifacts/ci.agent-2.phase7.yml/.patch`: `npm install -g npm@11`
   перед аудитом). В текущем (неперенесённом) workflow шаг `npm audit`
   редактировать нельзя — App не имеет права `workflows`; до переноса
   владельцем этот шаг будет красным по причине реестра (не зависимостей).

## Формат backup и команды

- Дамп: `pg_dump -Fc` (custom, zlib), сервер и тулинг закреплены на PostgreSQL 16
  (образ backup-сервиса собирает клиент ровно 16.15; CI-интеграция использует
  PGDG 16.x после переноса workflow). Sandbox-интеграция использовала pg_dump
  18.4 против сервера 18.4 (см. «Ограничения»).
- Имя: `hr-manager-YYYYMMDDTHHMMSSZ-<8 hex>.pgdump.enc` (UTC); run-id —
  детерминированный hex из request-id.
- Команды:
  ```bash
  # внутри backup-контейнера:
  backup-scheduler [scheduler|oneshot|check|drill|list|prune]
  # миграции (до переключения трафика, one-shot, advisory lock):
  infra/scripts/migrate.sh up|check|current|history
  # деплой с verified switch и автоматическим rollback:
  infra/scripts/deploy.sh --release <full-sha> [--target local|ssh] [--failure-drill]
  # ручной backup через API администратора:
  POST /admin/ops/backup {"reason": "...", "request_id": "..."}   # 202 / 409 / 403
  ```

## Фактические результаты (песочница агента)

- **Полный цикл backup → шифрование → restore drill** выполнялся на реальном
  PostgreSQL в песочнице (`tests/test_integration_backup.py`, 11 тестов, все
  зелёные; **два контура**: встроенный PG 18.4 + отдельный кластер со
  `scram-sha-256` как аналог CI): pg_dump → `HRMBCK1` (магия `HRMBCK1\n`
  проверена) → отсоединённый checksum → pg_restore в отдельную БД
  `hr_manager_drill_test` → `alembic upgrade head` → все 6 ключевых таблиц
  (`users`, `candidates`, `audit_log`, `events`, `candidate_terminations`,
  `analytics_facts`) + непустой `users` → запуск uvicorn на drill-БД и
  `/health` → 200 → cleanup (drill-БД удалена, production-БД не тронута).
- Повреждение ciphertext (бит в середине) → `backup-check --deep` failure и
  drill failure с аудитом `backup_restore_drill_failed`.
- Параллельный запуск → `flock` блокирует второй (exit 4, аудит
  `backup_failed`), файлы не повреждаются.
- Слабый ключ (не 32 байта), неверный key id, недоступная БД (порт 5499) →
  безопасные ошибки без утечки значений; ничего не публикуется.
- Частичный файл с валидным именем не считается backup и не мешает проверке
  свежести; retention удаляет только просроченное и сохраняет минимум.
- Advisory-lock миграций: второй `alembic upgrade` реально блокируется до
  снятия `pg_advisory_xact_lock(767147072)` первым.
- CLI end-to-end: `python -m app.cli backup-now --actor <admin>` (аутентификация
  по паролю против БД) и `backup-check --deep` — 0, аудит под администратором.
- Полный backend-сьют: **272 passed** с интеграциями (unit 216 + integration
  56, включая новые backup-интеграционные), ruff/mypy/format — чисто.
- Frontend: ESLint/tsc чисто, 101 тест, production build; `npm audit` — 0
  уязвимостей (npm@11, bulk-advisory endpoint; см. «Что поймал CI»).
- `bash -n` для всех скриптов `infra/scripts/*.sh`; `check_env.sh` прогнан
  поведенчески (warning-режим и жёсткий режим `BACKUP_ENABLED=true`).
- `git diff --check` чистый (для unified-diff патчей в review-artifacts
  добавлен `.gitattributes` — пустые контекстные строки формата diff).
- Сборка pg_dump/pg_restore 16.15 из исходников выполнена в песочнице тем же
  рецептом, что в `Dockerfile.backup` (configure → stub psqlscan → make →
  make install → `pg_dump --version` → 16.15); zlib 1.3.2 собран статически.

## Модель секретов

- Только переменные окружения / GitHub Secrets / volume-пути оператора:
  `SECRET_KEY`, `POSTGRES_PASSWORD`, `BOOTSTRAP_ADMIN_PASSWORD`,
  `BACKUP_KEY_ID`, `BACKUP_ENC_KEY` (base64, 32 байта), `BACKUP_LEGACY_KEYS`
  (JSON), `DEPLOY_HOST`/`DEPLOY_SSH_KEY` (деплой на свой хост), TLS-пара в
  `TLS_CERT_DIR` (только proxy-контейнер).
- Dev-значения явно помечены DEVELOPMENT ONLY и отклоняются
  production-guard'ами (config.py, check_env.sh).
- Backup — секретный актив: не попадает в git (`.pgdump*` вне репо), образы,
  CI-артефакты и логи; потеря ключа = невозможность восстановления (задокументировано).
- В репозитории и отчёте нет реальных секретов, PII и содержимого backup.

## RPO/RTO

- RPO ≤ 24 ч + время одного backup-окна (ежедневный полный backup в 02:00 UTC;
  ручной backup перед опасной операцией — минуты).
- RTO ≤ 4 ч (цель; restore drill регулярно измеряет фактическое время цикла).
- Полная таблица алертов (severity/dedup/cooldown/действия) — в
  `docs/backup-and-restore.md`.

## Миграции и rollback

- Новых Alembic-ревизий не потребовалось (audit-действия — строковый
  non-native Enum без CHECK-констрейнта; head остался `0006`).
- Стратегия: backward-compatible миграции или явный двухшаговый деплой;
  колонки/таблицы не удаляются, пока старый код их использует; rollback кода
  (предыдущий образ) отделён от rollback схемы (downgrade — только по явному
  безопасному плану, иначе restore-forward из backup); автоматический
  downgrade production запрещён (`migrate.sh` его не имеет).
- Деплой: миграции one-shot до переключения трафика, concurrency guard —
  `pg_advisory_xact_lock` в `alembic/env.py`.

## Smoke-проверки

- Деплой: `/health` 200 + `/ops/status.release_sha == RELEASE_SHA` (через
  exec в контейнер — в production порты не публикуются); провал readiness →
  автоматический откат на `release-prev`; `--failure-drill` проверяет откат
  намеренно сломанным релизом и повторный smoke после отката.
- Стек (CI, после переноса workflow): `/health` 200, frontend + `/api/health`,
  деградация 503 при остановке БД, реальный `*.pgdump.enc` в volume
  backup-сервиса, валидация proxy-оверлея (только 80/443, без dev-креденшелов).

## Изменённые файлы

Backend: `app/backup.py`, `app/backup_runner.py`, `app/metrics.py`,
`app/routers/ops.py`, `app/cli.py`, `app/config.py`, `app/models.py`,
`app/schemas.py`, `app/main.py`, `alembic/env.py`, `pyproject.toml`,
`requirements.txt`, `Dockerfile.backup`; тесты: `test_backup_lib.py`,
`test_ops_api.py`, `test_integration_backup.py`, `test_config.py`,
`test_production_overlay.py`.
Infra: `docker-compose.yml`, `compose.prod.yml`, `docker-compose.proxy.yml`,
`nginx/default.conf.template`, `nginx/README.md`,
`scripts/{backup_scheduler.sh,migrate.sh,deploy.sh,check_env.sh}`.
Docs/прочее: `README.md`, `docs/ARCHITECTURE.md`, `docs/backup-and-restore.md`,
`Makefile`, `.gitattributes`, `review-artifacts/{ci.agent-2.phase7.yml,
ci.agent-2.phase7.patch,release.agent-2.yml,release.agent-2.patch,README.md}`.

## Ограничения и невыполненные проверки

- **Docker недоступен в песочнице** (бинарника нет, Docker Hub недостижим):
  сборка образов (`Dockerfile.backup` и др.), `docker compose config/up`,
  deploy-smoke и failure-drill в тестовом контуре исполняются **только на
  GitHub-раннере**. В песочнице: рецепт сборки pg-инструментов проверен
  нативно (те же команды), Compose-файлы — статически (pytest-оверлей-тесты
  + YAML), скрипты — `bash -n` и поведенческими тестами `check_env.sh`.
- **CI с новым workflow не исполняется**: публикующая GitHub App не имеет
  права `workflows`; до переноса владельцем (`review-artifacts/README.md`)
  на PR выполняется прежний `ci.yml`. Важное уточнение против
  первоначального плана: на ubuntu-24.04 раннере **уже есть**
  pg_dump/pg_restore 16, поэтому backup-интеграционные тесты в старом CI не
  скипаются, а реально выполняются (см. «Что поймал CI»).
- **Шаг `npm audit` в текущем (неперенесённом) CI будет красным**: npm
  registry увёл legacy quick-audit endpoint, а npm < 11 (бандлится с Node
  22 на раннере) шлёт запрос именно туда — 400 «Invalid package tree»/
  зависание, к зависимостям отношения не имеет. Локально npm@11 → 0
  уязвимостей. Исправление — в передаваемом workflow
  (`review-artifacts/ci.agent-2.phase7.*`: `npm install -g npm@11` перед
  аудитом); изменить сам шаг App не может (нет права `workflows`).
- Sandbox-интеграция использовала встроенный PostgreSQL 18.4 и pg_dump 18.4
  (эквивалентный путь), production/dev/CI закрепляют PostgreSQL 16; смешение
  major-версий дампа и сервера не допускается (pg_dump отказывается от более
  нового сервера) — это документировано, а не проверено неявно.
- Единственный `pass` в коде — best-effort cleanup drill-БД
  (`# pragma: no cover`), не заглушка и не фиктивный успех.
- Отчёт не содержит значений секретов, PII и содержимого backup.
