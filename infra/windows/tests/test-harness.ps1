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
    # Циклы ожидания не спят реальные секунды: интервал 0, а число попыток в
    # самих проверках ограничено (см. Get-HrmDbSchemaRevision,
    # Start-HrmDbForSchemaCheck). Иначе мок-мир заставлял бы CI ждать минутами.
    $env:HRM_WAIT_INTERVAL_SECONDS = "0"
    $global:HrmNonInteractive = $true
    $global:HrmOpenBrowser = $false
    Remove-Item Env:HRM_PURGE_CONFIRMATION -ErrorAction SilentlyContinue
    Remove-Item Env:HRM_PILOT_PORT -ErrorAction SilentlyContinue
    Clear-HrmExternalMock
    Clear-HrmHttpMock
    Clear-HrmPreflightOverride
    Clear-HrmDockerOverride
    Clear-HrmProcessLaunchMock
    Clear-HrmProcessStartMock
    Reset-HrmRedaction
    Remove-Item Env:HRM_AUTOSTART_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:HRM_AUTOSTART_MOCK -ErrorAction SilentlyContinue
    Remove-Item Env:HRM_DESKTOP_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:HRM_LOG_FILE -ErrorAction SilentlyContinue
    Remove-Item Env:HRM_SOURCE_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:HRM_SUPERVISOR_MUTEX -ErrorAction SilentlyContinue
    # Швы ревизии стабильности: таймаут внешних команд, мьютекс единственности
    # цикла движка и ограничение числа циклов наблюдателя канала. Без сброса
    # значение из предыдущего теста утёкло бы в следующее.
    Remove-Item Env:HRM_EXTERNAL_TIMEOUT_SECONDS -ErrorAction SilentlyContinue
    Remove-Item Env:HRM_ENGINE_CYCLE_MUTEX -ErrorAction SilentlyContinue
    Remove-Item Env:HRM_CHANNEL_WATCH_MAX_CYCLES -ErrorAction SilentlyContinue
    # Мьютекс единственности цикла движка — уникальное имя на каждый кейс: как и
    # HRM_SUPERVISOR_MUTEX в supervisor.tests.ps1, тест не должен зависеть от
    # живых процессов машины (мьютекс Local\… может держать установка владельца).
    # Тесты, которым нужно «имя известно обоим процессам», задают его явно.
    $env:HRM_ENGINE_CYCLE_MUTEX = "Local\HRManagerPilotEngineCycleTest-" + [guid]::NewGuid().ToString("N")
    # Публичный ключ лицензии по умолчанию (валидный base64 от 32 байт):
    # Write-HrmPilotEnv и Assert-HrmLicensePublicKey fail-closed без ключа, а
    # большинству тестов ключ не важен. Тесты «ключа нет ни в одном источнике»
    # удаляют переменную явно (см. engine.tests.ps1, T2/T7.4).
    $env:HRM_LICENSE_PUBLIC_KEY = [Convert]::ToBase64String([byte[]]::new(32))
    # База переопределений Docker:
    #  - порт публикации считается свободным: реальный Get-NetTCPConnection на
    #    машине CI делал бы результат установки недетерминированным (занятый порт
    #    8080 подменил бы его на свободный). Тесты занятого порта включают
    #    port_free=false и задают free_port явно;
    #  - путь CLI контейнеров пиннится голым именем: движок обязан резолвить CLI
    #    (Invoke-HrmDocker), а «настоящий» путь с раннера сделал бы тесты
    #    зависимыми от того, установлен ли там Docker.
    # База живёт отдельно от набора конкретного теста (Set-HrmDockerOverride
    # заменяет набор целиком, но сливает его с базой — см. Docker.psm1), иначе
    # тесты, задающие свои ключи, теряли бы эти договорённости.
    Set-HrmDockerOverrideDefaults @{ port_free = $true; cli_path = "docker.exe" }
    Set-HrmDockerOverride @{}
    # Кэш выбора CLI контейнеров (регрессия D6) сбрасывается между кейсами:
    # путь, выбранный на файлах раннера в предыдущем тесте, не должен утекать в
    # следующий (там переопределение cli_path может быть другим).
    Reset-HrmDockerCliChoiceCache | Out-Null
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

function Get-HrmMockAlembicHistory {
    # Текст `alembic history` ИЗ ОБРАЗА: от головы до базовой ревизии
    # (формат совпадает с реальным выводом alembic: «<down> -> <rev>»).
    param([string]$Head = "0013")
    $lines = @()
    $headNumber = 0
    [void][int]::TryParse($Head, [ref]$headNumber)
    if ($headNumber -le 0) { return ($Head + " (head)`n<base> -> " + $Head) }
    for ($n = $headNumber; $n -ge 1; $n--) {
        $rev = "{0:d4}" -f $n
        $down = "<base>"
        if ($n -gt 1) { $down = "{0:d4}" -f ($n - 1) }
        $mark = ""
        if ($n -eq $headNumber) { $mark = " (head)" }
        $lines += ("{0} -> {1}{2}, migration {1}" -f $down, $rev, $mark)
    }
    return ($lines -join "`n")
}

function Get-HrmMockUriPort {
    # Порт из URI loopback-запроса ("" — не удалось разобрать).
    param([string]$Uri)
    $match = [regex]::Match([string]$Uri, "://[^:/]+:(\d+)")
    if ($match.Success) { return $match.Groups[1].Value }
    return ""
}

function Get-HrmMockCliName {
    # Базовое имя внешней команды для мок-мира: движок вызывает CLI контейнеров
    # РАЗРЕШЁННЫМ путём (например C:\Users\…\DockerDesktop\resources\bin\docker.exe),
    # поэтому моки сопоставляются по имени файла — иначе перестали бы совпадать
    # и голое имя из пина харнесса, и полный путь реальной установки.
    param([string]$Name)
    if (-not $Name) { return "" }
    try { return [System.IO.Path]::GetFileName($Name) } catch { return $Name }
}

function New-HrmFakeExternalProcess {
    # Двойник процесса внешней команды для проверки МЕХАНИЗМА запуска
    # (Invoke-HrmExternal + Set-HrmProcessStartMock): таймаут, остановка дерева,
    # чтение труб, код возврата. Настоящие программы не запускаются.
    #
    # Контракт двойника — ровно то, что использует Invoke-HrmExternal:
    #   StandardInput  → StreamWriter (WriteAsync/Close),
    #   StandardOutput → StreamReader (ReadToEndAsync → Task),
    #   StandardError  → StreamReader,
    #   WaitForExit([int]) → bool, Kill(), Dispose(), Id, ExitCode, HasExited.
    # Состояние (число ожиданий, факт Kill/Dispose) доступно через .State —
    # по нему тест доказывает, что зависший процесс действительно остановлен.
    param(
        [int]$ProcessId = 424242,
        [string]$Stdout = "",
        [string]$Stderr = "",
        [bool]$Hangs = $true,
        [int]$ExitCode = 0
    )
    $state = @{ exited = (-not $Hangs); killed = $false; waitCalls = 0; disposed = $false }
    $outStream = New-Object System.IO.MemoryStream
    $errStream = New-Object System.IO.MemoryStream
    $inStream = New-Object System.IO.MemoryStream
    if ($Stdout) {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Stdout)
        $outStream.Write($bytes, 0, $bytes.Length)
    }
    if ($Stderr) {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Stderr)
        $errStream.Write($bytes, 0, $bytes.Length)
    }
    $outStream.Position = 0
    $errStream.Position = 0
    $process = [pscustomobject]@{
        Id = $ProcessId
        StandardInput = (New-Object System.IO.StreamWriter($inStream))
        StandardOutput = (New-Object System.IO.StreamReader($outStream))
        StandardError = (New-Object System.IO.StreamReader($errStream))
        State = $state
    }
    $process | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value {
        param([int]$Milliseconds = -1)
        $this.State.waitCalls = [int]$this.State.waitCalls + 1
        if ($this.State.killed) { $this.State.exited = $true }
        if (-not $this.State.exited -and [int]$this.State.waitCalls -eq 1) {
            # Пауза на первом ожидании: асинхронное чтение труб двойника успевает
            # завершиться, и проверка «частичный вывод сохранён при таймауте» не
            # зависит от гонки планировщика задач.
            Start-Sleep -Milliseconds 50
        }
        return [bool]$this.State.exited
    } -Force
    $process | Add-Member -MemberType ScriptMethod -Name Kill -Value {
        $this.State.killed = $true
        $this.State.exited = $true
    } -Force
    $process | Add-Member -MemberType ScriptMethod -Name Dispose -Value {
        $this.State.disposed = $true
    } -Force
    $process | Add-Member -MemberType ScriptProperty -Name HasExited -Value {
        return [bool]$this.State.exited
    } -Force
    # ExitCode зависит от параметра функции-создателя → замыкание.
    $process | Add-Member -MemberType ScriptProperty -Name ExitCode -Value ({
            return [int]$ExitCode
        }.GetNewClosure()) -Force
    return $process
}

function New-HrmMockWorld {
    # Возвращает состояние моков и регистрирует моки внешних команд и HTTP.
    param([string]$ReleaseSha = "snapshot-sha-0013")
    $world = [pscustomobject]@{
        Calls = @()
        HttpCalls = @()
        IcaclsArgs = @()
        # Аргументы вызовов остановки дерева процессов (taskkill /PID <pid> /T /F):
        # по ним тест таймаута доказывает, что движок убил именно ДЕРЕВО.
        TaskkillArgs = @()
        Running = $false
        DockerMissing = $false
        # Плагин compose отсутствует: CLI отвечает как docker без плагина
        # («'compose' is not a docker command») — сценарий пилотной машины,
        # где docker --version и docker info работают, а compose нет.
        ComposePluginMissing = $false
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
        # Метка образа hrmanager.release_sha (ставится сборкой из HRM_RELEASE_SHA
        # env-файла, как в реальном `compose build` с build.labels).
        ImageReleaseSha = ""
        # Окружение, которое сообщает /api/health и /api/ops/status.
        HealthEnvironment = "pilot"
        # Порты, на которых отвечает ЧУЖОЕ приложение (например, старый
        # development-стек на 8080): ключ — порт (строка), значение — окружение.
        # Нужно, чтобы «нужное приложение на нужном порту» проверялось по-настоящему.
        ForeignPortEnvironments = @{}
        # Ревизия схемы в базе: "" — psql недоступен; "fresh" — таблицы
        # alembic_version нет; иначе — идентификатор ревизии.
        DbRevision = ""
        # Текст `alembic history` ИЗ ОБРАЗА (пусто — сгенерировать по AlembicHeads).
        ImageAlembicHistory = ""
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
        $cliName = Get-HrmMockCliName $Name
        if ($cliName -eq "docker.exe" -or $cliName -eq "docker") {
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
                if ($global:HRM_MockWorld.ComposePluginMissing) {
                    # Так отвечает docker CLI без плагина compose (код 1 + stderr).
                    return [pscustomobject]@{ Name = $Name; ExitCode = 1; Stdout = ""; Stderr = "docker: 'compose' is not a docker command. See 'docker --help'." }
                }
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
                # Реальная сборка запекает метку hrmanager.release_sha из
                # HRM_RELEASE_SHA pilot.env (build.labels): образ начинает
                # соответствовать снимку. Мок обязан вести себя так же, иначе
                # движок считал бы свежесобранные образы устаревшими.
                $envIndex = [array]::IndexOf([object[]]$Arguments, "--env-file")
                if ($envIndex -ge 0 -and $envIndex + 1 -lt $Arguments.Count -and (Test-Path $Arguments[$envIndex + 1])) {
                    $releaseLine = Get-Content $Arguments[$envIndex + 1] | Where-Object { $_ -like "HRM_RELEASE_SHA=*" } | Select-Object -First 1
                    if ($null -ne $releaseLine) {
                        $global:HRM_MockWorld.ImageReleaseSha = ($releaseLine -replace '^HRM_RELEASE_SHA=', '').Trim()
                    }
                }
                return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = "built"; Stderr = "" }
            }
            if ($Arguments.Count -ge 3 -and $Arguments[0] -eq "compose" -and ($Arguments -contains "exec")) {
                $joined = ($Arguments -join " ")
                if ($joined -match "psql" -and $joined -match "alembic_version") {
                    # Ревизия схемы из базы (только чтение) — см. DbRevision.
                    $revision = [string]$global:HRM_MockWorld.DbRevision
                    if (-not $revision) {
                        return [pscustomobject]@{ Name = $Name; ExitCode = 1; Stdout = ""; Stderr = "psql: could not connect to server: Connection refused" }
                    }
                    if ($revision -eq "fresh") {
                        return [pscustomobject]@{ Name = $Name; ExitCode = 1; Stdout = ""; Stderr = 'ERROR: relation "alembic_version" does not exist' }
                    }
                    return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = ($revision + "`n"); Stderr = "" }
                }
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
            if ($Arguments.Count -ge 1 -and $Arguments[0] -eq "run") {
                # Одноразовый контейнер ИЗ ОБРАЗА: alembic history/heads читают
                # вшитые в образ миграции (без подключения к базе).
                $joined = ($Arguments -join " ")
                if ($joined -match "alembic history") {
                    $text = [string]$global:HRM_MockWorld.ImageAlembicHistory
                    if (-not $text) { $text = Get-HrmMockAlembicHistory $global:HRM_MockWorld.AlembicHeads }
                    return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = $text; Stderr = "" }
                }
                if ($joined -match "alembic heads") {
                    return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = ($global:HRM_MockWorld.AlembicHeads + " (head)`n"); Stderr = "" }
                }
                return [pscustomobject]@{ Name = $Name; ExitCode = 1; Stdout = ""; Stderr = "unexpected run: $joined" }
            }
            if ($Arguments.Count -ge 2 -and $Arguments[0] -eq "image" -and $Arguments[1] -eq "inspect") {
                $joined = ($Arguments -join " ")
                if ($joined -match "hrmanager\.release_sha") {
                    # Метка снимка на образе (что «запечено» при сборке).
                    return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = ([string]$global:HRM_MockWorld.ImageReleaseSha); Stderr = "" }
                }
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
        if ((Get-HrmMockCliName $Name) -eq "taskkill.exe") {
            # Остановка дерева процессов по таймауту внешней команды: аргументы
            # фиксируются, настоящая программа не запускается.
            $global:HRM_MockWorld.TaskkillArgs += , @($Arguments)
            return [pscustomobject]@{ Name = $Name; ExitCode = 0; Stdout = "SUCCESS: The process tree has been terminated."; Stderr = "" }
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
        # Чужое приложение на конкретном порту (например, старый dev-стек на
        # 8080): окружение подменяется для ЭТОГО порта, остальные порты
        # отвечают как пилот. Так проверяется и подбор свободного порта, и
        # принадлежность readiness-ответа.
        $requestPort = Get-HrmMockUriPort $Uri
        $environment = [string]$global:HRM_MockWorld.HealthEnvironment
        $foreign = $global:HRM_MockWorld.ForeignPortEnvironments
        if ($requestPort -and $foreign.ContainsKey($requestPort)) { $environment = [string]$foreign[$requestPort] }
        if ($Uri -like "*/api/health") {
            $healthBody = [pscustomobject]@{
                status = "ok"; service = "hr-manager"; version = [string]$global:HRM_MockWorld.OpsBody.version; environment = $environment
                checks = [pscustomobject]@{ database = [pscustomobject]@{ status = "ok"; latency_ms = 2 } }
            }
            return @{ StatusCode = $global:HRM_MockWorld.BackendStatus; Body = $healthBody }
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
            $opsBody = $global:HRM_MockWorld.OpsBody
            if ($environment -ne "pilot" -and $null -ne $opsBody) {
                # Чужой стек отвечает своим окружением и своей версией: его
                # ответ не должен считаться готовностью пилота.
                $opsBody = [pscustomobject]@{
                    status = "ok"; service = "hr-manager"; version = "0.0.0-dev"; environment = $environment
                    release_sha = ""; database = [pscustomobject]@{ status = "ok"; latency_ms = 2 }
                    migrations = [pscustomobject]@{ current_revision = "0000"; expected_revision = "0000"; ok = $true }
                    backup = [pscustomobject]@{ available = $false }
                    notifications = $null
                }
            }
            return @{ StatusCode = $global:HRM_MockWorld.OpsStatusCode; Body = $opsBody }
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
