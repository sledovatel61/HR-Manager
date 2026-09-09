# Phase 12 — графическая установка Windows-пилота (Arena)

## PR и проверяемый код

- Baseline `origin/main`: `8d6c2a34e11da9a3aae8a4fc6cbd4aacf16c910f`.
- Рабочая ветка: `arena/phase-12-windows-installer-setup` (создана от
  `origin/main`; `main` не изменялся, merge не выполнялся, чужие ветки/worktree
  не использовались).
- Коммиты ветки (в порядке применения):
  - `37fadf4` — Phase 12 backend (first-run, hardened pilot config, overlay);
  - `8cc9bf1` — Phase 12 frontend (first-run wizard);
  - `ce8a8c0` — Phase 12 frontend (working mode = стартовый экран);
  - `2fb5a6e` — Windows installer engine, GUI wizard, build script, тесты.
- Отдельный коммит добавляет CI job `windows-installer` в
  `.github/workflows/ci.yml`; на момент написания отчёта он не может быть
  запушен, т.к. GitHub App сессии не имеет права `workflows: write`
  (см. «Ограничения»). Он будет добавлен в ветку сразу после выдачи права.
- Точный итоговый SHA (включая этот отчёт) после commit/push фиксируется в
  итоговом комментарии PR; CI-ссылки приводятся там же. Для точного checkout:
  `git rev-parse HEAD`.
- Миграционный head: `0013` (down_revision `0012`); путь `0012 → head` доказан
  тестом `test_upgrade_0012_to_head_preserves_data` на PostgreSQL.

## Что реализовано

### 1. Пилотный Compose-профиль

`infra/compose.pilot.yml` — отдельный явный overlay поверх
`infra/docker-compose.yml` (production/dev профили не ослаблены). Один контур
`name: hr-manager-pilot`: PostgreSQL, backend, frontend, worker и backup в
Linux-контейнерах Docker Desktop/WSL2. Наружу публикуется **только frontend на
`127.0.0.1:${PILOT_HTTP_PORT:-8080}:8080`**; db/backend/worker/backup — `ports:
!reset []`. Backend `command` — только uvicorn (без auto-migrate); миграция
выполняется движком один раз под advisory lock. `APP_ENV=pilot`, `APP_DEBUG
=false`, dev-секреты запрещены, Telegram/SMTP выключены, `mailpit` исключён
профилем. Секреты — только из installer env-file с `${VAR:?}`. Образы
release-tagged (`${PILOT_RELEASE_TAG:-pilot-current}`). Локально-доверенная
модель: `SESSION_COOKIE_SECURE=false` только для pilot (loopback HTTP),
SameSite+HttpOnly+CSRF сохраняются; production не меняется.

### 2. Графический установщик и движок

- `infra/windows/hr-manager.iss` — русский мастер Inno Setup **6.5.1**
  (закреплённая версия; `winget JRSoftware.InnoSetup --version 6.5.1` в CI).
  Сценарий: роль → фамилия → «Установить»; фамилия/роль передаются движку через
  JSON input file, а НЕ через командную строку процесса.
- `infra/windows/hr-manager.ps1` — один PowerShell entry point (автоматизация,
  не интерфейс): `install/start/stop/status/update/diagnostics/uninstall/check`;
  сменный command runner (`$script:CmdRunner`) для тестов; `-NonInteractive` и
  `-TestMode` (не трогает реальную установку); dot-source guard (Main не
  выполняется при dot-source, поэтому Pester вызывает функции); resume после
  перезагрузки из сохранённого `install-input.json`; криптографический ГСЧ
  (`RandomNumberGenerator`); ACL state dir (`icacls`, fail closed);
  update = lock → encrypted backup + integrity check → one-shot
  `alembic upgrade head` → переключение → bounded smoke (health/ops/migrations/
  release SHA) → code rollback **без** Alembic downgrade; diagnostics
  санитаризируется общими паттернами.
- `infra/windows/redaction-patterns.json` (+schema) — общие regex-правила
  редкации, потребляемые движком (.NET) и Python-тестами.

### 3. Секреты и локальное состояние

`POSTGRES_PASSWORD`, `SECRET_KEY`, `BACKUP_KEY_ID`, `BACKUP_ENC_KEY` генерируются
автоматически в `%LOCALAPPDATA%\HRManager\pilot.env` (ACL-ограничен), не
печатаются и не ротируются при повторном запуске. Токен первого запуска —
одноразовый, в env и fragment ссылки, не в URL-логах и не в командной строке.

### 4. Первый запуск в браузере

`GET /setup/first-run/status` (без PII), `POST /setup/first-run` (loopback-only,
constant-time токен, TTL, rate-limit, одноразовое атомарное погашение
`pilot_first_run_claims`), `POST /setup/password` (CSRF+политика). Один владелец
`admin` + явный `pilot_full_access` + `working_mode`; случайный невидимый пароль
с `password_change_required`; потерянная сессия восстанавливается перевооружением
пароля без второго владельца/takeover. SPA: `FirstRunFlow` (показывает фамилию и
рабочий режим, требует пароль), рабочий режим = стартовый экран Workspace.
Email/Telegram пропускаются в `SetupWizard` (существующий wizard уведомлений).

### 5. Update и rollback

Порядок §5 контракта реализован в `Invoke-Update` (preflight → lock →
target manifest → backup+check → миграция → переключение → smoke → фиксация;
при ошибке — возврат кода с проверкой health, без автоматического downgrade БД,
честный статус). Lock-файл гарантирует отсутствие двух одновременных update.

### 6. Status и diagnostics

`Invoke-Status` агрегирует Docker/daemon, Compose, app (stopped/starting/ready/
degraded), БД, migration head, worker, backup, release mismatch, Telegram/SMTP.
`Invoke-Diagnostics` собирает версии и санитаризированные логи через общие
паттерны редкации (запрещены env/connection strings/cookies/токены/пароли/
email/телефоны/ФИО/тексты сообщений).

### 7. Повторная установка и удаление

`uninstall` по умолчанию останавливает/удаляет контейнеры и ярлыки, **не удаляя
PostgreSQL и backup volumes**; `-RemoveData` требует точную фразу и предлагает
зашифрованный backup. Inno-деинсталлятор вызывает `uninstall` без `down -v`.

### 8. Совместимость и границы

PostgreSQL остаётся единственной пилотной БД; путь миграций от `0012`; данные
фаз 0–11 сохранены; production Compose/deploy/backup не изменены; PII/секреты в
git/логах/audit/diagnostics запрещены (проверяется тестами). Electron/Windows
service/MSI, встроенная поставка Docker Desktop, авто-принятие лицензии,
macOS/Linux desktop, облачный control plane, remote publish — **не входят**.

## Ключевые файлы

- `infra/compose.pilot.yml` — пилотный overlay.
- `infra/windows/hr-manager.ps1` — automation engine.
- `infra/windows/hr-manager.iss` — Inno Setup 6.5.1 мастер.
- `infra/windows/README.installer.ru.md` — пользовательская инструкция.
- `infra/windows/redaction-patterns.json` / `redaction-patterns.schema.json`.
- `infra/windows/release-manifest.schema.json` + `scripts/build-installer.ps1`.
- `infra/windows/hr-manager.Tests.ps1` — Pester-сценарии.
- `backend/app/config.py`, `models.py`, `schemas.py`, `routers/setup.py`,
  `bootstrap.py`, `alembic/versions/0013_pilot_first_run.py`.
- `backend/tests/test_pilot_first_run.py`, `test_pilot_overlay.py`,
  `test_integration_pilot.py`, `test_windows_engine.py`, обновлённые
  `test_migrations.py`/`conftest.py`.
- `frontend/src/features/notifications/FirstRunFlow.tsx`,
  `App.tsx`, `api.ts`, `types.ts`, `app-shell/Workspace.tsx` + тесты.
- `.github/workflows/ci.yml` — job `windows-installer`; `stack` валидирует
  pilot overlay.

## Toolchain, подпись и целостность

- Inno Setup **6.5.1** (winget `JRSoftware.InnoSetup`), PowerShell 5.1+,
  Docker Compose v2.24+ (`!reset`), Docker Desktop/WSL2.
- `build-installer.ps1 -ManifestOnly` пишет `release-manifest.json` (name,
  version, `release_sha` 40 hex, SHA-256/bytes по каждому файлу payload,
  `signature.signed`). `-Verify` пересчитывает хэши.
- **Честный статус подписи:** доверенный сертификат в текущей инфраструктуре не
  предоставлен, поэтому манифест записывает `signed=false` (детерминированный
  signing hook: задать `SIGNING_CERT_THUMBPRINT`/`ISCC` подписывающий шаг в
  CI/release). Неподписанный файл не выдаётся за подписанный.

## Проверки (локально подтверждённые)

Backend (`/home/user/.venv-hr`, Python 3.11):

- `ruff check .` — All checks passed.
- `ruff format --check .` — 104 files already formatted.
- `mypy app tests` — Success: no issues found in 90 source files.
- `pytest -m "not integration" -q` — **527 passed**, 102 deselected
  (включая 21 pilot first-run, 15 windows engine, 12 pilot overlay, migration
  upgrade 0012→head).
- `pytest tests/test_windows_engine.py -q` — 15 passed.

Frontend (Node 22.22.3):

- `npx tsc -b` — без ошибок.
- `npx eslint .` — без ошибок.
- `npx vitest run` — **22 files / 153 tests passed**.
- `npm run build` — успешно (90 modules).

Не выполнены локально (нет Docker/pwsh/Inno в песочнице; выполняются в CI):

- `pytest -m integration` (PostgreSQL) — job `integration`.
- `docker compose -f infra/docker-compose.yml -f infra/compose.pilot.yml config`
  — шаг в job `stack`.
- PowerShell parser + Pester + сборка `Setup.exe` + проверка manifest — job
  `windows-installer` (windows-latest).
- Изолированный Windows/Docker acceptance smoke — требуется Windows VM;
  локально недоступен (Linux-песочница без Docker).

## Ограничения и handoff

- **CI job `windows-installer` (в `.github/workflows/ci.yml`) не запушен** на
  момент написания отчёта: GitHub App сессии (`arena-ai-coding-agent[bot]`) не
  имеет права `workflows: write`, поэтому GitHub отклоняет любой коммит,
  изменяющий файлы в `.github/workflows/`. Изменение `ci.yml` подготовлено
  отдельным коммитом и будет добавлено в ветку сразу после выдачи права
  (GitHub Apps → arena-ai-coding-agent → Permissions → Workflows: Read and
  write). Сам job добавляет: PowerShell parser + Pester + сборку `Setup.exe`
  (winget Inno Setup 6.5.1) + проверку release-manifest + upload артефактов, а
  job `stack` дополнительно валидирует pilot overlay.
- Полный Windows acceptance (Setup.exe → роль → фамилия → install → ярлык →
  первый вход → status → stop/start → update/failure drill → uninstall с
  сохранением данных → reinstall) выполняется на windows-latest/Windows VM;
  локальная Linux-песочница его заменить не может. Команда и ошибка
  POSIX-`fcntl` на Windows host документированы в контракте; полный backend
  на Windows подтверждается Linux-контейнером/CI.
- Обновление принимает только явный локальный release-каталог с валидным
  `release-manifest.json` (trust boundary: подпись release-артефакта не
  поддерживается текущим процессом — задокументировано честно).
- Следующему агенту: после merge — зафиксировать финальный SHA, CI-ссылки и
  результат Windows acceptance в PR-комментарии; при необходимости добавить
  детерминированный signing-шаг в release-процесс.
