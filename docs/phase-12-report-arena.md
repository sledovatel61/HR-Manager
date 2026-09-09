# Phase 12 — локальный пилот Windows (Arena)

## PR и проверяемый код

- Ветка: `arena/01a084e5-hr-manager` → PR в `main` (merge не выполнялся).
- PR: https://github.com/sledovatel61/HR-Manager (номер — в комментарии PR).
- База ветки: `8d6c2a34e11da9a3aae8a4fc6cbd4aacf16c910f` (актуальный
  `origin/main`, проверено повторным fetch перед работой).
- Имя ветки: контракт предлагал `arena/phase-12-windows-installer-<suffix>`;
  фактическая ветка закреплена сессией Arena (`arena/01a084e5-hr-manager`) —
  переименование разорвало бы отслеживание сессии, поэтому использована она.
- Final SHA фиксируется в итоговом комментарии PR (отчёт входит в
  проверяемый tip; SHA не может содержаться внутри самого себя).

## Что реализовано

### Backend (первый вход пилота)

- `POST /api/setup/first-run/claim` — публичный только для петлевого адреса
  (Host 127.0.0.1 + Origin same-origin), одноразовый код 6 символов из
  алфавита без неоднозначных букв; в БД хранится только SHA-256 кода;
  TTL 15 минут; лимит 10 попыток/15 минут по IP и 8 неверных кодов на пару
  (отмена пары); `FOR UPDATE` сериализует конкурентные заявления; при любой
  уже существующей учётной записи — 409. Аудит не содержит ни кода, ни фамилии.
- `GET /api/setup/first-run/state` — публичные булевы признаки шага UI
  (pending/срок/fresh/pilot_owner_exists), без PII.
- `PUT /api/setup/first-run/password` — доступна ТОЛЬКО пока у владельца
  стоит `password_is_bootstrap`; политика 12–128, Argon2id, CSRF-сессия;
  после установки пароля эндпоинт закрыт навсегда.
- Клад владельца — один: роль `admin` + явный `pilot_full_access`
  (суперсет обоих документов-скоупов через `has_grant`), username детерминирован
  из фамилии (`romanize(фамилия)-pilot-<4hex>`), пароль генерируется сервером и
  НИКОГДА не возвращается/не логируется.
- `python -m app.cli pilot-pairing issue|status` — выдача кода для движка:
  JSON со STDIN (код/фамилия/режим не проходят через argv), валидация на входе,
  `status` возвращает коды 0/1/2 для «есть pending / всё готово / ждём».
- Миграция `0013_pilot_first_run`: `users.work_role`, `users.password_is_bootstrap`,
  таблица `pilot_pairings` (partial unique `WHERE status='pending'` — один
  активный код на инсталляцию).
- `config`/`host_guard`: пилот-режим (локальное доверие без HTTPS cookie
  secure только при loopback-развёртывании через явный флаг), main — подключение
  роутеров без изменения существующих путей.

### Пилотный Compose-контур

- `infra/compose.pilot.yml` — профиль пилота поверх dev-базы: публикуется
  ТОЛЬКО frontend на `127.0.0.1` (порт `HRMGR_PILOT_PORT`, по умолчанию 8081);
  db/backend/worker/backup/mailpit — `ports: !reset []`; изображения с тегом
  `pilot-${HRMGR_RELEASE_VERSION:?}` (без :latest); обязательные
  `${SECRET_KEY:?}`/`${POSTGRES_PASSWORD:?}`; `APP_ENV=production`, debug off,
  TELEGRAM/SMTP off, bootstrap-пароль пуст (первый вход — только pairing);
  миграции НЕ автоматические (явный `run --rm backend alembic upgrade head`,
  как делает движок); mailpit выключён профилем `pilot-disabled`.
- `backend/tests/test_pilot_overlay.py` — 10 статических тестов контракта
  (парсинг YAML с `!reset`, grep-проверки отсутствия dev-секретов/портов/отладки).

### PowerShell-движок (infra/windows/)

- `hr-manager.ps1` — действия install / repair / resume / ensure-running /
  start / stop / status / update / diagnostics / uninstall / preflight;
  неинтерактивен; результат — JSON (`-JsonOut`), коды возврата: 2 preflight,
  3 docker, 4 update/rollback, 5 конфиг/ACL, 6 диск, 7 неподтверждённое
  удаление данных.
- Секреты генерируются ОДИН раз (`secretKey`, `postgresPassword`,
  `backupEncKey/backupKeyId`) в `state\secrets.json` и `state\pilot.env` с
  явным ACL «только пользователь» (Windows — через `Get-Acl` с проверкой
  результата; не-Windows путь только для тестового контура — chmod 600);
  повреждённое хранилище — отказ (5), никакой тихой ротации.
- Фамилия/пары кодов: единственный канал — STDIN в контейнер
  (`docker compose exec -T backend python -m app.cli pilot-pairing issue`);
  файлы входа удаляются после успешной выдачи; тесты проверяют, что ни argv,
  ни логи, ни pilot.env их не содержат.
- install идемпотентен (preflight → целостность комплекта по `SHA256SUMS.txt` →
  секреты → build → up → явные миграции → smoke → выдача pairing → ярлыки);
  повторный запуск не создаёт второго владельца и не меняет секреты; сбой
  шага = «повтори безопасный шаг» без потери данных.
- update: блокировка `update.lock` (перехват сироты старше 2 часов) →
  проверка целостности НОВОГО комплекта → сборка образов (старая версия ещё
  работает) → РЕЗЕРВНАЯ КОПИЯ с deep-подтверждением (файл копии + «backup
  created» + «deep check ok», не код возврата) → swap файлов (прежний
  комплект в `app\previous` для отката) → остановка → `alembic upgrade head`
  (forward-only) → запуск → проверка health+release_sha+миграций. Отказ на
  проверке = откат КОДА (файлы+контейнеры назад), миграции не откатываются
  никогда; этап записан в `update-stage.json`, история в `update-history.json`.
- uninstall: `compose down` БЕЗ `-v` — тома PostgreSQL, резервные копии и
  состояние сохраняются; `-RemoveData` требует точной фразы
  «УДАЛИТЬ ДАННЫЕ HR MANAGER» (регистр/символы — всё важно), перед удалением
  экспортирует свежие копии в `final-backup`; ярлыки и RunOnce удаляются.
- resume после перезагрузки: `resume.json` + одноразовая метка
  `HKCU\...\RunOnce`; Docker Desktop — официальный установщик из
  `docker-desktop.json` (пустой sha256 = скачивание запрещено, fail closed;
  лицензию принимает пользователь, silent-установка не форсируется).
- diagnostics/status: все секреты под `Protect-HrmText` (+ контрольная проверка
  перед записью отчёта); статус различает «Docker не установлен» / «daemon не
  запущен», stopped/starting/running/degraded, миграции/worker/backup и
  «установленная версия ≠ запущенная»; Telegram/SMTP честно «не настроены»
  (флаги читаются из env контейнера, без значений).
- Тесты: `infra/windows/tests/Invoke-HrmTests.ps1` — parse-проверка всех
  скриптов + 25 сценарных тестов на mockable-точке `Invoke-HrmTool`/HTTP-хуке
  (без реального docker; идемпотентность, порядок update, откат, фразы
  удаления, ACL, редакция, коды возврата).

### GUI-установщик (Inno Setup)

- `infra/windows/installer/HrManagerPilot.iss` — русновандный мастер:
  страницы «фамилия» + «рабочий режим» (UX), ввод передаётся движку только
  временным JSON в `{tmp}`; прогресс/статусы — от движка; ошибки движка
  показываются как есть (русные, с подсказкой); ярлыки и «Программы и
  компоненты» — как в контракте; удаление — через движок с диалогом
  подтверждения фразы; per-user без администратора.
- Бинарники в git НЕ коммитятся: Setup.exe и bundle собираются в CI job
  `installer-windows`; версия Inno закреплена (`inno-lock.json`, 6.4.1) с
  проверкой Authenticode и опциональным pinned SHA-256; bundle несёт
  `release-manifest.json` + `SHA256SUMS.txt` (их проверяет движок).

### Фронтенд

- `/first-run` — автономная страница (App ветвит путь до auth-гейта, сессию не
  трогает): состояние → ввод кода (клиентская форма 6–8 символов, ошибки
  сервера как есть) → карточка созданной учётной записи (показывается
  детерминированный username, пароль не показывается никогда) → установка
  пароля (политика клиент+сервер) → чек-лист готовности (health/БД/миграции/
  worker/backup через публичный `/api/ops/status`) → «Открыть HR Manager».
  Настройки (часы/тихие часы/интеграции) остаются в разделе «Настройки» —
  пилот может их пропустить.

### CI

- `pilot-compose`: генерация эфемерных CI-секретов, `compose config` с
  проверкой «ровно один published порт, только 127.0.0.1», нет dev-паролей,
  mailpit под профилем; реальный `up --build` + явные миграции;
  `.github/scripts/pilot_first_run_smoke.py`: выдача кода через CLI по STDIN →
  неверный код 403 → claim (session+csrf, username `smolov-pilot-*`) →
  мёртвый код 410 → слабый пароль 422 → свой пароль → вход обычным логином →
  эндпоинт bootstrap закрыт (403) → `/ops/status` с release_sha → том backup.
- `installer-windows`: тесты движка pwsh-скриптом, `Build-Release.ps1`
  (bundle+манифест+суммы), проверка целостности тем же кодом движка, установка
  закреплённого Inno (Authenticode + pinned sha если задан), компиляция
  Setup.exe, тихий install/uninstall смоук (движок не запускается — флаг
  smoke), публикация артефактов.

## Результаты проверок (песочница)

- `ruff check . && ruff format --check . && mypy app tests` — 0 ошибок
  (92 файла).
- `pytest -m "not integration" -q` — **530 passed**.
- `pytest -m integration -q` (реальный PostgreSQL + pg_dump/restore) —
  **103 passed** (полный зелёный прогон финального кода; временная база
  pgserver, миграции 0013 применены).
- Фронтенд: `npm run lint` (0 warnings), `npm run typecheck`,
  `npm test -- --run` — **152 passed**, `npm run build` — OK.
- `python3 .github/scripts/pilot_first_run_smoke.py` — синтаксис
  (`py_compile`) OK; сам прогон — только в CI (в песочнице нет docker).
- PowerShell-скрипты: ручной аудит синтаксиса (расстановка кавычек/скобок
  скриптом) + parse-проверка в CI; pwsh в песочнице недоступен (зеркала
  заблокированы), поэтому прогон тестов движка — исключительно в
  `installer-windows` job.

## Честные ограничения

1. Живой сценарий «Windows + Docker Desktop» (установка/первый вход/ребут-
   резюме/обновление/удаление с данными) в этой среде НЕ выполнялся — он
   покрыт тестами движка на моках (все 25 сценариев) и ручным приёмочным
   прогоном `infra/windows/acceptance/Run-Windows-Acceptance.ps1`.
2. `docker-desktop.json` оставлен с пустым sha256: закрепление версии/хэша
   — действие релиз-инженера (в песочнице сайты Docker/Inno недоступны);
   движок при пустом значении скачивание ОТКАЗЫВАЕТСЯ выполнять (fail closed).
3. Windows PowerShell 5.1 поддерживается через запасной путь экранирования
   аргументов (нет `ArgumentList`), но официально рекомендуемая среда —
   PowerShell 7; CI-прогон тестов идёт на pwsh.
4. Пилот не добавляет RBAC для рабочих режимов (UX-настройка) и не открывает
   порты за пределы 127.0.0.1; HTTPS в пилоте нет осознанно (только локально).
5. Setup.exe публикуется как артефакт CI (бинарники не в git); подпись кода
   Windows (Authenticode-сертификат издателя) — внешний процесс, вне объёма.

## Как проверить самому

```powershell
pwsh -NoProfile -File infra/windows/tests/Invoke-HrmTests.ps1   # движок, моки
cd backend && pytest -m "not integration" && $env:TEST_DATABASE_URL=… && pytest -m integration
cd frontend && npm ci && npm run lint && npm run typecheck && npm test -- --run && npm run build
```
