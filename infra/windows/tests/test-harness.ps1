# Обвязка тестов движка (без зависимости от Pester): мини-DSL, загрузка
# модулей с моками, мок-мир внешних команд и HTTP. Запускается на Windows
# PowerShell 5.1+ и pwsh: `powershell -File run-tests.ps1`.

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$global:HRM_TestPassed = 0
$global:HRM_TestFailed = 0
$global:HRM_TestFailures = @()

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    try {
        & $Body
        $global:HRM_TestPassed++
        Write-Host "  [PASS] $Name"
    }
    catch {
        $global:HRM_TestFailed++
        $msg = ("{0}: {1}" -f $Name, $_.Exception.Message)
        $global:HRM_TestFailures += $msg
        Write-Host ("  [FAIL] $Name : {0}" -f $_.Exception.Message) -ForegroundColor Red
        # GitHub-аннотация: имя проваленного кейса + стек видны в check-runs
        # даже когда лог-приёмник недоступен.
        $stack = $_.ScriptStackTrace
        if (-not $stack) { $stack = "(без стектрейса)" }
        $flat = ($msg + " || " + $stack) -replace "[`r`n]+", " | "
        $title = $Name -replace "[`r`n:]+", " "
        Write-Host ("::error title={0}::{1}" -f $title, $flat)
    }
}

function Assert-HrmTrue {
    param([bool]$Condition, [string]$Message = "ожидалось истинное значение")
    if (-not $Condition) { throw $Message }
}

function Assert-HrmFalse {
    param([bool]$Condition, [string]$Message = "ожидалось ложное значение")
    if ($Condition) { throw $Message }
}

function Assert-HrmEqual {
    param($Expected, $Actual, [string]$Message = "значения не совпадают")
    if ("$Expected" -cne "$Actual") {
        throw ("{0} (ожидалось '{1}', получено '{2}')" -f $Message, $Expected, $Actual)
    }
}

function Assert-HrmContains {
    param([string]$Haystack, [string]$Needle, [string]$Message = "подстрока не найдена")
    if ([string]::IsNullOrEmpty($Haystack) -or -not $Haystack.Contains($Needle)) { throw $Message }
}

function Assert-HrmNotContains {
    param([string]$Haystack, [string]$Needle, [string]$Message = "запрещённая подстрока найдена")
    if (-not [string]::IsNullOrEmpty($Haystack) -and $Haystack.Contains($Needle)) { throw $Message }
}

function Assert-HrmThrows {
    param([string]$Message = "ожидалось исключение", [scriptblock]$Body)
    try {
        & $Body | Out-Null
    }
    catch {
        return
    }
    throw $Message
}

# --- Загрузка движка с изоляцией --------------------------------------------

function Initialize-HrmTestEngine {
    # Перезагружает модули движка, включает неинтерактивный режим и
    # изолированные каталоги (с пробелами и кириллицей — проверка контракта).
    param([string]$TestRoot = "")
    if (-not $TestRoot) {
        $TestRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM тест движка " + [System.Guid]::NewGuid().ToString("N").Substring(0, 8))
    }
    $engineDir = Join-Path $PSScriptRoot "..\engine"
    foreach ($module in @("Common", "Secrets", "Preflight", "Compose", "Bootstrap", "Update", "Diagnostics", "Install", "Crypto", "Channel")) {
        Import-Module (Join-Path $engineDir "$module.psm1") -Force -ErrorAction Stop
    }
    $env:HRM_NONINTERACTIVE = "1"
    $env:HRM_STATE_DIR = Join-Path $TestRoot "state каталог"
    $env:HRM_INSTALL_DIR = Join-Path $TestRoot "install каталог"
    $env:HRM_CLAIM_RETRY_SECONDS = "0"
    $global:HrmNonInteractive = $true
    $global:HrmOpenBrowser = $false
    Remove-Item Env:HRM_PURGE_CONFIRMATION -ErrorAction SilentlyContinue
    Remove-Item Env:HRM_PILOT_PORT -ErrorAction SilentlyContinue
    Clear-HrmExternalMock
    Clear-HrmHttpMock
    Clear-HrmPreflightOverride
    Reset-HrmRedaction
    New-Item -ItemType Directory -Path $env:HRM_STATE_DIR -Force | Out-Null
    New-Item -ItemType Directory -Path $env:HRM_INSTALL_DIR -Force | Out-Null
}

function Get-HrmTestStateDir { return $env:HRM_STATE_DIR }
function Get-HrmTestInstallDir { return $env:HRM_INSTALL_DIR }

function New-HrmFakeSnapshot {
    # Фальшивый «снимок релиза» для установки: infra/compose.pilot.yml берём
    # настоящий из репозитория (тест оверлея живёт отдельно), остальное — заглушки.
    param([string]$Root, [string]$ReleaseSha = "snapshot-sha-0013")
    $infra = Join-Path $Root "infra"
    New-Item -ItemType Directory -Path $infra -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $Root "backend") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $Root "frontend") -Force | Out-Null
    $repoRoot = Join-Path $PSScriptRoot "..\..\.."
    Copy-Item (Join-Path $repoRoot "infra\compose.pilot.yml") (Join-Path $infra "compose.pilot.yml") -Force
    Set-Content -Path (Join-Path $Root "backend\marker.txt") -Value "v1"
    (@{ release_sha = $ReleaseSha; version = "1.0.0" } | ConvertTo-Json) |
        Set-Content -Path (Join-Path $Root "release.json") -Encoding UTF8
}

# --- Мок-мир -----------------------------------------------------------------

function New-HrmMockWorld {
    # Возвращает состояние моков и регистрирует моки внешних команд и HTTP.
    param([string]$ReleaseSha = "snapshot-sha-0013")
    $world = [pscustomobject]@{
        Calls = @()
        HttpCalls = @()
        IcaclsArgs = @()
        Running = $false
        DockerMissing = $false
        ComposeVersion = "v2.29.7"
        AlembicCurrent = "0013 (head)"
        AlembicUpgradeCount = 0
        BackupNowCount = 0
        BackupCheckOk = $true
        WorkerCheckOk = $true
        ClaimResponses = @()
        ClaimCount = 0
        ReleaseSha = $ReleaseSha
        OpsStatusCode = 200
        OpsBody = $null
        BackupHealthStatusCode = 200
        BackupHealthBody = $null
        FrontendStatus = 200
        BackendStatus = 200
        TagCount = 0
        BuildCount = 0
        SimulateStaleRelease = $false
    }
    $world.OpsBody = [pscustomobject]@{
        status = "ok"
        release_sha = $ReleaseSha
        version = "0.13.0"
        environment = "pilot"
        database = [pscustomobject]@{ status = "ok"; latency_ms = 2 }
        migrations = [pscustomobject]@{ current_revision = "0013"; expected_revision = "0013"; ok = $true }
        backup = [pscustomobject]@{ available = $true; ok = $true; last_backup_at = "2026-09-09T00:00:00Z"; age_seconds = 3600 }
        notifications = [pscustomobject]@{ worker = [pscustomobject]@{ alive = $true; last_seen_at = "2026-09-09T00:00:00Z" } }
    }
    $world.BackupHealthBody = [pscustomobject]@{ status = "ok"; fresh = $true; age_seconds = 3600; last_backup_at = "2026-09-09T00:00:00Z" }

    # Скриптблоки моков вызываются из модуля движка ПОСЛЕ возврата из этой
    # функции, поэтому $world должен быть в глобальной области видимости.
    $global:HRM_MockWorld = $world

    Set-HrmExternalMock {
        param($Name, $Arguments, $Stdin, $IgnoreExitCode)
        $global:HRM_MockWorld.Calls += [pscustomobject]@{ Name = $Name; Args = @($Arguments); Stdin = $Stdin }
        if ($Name -eq "docker.exe" -or $Name -eq "docker") {
            if ($global:HRM_MockWorld.DockerMissing) {
                return [pscustomobject]@{ Name = $Name; ExitCode = 1; Stdout = ""; Stderr = "'docker' is not recognized" }
            }
            if ($Arguments.Count -ge 1 -and $Arguments[0] -eq "--version") {
                return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = "Docker version 27.3.1, build ce12230"; Stderr = "" }
            }
            if ($Arguments.Count -ge 1 -and $Arguments[0] -eq "info") {
                return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = "27.3.1"; Stderr = "" }
            }
            if ($Arguments.Count -ge 2 -and $Arguments[0] -eq "compose" -and ($Arguments -contains "version")) {
                return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = $global:HRM_MockWorld.ComposeVersion; Stderr = "" }
            }
            if ($Arguments.Count -ge 3 -and $Arguments[0] -eq "compose" -and ($Arguments -contains "config")) {
                return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = ""; Stderr = "" }
            }
            if ($Arguments.Count -ge 3 -and $Arguments[0] -eq "compose" -and ($Arguments -contains "ps")) {
                if ($global:HRM_MockWorld.Running) {
                    return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = '[{"Name":"backend","State":"running"},{"Name":"frontend","State":"running"}]'; Stderr = "" }
                }
                return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = "[]"; Stderr = "" }
            }
            if ($Arguments.Count -ge 2 -and $Arguments[0] -eq "compose" -and ($Arguments -contains "up")) {
                $global:HRM_MockWorld.Running = $true
                if (-not $global:HRM_MockWorld.SimulateStaleRelease) {
                    $envIndex = [array]::IndexOf([object[]]$Arguments, "--env-file")
                    if ($envIndex -ge 0 -and $envIndex + 1 -lt $Arguments.Count -and (Test-Path $Arguments[$envIndex + 1])) {
                        $releaseLine = Get-Content $Arguments[$envIndex + 1] | Where-Object { $_ -like "HRM_RELEASE_SHA=*" } | Select-Object -First 1
                        if ($releaseLine) {
                            $global:HRM_MockWorld.OpsBody.release_sha = ($releaseLine -replace '^HRM_RELEASE_SHA=', '').Trim()
                        }
                    }
                }
                return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = "started"; Stderr = "" }
            }
            if ($Arguments.Count -ge 2 -and $Arguments[0] -eq "compose" -and ($Arguments -contains "down")) {
                $global:HRM_MockWorld.Running = $false
                return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = "stopped"; Stderr = "" }
            }
            if ($Arguments.Count -ge 2 -and $Arguments[0] -eq "compose" -and ($Arguments -contains "build")) {
                $global:HRM_MockWorld.BuildCount++
                return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = "built"; Stderr = "" }
            }
            if ($Arguments.Count -ge 3 -and $Arguments[0] -eq "compose" -and ($Arguments -contains "exec")) {
                $joined = ($Arguments -join " ")
                if ($joined -match "alembic current") {
                    return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = $global:HRM_MockWorld.AlembicCurrent; Stderr = "" }
                }
                if ($joined -match "alembic upgrade") {
                    $global:HRM_MockWorld.AlembicUpgradeCount++
                    if (-not $global:HRM_MockWorld.SimulateStaleRelease) {
                        $envIndex = [array]::IndexOf([object[]]$Arguments, "--env-file")
                        if ($envIndex -ge 0 -and $envIndex + 1 -lt $Arguments.Count -and (Test-Path $Arguments[$envIndex + 1])) {
                            $releaseLine = Get-Content $Arguments[$envIndex + 1] | Where-Object { $_ -like "HRM_RELEASE_SHA=*" } | Select-Object -First 1
                            if ($releaseLine) {
                                $global:HRM_MockWorld.OpsBody.release_sha = ($releaseLine -replace '^HRM_RELEASE_SHA=', '').Trim()
                            }
                        }
                    }
                    return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = "INFO [alembic.runtime.migration] Running upgrade -> 0013"; Stderr = "" }
                }
                if ($joined -match "worker-check") {
                    if ($global:HRM_MockWorld.WorkerCheckOk) {
                        return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = "worker ok"; Stderr = "" }
                    }
                    return [pscustomobject]@{ Name = $Name; ExitCode = 1; Stdout = ""; Stderr = "worker stale" }
                }
            }
            if ($Arguments.Count -ge 3 -and $Arguments[0] -eq "compose" -and ($Arguments -contains "run")) {
                $joined = ($Arguments -join " ")
                if ($joined -match "backup-now|\bbackup oneshot\b") {
                    $global:HRM_MockWorld.BackupNowCount++
                    return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = "backup ok"; Stderr = "" }
                }
                if ($joined -match "backup-check|\bbackup check\b") {
                    if ($global:HRM_MockWorld.BackupCheckOk) {
                        return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = "integrity ok"; Stderr = "" }
                    }
                    return [pscustomobject]@{ Name = $Name; ExitCode = 1; Stdout = ""; Stderr = "integrity check failed" }
                }
                if ($joined -match "backup-list") {
                    return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = "2026-09-09T01:00:00Z ok abc.enc"; Stderr = "" }
                }
            }
            if ($Arguments.Count -ge 2 -and $Arguments[0] -eq "image" -and $Arguments[1] -eq "inspect") {
                return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = "sha256:old-image-id"; Stderr = "" }
            }
            if ($Arguments.Count -ge 2 -and $Arguments[0] -eq "tag") {
                $global:HRM_MockWorld.TagCount++
                return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = ""; Stderr = "" }
            }
        }
        if ($Name -eq "icacls.exe") {
            $global:HRM_MockWorld.IcaclsArgs += , @($Arguments)
            return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = ""; Stderr = "" }
        }
        if ($Name -eq "git.exe") {
            return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = $global:HRM_MockWorld.ReleaseSha; Stderr = "" }
        }
        return [pscustomobject]@{ Name = $Name; ExitCode = 1; Stdout = ""; Stderr = "unexpected command: $Name $($Arguments -join ' ')" }
    }

    Set-HrmHttpMock {
        param($Uri, $Method, $Body, $Headers)
        $global:HRM_MockWorld.HttpCalls += [pscustomobject]@{ Uri = $Uri; Method = $Method; Body = $Body; Headers = $Headers }
        if ($Uri -like "*/api/health") {
            return @{ StatusCode = $global:HRM_MockWorld.BackendStatus; Body = [pscustomobject]@{ status = "ok" } }
        }
        if ($Uri -like "*/api/setup/owner/claim") {
            $global:HRM_MockWorld.ClaimCount++
            if ($global:HRM_MockWorld.ClaimResponses.Count -gt 0) {
                $response = $global:HRM_MockWorld.ClaimResponses[0]
                $global:HRM_MockWorld.ClaimResponses = @($global:HRM_MockWorld.ClaimResponses | Select-Object -Skip 1)
                return $response
            }
            return @{ StatusCode = 201; Body = [pscustomobject]@{ ticket = "ticket-test-123" } }
        }
        if ($Uri -like "*/api/ops/status") {
            return @{ StatusCode = $global:HRM_MockWorld.OpsStatusCode; Body = $global:HRM_MockWorld.OpsBody }
        }
        if ($Uri -like "*/api/ops/backup-health") {
            return @{ StatusCode = $global:HRM_MockWorld.BackupHealthStatusCode; Body = $global:HRM_MockWorld.BackupHealthBody }
        }
        # Фронтенд
        return @{ StatusCode = $global:HRM_MockWorld.FrontendStatus; Body = "ok" }
    }

    return $world
}

function Get-HrmWorldCallArgs {
    # Аргументы всех вызовов docker/icacls — для проверки «секретов нет в командной строке».
    param($World)
    $all = @()
    foreach ($call in $World.Calls) { $all += $call.Args }
    return $all
}
