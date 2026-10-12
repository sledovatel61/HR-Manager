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
        # GitHub-аннотация: имя проваленного кейса + стек видны в check-runs
        # даже когда лог-приёмник недоступен.
        $stack = $_.ScriptStackTrace
        if (-not $stack) { $stack = "(без стектрейса)" }
        # Компактный стектрейс кладём В САМ СПИСОК провалов: в аннотации
        # ::error:: помещается только 10 записей на шаг, а notice-список через
        # API виден целиком (логи и артефакты скачать из CI нельзя).
        $shortStack = ($stack -replace "[`r`n]+", " | ")
        if ($shortStack.Length -gt 400) { $shortStack = $shortStack.Substring(0, 400) }
        $global:HRM_TestFailures += ($msg + " || " + $shortStack)
        Write-Host ("  [FAIL] $Name : {0}" -f $_.Exception.Message) -ForegroundColor Red
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

function Assert-HrmContainsRedacted {
    # Маркер редакции в JSON-файлах: ConvertTo-Json (Windows PowerShell 5.1)
    # экранирует ` < ` и ` > ` как \u003c/\u003e, поэтому проверяем и литерал, и
    # экранированную форму — читатель JSON в обоих случаях видит <redacted>.
    param([string]$Text, [string]$Message = "нет маркера редакции")
    if ([string]::IsNullOrEmpty($Text)) { throw $Message }
    $unescaped = [regex]::Replace($Text, "\\u003c", "<", "IgnoreCase")
    $unescaped = [regex]::Replace($unescaped, "\\u003e", ">", "IgnoreCase")
    Assert-HrmContains $unescaped "<redacted>" $Message
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
    foreach ($module in @("Common", "Secrets", "Preflight", "Compose", "Bootstrap", "Update",
            "Diagnostics", "Install", "Snapshot", "Crypto", "Channel", "Lan", "SupportBundle",
            "Docker", "Supervisor", "Tray")) {
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
    Clear-HrmDockerOverride
    Clear-HrmDownloadMock
    Clear-HrmProcessLaunchMock
    Reset-HrmRedaction
    Remove-Item Env:HRM_AUTOSTART_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:HRM_AUTOSTART_MOCK -ErrorAction SilentlyContinue
    Remove-Item Env:HRM_DESKTOP_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:HRM_LOG_FILE -ErrorAction SilentlyContinue
    Remove-Item Env:HRM_SOURCE_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:HRM_SUPERVISOR_MUTEX -ErrorAction SilentlyContinue
    # Публичный ключ лицензии по умолчанию (валидный base64 от 32 байт):
    # Write-HrmPilotEnv и Assert-HrmLicensePublicKey fail-closed без ключа, а
    # большинству тестов ключ не важен. Тесты «ключа нет ни в одном источнике»
    # удаляют переменную явно (см. engine.tests.ps1, T2/T7.4).
    $env:HRM_LICENSE_PUBLIC_KEY = [Convert]::ToBase64String([byte[]]::new(32))
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
    # Лицензионный публичный ключ — внешний файл snapshot (копируется установщиком в StateDir)
    $licDir = Join-Path $infra "license"
    New-Item -ItemType Directory -Path $licDir -Force | Out-Null
    $pubBytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($pubBytes)
    $pubB64 = [Convert]::ToBase64String($pubBytes)
    Set-Content -Path (Join-Path $licDir "public_key.b64") -Value $pubB64 -Encoding UTF8 -NoNewline
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
        RemovedVolumes = @()
        DownOk = $true
        BackupNowOk = $true
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
        # 0.15.0: пилотный supervisor/Docker
        PgVolumeMissing = $false
        AlembicHeads = "0013"
        ContainersUp = $true
        WslAvailable = $true
        SuspendEngine = $false
        ContainersJson = ""
        UpFails = $false
        UpCount = 0
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
                if ($global:HRM_MockWorld.SuspendEngine) {
                    return [pscustomobject]@{ Name = $Name; ExitCode = 1; Stdout = ""; Stderr = "engine is not running" }
                }
                if (($Arguments -join " ") -match "OSType") {
                    # Linux-движок по умолчанию: стек HR Manager — Linux-контейнеры
                    # (Test-HrmDockerLinuxEngineReady опрашивает OSType).
                    return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = "linux"; Stderr = "" }
                }
                return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = "27.3.1"; Stderr = "" }
            }
            if ($Arguments.Count -ge 2 -and $Arguments[0] -eq "compose" -and ($Arguments -contains "version")) {
                return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = $global:HRM_MockWorld.ComposeVersion; Stderr = "" }
            }
            if ($Arguments.Count -ge 3 -and $Arguments[0] -eq "compose" -and ($Arguments -contains "config")) {
                return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = ""; Stderr = "" }
            }
            if ($Arguments.Count -ge 3 -and $Arguments[0] -eq "compose" -and ($Arguments -contains "ps")) {
                if ($global:HRM_MockWorld.ContainersJson) {
                    return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = $global:HRM_MockWorld.ContainersJson; Stderr = "" }
                }
                if ($global:HRM_MockWorld.Running) {
                    return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = '[{"Name":"backend","State":"running"},{"Name":"frontend","State":"running"}]'; Stderr = "" }
                }
                return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = "[]"; Stderr = "" }
            }
            if ($Arguments.Count -ge 2 -and $Arguments[0] -eq "compose" -and ($Arguments -contains "up")) {
                $global:HRM_MockWorld.UpCount++
                if ($global:HRM_MockWorld.UpFails) {
                    return [pscustomobject]@{ Name = $Name; ExitCode = 1; Stdout = ""; Stderr = "container failed to start" }
                }
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
                if (-not $global:HRM_MockWorld.DownOk) {
                    return [pscustomobject]@{ Name = $Name; ExitCode = 1; Stdout = ""; Stderr = "down failed" }
                }
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
                if ($joined -match "alembic heads") {
                    return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = $global:HRM_MockWorld.AlembicHeads; Stderr = "" }
                }
                if ($joined -match "worker-check") {
                    if ($global:HRM_MockWorld.WorkerCheckOk) {
                        return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = "worker ok"; Stderr = "" }
                    }
                    return [pscustomobject]@{ Name = $Name; ExitCode = 1; Stdout = ""; Stderr = "worker stale" }
                }
            }
            if ($Arguments.Count -ge 3 -and $Arguments[0] -eq "compose" -and ($Arguments -contains "logs")) {
                # Container logs for support-bundle: return redacted-ish dummy logs
                $svc = $Arguments[-1]
                $dummy = "[$svc] dummy log line 1`n[$svc] dummy log line 2 with secret SHOULD_BE_REDACTED"
                return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = $dummy; Stderr = "" }
            }
            if ($Arguments.Count -ge 2 -and $Arguments[0] -eq "images") {
                # For Get-HrmPreviousImagePresent and host report
                return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = "hr-manager-pilot-backend:pilot`nhr-manager-pilot-frontend:pilot"; Stderr = "" }
            }
            if ($Arguments.Count -ge 3 -and $Arguments[0] -eq "compose" -and ($Arguments -contains "run")) {
                $joined = ($Arguments -join " ")
                if ($joined -match "backup-now|\bbackup oneshot\b") {
                    $global:HRM_MockWorld.BackupNowCount++
                    if (-not $global:HRM_MockWorld.BackupNowOk) {
                        return [pscustomobject]@{ Name = $Name; ExitCode = 1; Stdout = ""; Stderr = "backup failed" }
                    }
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
            if ($Arguments.Count -ge 2 -and $Arguments[0] -eq "volume" -and $Arguments[1] -eq "inspect") {
                $volumeName = ""
                if ($Arguments.Count -ge 3) { $volumeName = [string]$Arguments[2] }
                if ($volumeName -eq "hr-manager-pilot_pilot_pgdata" -and $global:HRM_MockWorld.PgVolumeMissing) {
                    return [pscustomobject]@{ Name = $Name; ExitCode = 1; Stdout = ""; Stderr = "no such volume" }
                }
                # ВАЖНО: ответ собирается ConvertTo-Json, а НЕ оператором -f.
                # Для оператора -f .NET-строка форматирования разбирает фигурные
                # скобки как placeholder, поэтому JSON-шаблон с фигурными скобками
                # падал с "Input string was not in a correct format" — мок отвечал
                # ошибкой, и обновление считалось сломанным (rollback) при живых
                # данных. Контракт guard-теста статики: JSON собираем ConvertTo-Json.
                $volumeJson = ConvertTo-Json -InputObject ([pscustomobject]@{ Name = $volumeName }) -Compress
                return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = $volumeJson; Stderr = "" }
            }
            if ($Arguments.Count -eq 3 -and $Arguments[0] -eq "volume" -and $Arguments[1] -eq "rm") {
                $global:HRM_MockWorld.RemovedVolumes += $Arguments[2]
                return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = "removed"; Stderr = "" }
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
        if ($Name -eq "netsh.exe") {
            # Firewall rule: just record, succeed
            return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = "Ok."; Stderr = "" }
        }
        if ($Name -eq "git.exe") {
            return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = $global:HRM_MockWorld.ReleaseSha; Stderr = "" }
        }
        if ($Name -eq "wsl.exe") {
            if (-not $global:HRM_MockWorld.WslAvailable) {
                return [pscustomobject]@{ Name = $Name; ExitCode = 1; Stdout = ""; Stderr = "WSL is not installed" }
            }
            return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = "  NAME      STATE           VERSION`n* Ubuntu    Running         2"; Stderr = "" }
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
        if ($Uri -like "*/api/license/status") {
            return @{ StatusCode = 200; Body = [pscustomobject]@{ has_license = $true; is_valid = $true; license = [pscustomobject]@{ expires_at = "2026-12-31"; max_active_users = 5; client_name = "Пилот Марии" } } }
        }
        if ($Uri -like "*/api/admin/ops/pilot-readiness") {
            return @{ StatusCode = 200; Body = [pscustomobject]@{ verdict = "готово" } }
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
