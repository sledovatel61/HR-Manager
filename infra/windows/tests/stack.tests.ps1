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

Write-Host ("Стек: {0} пройдено, {1} провалено" -f $global:HRM_TestPassed, $global:HRM_TestFailed)
