#requires -Version 5.1
<#
.SYNOPSIS
  HR Manager — локальный Windows-пилот: automation engine установки, запуска,
  диагностики и обновления. Внутренний движок графического установщика
  HR Manager Setup.exe (Inno Setup), а НЕ пользовательский интерфейс.

.DESCRIPTION
  Один PowerShell entry point с закрытым набором действий:

    install      — preflight, генерация секретов, создание владельца, запуск
    start        — идемпотентный запуск уже установленного контура
    stop         — остановка контейнеров без удаления данных
    status       — сводное состояние без секретов/PII (JSON)
    update       — безопасное обновление (lock -> backup -> миграция -> smoke)
    diagnostics  — санитаризированный отчёт для поддержки
    uninstall    — удаление launcher/ярлыков/контейнеров; данные сохраняются
    check        — только preflight без изменений машины

  Секреты, фамилия и роль НЕ передаются в командной строке процесса: фамилия и
  роль приходят в JSON input file (ACL-ограничен), секреты — в env file
  (--env-file), токен первого запуска — только в env и fragment ссылки.
  Тестируемость: сменный command runner ($script:CmdRunner) + -TestMode, не
  трогающий реальную установку; см. hr-manager.Tests.ps1 и
  backend/tests/test_windows_engine.py. При dot-source скрипт не выполняет
  Main, поэтому Pester может вызывать отдельные функции.

.NOTES
  Trust boundary: обновление принимает только явный локальный release-каталог,
  целостность payload проверяется по release-manifest.json (SHA-256).
  Цифровая подпись Setup.exe — см. docs/phase-12-report-arena.md.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'start', 'stop', 'status', 'update', 'diagnostics', 'uninstall', 'check')]
    [string]$Action = 'check',

    # Каталог локального состояния (секреты + state). По умолчанию
    # %LOCALAPPDATA%\HRManager. Никогда не внутри checkout и не в git.
    [string]$StateDir = '',

    # Корень release-payload (содержит infra\, backend\, frontend\,
    # release-manifest.json). По умолчанию — каталог, где лежит этот скрипт,
    # поднятый до корня payload (infra\windows -> корень).
    [string]$PayloadDir = '',

    # JSON input file: {"surname":"...","working_mode":"hr|manager|admin"}.
    [string]$InputFile = '',

    # CI/installer mode: никаких интерактивных ожиданий и подсказок.
    [switch]$NonInteractive,

    # Dry-run режим тестов: не трогает реальную установку, реальные volumes,
    # пользовательские данные и Docker. Состояние пишет во временный каталог.
    [switch]$TestMode,

    # update: явный локальный release-каталог (payload нужной версии).
    [string]$TargetRelease = '',

    # Порт loopback-публикации frontend (см. compose.pilot.yml).
    [int]$HttpPort = 8080,

    # uninstall -RemoveData: фраза подтверждения удаления данных.
    [string]$RemoveDataPhrase = '',

    # Путь к установленному Setup.exe (для ярлыка диагностики). Опционален.
    [string]$LauncherExe = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Коды выхода (для GUI и CI). 0 — успех; иначе — понятное русское сообщение.
# ---------------------------------------------------------------------------
$script:EXIT_OK            = 0
$script:EXIT_USAGE         = 2
$script:EXIT_PREFLIGHT     = 3
$script:EXIT_DOCKER_MISS   = 4
$script:EXIT_DAEMON_DOWN   = 5
$script:EXIT_COMPOSE_BAD   = 6
$script:EXIT_PORT_BUSY     = 7
$script:EXIT_DISK_LOW      = 8
$script:EXIT_STATE_FAIL    = 9
$script:EXIT_UPDATE_LOCKED = 10
$script:EXIT_BACKUP_FAIL   = 11
$script:EXIT_MIGRATE_FAIL  = 12
$script:EXIT_SMOKE_FAIL    = 13
$script:EXIT_ROLLBACK_FAIL = 14
$script:EXIT_UPDATE_BAD    = 15
$script:EXIT_INTERNAL      = 70

$script:ExitCode = $script:EXIT_OK

# Пишет ожидаемую пользовательскую ошибку в stderr, не бросая исключение
# (ErrorActionPreference='Stop' не должен превращать понятные ошибки в панику).
function Write-UserError {
    param([string]$Message)
    [Console]::Error.WriteLine($Message)
}

# ---------------------------------------------------------------------------
# Сменный command runner (mock в тестах). Сигнатура: { param($Exe, $Args) }.
# Возвращает exit code внешней команды.
# ---------------------------------------------------------------------------
$script:CmdRunner = $null

function Invoke-Cmd {
    param([string]$Exe, [string[]]$Args)
    if ($null -ne $script:CmdRunner) {
        return & $script:CmdRunner -Exe $Exe -Args $Args
    }
    & $Exe @Args
    return $LASTEXITCODE
}

# ---------------------------------------------------------------------------
# Случайные секреты (криптографический ГСЧ RandomNumberGenerator).
# ---------------------------------------------------------------------------
function Get-RandomBytes {
    param([int]$Count)
    $bytes = New-Object byte[] $Count
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ,$bytes
}

function Get-NewHex {
    param([int]$Bytes)
    $b = Get-RandomBytes $Bytes
    return ([System.BitConverter]::ToString($b) -replace '-', '').ToLowerInvariant()
}

function Get-NewBase64 {
    param([int]$Bytes)
    $b = Get-RandomBytes $Bytes
    return [System.Convert]::ToBase64String($b)
}

function Get-NewToken {
    # URL-safe, без паддинга: для fragment ссылки и env файла.
    param([int]$Bytes = 32)
    $b64 = Get-NewBase64 $Bytes
    return ($b64 -replace '\+', '-' -replace '/', '_' -replace '=', '')
}

# ---------------------------------------------------------------------------
# Пути и параметры
# ---------------------------------------------------------------------------
function Resolve-Paths {
    if (-not $StateDir) {
        $StateDir = if ($TestMode) { Join-Path ([System.IO.Path]::GetTempPath()) 'hr-manager-test-state' }
                    elseif ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'HRManager' }
                    else { Join-Path $env:TEMP 'HRManager' }
    }
    if (-not $PayloadDir) {
        $PayloadDir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    }
    $script:StateDir = $StateDir
    $script:PayloadDir = $PayloadDir
    $script:EnvFile = Join-Path $StateDir 'pilot.env'
    $script:StateFile = Join-Path $StateDir 'state.json'
    # Resume после перезагрузки: если input file не передан повторно, берём
    # сохранённую копию из state dir (пользователь не заполняет форму заново).
    $savedInput = Join-Path $StateDir 'install-input.json'
    $script:SavedInputFile = $savedInput
    if ($InputFile) {
        $script:InputFile = $InputFile
    } elseif (Test-Path -LiteralPath $savedInput) {
        $script:InputFile = $savedInput
    } else {
        $script:InputFile = ''
    }
    $script:WindowsDir = Join-Path $PayloadDir 'infra\windows'
    $script:RedactionFile = Join-Path $WindowsDir 'redaction-patterns.json'
    $script:ManifestFile = Join-Path $PayloadDir 'release-manifest.json'
    $script:ComposeFile = Join-Path $PayloadDir 'infra\docker-compose.yml'
    $script:PilotOverlay = Join-Path $PayloadDir 'infra\compose.pilot.yml'
    $script:HttpPort = $HttpPort
    $script:LauncherExe = $LauncherExe
}

# ---------------------------------------------------------------------------
# JSON helpers (PS 5.1-safe)
# ---------------------------------------------------------------------------
function Read-JsonFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Write-JsonFile {
    param([string]$Path, $Object)
    $json = ConvertTo-Json -InputObject $Object -Depth 10 -Compress
    Set-Content -LiteralPath $Path -Value $json -Encoding UTF8
}

function Read-State {
    return Read-JsonFile $script:StateFile
}

function Write-State {
    param($State)
    New-Item -ItemType Directory -Path $script:StateDir -Force | Out-Null
    Write-JsonFile -Path $script:StateFile -Object $State
}

# ---------------------------------------------------------------------------
# Валидация формы установки
# ---------------------------------------------------------------------------
function Test-WorkingMode {
    param([string]$Mode)
    return ($Mode -eq 'hr' -or $Mode -eq 'manager' -or $Mode -eq 'admin')
}

function Test-Surname {
    param([string]$Surname)
    if ([string]::IsNullOrWhiteSpace($Surname)) { return $false }
    $s = $Surname.Trim()
    if ($s.Length -gt 60) { return $false }
    # Кириллица, латиница, дефис, пробел, апостроф. Без команд, без спецсимволов.
    if ($s -notmatch '^[\p{L}\p{M}\- ''\'' ]+$') { return $false }
    return $true
}

# ---------------------------------------------------------------------------
# Redaction (общие паттерны из redaction-patterns.json; проверяются тестами)
# ---------------------------------------------------------------------------
function Get-RedactionPatterns {
    $spec = Read-JsonFile $script:RedactionFile
    if ($null -eq $spec -or $null -eq $spec.patterns) { return @() }
    $result = @()
    foreach ($p in $spec.patterns) {
        $result += [string]$p.pattern
    }
    return ,$result
}

function Redact-Text {
    param([string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return $Text }
    $out = $Text
    $patterns = Get-RedactionPatterns
    foreach ($pattern in $patterns) {
        try { $out = [System.Text.RegularExpressions.Regex]::Replace($out, $pattern, '<REDACTED>') } catch { }
    }
    return $out
}

# ---------------------------------------------------------------------------
# Compose
# ---------------------------------------------------------------------------
function Get-ComposeArgs {
    param([string[]]$Extra)
    $args = @(
        'compose',
        '-f', $script:ComposeFile,
        '-f', $script:PilotOverlay,
        '--env-file', $script:EnvFile,
        '-p', 'hr-manager-pilot'
    )
    if ($Extra) { $args += $Extra }
    return ,$args
}

function Invoke-Compose {
    param([string[]]$Extra)
    $args = Get-ComposeArgs $Extra
    $code = Invoke-Cmd -Exe 'docker' -Args $args
    if ($code -ne 0) {
        throw "Команда docker compose завершилась с кодом $code."
    }
}

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
function Get-FreeSpaceMB {
    param([string]$Path)
    try {
        $root = [System.IO.Path]::GetPathRoot($Path)
        if (-not $root) { return -1 }
        $drive = [System.IO.DriveInfo]::new($root)
        return [int]([Math]::Floor($drive.AvailableFreeSpace / 1MB))
    } catch {
        return -1
    }
}

function Test-PortFree {
    param([int]$Port)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect('127.0.0.1', $Port)
        return $false
    } catch {
        return $true
    } finally {
        $client.Dispose()
    }
}

function Get-DockerVersion {
    try {
        $out = & docker --version 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        return ($out -join ' ').Trim()
    } catch {
        return $null
    }
}

function Get-ComposeVersion {
    try {
        $out = & docker compose version 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        $line = ($out | Select-Object -First 1) -join ' '
        return $line.Trim()
    } catch {
        return $null
    }
}

function Get-ComposeMajorMinor {
    param([string]$VersionLine)
    if (-not $VersionLine) { return $null }
    if ($VersionLine -match '[vV]?(\d+)\.(\d+)') {
        return @([int]$Matches[1], [int]$Matches[2])
    }
    return $null
}

function Test-DockerDaemon {
    try {
        & docker info *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Invoke-Preflight {
    $errors = @()

    $psv = $PSVersionTable.PSVersion
    if ($psv.Major -lt 5 -or ($psv.Major -eq 5 -and $psv.Minor -lt 1)) {
        $errors += 'Требуется Windows PowerShell 5.1 или новее.'
    }

    $dockerVersion = Get-DockerVersion
    if (-not $dockerVersion) {
        $errors += 'Docker CLI не найден. Установите Docker Desktop (WSL2) и перезапустите установку.'
    } elseif (-not (Test-DockerDaemon)) {
        $errors += 'Движок Docker не запущен. Запустите Docker Desktop и подождите, пока он станет готов.'
    }

    $composeVersion = Get-ComposeVersion
    if (-not $composeVersion) {
        $errors += 'Docker Compose v2 не найден (нужен `docker compose`).'
    } else {
        $ver = Get-ComposeMajorMinor $composeVersion
        if ($null -eq $ver -or $ver[0] -lt 2 -or ($ver[0] -eq 2 -and $ver[1] -lt 24)) {
            $errors += 'Нужен Docker Compose v2.24+ (для пилотного overlay). Обновите Docker Desktop.'
        }
    }

    if (-not (Test-PortFree $script:HttpPort)) {
        $errors += "Порт $($script:HttpPort) занят. Освободите его или выберите другой порт."
    }

    $freeMB = Get-FreeSpaceMB $script:StateDir
    if ($freeMB -ge 0 -and $freeMB -lt 2048) {
        $errors += 'Недостаточно места на диске (нужно минимум 2 ГБ свободно).'
    }

    if (-not (Test-Path -LiteralPath $script:ComposeFile) -or -not (Test-Path -LiteralPath $script:PilotOverlay)) {
        $errors += 'Не найден состав установки (compose-файлы). Повторите установку из официального Setup.exe.'
    }

    try {
        New-Item -ItemType Directory -Path $script:StateDir -Force -ErrorAction Stop | Out-Null
        $probe = Join-Path $script:StateDir '.write-probe'
        Set-Content -LiteralPath $probe -Value 'ok' -ErrorAction Stop
        Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
    } catch {
        $errors += "Каталог состояния недоступен для записи: $script:StateDir"
    }

    return ,$errors
}

# ---------------------------------------------------------------------------
# Секреты и env file
# ---------------------------------------------------------------------------
function Initialize-Secrets {
    # Повторный запуск не ротирует секреты: если pilot.env уже есть — вернуть
    # как есть, чтобы не потерять ключ расшифровки существующих backup.
    if (Test-Path -LiteralPath $script:EnvFile) {
        return
    }
    $secrets = @{
        POSTGRES_DB        = 'hr_manager'
        POSTGRES_USER      = 'hr_manager'
        POSTGRES_PASSWORD  = Get-NewHex 16
        SECRET_KEY         = Get-NewHex 32
        BACKUP_KEY_ID      = ('pilot-' + (Get-NewHex 6))
        BACKUP_ENC_KEY     = Get-NewBase64 32
        PILOT_HTTP_PORT    = [string]$script:HttpPort
        RELEASE_SHA        = (Get-PayloadReleaseSha)
    }
    $lines = @()
    foreach ($key in $secrets.Keys) {
        $lines += "$key=$($secrets[$key])"
    }
    New-Item -ItemType Directory -Path $script:StateDir -Force | Out-Null
    Set-Content -LiteralPath $script:EnvFile -Value $lines -Encoding ASCII
    Protect-StateDir
}

function Get-EnvValue {
    param([string]$Key, [string]$Default = '')
    if (-not (Test-Path -LiteralPath $script:EnvFile)) { return $Default }
    foreach ($line in Get-Content -LiteralPath $script:EnvFile -Encoding ASCII) {
        if ($line -match "^$([Regex]::Escape($Key))=(.*)$") {
            return $Matches[1]
        }
    }
    return $Default
}

function Set-EnvValue {
    # Переписать одну переменную env файла, не печатая значения.
    param([string]$Key, [string]$Value)
    $lines = @()
    if (Test-Path -LiteralPath $script:EnvFile) {
        $lines = Get-Content -LiteralPath $script:EnvFile -Encoding ASCII | Where-Object { $_ -notmatch "^$([Regex]::Escape($Key))=" }
    }
    if ($Value) {
        $lines += "$Key=$Value"
    }
    Set-Content -LiteralPath $script:EnvFile -Value $lines -Encoding ASCII
    Protect-StateDir
}

function Protect-StateDir {
    # Windows: ограничить ACL текущим пользователем; fail closed при неудаче
    # только на Windows (на Linux/CI этот шаг недоступен и пропускается).
    if ($env:OS -ne 'Windows_NT') { return }
    try {
        $user = "$env:USERDOMAIN\$env:USERNAME"
        icacls $script:StateDir /inheritance:r /grant:r "${user}:(OI)(CI)F" *> $null
        icacls $script:EnvFile /inheritance:r /grant:r "${user}:F" *> $null
    } catch {
        throw 'Не удалось безопасно сохранить секреты (ACL). Установка прервана.'
    }
}

# ---------------------------------------------------------------------------
# Первый запуск: одноразовый токен и deep link
# ---------------------------------------------------------------------------
function Invoke-FirstRunArm {
    param([string]$Surname, [string]$WorkingMode)
    $token = Get-NewToken 32
    $expiry = [DateTime]::UtcNow.AddHours(2).ToString('o')
    Set-EnvValue 'FIRST_RUN_TOKEN' $token
    Set-EnvValue 'FIRST_RUN_EXPIRES_AT' $expiry
    Set-EnvValue 'PILOT_SURNAME' $Surname
    Set-EnvValue 'PILOT_WORKING_MODE' $WorkingMode
    Invoke-Compose @('up', '-d', '--wait', '--wait-timeout', '300')
    $script:ArmedToken = $token
}

function Get-FirstRunStatus {
    try {
        return Invoke-RestMethod -Uri "http://127.0.0.1:$($script:HttpPort)/api/setup/first-run/status" -TimeoutSec 5
    } catch {
        return $null
    }
}

function Invoke-FirstRunClear {
    Set-EnvValue 'FIRST_RUN_TOKEN' ''
    Set-EnvValue 'FIRST_RUN_EXPIRES_AT' ''
    Set-EnvValue 'PILOT_SURNAME' ''
    Invoke-Compose @('up', '-d')
}

function Wait-FirstRunConsumed {
    param([int]$TimeoutSeconds = 300)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        $status = Get-FirstRunStatus
        if ($null -ne $status -and $status.pending -eq $false) {
            return $true
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

# ---------------------------------------------------------------------------
# HTTP smoke / статус
# ---------------------------------------------------------------------------
function Invoke-HealthProbe {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$($script:HttpPort)/api/health" -TimeoutSec 10 -UseBasicParsing
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Invoke-OpsStatus {
    try {
        return Invoke-RestMethod -Uri "http://127.0.0.1:$($script:HttpPort)/api/ops/status" -TimeoutSec 10
    } catch {
        return $null
    }
}

function Invoke-BackupHealthProbe {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$($script:HttpPort)/api/ops/backup-health" -TimeoutSec 10 -UseBasicParsing
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

# ---------------------------------------------------------------------------
# Ярлыки (Windows only)
# ---------------------------------------------------------------------------
function New-AppShortcut {
    param([string]$Name, [string]$UrlOrExe)
    if ($env:OS -ne 'Windows_NT') { return }
    $desktop = [Environment]::GetFolderPath('Desktop')
    $lnk = Join-Path $desktop "$Name.lnk"
    try {
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($lnk)
        $shortcut.TargetPath = $UrlOrExe
        $shortcut.Save()
    } catch {
        throw 'Не удалось создать ярлык.'
    }
}

function Open-Browser {
    param([string]$Url)
    if ($env:OS -ne 'Windows_NT') { return }
    Start-Process $Url
}

# ---------------------------------------------------------------------------
# payload manifest
# ---------------------------------------------------------------------------
function Get-PayloadReleaseSha {
    $manifest = Read-JsonFile $script:ManifestFile
    if ($null -ne $manifest -and $null -ne $manifest.release_sha) {
        return [string]$manifest.release_sha
    }
    return ''
}

# ---------------------------------------------------------------------------
# Действия. Каждое устанавливает $script:ExitCode и возвращает управление;
# понятные ошибки пишутся в stderr через Write-UserError.
# ---------------------------------------------------------------------------
function Invoke-Install {
    $input = Read-JsonFile $script:InputFile
    if ($null -eq $input -or -not (Test-Surname ([string]$input.surname)) -or -not (Test-WorkingMode ([string]$input.working_mode))) {
        Write-UserError 'Не задана корректная фамилия или рабочая роль (input file).'
        $script:ExitCode = $script:EXIT_USAGE
        return
    }
    $surname = ([string]$input.surname).Trim()
    $mode = [string]$input.working_mode

    # Сохранить форму для безопасного resume после перезагрузки.
    New-Item -ItemType Directory -Path $script:StateDir -Force | Out-Null
    Copy-Item -LiteralPath $script:InputFile -Destination $script:SavedInputFile -Force -ErrorAction SilentlyContinue

    $errors = Invoke-Preflight
    if ($errors.Count -gt 0) {
        foreach ($e in $errors) { Write-UserError $e }
        $script:ExitCode = $script:EXIT_PREFLIGHT
        return
    }

    Initialize-Secrets

    $state = Read-State
    if ($null -eq $state) {
        $state = [pscustomobject]@{
            installed_release_sha = (Get-PayloadReleaseSha)
            installed_version     = '0.1.0'
            working_mode          = $mode
            first_run_claimed     = $false
            installed_at          = [DateTime]::UtcNow.ToString('o')
        }
        Write-State $state
    }

    Invoke-Compose @('up', '-d', '--wait', '--wait-timeout', '300')

    # One-shot миграция под advisory lock (никогда не при старте контейнера).
    Invoke-Compose @('run', '--rm', 'backend', 'alembic', 'upgrade', 'head')

    Invoke-Compose @('up', '-d', '--wait', '--wait-timeout', '300')

    if (-not (Invoke-HealthProbe)) {
        Write-UserError 'Приложение не ответило после установки. Откройте «Диагностика HR Manager».'
        $script:ExitCode = $script:EXIT_SMOKE_FAIL
        return
    }

    Invoke-FirstRunArm -Surname $surname -WorkingMode $mode

    $appUrl = "http://127.0.0.1:$($script:HttpPort)/"
    New-AppShortcut 'HR Manager' $appUrl
    if ($script:LauncherExe) {
        New-AppShortcut 'Диагностика HR Manager' $script:LauncherExe
    }
    $firstRunUrl = "http://127.0.0.1:$($script:HttpPort)/#first-run?t=$($script:ArmedToken)"
    Open-Browser $firstRunUrl

    Write-Output 'Установка завершена. Откройте HR Manager и завершите первый запуск в браузере.'
    $script:ExitCode = $script:EXIT_OK
}

function Invoke-Start {
    $errors = Invoke-Preflight
    if ($errors.Count -gt 0) {
        foreach ($e in $errors) { Write-UserError $e }
        $script:ExitCode = $script:EXIT_PREFLIGHT
        return
    }
    if (-not (Test-Path -LiteralPath $script:EnvFile)) {
        Write-UserError 'Установка не найдена. Запустите установку.'
        $script:ExitCode = $script:EXIT_STATE_FAIL
        return
    }
    Invoke-Compose @('up', '-d', '--wait', '--wait-timeout', '300')
    Write-Output 'Приложение запущено.'
    $script:ExitCode = $script:EXIT_OK
}

function Invoke-Stop {
    if (-not (Test-Path -LiteralPath $script:EnvFile)) {
        Write-Output 'Установка не найдена — останавливать нечего.'
        $script:ExitCode = $script:EXIT_OK
        return
    }
    Invoke-Compose @('stop')
    Write-Output 'Приложение остановлено. Данные сохранены.'
    $script:ExitCode = $script:EXIT_OK
}

function Invoke-Status {
    $result = @{
        docker = @{ installed = $false; daemon = $false }
        compose = @{ version = '' }
        app = @{ state = 'stopped'; url = "http://127.0.0.1:$($script:HttpPort)" }
        database = @{ state = 'unknown' }
        migrations = @{ ok = $null; current = $null; expected = $null }
        worker = @{ state = 'unknown' }
        backup = @{ state = 'unknown' }
        release = @{ installed = ''; running = ''; mismatch = $false }
        integrations = @{ telegram = $false; smtp = $false }
    }
    if ($null -ne (Get-DockerVersion)) { $result.docker.installed = $true }
    $result.docker.daemon = Test-DockerDaemon
    $result.compose.version = [string](Get-ComposeVersion)

    $installed = ''
    $state = Read-State
    if ($null -ne $state) { $installed = [string]$state.installed_release_sha }
    $result.release.installed = $installed

    $ops = Invoke-OpsStatus
    if ($null -eq $ops) {
        $result.app.state = if ($result.docker.daemon) { 'starting' } else { 'stopped' }
    } else {
        $result.app.state = if ($ops.status -eq 'ok') { 'ready' } else { 'degraded' }
        if ($null -ne $ops.database) { $result.database.state = [string]$ops.database.status }
        if ($null -ne $ops.migrations) {
            $result.migrations.ok = $ops.migrations.ok
            $result.migrations.current = [string]$ops.migrations.current_revision
            $result.migrations.expected = [string]$ops.migrations.expected_revision
        }
        if ($null -ne $ops.notifications -and $null -ne $ops.notifications.worker) {
            $result.worker.state = [string]$ops.notifications.worker.status
        }
        if ($null -ne $ops.backup) {
            $result.backup.state = if ($ops.backup.available) { 'ok' } else { 'missing' }
            if ($ops.backup.available -and $ops.backup.ok -eq $false) { $result.backup.state = 'stale' }
        }
        $result.release.running = [string]$ops.release_sha
        if ($installed -and $ops.release_sha -and $installed -ne $ops.release_sha) {
            $result.release.mismatch = $true
        }
    }

    $json = ConvertTo-Json -InputObject $result -Depth 6 -Compress
    Write-Output $json
    $script:ExitCode = $script:EXIT_OK
}

function Invoke-Update {
    $errors = Invoke-Preflight
    if ($errors.Count -gt 0) {
        foreach ($e in $errors) { Write-UserError $e }
        $script:ExitCode = $script:EXIT_PREFLIGHT
        return
    }
    if (-not (Test-Path -LiteralPath $script:EnvFile)) {
        Write-UserError 'Установка не найдена — обновлять нечего.'
        $script:ExitCode = $script:EXIT_STATE_FAIL
        return
    }
    if (-not $TargetRelease -or -not (Test-Path -LiteralPath $TargetRelease)) {
        Write-UserError 'Укажите существующий локальный release-каталог (-TargetRelease).'
        $script:ExitCode = $script:EXIT_UPDATE_BAD
        return
    }

    $lockFile = Join-Path $script:StateDir 'update.lock'
    if (Test-Path -LiteralPath $lockFile) {
        Write-UserError 'Обновление уже выполняется. Подождите его завершения.'
        $script:ExitCode = $script:EXIT_UPDATE_LOCKED
        return
    }
    Set-Content -LiteralPath $lockFile -Value ([DateTime]::UtcNow.ToString('o'))
    try {
        $targetManifest = Join-Path $TargetRelease 'release-manifest.json'
        if (-not (Test-Path -LiteralPath $targetManifest)) {
            Write-UserError 'Целевой release не содержит манифест целостности.'
            $script:ExitCode = $script:EXIT_UPDATE_BAD
            return
        }
        $manifest = Read-JsonFile $targetManifest
        $targetSha = [string]$manifest.release_sha
        if (-not $targetSha) {
            Write-UserError 'Целевой release не содержит SHA.'
            $script:ExitCode = $script:EXIT_UPDATE_BAD
            return
        }

        Invoke-Compose @('run', '--rm', '--no-deps', '-e', "BACKUP_REASON=update $targetSha", 'backup', 'oneshot')
        Invoke-Compose @('run', '--rm', '--no-deps', 'backup', 'check')
        if (-not (Invoke-BackupHealthProbe)) {
            Write-UserError 'Резервная копия не подтверждена — обновление остановлено.'
            $script:ExitCode = $script:EXIT_BACKUP_FAIL
            return
        }

        Invoke-Compose @('run', '--rm', 'backend', 'alembic', 'upgrade', 'head')
        Invoke-Compose @('up', '-d', '--wait', '--wait-timeout', '300')

        if (-not (Invoke-HealthProbe)) {
            throw 'Smoke: health failed'
        }
        $ops = Invoke-OpsStatus
        if ($null -eq $ops) { throw 'Smoke: /ops/status failed' }
        if ($ops.migrations.ok -ne $true) { throw 'Smoke: migration drift' }
        if ($ops.release_sha -ne $targetSha) { throw 'Smoke: release SHA mismatch' }

        $state = Read-State
        if ($null -eq $state) { $state = [pscustomobject]@{} }
        $state | Add-Member -NotePropertyName installed_release_sha -NotePropertyValue $targetSha -Force
        $state | Add-Member -NotePropertyName updated_at -NotePropertyValue ([DateTime]::UtcNow.ToString('o')) -Force
        Write-State $state
        Write-Output "Обновление завершено: $targetSha"
        $script:ExitCode = $script:EXIT_OK
    } catch {
        # Возврат предыдущего кода (автоматический откат схемы БД запрещён).
        try {
            Invoke-Compose @('up', '-d', '--wait', '--wait-timeout', '300')
            if (Invoke-HealthProbe) {
                Write-Output 'Обновление не удалось. Предыдущая версия восстановлена, данные сохранены.'
                $script:ExitCode = $script:EXIT_SMOKE_FAIL
            } else {
                Write-UserError 'Обновление не удалось и восстановление не подтверждено. Откройте диагностику.'
                $script:ExitCode = $script:EXIT_ROLLBACK_FAIL
            }
        } catch {
            Write-UserError 'Обновление не удалось. Откройте диагностику.'
            $script:ExitCode = $script:EXIT_ROLLBACK_FAIL
        }
    } finally {
        Remove-Item -LiteralPath $lockFile -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-Diagnostics {
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add('HR Manager — диагностика (санитаризировано)')
    $lines.Add('ОС: ' + (Redact-Text ([string]$env:OS)))
    $lines.Add('PowerShell: ' + $PSVersionTable.PSVersion.ToString())
    $lines.Add('Docker: ' + (Redact-Text ([string](Get-DockerVersion))))
    $lines.Add('Compose: ' + (Redact-Text ([string](Get-ComposeVersion))))
    $lines.Add('Daemon: ' + (Test-DockerDaemon))
    $lines.Add('StateDir writable: ' + ([bool](Test-Path -LiteralPath $script:StateDir)))

    $ops = Invoke-OpsStatus
    if ($null -ne $ops) {
        $lines.Add('App status: ' + (Redact-Text ([string]$ops.status)))
        $lines.Add('Release: ' + (Redact-Text ([string]$ops.release_sha)))
        $lines.Add('Migrations ok: ' + ([string]$ops.migrations.ok))
        $lines.Add('Backup available: ' + ([string]$ops.backup.available))
    } else {
        $lines.Add('App status: недоступно')
    }

    try {
        $logs = & docker compose -f $script:ComposeFile -f $script:PilotOverlay -p hr-manager-pilot logs --tail 40 2>$null
        foreach ($l in $logs) { $lines.Add(Redact-Text ([string]$l)) }
    } catch { }

    $report = $lines -join [Environment]::NewLine
    $outFile = Join-Path $script:StateDir 'diagnostics.txt'
    New-Item -ItemType Directory -Path $script:StateDir -Force | Out-Null
    Set-Content -LiteralPath $outFile -Value $report -Encoding UTF8
    Write-Output $report
    $script:ExitCode = $script:EXIT_OK
}

function Invoke-Uninstall {
    param([switch]$RemoveData)
    if ($RemoveData) {
        if ($RemoveDataPhrase -ne 'УДАЛИТЬ ДАННЫЕ HR MANAGER') {
            Write-UserError 'Для удаления данных введите точную фразу подтверждения.'
            $script:ExitCode = $script:EXIT_USAGE
            return
        }
        if (Test-Path -LiteralPath $script:EnvFile) {
            Invoke-Compose @('run', '--rm', '--no-deps', '-e', 'BACKUP_REASON=перед удалением данных', 'backup', 'oneshot')
        }
    }
    if (Test-Path -LiteralPath $script:EnvFile) {
        Invoke-Compose @('down')
        if ($RemoveData) {
            Invoke-Compose @('down', '-v')
        }
    }
    if ($env:OS -eq 'Windows_NT') {
        $desktop = [Environment]::GetFolderPath('Desktop')
        Remove-Item (Join-Path $desktop 'HR Manager.lnk') -Force -ErrorAction SilentlyContinue
        Remove-Item (Join-Path $desktop 'Диагностика HR Manager.lnk') -Force -ErrorAction SilentlyContinue
    }
    Write-Output ('Удаление завершено. ' + $(if ($RemoveData) { 'Данные удалены.' } else { 'База данных и резервные копии сохранены.' }))
    $script:ExitCode = $script:EXIT_OK
}

# ---------------------------------------------------------------------------
# Main (выполняется только при прямом запуске, не при dot-source)
# ---------------------------------------------------------------------------
function Main {
    Resolve-Paths
    switch ($Action) {
        'install'      { Invoke-Install }
        'start'        { Invoke-Start }
        'stop'         { Invoke-Stop }
        'status'       { Invoke-Status }
        'update'       { Invoke-Update }
        'diagnostics'  { Invoke-Diagnostics }
        'uninstall'    { Invoke-Uninstall -RemoveData:([bool]$RemoveDataPhrase) }
        'check'        {
            $errors = Invoke-Preflight
            if ($errors.Count -gt 0) {
                foreach ($e in $errors) { Write-UserError $e }
                $script:ExitCode = $script:EXIT_PREFLIGHT
            } else {
                Write-Output 'Проверка пройдена.'
                $script:ExitCode = $script:EXIT_OK
            }
        }
        default {
            Write-UserError "Неизвестное действие: $Action"
            $script:ExitCode = $script:EXIT_USAGE
        }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        Main
        exit $script:ExitCode
    } catch {
        [Console]::Error.WriteLine((Redact-Text ([string]$_.Exception.Message)))
        exit $script:EXIT_INTERNAL
    }
}
