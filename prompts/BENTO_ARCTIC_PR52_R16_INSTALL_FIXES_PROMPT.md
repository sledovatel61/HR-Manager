# Промпт агенту (раунд 16): установщик HR Manager — Docker-гейт в обновлении, честный отказ по ключу лицензии, CI-валидация ветки расследования

Ты — агент-исполнитель на арене. Задача: закрыть оставшиеся дефекты Windows-установщика
HR Manager, подтверждённые ревьюером чтением кода и прогоном CI. Работай только в своей
ветке от указанной базы. Не мержи PR, не меняй `main`, не переписывай историю.

Перед началом подтверди HEAD и базовый коммит:

```
git clone https://github.com/sledovatel61/HR-Manager.git
cd HR-Manager
git fetch origin agent/windows-installer-investigation-2026-10-09
git checkout -b arena/<твой-ид>-install-fixes FETCH_HEAD
git log --oneline -1   # ожидается c54aba2 "Publish Windows installer investigation handoff"
```

## 1. Контекст (проверено ревьюером лично — перепроверять не нужно, отрицать нельзя)

- PR #52 вмержен в `main` (merge-коммит `3e90cd58486a830e443b4f1d9a8eac2f4d51425a`,
  2026-10-08). Содержимое `main` = содержимое `35d337d`.
- Ветка `agent/windows-installer-investigation-2026-10-09` (коммит
  `c54aba22c1f1ff9bc6960105bff5a91cb3fa05aa`) = `main` + 12 файлов работы оркестратора.
  **Это твоя база. `main` как база не годится** — в нём нет ни одной правки оркестратора.
- На ветке оркестратора **нет ни одного прогона CI** (пуш без PR). Локальный прогон
  PowerShell 5.1 на машине владельца: ровно один провал — `supervisor.tests.ps1:123`
  (первый захват блокировки supervisor). Причина не расследована — это задача T5.

### Что в `c54aba2` уже исправлено — НЕ трогать, не «улучшать» заново

- `infra/windows/engine/Secrets.psm1:221` — `pilot_created` читается через
  `Get-HrmInstallRecordField` (дефект StrictMode закрыт).
- `infra/windows/engine/Update.psm1:700` — `Install-HrmLicensePublicKey` вызывается
  перед первой генерацией `pilot.env` (строка 701).
- `infra/windows/engine/Install.psm1:263` — восстановление ключа + `pilot.env` до
  `Start-HrmStack` в ветке «уже установлено».
- `infra/windows/engine/Snapshot.psm1` — проверенный снимок переиспользуется до
  проверки `files_replaced`.
- `infra/windows/engine/Tray.psm1` — tooltip обрезается до 63 символов (лимит .NET).
- `installer/installer.iss` — записи `[Run]` без `postinstall`/`skipifsilent`; у движка
  снят `nowait` (Setup ждёт движок).
- Регрессии: `static.tests.ps1` (+13), `pilot-final.tests.ps1` (+6) — ключ лицензии
  восстанавливается при повторной установке и при обновлении.
- `installer/Test-Setup.ps1` / `installer/Test-Setup.cmd` — диагностический лаунчер
  (стримит логи Setup и движка). Документ-хандофф
  `docs/windows-installer-investigation-2026-10-09.md` — не удалять.

### Что НЕ исправлено — это твоя работа (§2)

## 2. Задачи

### T1 (P1) Гейт готовности Docker в пути обновления

Факт: в `infra/windows/engine/Update.psm1` **нет ни одного** вызова
`Invoke-HrmDockerPrepare` / `Assert-HrmPreflight` (`grep -c DockerPrepare Update.psm1` = 0).
Install-путь гейт имеет: `infra/windows/engine/Install.psm1:257-262`.

Симптом у владельца (лог `setup-engine-20261009-115648.log`): при выключенном Docker
Desktop compose падает с сырым текстом
`failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine … The
system cannot find the file specified` — и это уже после backup-ворот, то есть после
операций, которым нужен Docker.

Требование:

- В `Update-HrmApp` после захвата `update.lock` и до фазы `prepare` (то есть до любой
  compose-операции, включая backup gate) вызвать
  `Invoke-HrmDockerPrepare -InstallDir $InstallDir -StateDir $StateDir -Port $port
  -AllowInstall:$false -Interactive:$false` (обновление не ставит Docker самостоятельно) и
  при `-not $prepare.ok` бросить `$prepare.message` — понятный человеку текст из
  `infra/windows/engine/Docker.psm1` (`Invoke-HrmDockerPrepare`, состояния
  `Get-HrmDockerDesktopState`).
- Текст диагностики `infra/windows/engine/Update.psm1:218`
  («Docker Engine не отвечает — обновление начнётся после его запуска») привести в
  соответствие с новым поведением.

Ожидаемый результат: Docker Desktop выключен → обновление останавливается **до** Compose
с диагностическим сообщением; compose не вызывается ни разу (проверяется счётчиком вызовов
в мок-мире).

### T2 (P1) Честный отказ по публичному ключу лицензии + раздельные диагностики

Факты:

- `Install-HrmLicensePublicKey` (`infra/windows/engine/Secrets.psm1:143-200`) —
  fail-silent: если ключ не найден, пишет warn и возвращает `""`.
- `Write-HrmPilotEnv` (`Secrets.psm1:233`) берёт ключ только из
  `StateDir\license_public_key.b64` и при отсутствии пишет **пустую** строку
  `HRM_LICENSE_PUBLIC_KEY=`.
- Compose требует непустое значение: `infra/compose.pilot.yml:87,138,163`
  (`${HRM_LICENSE_PUBLIC_KEY:?HRM_LICENSE_PUBLIC_KEY is required for the pilot}`) — отсюда
  ошибка владельца «required variable HRM_LICENSE_PUBLIC_KEY is missing a value» для
  backend, backup и worker.
- Тест `infra/windows/tests/engine.tests.psm1:138-142` **закрепляет дефект**: требует,
  чтобы без файла ключа строка была пустой.

Требование:

- После восстановления ключа (`Update.psm1:700`, `Install.psm1:263`, `Install.psm1:306`) и
  внутри `Write-HrmPilotEnv` (после `Secrets.psm1:233`): пустой ключ → `throw` с
  **раздельными** сообщениями и кодами для ситуаций:
  (a) файл ключа отсутствует во всех источниках (StateDir, `{app}\infra\license\public_key.b64`,
  каталог релиза/snapshot);
  (b) Docker daemon недоступен (сообщение из T1);
  (c) `installed.json` нечитаем или невалиден.
  Одно общее «что-то пошло не так» не принимается.
- `engine.tests.ps1:128-161` переработать: без ключа — исключение с внятным текстом (не
  пустая строка); с ключом — строка `HRM_LICENSE_PUBLIC_KEY=<key>` есть и является
  последней (как сейчас проверяется для успешного случая).
- Нормализовать аргументы на `Update.psm1:700`: сейчас
  `-SourceDir $InstallDir -InstallDir $ReleaseDir` — роли переставлены относительно
  контракта функции и вызова на `Update.psm1:918`. Привести к
  `-SourceDir $ReleaseDir -InstallDir $InstallDir` (функция ищет в обоих путях, поэтому
  сейчас «работает случайно» — это хрупко).

### T3 (P2) Полнота снимка включает ключ лицензии

`Assert-HrmSnapshotComplete` (`infra/windows/engine/Install.psm1:57-72`) проверяет
`infra\compose.pilot.yml`, `backend`, `frontend`, но **не** `infra\license\public_key.b64`.
Сборка без ключа проходит проверку полноты и падает позже на compose. Добавь ключ в
обязательный состав снимка. (Мок-мир ключ создаёт — `New-HrmFakeSnapshot` в
`infra/windows/tests/test-harness.ps1`, поэтому существующие тесты не ломаются.)

### T4 (P1) CI-валидация изменения `installer.iss` и смоука

Факт: с `c54aba2` записи `[Run]` потеряли `postinstall`/`skipifsilent`, а у движка снят
`nowait` — значит silent-установка (`/VERYSILENT`) **впервые** запускает движок синхронно.
CI-смоук (`infra/windows/tests/ci-silent-smoke.ps1`, job `Windows engine tests +
installer smoke`, runner `windows-latest`) теперь выполняет полный движок, которому нужен
Docker Desktop с Linux engine. На раннере его может не быть → после твоего гейта из T1
движок встанет с понятным отказом → Setup завершится ненулевым кодом → смоук станет
красным. На ветке оркестратора CI не запускался ни разу, так что это непроверенный риск
номер один.

Требование:

- Сразу открыть PR из своей ветки (CI срабатывает по `pull_request`). Дождаться job
  `Windows engine tests + installer smoke`.
- Прочитать аннотации check-run'а (логи шагов извне не читаются — только аннотации).
- Если раннер без Docker Desktop Linux engine — минимальное честное решение: смоук **до**
  запуска Setup определяет доступность Linux-движка; если недоступен — Setup запускается,
  и смоук требует, что движок остановился именно на Docker-гейте с ожидаемым сообщением
  (а не «успех»), а в аннотации `::notice` явно пишет, какой участок пути не покрыт.
  Маскировать реальный отказ нельзя. Альтернатива (ставить Docker Desktop в job) — только
  с обоснованием, почему выбрана она.
- Итог: все гейты CI зелёные на твоём коммите; число тестов движка из аннотации
  `HRM engine tests` (было 210 + новые из T5/T7).

### T5 (P2) Герметичный тест единственности supervisor

Факт: `Enter-HrmSupervisorLock` (`infra/windows/engine/Supervisor.psm1:100-116`) берёт
жёсткий машино-широкий named mutex `Local\HRManagerPilotSupervisor`
(`Supervisor.psm1:20`). На машине владельца весь день живут процессы от установок —
первый захват в тесте падает (это и есть провал `supervisor.tests.ps1:123`).

Требование: имя мьютекса переопределяемо через env (например `HRM_SUPERVISOR_MUTEX`);
`New-HrmSupervisorTestContext` (`infra/windows/tests/supervisor.tests.ps1:11`) задаёт
уникальное имя на контекст. Тест не должен зависеть от живых процессов машины.

### T6 (P2) Дыра статического теста

`infra/windows/tests/iteration15.tests.ps1:598` ищет прямой доступ к полям записи
установки только у переменных `record|existing|installed`. Переменная `$state` в список не
входит — поэтому дефект класса «Secrets.psm1:221» тестом не ловился. Добавь `state` (и
`data`, если нужно) в regex и **докажи**, что тест краснеет на возвращённом дефекте:
временно верни прямой доступ, покажи провал, откати.

### T7 (P2) Регрессионные тесты (минимум, все — поведенческие, не по тексту логов)

1. Старая `installed.json` без `pilot_created` → `Write-HrmPilotEnv` и `Update-HrmApp` не
   падают (StrictMode).
2. Ключ найден в `{app}\infra\license\public_key.b64` → скопирован в StateDir, `pilot.env`
   непуст **до** backup gate.
3. Ключ только в каталоге релиза/snapshot → тот же результат.
4. Ключа нет ни в одном источнике → throw с внятным текстом; compose **не вызван**
   (счётчик мок-вызовов).
5. Docker daemon недоступен (`Set-HrmDockerOverride @{ desktop = "installed_stopped" }`) →
   update бросает сообщение prepare; compose не вызван.
6. Снимок без `infra\license\public_key.b64` → `Assert-HrmSnapshotComplete` отказывает.
7. Supervisor: уникальное имя мьютекса через env → тест единственности зелёный даже когда
   машинный мьютекс «занят» (симуляция через env-имя).

## 3. Гейты (сохранить exit code каждого шага)

- **CI — авторитет:** PR зелёный целиком: `Windows engine tests + installer smoke`,
  `Backend checks`, `Frontend checks`, `Backend integration tests`, `Release pipeline
  fail-closed policy`, `Compose stack smoke test`. (`Docker Desktop publisher audit` —
  dispatch-only, skipped, это не провал.)
- `python3 infra/windows/tests/lint-engine.py` — 0.
- `git diff --check` — чисто.
- Новые `.ps1` — UTF-8 **с BOM** (проверяется статикой); workflow-шаги `shell: powershell` —
  только ASCII (правило линта + `backend/tests/test_workflow_step_encoding.py`).
- Локальный прогон PowerShell 5.1 (`infra/windows/tests/run-tests.ps1`) — приложить, если
  среда есть, но авторитет — CI на `windows-latest`.

## 4. Запреты

- Не мержить PR, не трогать `main`, не переписывать историю, не удалять ветки.
- Не публиковать секреты, `pilot.env`, приватные ключи, сырые логи, EXE. Публичный ключ
  лицензии (`infra/license/public_key.b64`) — не секрет.
- Не утверждать «исправлено» без зелёного CI на своём коммите. Что проверить невозможно
  (живая Windows-машина владельца, Docker Desktop на раннере, содержимо конкретного EXE) —
  перечисли явно как непроверенное.
- 211 зелёных unit-тестов НЕ являются доказательством работающей установки — нужен
  реальный путь выполнения (гейты T1/T2/T4).

## 5. Формат финального отчёта

1. Изменённые файлы.
2. Краткая суть каждой правки.
3. Почему Docker-гейт закрывает ошибку named pipe (путь: Setup → Install-HrmApp →
   Update-HrmApp → backup gate).
4. Как гарантируется непустой `HRM_LICENSE_PUBLIC_KEY` до первого Compose.
5. Как разделены диагностические коды/сообщения (Docker / пустой ключ / нет файла ключа /
   невалидная запись установки).
6. Список добавленных и изменённых тестов.
7. Команды, exit codes, номера прогонов CI и ключевые аннотации.
8. Оставшиеся ограничения и риски (явно, без выдачи непроверенного за проверенное).
9. Итоговый commit SHA и ссылка на PR.
