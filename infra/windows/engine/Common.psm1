# Общий слой движка: пути, журнал, редакция секретов, внешние команды.
# Все внешние вызовы (docker, icacls, тесты сети) и все интерактивные
# действия (запросы, открытие браузера) проходят через функции этого
# модуля, чтобы Pester-тесты могли их мокать, не трогая реальную машину.

Set-StrictMode -Version 2.0

# The modules are also imported directly by the test harness and can be used
# by operators without going through hr-manager.ps1.  Define cross-module
# flags defensively so StrictMode never turns a read into a runtime failure.
if ($null -eq (Get-Variable -Name HrmNonInteractive -Scope Global -ErrorAction SilentlyContinue)) {
    $global:HrmNonInteractive = $false
}
if ($null -eq (Get-Variable -Name HrmOpenBrowser -Scope Global -ErrorAction SilentlyContinue)) {
    $global:HrmOpenBrowser = $false
}

# --- Каталоги ---------------------------------------------------------------

function Get-HrmStateDir {
    param([string]$OverrideDir = "")
    if ($OverrideDir) { return $OverrideDir }
    if ($env:HRM_STATE_DIR) { return $env:HRM_STATE_DIR }
    return Join-Path $env:LOCALAPPDATA "HRManager"
}

function Get-HrmDefaultInstallDir {
    if ($env:HRM_INSTALL_DIR) { return $env:HRM_INSTALL_DIR }
    return Join-Path $env:LOCALAPPDATA "Programs\HRManager"
}

function Get-HrmSecretsFile { param([string]$StateDir) return (Join-Path $StateDir "secrets.json") }
function Get-HrmEnvFile     { param([string]$StateDir) return (Join-Path $StateDir "pilot.env") }
function Get-HrmInputFile   { param([string]$StateDir) return (Join-Path $StateDir "first-run-input.json") }
function Get-HrmInstalledFile { param([string]$StateDir) return (Join-Path $StateDir "installed.json") }
function Get-HrmUpdateLock  { param([string]$StateDir) return (Join-Path $StateDir "update.lock") }
function Get-HrmUpdateJournal { param([string]$StateDir) return (Join-Path $StateDir "update-journal.json") }
function Get-HrmSetupUrlFile { param([string]$StateDir) return (Join-Path $StateDir "first-run-url.txt") }

# --- Журнал -----------------------------------------------------------------

function Write-HrmLog {
    param([string]$Level, [string]$Message)
    $clean = Redact-HrmText $Message
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Write-Output "[$stamp] [$Level] $clean"
}

# --- Редакция секретов -----------------------------------------------------

$script:HrmSecrets = @()

function Reset-HrmRedaction {
    # Тестовый шов: очистка реестра редакции.
    $script:HrmSecrets = @()
}

function Register-HrmSecret {
    param([string]$Value)
    if (-not [string]::IsNullOrEmpty($Value) -and $Value.Length -ge 8) {
        if ($script:HrmSecrets -notcontains $Value) { $script:HrmSecrets += $Value }
    }
}

function Redact-HrmText {
    param([string]$Text)
    $out = $Text
    foreach ($secret in $script:HrmSecrets) {
        $out = $out.Replace($secret, "<redacted>")
    }
    return $out
}

function Protect-HrmOutput {
    # Выводит только отредактированный текст.
    param([string]$Text)
    Write-Output (Redact-HrmText $Text)
}

# --- Внешние команды (единая точка мока) ------------------------------------

$script:MockExternal = $null
$script:MockHttp = $null
$script:MockDownload = $null
$script:MockProcessLaunch = $null

function Set-HrmExternalMock {
    # Тестовый шов: подменяет ВСЕ внешние команды (docker/icacls/…).
    param([scriptblock]$Mock)
    $script:MockExternal = $Mock
}

function Clear-HrmExternalMock { $script:MockExternal = $null }

function Set-HrmHttpMock {
    # Тестовый шов: подменяет ВСЕ HTTP-вызовы (loopback).
    param([scriptblock]$Mock)
    $script:MockHttp = $Mock
}

function Clear-HrmHttpMock { $script:MockHttp = $null }

function Invoke-HrmExternal {
    # Запуск внешней команды. НИКОГДА не вызывайте docker/icacls/etc.
    # напрямую из движка — только через эту функцию (Pester мокает её).
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string[]]$Arguments = @(),
        [string]$Stdin = "",
        [switch]$IgnoreExitCode
    )
    if ($null -ne $script:MockExternal) {
        return & $script:MockExternal -Name $Name -Arguments $Arguments -Stdin $Stdin -IgnoreExitCode:$IgnoreExitCode
    }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Name
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = $true
    $psi.CreateNoWindow = $true
    # ProcessStartInfo.ArgumentList only exists in modern .NET. Windows
    # PowerShell 5.1 runs on .NET Framework, so construct the command line
    # using the documented CommandLineToArgvW escaping rules instead.
    $quotedArguments = foreach ($arg in $Arguments) {
        $value = [string]$arg
        if ($value.Length -gt 0 -and $value -notmatch '[\s"]') {
            $value
            continue
        }
        $escaped = [regex]::Replace($value, '(\\*)"', '$1$1\"')
        $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
        '"' + $escaped + '"'
    }
    $psi.Arguments = $quotedArguments -join " "
    $process = [System.Diagnostics.Process]::Start($psi)
    if ($Stdin) { $process.StandardInput.Write($Stdin) }
    $process.StandardInput.Close()
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    $result = [pscustomobject]@{
        Name = $Name
        ExitCode = $process.ExitCode
        Stdout = $stdout
        Stderr = $stderr
    }
    if (-not $IgnoreExitCode -and $process.ExitCode -ne 0) {
        throw ("Команда '{0}' завершилась с кодом {1}: {2}" -f $Name, $process.ExitCode, (Redact-HrmText $stderr.Trim()))
    }
    return $result
}

function Set-HrmDownloadMock {
    # Тестовый шов: подменяет скачивание файла (официальный установщик).
    param([scriptblock]$Mock)
    $script:MockDownload = $Mock
}

function Clear-HrmDownloadMock { $script:MockDownload = $null }

function Set-HrmProcessLaunchMock {
    # Тестовый шов: подменяет запуск процессов (Docker Desktop, установщик,
    # процесс движка). В тестах реальные программы не запускаются.
    param([scriptblock]$Mock)
    $script:MockProcessLaunch = $Mock
}

function Clear-HrmProcessLaunchMock { $script:MockProcessLaunch = $null }

function Save-HrmDownload {
    # Скачивание файла (только официальные источники, вызывающий обязан
    # проверить источник и подпись). Единственная точка скачивания — мокабельна.
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    if ($null -ne $script:MockDownload) {
        return (& $script:MockDownload -Uri $Uri -Destination $Destination)
    }
    $dir = Split-Path $Destination -Parent
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $previous = $ProgressPreference
    $ProgressPreference = "SilentlyContinue"
    try {
        Invoke-WebRequest -Uri $Uri -OutFile $Destination -UseBasicParsing -TimeoutSec 600
    }
    finally { $ProgressPreference = $previous }
    if (-not (Test-Path $Destination)) {
        throw "Не удалось скачать файл: $Uri"
    }
    return $Destination
}

function Get-HrmFileSha256 {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return "" }
    return (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Invoke-HrmDocker {
    param([string[]]$Arguments = @(), [string]$Stdin = "", [switch]$IgnoreExitCode)
    return Invoke-HrmExternal -Name "docker" -Arguments $Arguments -Stdin $Stdin -IgnoreExitCode:$IgnoreExitCode
}

function Invoke-HrmHttp {
    # Единственная точка HTTP-вызовов (loopback) — мокабельна в тестах.
    # Возвращает @{ StatusCode; Body }; НЕ бросает исключение на 4xx/5xx,
    # чтобы вызывающий код честно различал состояния (409 и т.п.).
    param([string]$Uri, [string]$Method = "GET", [object]$Body = $null, [hashtable]$Headers = @{})
    if ($null -ne $script:MockHttp) {
        return & $script:MockHttp -Uri $Uri -Method $Method -Body $Body -Headers $Headers
    }
    $params = @{ Uri = $Uri; Method = $Method; UseBasicParsing = $true; TimeoutSec = 10 }
    if ($null -ne $Body) {
        $params["ContentType"] = "application/json"
        $params["Body"] = ($Body | ConvertTo-Json -Compress -Depth 8)
    }
    if ($Headers.Count -gt 0) { $params["Headers"] = $Headers }
    try {
        $response = Invoke-WebRequest @params
        $parsed = $null
        try { $parsed = $response.Content | ConvertFrom-Json } catch { $parsed = $response.Content }
        return @{ StatusCode = [int]$response.StatusCode; Body = $parsed }
    }
    catch {
        $web = $_.Exception.Response
        if ($null -ne $web) {
            $code = [int]$web.StatusCode
            $content = $_.ErrorDetails.Message
            return @{ StatusCode = $code; Body = $content }
        }
        throw
    }
}

# --- Интерактив (мокабельно) ------------------------------------------------

function Test-HrmInteractive {
    if ($global:HrmNonInteractive -or $env:HRM_NONINTERACTIVE -eq "1") { return $false }
    return [Environment]::UserInteractive
}

function Invoke-HrmPrompt {
    # Возвращает $Default в неинтерактивном режиме.
    param([string]$Prompt, [string]$Default = "")
    if (-not (Test-HrmInteractive)) { return $Default }
    return Read-Host $Prompt
}

function Invoke-HrmConfirmationPrompt {
    param([string]$Prompt, [bool]$Default = $false)
    if (-not (Test-HrmInteractive)) { return $Default }
    $answer = Read-Host "$Prompt (Y/N)"
    return ($answer -match "^(y|д|да)$")
}

function Invoke-HrmOpenBrowser {
    param([string]$Url, [string]$StateDir = "")
    $open = (Test-HrmInteractive -or $global:HrmOpenBrowser -or $env:HRM_OPEN_BROWSER -eq "1")
    if (-not (Test-HrmInteractive)) {
        # Неинтерактивно: одноразовая ссылка — всегда в защищённый файл.
        $stateDir = if ($StateDir) { $StateDir } else { Get-HrmStateDir }
        $file = Get-HrmSetupUrlFile $stateDir
        Set-Content -Path $file -Value $Url -Encoding UTF8
        Protect-HrmFile $stateDir $file
        if ($open) {
            # Установщик (-OpenBrowser / HRM_OPEN_BROWSER=1): и файл, и браузер.
            Start-Process $Url
            return
        }
        Write-HrmLog "info" "Неинтерактивный режим: ссылка первого запуска записана в $file (браузер не открывается)."
        return
    }
    Start-Process $Url
}

function Start-HrmChannelWatcherProcess {
    # Отдельный скрытый процесс наблюдателя канала обновлений (Phase 13).
    param([string]$InstallDir = "", [string]$StateDir = "")
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $entry = Join-Path (Split-Path $PSScriptRoot -Parent) "hr-manager.ps1"
    $arguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", ('"{0}"' -f $entry),
        "-Action", "channel", "-Watch",
        "-InstallDir", ('"{0}"' -f $InstallDir),
        "-StateDir", ('"{0}"' -f $StateDir)
    )
    Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -WindowStyle Hidden | Out-Null
    Write-HrmLog "info" "Канал: запущен процесс наблюдателя."
}

# --- Защита файлов состояния --------------------------------------------------

function Protect-HrmFile {
    # ACL: наследование выключено, полный доступ только текущему пользователю.
    param([string]$StateDir, [string]$Path)
    if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Path $StateDir -Force | Out-Null }
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    # (OI)(CI) are inheritance flags for a directory. Applied to a regular
    # file they create an inherit-only ACE, leaving the file itself unreadable
    # after /inheritance:r. This used to make atomically replaced state files
    # (notably secrets.json and update-journal.json) fail with Access Denied.
    $isDirectory = (Test-Path -LiteralPath $Path -PathType Container)
    $grant = if ($isDirectory) {
        "{0}:(OI)(CI)F" -f $identity
    }
    else {
        "{0}:F" -f $identity
    }
    Invoke-HrmExternal -Name "icacls.exe" -Arguments @($Path, "/inheritance:r", "/grant:r", $grant) | Out-Null
}

function Set-HrmJsonFile {
    # Атомарная запись JSON-файла состояния (temp + Move-Item).
    param([string]$StateDir, [string]$RelativeName, [object]$Value)
    $dir = Join-Path $StateDir (Split-Path $RelativeName -Parent)
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $target = Join-Path $StateDir $RelativeName
    $tmp = "$target.tmp"
    $Value | ConvertTo-Json -Depth 10 | Set-Content -Path $tmp -Encoding UTF8
    Move-Item -Path $tmp -Destination $target -Force
    Protect-HrmFile $StateDir $target
}

function Get-HrmJsonFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    return (Get-Content -Path $Path -Raw -Encoding UTF8 | ConvertFrom-Json)
}

# --- Мелкие утилиты -----------------------------------------------------------

function New-HrmHex {
    # Криптографически стойкая случайная hex-строка (никогда не в аргументах).
    param([int]$Bytes = 32)
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $buffer = New-Object byte[] $Bytes
    $rng.GetBytes($buffer)
    return (($buffer | ForEach-Object { $_.ToString("x2") }) -join "")
}

function Wait-HrmCondition {
    # Ожидание условия с таймаутом. Возвращает $true/$false.
    param([scriptblock]$Condition, [int]$TimeoutSeconds = 180, [int]$IntervalSeconds = 3)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            if (& $Condition) { return $true }
        }
        catch { }
        Start-Sleep -Seconds $IntervalSeconds
    }
    try { return [bool](& $Condition) } catch { return $false }
}

function Get-HrmPort {
    # Порт публикации фронтенда: -Port > HRM_PILOT_PORT > 8080.
    param([int]$RequestedPort = 0)
    if ($RequestedPort -gt 0) { return $RequestedPort }
    $envPort = 0
    if ([int]::TryParse([string]$env:HRM_PILOT_PORT, [ref]$envPort) -and $envPort -gt 0) { return $envPort }
    return 8080
}

function Get-HrmBaseUrl {
    param([int]$Port = 0)
    $p = Get-HrmPort $Port
    return ("http://127.0.0.1:{0}" -f $p)
}

# --- Запуск процессов движка и внешних программ -------------------------------
# ЕДИНСТВЕННОЕ место, где допускается Start-Process/Process.Start: статический
# тест (static.tests.ps1) запрещает эти вызовы в остальных модулях движка.
# Никакие секреты сюда не передаются: только пути, действия и флаги.

function Start-HrmDetached {
    # Запуск внешней программы без ожидания завершения.
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [switch]$Hidden,
        [string]$WorkingDirectory = "",
        [switch]$RunAsAdmin
    )
    $startArgs = @{ FilePath = $FilePath }
    if ($Arguments.Count -gt 0) { $startArgs["ArgumentList"] = $Arguments }
    if ($WorkingDirectory) { $startArgs["WorkingDirectory"] = $WorkingDirectory }
    if ($Hidden) { $startArgs["WindowStyle"] = "Hidden" }
    if ($RunAsAdmin) { $startArgs["Verb"] = "RunAs" }
    if ($null -ne $script:MockProcessLaunch) {
        return (& $script:MockProcessLaunch -FilePath $FilePath -Arguments $Arguments -Mode $(if ($RunAsAdmin) { "elevated_detached" } else { "detached" }))
    }
    $process = Start-Process @startArgs -PassThru
    return $process
}

function Start-HrmElevatedAndWait {
    # Запуск программы с повышением прав (UAC) и ожиданием завершения.
    # Возвращает @{ Started; CanceledByUser; ExitCode }.
    # Отмена UAC пользователем — штатный результат, а не сбой движка.
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = ""
    )
    $startArgs = @{ FilePath = $FilePath; Verb = "RunAs"; PassThru = $true }
    if ($Arguments.Count -gt 0) { $startArgs["ArgumentList"] = $Arguments }
    if ($WorkingDirectory) { $startArgs["WorkingDirectory"] = $WorkingDirectory }
    if ($null -ne $script:MockProcessLaunch) {
        return (& $script:MockProcessLaunch -FilePath $FilePath -Arguments $Arguments -Mode "elevated_wait")
    }
    try {
        $process = Start-Process @startArgs
    }
    catch {
        return [pscustomobject]@{ Started = $false; CanceledByUser = $true; ExitCode = -1 }
    }
    $process.WaitForExit()
    return [pscustomobject]@{ Started = $true; CanceledByUser = $false; ExitCode = $process.ExitCode }
}

function Start-HrmEngineProcess {
    # Скрытый процесс движка (действие hr-manager.ps1) с журналом в StateDir.
    # Трей и установщик используют это, чтобы долгие операции не блокировали
    # интерфейс и не открывали пользователю консоль.
    param(
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [Parameter(Mandatory = $true)][string]$Action,
        [string]$InstallDir = "",
        [string]$StateDir = "",
        [string]$ExtraArguments = "",
        [string]$LogFile = ""
    )
    $arguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-NonInteractive",
        "-File", ('"{0}"' -f $ScriptPath),
        "-Action", $Action
    )
    if ($InstallDir) { $arguments += @("-InstallDir", ('"{0}"' -f $InstallDir)) }
    if ($StateDir) { $arguments += @("-StateDir", ('"{0}"' -f $StateDir)) }
    if ($ExtraArguments) { $arguments += $ExtraArguments.Split(" ") }
    $startArgs = @{
        FilePath = "powershell.exe"
        ArgumentList = $arguments
        WindowStyle = "Hidden"
        PassThru = $true
    }
    if ($LogFile) {
        $logDir = Split-Path $LogFile -Parent
        if ($logDir -and -not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
        $startArgs["RedirectStandardOutput"] = $LogFile
        $startArgs["RedirectStandardError"] = "$LogFile.err"
    }
    if ($null -ne $script:MockProcessLaunch) {
        return (& $script:MockProcessLaunch -FilePath "powershell.exe" -Arguments $arguments -Mode "engine" -LogFile $LogFile)
    }
    return (Start-Process @startArgs)
}

function Get-HrmEngineLogsDir {
    param([string]$StateDir)
    return (Join-Path $StateDir "logs")
}

function Get-HrmTimestampedLogFile {
    # Путь журнала операции: StateDir\logs\<action>-<дата-время>.log
    param([string]$StateDir, [string]$Action)
    $stamp = (Get-Date).ToString("yyyyMMdd-HHmmss")
    return (Join-Path (Get-HrmEngineLogsDir $StateDir) ("{0}-{1}.log" -f $Action, $stamp))
}

function Get-HrmProcessRunning {
    # Проверка запущенного процесса по имени (без запуска внешних команд).
    param([string]$Name)
    if (-not $Name) { return $false }
    try {
        $processes = @(Get-Process -Name $Name -ErrorAction SilentlyContinue)
        return ($processes.Count -gt 0)
    }
    catch { return $false }
}
