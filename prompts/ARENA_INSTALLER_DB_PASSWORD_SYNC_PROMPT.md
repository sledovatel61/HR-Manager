# Новый PR: установщик «успешно» ставит файлы, но приложение не поднимается — пароль PostgreSQL не совпадает с существующим томом

Ты — исполнитель. Работаешь в ЧИСТОМ новом PR. Ниже — проверенное расследование с якорями по коду и
доказательствами из отчёта поддержки. Не нужно заново искать первопричину: она установлена. Твоя задача —
починить, закрыть вторичные дефекты, которые она обнажила, и ДОКАЗАТЬ починку на реальной машине.

---

## 0. Что произошло (факты, проверенные по артефактам)

- PR #56 (герметичность теста D5) **смержен** в main (merge-commit `178736b3`). Верх main:
  `a391fed` «Add files via upload» (это отчёт поддержки, залитый владельцем) → `178736b3` → `57900d4`.
- Владелец собрал и установил `HR-Manager-Setup-0.15.0.exe` (собран из `178736b3`, 14:14 МСК 14.10.2026)
  на машине с **Docker Desktop: Engine 29.7.2, Compose v5.5.0, daemon linux, context desktop-linux**.
  Установка запускалась трижды за день (14:44 и 18:50 — одинаковый исход).
- Отчёт поддержки лежит **в main**: `HR-Manager-report-2026-10-14_14-45-31.zip` (651 КБ). Что внутри (проверено):
  - `logs-backend.txt`, `logs-worker.txt` — многократно
    `FATAL:  password authentication failed for user "hr_manager"`; backend падает на
    `alembic upgrade head` (миграции), worker — на подключении к БД.
  - `logs-frontend.txt`, `logs-backup.txt` — **пустые**.
  - `health-health.json`, `health-ops-status.json`, `health-ops-backup-health.json`,
    `license-status.json`, `pilot-readiness.json` — все: «Невозможно соединиться с удаленным сервером».
  - `diagnostics.json` — `docker: ok`, `app: degraded`, `database/migration/worker: unknown`,
    `compose_plugin_found: true` (`%USERPROFILE%\.docker\cli-plugins\docker-compose.exe`),
    `url: http://127.0.0.1:8081`, `installed_release_sha: 178736b3…`.
  - `install-record.json` — port 8081, version 0.15.0, `pilot_created: false`.
  - Лог Inno (`installer-Setup Log 2026-10-14 #003.txt`, хвост): `[Run]` → `powershell … hr-manager.ps1
    -Action install … -NonInteractive -OpenBrowser`, старт 14:44:42, `Process exit code: 1` в 14:45:01
    (**19 секунд**), далее `HRM: Finish page reports incomplete engine status=running`,
    `HRM: incomplete setup marker normalized to failed for tray recovery`,
    `HRM: setup engine result=running custom exit=1`.
    (В архиве цифры частично съедены автоматической редакцией персональных данных:
    `Process exit code: <redacted>` — это был `1`.)
- Docker Desktop и Engine при этом РАБОТАЮТ, контейнер `db` здоров. Первичная причина — не Compose
  («dependency failed to start» — вторичное сообщение), а отказ аутентификации в PostgreSQL.

---

## 1. Первопричина (проверена по коду, якоря)

1. `infra/compose.pilot.yml:49-56` — в сервисе `db`:
   `POSTGRES_DB: hr_manager`, `POSTGRES_USER: hr_manager`, `POSTGRES_PASSWORD: ${HRM_POSTGRES_PASSWORD:?…}`,
   том `pilot_pgdata:/var/lib/postgresql/data`. Комментарий на `compose.pilot.yml:175-178` подтверждает:
   официальный образ создаёт `POSTGRES_USER` как суперпользователя.
2. **Официальный образ postgres применяет `POSTGRES_PASSWORD` только при ПЕРВОЙ инициализации пустого
   тома.** На существующем томе переменная молча игнорируется, пароль роли остаётся прежним.
3. `infra/windows/engine/Secrets.psm1:236` (`Write-HrmPilotEnv`) **всегда** вызывает
   `Get-HrmSecret $StateDir "HRM_POSTGRES_PASSWORD"`, а `Get-HrmSecret` (`Secrets.psm1`, ветка
   «создаёт один раз и больше никогда не меняет») **генерирует новый пароль, если его нет в
   `StateDir\secrets.json`**.
4. Итог: **срок жизни секрета (каталог состояния) короче срока жизни данных (том
   `hr-manager-pilot_pilot_pgdata`)**. У пилота том от 2026-09-10. Если каталог состояния
   (`%LOCALAPPDATA%\HRManager`) потерян/пересоздан, движок пишет в `pilot.env` НОВЫЙ пароль, роль
   `hr_manager` в томе хранит СТАРЫЙ → backend/worker: `FATAL: password authentication failed`,
   бесконечный перезапуск, приложение недоступно.

**Технически починка возможна и безопасна:** `docker compose exec` подключается к postgres по
**локальному сокету**, где initdb по умолчанию оставляет `local all all trust` — аутентификация паролем
не требуется. Поэтому роль можно привести к текущему паролю даже не зная старого. Этот приём уже
используется в продакшене: `Get-HrmDbSchemaRevision` (`Compose.psm1:477-505`) делает
`compose exec -T db psql -U hr_manager -d hr_manager -tAc "SELECT version_num FROM alembic_version"`.
У `Invoke-HrmCompose` (`Compose.psm1:36-41`) уже есть параметр `-Stdin` — пароль можно передать
через stdin, а не через аргументы процесса.

### Вторичные дефекты, всплывшие вместе с основным (чинить в этом же PR)

**A. Установщик врёт при отказе.** Движок вышел с кодом 1, но маркер `setup-run.json` остался
`running`: старт пишется в `hr-manager.ps1:157` (`Set-HrmSetupMarker -Status "running"`), отказ — в
`hr-manager.ps1:344` (`-Status "failed"`). В логе Inno маркер на момент Finish page = `running`, и
трею пришлось «нормализовать» его в failed (`HRM: incomplete setup marker normalized to failed for
tray recovery`). Finish page показал «incomplete … status=running» — пользователь не получил ни
правдивого состояния, ни текста причины. Успех маркер не чистит (`Clear-HrmSetupMarker`,
`Common.psm1` рядом с `Set-HrmSetupMarker:185`).

**B. В отчёте поддержки нет журнала самого движка.** `infra/windows/engine/SupportBundle.psm1`
(шаг 7) содержит комментарий «engine logs: последние журналы из Common Write-HrmLog? **Нет файла**,
но есть install logs» — это неверно: журналы есть, `Get-HrmEngineLogsDir` / `Get-HrmTimestampedLogFile`
(`Common.psm1:769-779`) пишут `StateDir\logs\<action>-<yyyyMMdd-HHmmss>.log` (переменная
`HRM_LOG_FILE`, выставляется в `Start-HrmEngineProcess`). Именно поэтому точную точку 19-секундного
отказа из архива установить невозможно — её надо и получить с журналом, и навсегда закрыть в bundler.

---

## 2. Что делать НЕЛЬЗЯ (жёсткие запреты)

- Не удалять и не пересоздавать том `pilot_pgdata`, не использовать `down -v`, не вызывать
  `Remove-HrmPilotDataVolume` (`Compose.psm1:590`; легименное применение — только uninstall с
  `-PurgeData`, `Install.psm1:661-688`) как способ починки. Данные пилота (Мария Павловна) должны
  сохраниться.
- Не «регенерировать пароль и пересоздать том» — это потеря данных.
- Не менять схему хранения секретов (`secrets.json` / `pilot.env`), не ослаблять compose-требование
  `${HRM_POSTGRES_PASSWORD:?}`, не убирать ACL с каталога состояния.
- Не передавать пароль в аргументах процессов (виден в списке процессов Windows), не через
  `psql -v`, не в `-e PGPASSWORD=…`; не писать его в журнал, диагностику, URL и отчёты — только
  stdin + `Redact-HrmText`.
- Не коммитить в git отчёт поддержки, `secrets.json`, `pilot.env`, пароль, любой bundle.
- Не трогать лицензионный код, бэкап-код и логику readiness (`Wait-HrmReady`), не расширять объём
  вне списка задач.

---

## 3. Задачи

### Задача 1 (главная) — синхронизация пароля роли с существующим томом

1. Новая функция в `infra/windows/engine/Compose.psm1`, например
   `Sync-HrmPostgresRolePassword -InstallDir -StateDir`, возвращающая **ровно один объект**
   `@{ ok; state; message; synced }`. Контракт «журнал не в success-конвейере» (`Write-HrmLog` пишет в
   `$env:HRM_LOG_FILE` или `Write-Host`, `Common.psm1:58-77`) нарушать нельзя — есть регрессионный кейс
   `stack.tests.ps1:30` («результат Start-HrmStack — ровно один объект с ok»).
2. Механизм:
   ```powershell
   $sql = "ALTER ROLE hr_manager PASSWORD '" + ($password -replace "'", "''") + "';"
   $res = Invoke-HrmCompose $InstallDir $StateDir @("exec","-T","db","psql","-U","hr_manager","-d","hr_manager","-v","ON_ERROR_STOP=1") -Stdin $sql -IgnoreExitCode
   ```
   Пароль берётся из `Get-HrmSecret $StateDir "HRM_POSTGRES_PASSWORD"` **только если он уже есть**;
   если секрета нет (старые тестовые каталоги состояния без `secrets.json`) — синхронизация
   пропускается (`synced = $false`, без ошибки).
3. Вызов: в `Start-HrmStack` (`Compose.psm1:160+`) после `up -d`, когда состояние стека
   `partial`/`degraded` — **до** проверки схемы (`Test-HrmSchemaCompatibleWithImage`) и **до**
   `Repair-HrmStack`, чтобы пересоздание/перезапуск происходили уже с рабочим паролем. Тот же вызов в
   предсуществующей ветке «контейнеры уже подняты, но состояние не running» (`Compose.psm1:212-218`).
   Идемпотентно: повторный вызов безопасен.
4. Отказ синхронизации: именованное состояние (предлагаю `database_auth_failed` — зафиксируй его в
   документации/комментарии состояний), `ok = $false`, сообщение по-русски называет причину и
   следующий шаг («пароль роли hr_manager в томе pilot_pgdata не совпал с каталогом состояния;
   данные не удалялись; …»), `Write-HrmLog "error"` с редакцией секретов. Движок **не имеет права**
   вернуть успех, если синхронизация не удалась.
5. После успешной синхронизации дождаться, что стек становится `running` (backend/worker/backup
   поднимаются по restart-policy), и только потом возвращать результат.

### Задача 2 — правдивый отчёт установщика при отказе

- Установить, почему при `exit 1` маркер остался `running` (нужен журнал движка с поражённой машины,
  см. задачу 3 — получи его, если есть доступ; иначе разбери путь кода).
- Требование: при **любом** отказе установки/обновления `setup-run.json` = `failed` с фактическим
  сообщением (без секретов); при успехе маркер удаляется (`Clear-HrmSetupMarker`). Finish page не
  должен показывать «incomplete … status=running».
- Покрыть тестом: отказ на пути установки оставляет `failed` + сообщение.

### Задача 3 — отчёт поддержки обязан содержать журналы движка

- `SupportBundle.psm1`, шаг 7: добавить последние N файлов из `Get-HrmEngineLogsDir $StateDir`
  (`Common.psm1:769-779`), проредacted (`Redact-HrmText` / `Redact-HrmPii`), имена в духе существующих
  (`logs-install.txt`, `logs-engine.txt`). Исправить неверный комментарий «Нет файла».
- Тест: в мок-мире журналы движка попадают в комплект.

### Задача 4 — тесты (все зелёные, ничего не ослаблять)

- Харнесс (`test-harness.ps1`, ветка `compose … exec`, около строки 470): добавить
  детерминированную ветку для `ALTER ROLE` и флаги мира (например `DbPasswordSyncFails`,
  счётчик `DbPasswordSyncCount`, `DbPasswordSyncArgs` для проверки аргументов). Без этого мок
  ответит «unexpected command» и новый код всегда будет «падать».
- Кейсы (минимум):
  1. стек `partial`/`degraded` из-за пароля → синхронизация → стек `running`; `down`/удаление тома
     не вызывались;
  2. синхронизация упала → `database_auth_failed`, `ok=$false`, сообщение называет причину;
  3. здоровый стек → лишней синхронизации нет (если выбрано условие «только когда не здоров»);
  4. секрета нет → пропуск без ошибки;
  5. регрессия `stack.tests.ps1:30` (ровно один объект) не сломана;
  6. пароль не попадает ни в аргументы вызова, ни в журнал/сообщение/состояние.
- Ни один существующий кейс не удалять и не ослаблять; счётчики кейсов по suite сверить до/после.

### Задача 5 — доказательства на реальной машине (acceptance)

1. **Воспроизведение до правки:** том `hr-manager-pilot_pilot_pgdata`, созданный старым паролем, +
   свежий каталог состояния (новый `secrets.json`) → запустить движок/установщик → зафиксировать отказ
   (`FATAL: password authentication failed`). Приложить выдержку.
2. **После правки:** переустановить/запустить → backend `healthy`, frontend отдаёт страницу, браузер
   открывается на `http://127.0.0.1:8081`, worker и backup поднимаются, **данные сохранены**
   (сравнить наличие строк/таблиц до и после).
3. Полный `infra\windows\tests\run-tests.ps1` **без `-Skip`** на машине с Docker Desktop — приложить
   ПОЛНЫЙ вывод всех suite. Эталон сейчас: `ВСЕ ТЕСТЫ ПРОЙДЕНЫ (269)`.
4. `python infra\windows\tests\lint-engine.py`, `py_compile` всех изменённых `.py`, `git diff --check`.
5. Пересобрать установщик (`installer/build.ps1`) и, если есть доступ к поражённой машине, поставить
   именно его и приложить результат (healthy + браузер + данные).

---

## 4. Acceptance criteria (все обязательны)

1. На машине с Docker Desktop и **существующим томом со старым паролем** приложение поднимается:
   backend healthy, `http://127.0.0.1:8081` отвечает, worker/backup работают; том не удалялся, данные
   целы (доказательство — вывод до/после).
2. Пароль роли синхронизируется идемпотентно; секрет не встречается в аргументах процессов, журналах,
   диагностике, отчёте поддержки и URL.
3. Отказ синхронизации даёт именованное состояние и понятное сообщение; движок не рапортует успех.
4. При любом отказе установки маркер `setup-run.json` = `failed` с сообщением; при успехе он удаляется;
   Finish page не показывает противоречивое «incomplete … status=running».
5. Отчёт поддержки содержит журналы движка; неверный комментарий в `SupportBundle.psm1` исправлен.
6. Полный runner зелёный на машине с Docker Desktop (полный вывод приложен), `lint-engine.py`,
   `py_compile`, `git diff --check` проходят; CI зелёный на финальном SHA.
7. Ни один кейс не удалён и не ослаблен; поведение в отношении томов и данных не изменилось, кроме
   согласования пароля роли.

## 5. Out of scope (не делать в этом PR)

- Удаление `HR-Manager-report-2026-10-14_14-45-31.zip` из main — решение владельца, отдельным действием.
- Та же класс-проблема для `HRM_BACKUP_KEY` (потеря `secrets.json` делает старые бэкапы
  нерасшифровываемыми): задокументировать как известное ограничение (комментарий/README), но не чинить.
- Ротация пароля по расписанию, смена схемы хранения секретов, редизайн readiness, лицензии, бэкапы.
- Любые правки, не связанные с задачами 1-5.

## 6. Частые ловушки (учитывать)

- `Get-HrmDockerOverrideValue` возвращает элементы в конвейер: явно заданный пустой массив выглядит как
  `$null`. Для override-ключей, где пустой массив осмыслен, наличие ключа проверяй отдельно
  (`Test-HrmDockerOverrideKey`, `Docker.psm1`).
- `Write-HrmLog` не должен попадать в success-конвейер функций, возвращающих объект результата.
- `compose exec` без `-T` ломает неинтерактивный ввод со stdin — всегда `-T`.
- Пароль в аргументах процесса виден другим пользователям/админам машины — только stdin.
- Мок харнесса отвечает `unexpected command: …` на неизвестные compose-команды: новую exec-команду
  обязательно добавь в мок, иначе «зелёные» тесты будут проверять заведомо падающую ветку.
- Ставь `Set-StrictMode -Version 2.0`-совместимый код: обращение к несуществующим свойствам бросает
  исключение (см. `Test-HrmContainerRunning`, `Compose.psm1:80`).

## 7. Как работать и что сдать

- Ветка: **новая**, от актуального `main` (в нём уже смержен PR #56). PR — **против `main`**.
  Чужие ветки и PR не трогать. Не перебазировать и не переписывать main.
- Не коммитить отчёт поддержки, секреты, `pilot.env`, bundle, пароль.
- Definition of done: ветка запушена → PR открыт → в PR указаны: SHA, ссылка на зелёный CI, лог
  воспроизведения дефекта (до правки), полный вывод `run-tests.ps1` на машине с Docker Desktop,
  выдержка переустановки (healthy + браузер + сохранённые данные), и явное подтверждение, что том
  `pilot_pgdata` не удалялся и данные целы.
- Если найдёшь, что первопричина шире (например, причина потери `secrets.json` — отдельный дефект),
  доложи отдельно: починка из задачи 1 не должна зависеть от этого ответа.
