# Тесты состояния Compose-стека: уже запущен, частично запущен, unhealthy,
# сломанный запуск, повторный запуск и первый запуск. Данные (тома) не
# удаляются ни в одном сценарии. Реальная машина не затрагивается.

param([string]$HarnessPath = $PSScriptRoot)

. (Join-Path $HarnessPath "test-harness.ps1")

Write-Host "== Compose-стек: состояния и безопасный ремонт =="

function New-HrmStackTestContext {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows = $true; powershell = $true; docker = $true; daemon = $true; compose = "v2.29.7 (mock)"; port = $true; state_dir = $true; space = $true; config = $true }
    Set-HrmDockerOverride @{ desktop = "engine_ready"; engine = $true; wsl = "ok"; virtualization = "enabled"; free_mb = 20480; port_free = $true; admin = $false }
    return $world
}

Test-Case "первый запуск: контейнеров нет — стек собирается и поднимается" {
    $world = New-HrmStackTestContext
    $install = Get-HrmTestInstallDir
    $state = Get-HrmTestStateDir
    $before = Get-HrmStackState -InstallDir $install -StateDir $state
    Assert-HrmEqual "absent" $before.state "до установки контейнеров быть не должно"
    $result = Start-HrmStack -InstallDir $install -StateDir $state
    Assert-HrmTrue $result.ok "первый запуск должен пройти"
    Assert-HrmTrue ($world.BuildCount -ge 1) "образы должны собираться при первом запуске"
}

# Регресс PR #57 (pwsh 338/1, этот файл, строка 24): «контейнеров нет» и
# «состояние неопределимо» — разные состояния на ОБОИХ хостах. Пустой успешно
# разобранный список — absent; ошибка compose ps и нечитаемый вывод — unknown;
# непустые списки (JSON-массив из одного элемента, построчный JSON Compose
# 2.21+) считаются как раньше.
Test-Case "пустой стек: compose ps -> [] (код 0) — absent, а не unknown" {
    $world = New-HrmStackTestContext
    $world.ContainersJson = "[]"
    $install = Get-HrmTestInstallDir
    $state = Get-HrmTestStateDir
    $containers = Get-HrmComposeContainers -InstallDir $install -StateDir $state
    Assert-HrmFalse ($null -eq $containers) "успешно разобранный пустой список не равен «состояние неопределимо»"
    Assert-HrmEqual 0 @($containers).Count "в пустом стеке нет контейнеров"
    $stack = Get-HrmStackState -InstallDir $install -StateDir $state
    Assert-HrmEqual "absent" $stack.state "пустой стек должен быть absent"
    Assert-HrmEqual "Контейнеры ещё не созданы." $stack.message "текст состояния absent"
    Assert-HrmEqual 0 $stack.total "в пустом стеке нет контейнеров"
}

Test-Case "compose ps недоступен (код не 0) — по-прежнему unknown, а не absent" {
    $world = New-HrmStackTestContext
    $world.DockerMissing = $true
    $install = Get-HrmTestInstallDir
    $state = Get-HrmTestStateDir
    Assert-HrmTrue ($null -eq (Get-HrmComposeContainers -InstallDir $install -StateDir $state)) "ошибка compose ps не может стать пустым списком"
    $stack = Get-HrmStackState -InstallDir $install -StateDir $state
    Assert-HrmEqual "unknown" $stack.state "«не удалось определить» не превращается в «контейнеров нет»"
    Assert-HrmEqual "Не удалось получить состояние контейнеров." $stack.message "текст состояния unknown"
}

Test-Case "нечитаемый вывод compose ps (код 0) — по-прежнему unknown" {
    $world = New-HrmStackTestContext
    $world.ContainersJson = "compose: unexpected output (not json)"
    $install = Get-HrmTestInstallDir
    $state = Get-HrmTestStateDir
    Assert-HrmTrue ($null -eq (Get-HrmComposeContainers -InstallDir $install -StateDir $state)) "нечитаемый вывод не может стать пустым списком"
    $stack = Get-HrmStackState -InstallDir $install -StateDir $state
    Assert-HrmEqual "unknown" $stack.state "нечитаемый вывод — unknown"
}

Test-Case "пустой проект Compose 2.21+: пустой вывод compose ps (код 0) — absent, а не unknown" {
    # Docker Compose 2.21+ печатает для пустого проекта НИЧЕГО (построчный JSON
    # без строк): v2.40.3 на CI-раннере — код 0, 0 символов (PR #57, repro).
    # Это успешно полученный пустой список на ОБОИХ хостах, а не «неопределимо».
    $world = New-HrmStackTestContext
    $world.ComposePsEmptyOutput = $true
    $install = Get-HrmTestInstallDir
    $state = Get-HrmTestStateDir
    $containers = Get-HrmComposeContainers -InstallDir $install -StateDir $state
    Assert-HrmFalse ($null -eq $containers) "пустой вывод успешного compose ps — пустой список, а не «неопределимо»"
    Assert-HrmEqual 0 @($containers).Count "контейнеров нет"
    $stack = Get-HrmStackState -InstallDir $install -StateDir $state
    Assert-HrmEqual "absent" $stack.state "пустой проект Compose 2.21+ — absent"
    Assert-HrmEqual "Контейнеры ещё не созданы." $stack.message "текст состояния absent"
}

Test-Case "JSON null вместо списка compose ps (код 0) — по-прежнему unknown" {
    # Граница фикса: absent — только для пустого списка (пустой вывод или «[]»);
    # значение, которое не является списком контейнеров, остаётся unknown.
    $world = New-HrmStackTestContext
    $world.ContainersJson = "null"
    $install = Get-HrmTestInstallDir
    $state = Get-HrmTestStateDir
    Assert-HrmTrue ($null -eq (Get-HrmComposeContainers -InstallDir $install -StateDir $state)) "JSON null не может стать пустым списком"
    $stack = Get-HrmStackState -InstallDir $install -StateDir $state
    Assert-HrmEqual "unknown" $stack.state "JSON null — unknown"
}

Test-Case "один контейнер в JSON-массиве и построчный JSON compose ps считаются как раньше" {
    $world = New-HrmStackTestContext
    $install = Get-HrmTestInstallDir
    $state = Get-HrmTestStateDir
    $world.ContainersJson = '[{"Name":"backend","State":"running"}]'
    $single = Get-HrmStackState -InstallDir $install -StateDir $state
    Assert-HrmEqual "running" $single.state "один запущенный контейнер в JSON-массиве"
    Assert-HrmEqual 1 $single.total "ровно один контейнер"
    $world.ContainersJson = '{"Name":"db","State":"running"}' + "`n" + '{"Name":"backend","State":"exited"}'
    $lines = Get-HrmStackState -InstallDir $install -StateDir $state
    Assert-HrmEqual "partial" $lines.state "построчный JSON: запущен один контейнер из двух"
    Assert-HrmEqual 2 $lines.total "оба контейнера из построчного JSON"
    Assert-HrmEqual 1 $lines.running "запущен один контейнер"
}

Test-Case "результат Start-HrmStack — ровно один объект с ok (журнал не в конвейере)" {
    $null = New-HrmStackTestContext
    $install = Get-HrmTestInstallDir
    $state = Get-HrmTestStateDir
    $result = Start-HrmStack -InstallDir $install -StateDir $state
    # Регрессия: ранее Write-HrmLog писал в success stream, строки журнала
    # примешивались к результату, и $result.ok падал с PropertyNotFoundException.
    Assert-HrmEqual 1 @($result).Count "в результат Start-HrmStack попали лишние объекты (журнал?)"
    Assert-HrmTrue $result.ok "первый запуск должен пройти"
}

Test-Case "повторный запуск: стек уже запущен — без пересборки и без дублей" {
    $world = New-HrmStackTestContext
    $install = Get-HrmTestInstallDir
    $state = Get-HrmTestStateDir
    $world.Running = $true
    $buildsBefore = $world.BuildCount
    $result = Start-HrmStack -InstallDir $install -StateDir $state
    Assert-HrmTrue $result.ok "запуск уже работающего стека должен быть успешным"
    Assert-HrmEqual "running" $result.state "состояние должно быть running"
    Assert-HrmEqual $buildsBefore $world.BuildCount "повторный запуск не должен пересобирать образы"
}

Test-Case "частично запущенный стек ремонтируется без удаления томов" {
    $world = New-HrmStackTestContext
    $install = Get-HrmTestInstallDir
    $state = Get-HrmTestStateDir
    $world.ContainersJson = '[{"Name":"db","State":"running"},{"Name":"backend","State":"exited"}]'
    $stateBefore = Get-HrmStackState -InstallDir $install -StateDir $state
    Assert-HrmEqual "partial" $stateBefore.state "частичный запуск должен распознаваться"
    $result = Start-HrmStack -InstallDir $install -StateDir $state
    Assert-HrmTrue $result.ok "ремонт должен завершиться успешно"
    $calls = (Get-HrmWorldCallArgs $world) -join " "
    Assert-HrmNotContains $calls "down -v" "тома не должны удаляться при ремонте"
    Assert-HrmEqual 0 @($world.RemovedVolumes).Count "ни один том не должен удаляться"
}

Test-Case "нездоровый сервис (degraded) приводит к пересозданию, а не к удалению данных" {
    $world = New-HrmStackTestContext
    $install = Get-HrmTestInstallDir
    $state = Get-HrmTestStateDir
    $world.ContainersJson = '[{"Name":"backend","State":"running","Health":"unhealthy"},{"Name":"frontend","State":"running","Health":"healthy"}]'
    $before = Get-HrmStackState -InstallDir $install -StateDir $state
    Assert-HrmEqual "degraded" $before.state "unhealthy должен распознаваться"
    $calls = (Get-HrmWorldCallArgs $world) -join " "
    Assert-HrmEqual 0 @($world.RemovedVolumes).Count "тома не должны удаляться"
    Assert-HrmNotContains $calls "volume rm" "тома не должны удаляться"
}

Test-Case "сломанный запуск (up падает) — честная ошибка и кнопка отчёта, без удаления данных" {
    $world = New-HrmStackTestContext
    $install = Get-HrmTestInstallDir
    $state = Get-HrmTestStateDir
    $world.UpFails = $true
    $result = Start-HrmStack -InstallDir $install -StateDir $state
    Assert-HrmFalse $result.ok "падение up не должно считаться успехом"
    Assert-HrmContains $result.message "Создать отчёт для поддержки" "нужна подсказка про отчёт"
    Assert-HrmEqual 0 @($world.RemovedVolumes).Count "тома не должны удаляться при сбое"
}

Test-Case "Docker недоступен: состояние unknown, без бесконечных повторов" {
    $world = New-HrmStackTestContext
    $install = Get-HrmTestInstallDir
    $state = Get-HrmTestStateDir
    $world.DockerMissing = $true
    $snapshot = Get-HrmStackState -InstallDir $install -StateDir $state
    Assert-HrmEqual "unknown" $snapshot.state "без Docker состояние должно быть unknown"
    $result = Start-HrmStack -InstallDir $install -StateDir $state
    Assert-HrmFalse $result.ok "без Docker стек не может быть запущен"
}

Test-Case "остановленный стек поднимается без пересборки образов" {
    $world = New-HrmStackTestContext
    $install = Get-HrmTestInstallDir
    $state = Get-HrmTestStateDir
    $world.ContainersJson = '[{"Name":"db","State":"exited"},{"Name":"backend","State":"exited"}]'
    $before = Get-HrmStackState -InstallDir $install -StateDir $state
    Assert-HrmEqual "stopped" $before.state "остановленный стек должен распознаваться"
    $result = Start-HrmStack -InstallDir $install -StateDir $state
    Assert-HrmTrue $result.ok "остановленный стек должен подниматься"
}

Test-Case "миграции: ожидаемая голова берётся из развёрнутого образа, а не из константы" {
    New-HrmStackTestContext
    $install = Get-HrmTestInstallDir
    $state = Get-HrmTestStateDir
    $world = $global:HRM_MockWorld
    $world.AlembicHeads = "0022"
    Assert-HrmEqual "0022" (Get-HrmMigrationsHead -InstallDir $install -StateDir $state) "голова должна читаться из образа"
    Assert-HrmEqual "0013" (Get-HrmMigrationsState -InstallDir $install -StateDir $state) "текущая ревизия должна читаться из работающего бэкенда"
}

Write-Host "== Готовность: принадлежность ответа целевому пилоту и релизу =="

Test-Case "readiness: чужой HTTP 200 на порту не считается готовностью пилота" {
    $world = New-HrmStackTestContext
    $world.HealthEnvironment = "development"
    $baseUrl = "http://127.0.0.1:8080"
    Assert-HrmFalse (Test-HrmBackendReady $baseUrl) "ответ development-стека не принадлежит пилоту"
    $thrown = ""
    try { Wait-HrmReady $baseUrl 1 -ExpectedReleaseSha "sha-expected" } catch { $thrown = $_.Exception.Message }
    Assert-HrmContains $thrown "ДРУГОЕ приложение" "сообщение должно объяснять, что на порту чужой стек"
}

Test-Case "readiness: подтверждается только ожидаемый release_sha в работе" {
    $world = New-HrmStackTestContext
    $world.OpsBody.release_sha = "sha-current"
    $baseUrl = "http://127.0.0.1:8080"
    Assert-HrmTrue (Test-HrmBackendReady $baseUrl) "пилот должен отвечать как pilot"
    Wait-HrmReady $baseUrl 1 -ExpectedReleaseSha "sha-current"
    $thrown = ""
    try { Wait-HrmReady $baseUrl 1 -ExpectedReleaseSha "sha-other" } catch { $thrown = $_.Exception.Message }
    Assert-HrmContains $thrown "ожидалась" "сообщение должно называть ожидаемую версию"
}

Write-Host "== Образ ↔ снимок ↔ база: приведение и отказ ДО запуска =="

Test-Case "образы не соответствуют снимку: пересборка и приведение к установленной версии" {
    $world = New-HrmStackTestContext
    $install = Get-HrmTestInstallDir
    New-HrmFakeSnapshot -Root $install -ReleaseSha "snapshot-sha-new"
    $world.Running = $true
    $world.ImageReleaseSha = "snapshot-sha-old"
    $buildsBefore = $world.BuildCount
    $result = Start-HrmStack -InstallDir $install -StateDir (Get-HrmTestStateDir)
    Assert-HrmTrue $result.ok "приведение к развёрнутому снимку должно завершиться успешно"
    Assert-HrmTrue ($world.BuildCount -gt $buildsBefore) "устаревшие образы должны быть пересобраны"
    Assert-HrmEqual "snapshot-sha-new" $world.ImageReleaseSha "метка образа должна стать меткой развёрнутого снимка"
}

Test-Case "образы соответствуют снимку: запуск без пересборки" {
    $world = New-HrmStackTestContext
    $install = Get-HrmTestInstallDir
    New-HrmFakeSnapshot -Root $install -ReleaseSha "snapshot-sha-same"
    $world.Running = $true
    $world.ImageReleaseSha = "snapshot-sha-same"
    $buildsBefore = $world.BuildCount
    $result = Start-HrmStack -InstallDir $install -StateDir (Get-HrmTestStateDir)
    Assert-HrmTrue $result.ok "запуск должен пройти"
    Assert-HrmEqual $buildsBefore $world.BuildCount "соответствующие снимку образы пересобирать нельзя"
}

Test-Case "схема базы новее образа: отказ ДО запуска контейнеров (без crash-loop и без удаления данных)" {
    $world = New-HrmStackTestContext
    $world.ContainersJson = '[{"Name":"db","State":"running"},{"Name":"backend","State":"exited"}]'
    $world.DbRevision = "0022"
    $world.AlembicHeads = "0021"
    $upsBefore = $world.UpCount
    $result = Start-HrmStack -InstallDir (Get-HrmTestInstallDir) -StateDir (Get-HrmTestStateDir)
    Assert-HrmFalse $result.ok "несовместимая схема не должна приводить к запуску"
    Assert-HrmEqual "schema_incompatible" $result.state "состояние должно называть несовместимость схемы"
    Assert-HrmContains $result.message "0022" "сообщение должно называть ревизию базы"
    Assert-HrmContains $result.message "резервной копии" "сообщение должно подсказывать безопасный путь"
    Assert-HrmEqual $upsBefore $world.UpCount "контейнеры приложения не должны запускаться"
    Assert-HrmEqual 0 @($world.RemovedVolumes).Count "тома не должны удаляться"
}

Test-Case "схема совместима с образом: стек поднимается из частичного состояния" {
    $world = New-HrmStackTestContext
    $world.ContainersJson = '[{"Name":"db","State":"running"},{"Name":"backend","State":"exited"}]'
    $world.DbRevision = "0013"
    $world.AlembicHeads = "0013"
    $result = Start-HrmStack -InstallDir (Get-HrmTestInstallDir) -StateDir (Get-HrmTestStateDir)
    Assert-HrmTrue $result.ok "совместимая схема не должна блокировать запуск"
}

Write-Host ("Стек: {0} пройдено, {1} провалено" -f $global:HRM_TestPassed, $global:HRM_TestFailed)
