# Отчёт Phase 12 — локальный пилот Windows (arena)

- **PR:** https://github.com/sledovatel61/HR-Manager/pull/22
- **Ветка:** `arena/01a084e4-hr-manager` (см. примечание о ветке ниже)
- **Baseline SHA (origin/main на старте):** `8d6c2a34e11da9a3aae8a4fc6cbd4aacf16c910f`
- **Коммиты реализации:**
  - `4d7dca1` — backend этапа 12 + миграция `0013` + `infra/compose.pilot.yml`
  - `a98ddc2` — адаптации тестов под PGlite/pg8000
  - `b40fb99` — фронтенд: экран первого запуска + бейдж режима работы
  - `3e055c0` — PowerShell-движок `infra/windows/`
  - `5619c18` — тесты движка + `infra/windows/README.md`
  - `19661e9` — установщик Inno Setup + воспроизводимая сборка
  - `7afa253` — фикс mypy (pg8000-патч conftest: import-not-found в CI /
    import-untyped в локальном PGlite-окружении; убраны неиспользуемые
    ignore)
- **Final code SHA:** `7afa253` (+ docs-коммит `6656878` с этим отчётом)
- **CI (полный зелёный):** run
  https://github.com/sledovatel61/HR-Manager/actions/runs/34347421698 —
  Backend checks 2m17s, Backend integration tests (PostgreSQL) 1m54s,
  Frontend checks 1m8s, Compose stack smoke 2m5s.
- **Миграционная голова:** `0013` (`users.working_mode` + обмен первого запуска)
- **Merge в `main`:** не выполнялся (по правилам — владелец).

> **Примечание о ветке.** Задание называло ветку `arena/01a081aa-hr-manager`
> (в ней лежал промпт, commit `d0fc394…`). Ветка-носитель промпта уже удалена
> из origin; сессия Arena закреплена за веткой `arena/01a084e4-hr-manager`,
> созданной от того же `origin/main` (`8d6c2a34…`). Вся работа — в
> `arena/01a084e4-hr-manager`.

## Что сделано

Обычный пользователь Windows 10/11 x64 ставит и обновляет HR Manager в
несколько кликов: `HR Manager Setup.exe` → роль (`HR | Руководитель |
Администратор`) → фамилия → «Установить» → браузер открывает защищённую
страницу первого запуска (`#setup=<ticket>`), где задаётся пароль.
Никаких команд Docker/Postgres/Alembic, `.env` или PowerShell в основном
сценарии. Приложение остаётся клиент-серверным web-продуктом с
PostgreSQL (не Electron/SQLite).

### 1. Установщик (`installer/`)

- **`installer.iss`** — мастер Inno Setup на русском: роль → фамилия →
  часовой пояс (по умолчанию `Europe/Moscow`) → «Установить».
  `PrivilegesRequired=lowest` (без UAC), запись в
  «Параметры → Приложения» + деинсталлятор. Мастер пишет защищённый
  `first-run-input.json` в `%LOCALAPPDATA%\HRManager` и передаёт всё
  остальное движку (`-Action install -NonInteractive -OpenBrowser`).
  Docker Desktop установщик не скачивает и лицензию не принимает.
- **`installer/build.ps1`** — воспроизводимая сборка с **закреплённым**
  инструментом: Inno Setup **6.7.3** (официальный релиз `jrsoftware/issrc`),
  SHA256 `9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732`
  (проверяется до использования; версия/хеш сверены с winget-манифестом
  `JRSoftware.InnoSetup`). Результат: `HR-Manager-Setup-<Version>.exe` +
  `release-manifest.json` (release_sha, версия, хеш exe и детерминированные
  SHA256 всех файлов пакета — проверка воспроизводимости по коммиту).
  Бинарный установщик в git не хранится.
- **Подпись — честно:** сборка **не подписана** (в репозитории нет
  сертификата/ключа, подделывать подпись нельзя). Предусмотрен хук
  `SignTool` + пошаговая инструкция в `installer/README.md`.
- **CI-сборка:** подготовлен джоб `windows-installer` (движок-тесты на
  Windows PowerShell 5.1 и pwsh, затем сборка и публикация артефактов) —
  см. раздел «Ограничения»: патч CI не удалось запуш…ить этой сессией,
  он приведён ниже целиком.

### 2. Движок (`infra/windows/hr-manager.ps1` + 8 модулей)

Действия: `install / start / stop / status / open / update / uninstall /
diagnostics / resume`. Ключевые свойства:

- **Секреты** (6 шт.: пароль БД, ключ подписи, bootstrap-пароль, ключ и id
  бэкапа, одноразовый токен обмена) генерируются один раз, хранятся только
  в каталоге состояния с ACL «только текущий пользователь» и передаются в
  контейнеры через `docker compose --env-file pilot.env`. **Никогда** — в
  командной строке процесса, выводе, журналах, диагностике, URL; весь
  вывод проходит редакцию (`<redacted>`). Токен обмена после создания
  владельца заменяется неиспользуемой заглушкой `retired-…` (compose
  требует непустое `${HRM_EXCHANGE_TOKEN:?}`, сервер её не применяет).
- **Единственная точка запуска процессов** `Invoke-HrmExternal`
  (ProcessStartInfo, `ArgumentList`, без shell) и loopback-HTTP
  `Invoke-HrmHttp` — обе мокабельны (тесты не трогают реальную машину).
- **Честный префлайт:** Windows 10/11, PS 5.1+ x64, Docker CLI, демон,
  Compose v2 ≥ 2.24, свободный порт, записываемый каталог состояния,
  место ≥ 5 ГБ, валидность compose-конфигурации. Без молчаливых согласий;
  UAC/перезагрузка снимаются сохранением состояния + `resume`.
- **Первый запуск:** claim по loopback (`X-Real-IP: 127.0.0.1`) с токеном
  обмена → тикет → браузер на `http://127.0.0.1:<port>/#setup=<ticket>`.
  Движок администратора не создаёт; пароль задаёт пользователь в UI.
- **Обновление:** доверенный `-ReleaseDir` (документированная граница
  доверия), `update.lock` (+ перехват прерванных), журнал фаз
  `prepare→backup→build→switch→migrate→smoke→done` с возобновлением,
  бэкап-ворота (`backup-now` + `backup-check --deep`) ДО миграции, сборка
  образов без остановки работающего приложения, однократный
  `alembic upgrade head`, ограниченная готовность + smoke (фронтенд,
  `/api/health`, `/api/ops/status` с release_sha и миграциями,
  `worker-check`, отсутствие дрейфа). Провал → возврат к предыдущим
  образам, **даунгрейд БД не выполняется никогда**.
- **Диагностика:** состояния `docker (missing|daemon_down|ok)`, `app
  (stopped|starting|ready|degraded)`, `database (unknown|ok|down)`,
  `migration (ok|drift|unknown)`, `worker (ok|stale|unknown)`, `backup
  (ok|stale|failed|missing|unknown)`, `version (match|mismatch|unknown)`,
  `smtp/telegram (not_configured)`; JSON-вывод; регрессионные тесты
  редакции секретов.
- **Удаление:** данные Postgres и зашифрованные бэкапы сохраняются по
  умолчанию; `-PurgeData` требует точную фразу подтверждения и
  предлагает шифрованный бэкап; Docker Desktop/WSL2 не удаляются.
- **Неинтерактивный режим** документирован (`HRM_NONINTERACTIVE=1`,
  `HRM_STATE_DIR`, `HRM_INSTALL_DIR`, ссылка первого запуска — в
  защищённый файл, фраза удаления — `HRM_PURGE_CONFIRMATION`).

### 3. Пилотный overlay (`infra/compose.pilot.yml`)

Проект `hr-manager-pilot` (Postgres/backend/frontend/worker/backup в
Linux-контейнерах), публикация только `127.0.0.1:${HRM_PILOT_PORT}`,
именованные тома `pilot_pgdata`/`pilot_backups` (переживают
stop/restart/update), внешние SMTP/Telegram выключены, mailpit — за
неактивным профилем, `APP_DEBUG=false`, все секреты —
`${VAR:?}` (обязательны, включая `HRM_BOOTSTRAP_ADMIN_PASSWORD`).
`compose.prod.yml` не изменён. Модель доверия loopback (не-Secure куки
при 127.0.0.1, остальные ограничения production-класса сохранены)
задокументирована в заголовке файла и проверяется тестом.

### 4. Backend и фронтенд этапа 12 (коммиты `4d7dca1`, `a98ddc2`, `b40fb99`)

- `/setup/owner/{preview,redeem}` + `/setup/state`: одноразовый
  loopback-обмен (токен ≥43 симв., хранится только как SHA-256, атомарно
  потребляется, аудит `pilot_owner_claimed/created`, advisory-lock против
  гонок, rate-limit, CSRF на redeem), тикет через URL-фрагмент.
- Единственный владелец: роль `admin` + грант `pilot_full_access` (все
  скоупы phase 11); техническое имя `owner.<транслитерация>`; режим
  работы — поле профиля (`users.working_mode`, миграция `0013`), RBAC не
  ослаблен.
- Фронтенд: экран первого запуска (превью тикета → пароль ≥12 символов →
  часовой пояс/рабочие дни/тихие часы), очистка фрагмента после успеха,
  бейдж режима работы в шапке; aria-атрибуты (`aria-describedby`,
  `role="status"/"alert"`), русские подписи.

### 5. Тесты

- **Движок (38 случаев, `infra/windows/tests/run-tests.ps1`):** парсер
  PowerShell всех файлов; отсутствие битых символов и секретов-литералов;
  единственная точка запуска процессов + белый список команд; оверлей
  loopback-only и обязательные `${VAR:?}`; секреты (уникальность,
  персистентность, env-файл, retired-токен); редакция; провалы префлайта
  (docker/daemon/port/space/compose); установка на путях с пробелами и
  кириллицей; **«секреты никогда не в командной строке»** (перебор всех
  аргументов всех мок-вызовов); claim (ретраи, 409); повторная установка;
  обновление (lock, stale-lock, бэкап-ворота, возобновление, smoke-
  провалы, откат без даунгрейда); удаление (данные сохраняются / фраза);
  все состояния диагностики + регрессия редакции; resume. **Ни один тест
  не трогает реальную установку.**
- **Backend:** 540 non-integration + 20 (миграции up/down/up до `0013` с
  сохранением данных, пилотный первый запуск, overlay) — локально
  зелёные; ruff/mypy зелёные. Интеграционная батарея — в CI на
  postgres:16 (локальный PGlite ограничен: 3 теста истинной конкурентности
  — CI-only).
- **Фронтенд:** 143 теста (в т.ч. 4 — экран первого запуска), tsc,
  eslint, production-сборка — зелёные.
- **UI-автоматизация/доступность:** компонентные тесты + aria-атрибуты;
  сквозная UI-автоматизация установщика (Windows-машина) выходит за
  рамки CI и задокументирована как ожидание ручной приёмки.

## Threat model (кратко)

1. **Секреты:** только каталог состояния (ACL: текущий пользователь) →
   контейнеры через `--env-file`; не в argv/логах/диагностике/URL; нет в
   git; редакция вывода.
2. **Первый запуск:** обмен только с loopback (`X-Real-IP`), токен
   одноразовый (SHA-256, атомарное потребление, rate-limit), тикет
   короткоживущий, обмен отключается после создания владельца; пароль —
   только пользователь, Argon2id.
3. **Обновление:** доверенный каталог релиза — граница доверия;
   целостность данных гарантируется шифрованным бэкапом с проверкой до
   миграции; откат — только код, БД не даунгрейдится; блокировка и журнал
   фаз против параллельных/прерванных обновлений.
4. **Сеть:** единственный опубликованный порт — `127.0.0.1`; куки
   не-Secure только в рамках задокументированной loopback-модели;
   production-контур не ослаблен.
5. **Удаление:** данные удаляются только по точной фразе; предложение
   шифрованного бэкапа; Docker Desktop/WSL2 неприкосновенны.

## Ограничения (честно)

1. **Патч CI не запушен этой сессией.** GitHub App сессии не имеет права
   `workflows`, сервер отклонил пуш коммита, меняющего
   `.github/workflows/ci.yml`. Патч оставлен в рабочем дереве и приведён
   ниже целиком — применить одним коммитом (после мержа PR):

```yaml
  windows-installer:
    name: Windows engine tests + installer build
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4

      - name: PowerShell engine tests (Windows PowerShell 5.1)
        shell: powershell
        run: powershell -NoProfile -ExecutionPolicy Bypass -File infra/windows/tests/run-tests.ps1

      - name: PowerShell engine tests (pwsh)
        shell: pwsh
        run: pwsh -NoProfile -File infra/windows/tests/run-tests.ps1

      - name: Build HR Manager Setup.exe (Inno Setup 6.7.3, pinned + SHA256-verified)
        shell: pwsh
        run: pwsh -NoProfile -ExecutionPolicy Bypass -File installer/build.ps1 -Version 0.13.0

      - name: Upload installer artifacts
        uses: actions/upload-artifact@v4
        with:
          name: hr-manager-windows-setup
          path: |
            installer/output/*.exe
            installer/release-manifest.json
```
   (добавляется в `.github/workflows/ci.yml` после джоба `stack`).

2. **PowerShell-тесты и ISCC не выполнялись в песочнице** (Linux, нет
   pwsh; CDN GitHub Releases заблокирован). Авторитетное исполнение —
   вышеуказанный CI-джоб на `windows-latest`. В песочнице пройдена
   структурная проверка `infra/windows/tests/lint-engine.py` (баланс
   скобок, U+FFFD, литералы секретов, белый список команд, оверлей) и
   тщательная ручная ревизия под Windows PowerShell 5.1 (избегается
   `Add-Member` на Hashtable, глобальные флаги вместо модульных для
   межмодульных контрактов и т.п.).
3. **Exe не подписан** — см. раздел 1 (хук и инструкция предоставлены).
4. **Интеграционные тесты с реальной конкуренцией** — только CI
   (postgres:16); локальный PGlite сериализует запросы (известное
   ограничение из phase 11).
5. Первый запуск после установки открывает браузер через
   `Start-Process`; в RDP/без браузера — ссылка дублируется в
   `first-run-url.txt` (защищённый файл).

## Handoff

- **Документация:** `installer/README.md` (сборка, хеши, подпись),
  `infra/windows/README.md` (движок, секреты, состояния, тесты), секция
  «Локальный пилот Windows» в `README.md`, заголовок
  `infra/compose.pilot.yml` (модель доверия loopback).
- **Применить после мержа:** CI-патч из «Ограничений»; затем собрать
  релиз (`installer/build.ps1`), подписать (инструкция в
  `installer/README.md`) и опубликовать exe + `release-manifest.json`.
- **Следующие шаги пилота:** ручная приёмка установщика на чистой
  Windows 10/11 VM; проверка восстановления после перезагрузки в середине
  установки/обновления; при выпуске нового тега — обновление
  `ExpectedHeadRevision` в `engine/Compose.psm1`/`Update.psm1` при
  добавлении миграций.
