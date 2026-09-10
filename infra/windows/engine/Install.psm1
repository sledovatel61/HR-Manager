# Установка, запуск/остановка/статус, открытие, удаление и возобновление.

Set-StrictMode -Version 2.0

function Get-HrmReleaseSha {
    # SHA релиза установленного снимка: release.json (пишет сборка установщика)
    # либо git-рев из checkout.
    param([string]$InstallDir, [string]$StateDir)
    $releaseFile = Join-Path $InstallDir "release.json"
    if (Test-Path $releaseFile) {
        $data = Get-HrmJsonFile $releaseFile
        if ($null -ne $data -and $data.release_sha) { return [string]$data.release_sha }
    }
    $git = Join-Path $InstallDir ".git"
    if (Test-Path $git) {
        $rev = Invoke-HrmExternal -Name "git.exe" -Arguments @("-C", $InstallDir, "rev-parse", "HEAD") -IgnoreExitCode
        if ($rev.ExitCode -eq 0) { return $rev.Stdout.Trim() }
    }
    return ""
}

function Copy-HrmSnapshot {
    # Копирует снимок приложения (infra/, backend/, frontend/, release.json)
    # из SourceDir в InstallDir. Не трогает .git каталог назначения.
    param([string]$SourceDir, [string]$InstallDir)
    foreach ($name in @("infra", "backend", "frontend", "release.json")) {
        $source = Join-Path $SourceDir $name
        if (-not (Test-Path $source)) {
            if ($name -eq "release.json") { continue }
            throw "В исходном каталоге нет ${name}: $SourceDir"
        }
        $destination = Join-Path $InstallDir $name
        if (Test-Path $destination) { Remove-Item $destination -Recurse -Force }
        Copy-Item -Path $source -Destination $destination -Recurse -Force
    }
}

function Install-HrmApp {
    # Основной сценарий: повторный запуск распознаёт существующую установку
    # и только открывает/восстанавливает её.
    param(
        [string]$SourceDir = "",
        [string]$InstallDir = "",
        [string]$StateDir = "",
        [int]$Port = 0
    )
    if (-not $SourceDir) {
        # Каталог снимка — ближайший родитель, содержащий infra/compose.pilot.yml
        # (для checkout это корень репозитория, для установки — каталог HRManager).
        $probe = $PSScriptRoot
        for ($i = 0; $i -lt 4; $i++) {
            if (Test-Path (Join-Path $probe "infra\compose.pilot.yml")) { break }
            $probe = Split-Path $probe -Parent
        }
        $SourceDir = $probe
    }
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $port = Get-HrmPort $Port

    $existing = Get-HrmInstallRecord $StateDir
    if ($null -ne $existing -and $existing.release_sha) {
        Write-HrmLog "info" ("Существующая установка найдена: {0}" -f $existing.release_sha)
        Write-HrmLog "info" "Повторный запуск установки не меняет данные и секреты."
        if (Test-HrmComposeRunning $InstallDir $StateDir) {
            Write-HrmLog "info" "Приложение уже запущено."
        }
        else {
            Write-HrmLog "info" "Запускаю приложение…"
            Start-HrmStack $InstallDir $StateDir
            Wait-HrmReady (Get-HrmBaseUrl $port)
        }
        Start-HrmFirstRun -InstallDir $InstallDir -StateDir $StateDir -Port $port
        return
    }

    # --- Первичная установка ---
    Assert-HrmPreflight -InstallDir $InstallDir -StateDir $StateDir -Port $port -SkipCompose
    Initialize-HrmStateDir $StateDir
    Protect-HrmFile $StateDir $StateDir
    # Файл ввода первого запуска (если его записал мастер установки)
    # тоже защищается ACL — фамилия владельца не должна читаться другими
    # пользователями машины.
    $inputFile = Get-HrmInputFile $StateDir
    if (Test-Path $inputFile) { Protect-HrmFile $StateDir $inputFile }
    if (-not (Test-Path $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null }
    Copy-HrmSnapshot $SourceDir $InstallDir

    $releaseSha = Get-HrmReleaseSha $InstallDir $StateDir
    Set-HrmInstallRecord $StateDir @{
        release_sha = $releaseSha
        install_dir = $InstallDir
        state_dir = $StateDir
        port = $port
        installed_at = (Get-Date).ToString("o")
        pilot_created = $false
    }

    $null = Write-HrmPilotEnv $StateDir $releaseSha $port
    # Перепроверяем конфигурацию уже с env-файлом.
    Assert-HrmPreflight -InstallDir $InstallDir -StateDir $StateDir -Port $port | Out-Null

    Start-HrmStack $InstallDir $StateDir
    Wait-HrmReady (Get-HrmBaseUrl $port)
    Start-HrmFirstRun -InstallDir $InstallDir -StateDir $StateDir -Port $port
    Write-HrmLog "info" ("Установка завершена. Приложение: {0}" -f (Get-HrmBaseUrl $port))
}

function Start-HrmApp {
    param([string]$InstallDir = "", [string]$StateDir = "")
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $record = Get-HrmInstallRecord $StateDir
    if ($null -eq $record) { throw "Установка не найдена. Выполните -Action install." }
    $port = [int]$record.port
    Assert-HrmPreflight -InstallDir $InstallDir -StateDir $StateDir -Port $port | Out-Null
    Start-HrmStack $InstallDir $StateDir
    Wait-HrmReady (Get-HrmBaseUrl $port)
    # Наблюдатель канала обновлений: фоновый опрос и выполнение ЯВНО
    # поставленной команды установки (в неинтерактивном режиме — только
    # одноразовый цикл, тесты вызывают channel напрямую).
    if (-not (Test-HrmInteractive)) {
        Invoke-HrmChannelOnce -InstallDir $InstallDir -StateDir $StateDir
    }
    else {
        Start-HrmChannelWatcherProcess -InstallDir $InstallDir -StateDir $StateDir
    }
    Write-HrmLog "info" ("Приложение запущено: {0}" -f (Get-HrmBaseUrl $port))
}

function Stop-HrmApp {
    param([string]$InstallDir = "", [string]$StateDir = "")
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    if (-not (Get-HrmInstallRecord $StateDir)) { throw "Установка не найдена." }
    Stop-HrmChannelWatch -StateDir $StateDir
    if (Test-HrmComposeRunning $InstallDir $StateDir) {
        Stop-HrmStack $InstallDir $StateDir
    }
    else {
        Write-HrmLog "info" "Приложение не запущено."
    }
}

function Get-HrmAppStatus {
    param([string]$InstallDir = "", [string]$StateDir = "")
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $record = Get-HrmInstallRecord $StateDir
    $port = Get-HrmPort
    if ($null -ne $record -and $record.port) { $port = [int]$record.port }
    $baseUrl = Get-HrmBaseUrl $port
    $running = Test-HrmComposeRunning $InstallDir $StateDir
    $frontend = if ($running) { Test-HrmFrontendReady $baseUrl } else { $false }
    $backend = if ($running) { Test-HrmBackendReady $baseUrl } else { $false }
    $ops = if ($backend) { Get-HrmOpsStatus $baseUrl } else { $null }

    $lines = @()
    $lines += "HR Manager — статус"
    $lines += ("Установлено:      {0}" -f $(if ($record) { $record.release_sha } else { "нет" }))
    $lines += ("Контейнеры:       {0}" -f $(if ($running) { "запущены" } else { "остановлены" }))
    $lines += ("Фронтенд:         {0}" -f $(if ($frontend) { "готов ($baseUrl)" } else { "недоступен" }))
    $lines += ("Бэкенд:           {0}" -f $(if ($backend) { "готов" } else { "недоступен" }))
    if ($null -ne $ops) {
        $lines += ("Версия в работе:  {0}" -f $ops.release_sha)
        $lines += ("Миграции:         {0} (ожидается {1})" -f $ops.migrations.current_revision, $ops.migrations.expected_revision)
    }
    $lines | ForEach-Object { Protect-HrmOutput $_ }
}

function Open-HrmApp {
    param([string]$InstallDir = "", [string]$StateDir = "")
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $record = Get-HrmInstallRecord $StateDir
    $port = Get-HrmPort
    if ($null -ne $record -and $record.port) { $port = [int]$record.port }
    if (-not (Test-HrmComposeRunning $InstallDir $StateDir)) {
        Write-HrmLog "info" "Приложение не запущено — запускаю…"
        Start-HrmStack $InstallDir $StateDir
        Wait-HrmReady (Get-HrmBaseUrl $port)
    }
    Invoke-HrmOpenBrowser (Get-HrmBaseUrl $port)
}

function Remove-HrmApp {
    # Удаление: контейнеры снимаются, тома Postgres/бэкапов СОХРАНЯЮТСЯ.
    # Docker Desktop и WSL2 никогда не удаляются. -PurgeData удаляет данные
    # только после точной фразы-подтверждения и предложения бэкапа.
    param(
        [string]$InstallDir = "",
        [string]$StateDir = "",
        [bool]$PurgeData = $false
    )
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    if (-not (Get-HrmInstallRecord $StateDir)) {
        Write-HrmLog "info" "Установка не найдена — удалять нечего."
        return
    }
    Stop-HrmChannelWatch -StateDir $StateDir
    if (Test-HrmComposeRunning $InstallDir $StateDir) {
        Stop-HrmStack $InstallDir $StateDir
    }
    if ($PurgeData) {
        # Фраза-подтверждение. Неинтерактивно фраза берётся из
        # HRM_PURGE_CONFIRMATION (документировано в infra/windows/README.md).
        $typed = ""
        if ($env:HRM_PURGE_CONFIRMATION) { $typed = $env:HRM_PURGE_CONFIRMATION }
        if (-not $typed) {
            $typed = Invoke-HrmPrompt -Prompt "Удаление данных необратимо. Напечатайте точно: УДАЛИТЬ ДАННЫЕ HR MANAGER" -Default ""
        }
        if ($typed -ne "УДАЛИТЬ ДАННЫЕ HR MANAGER") {
            throw "Фраза подтверждения не совпала — данные не удалены."
        }
        $offer = Invoke-HrmConfirmationPrompt -Prompt "Создать шифрованный бэкап перед удалением? (рекомендуется)" -Default $true
        if ($offer) {
            Invoke-HrmCompose $InstallDir $StateDir @("run", "--rm", "backup", "python", "-m", "app.cli", "backup-now", "--as-scheduler", "--reason", "pre-uninstall backup") | Out-Null
            Invoke-HrmCompose $InstallDir $StateDir @("run", "--rm", "backup", "python", "-m", "app.cli", "backup-check", "--deep", "--as-scheduler") | Out-Null
            Write-HrmLog "info" "Бэкап перед удалением создан и проверен (том pilot_backups)."
        }
        Invoke-HrmCompose $InstallDir $StateDir @("down", "-v", "--remove-orphans") | Out-Null
        Write-HrmLog "info" "Тома данных удалены."
    }
    # Каталоги состояния/установки удаляет деинсталлятор Inno Setup;
    # движок оставляет их, чтобы повторная установка восстановила данные.
    Write-HrmLog "info" "Приложение удалено. Docker Desktop и WSL2 не тронуты."
}

function Resume-HrmOperation {
    # Возобновление прерванной операции (после перезагрузки/UAC):
    # если есть журнал обновления — продолжает обновление; иначе просто
    # поднимает стек и доводит первый запуск.
    param([string]$InstallDir = "", [string]$StateDir = "")
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $journal = Get-HrmUpdateJournal $StateDir
    if (Test-Path $journal) {
        $data = Get-HrmJsonFile $journal
        if ($null -ne $data -and $data.phase) {
            Write-HrmLog "info" ("Найден прерванный процесс обновления (фаза {0}) — продолжаю." -f $data.phase)
            Update-HrmApp -ReleaseDir ([string]$data.release_dir) -InstallDir $InstallDir -StateDir $StateDir
            return
        }
    }
    Write-HrmLog "info" "Прерванных операций нет; поднимаю приложение."
    Start-HrmApp -InstallDir $InstallDir -StateDir $StateDir
    $port = Get-HrmPort
    Start-HrmFirstRun -InstallDir $InstallDir -StateDir $StateDir -Port $port
}
