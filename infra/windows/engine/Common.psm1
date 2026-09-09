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
        Protect-HrmFile $stateDir $file
        Set-Content -Path $file -Value $Url -Encoding UTF8
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

# --- Защита файлов состояния --------------------------------------------------

function Protect-HrmFile {
    # ACL: наследование выключено, полный доступ только текущему пользователю.
    param([string]$StateDir, [string]$Path)
    if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Path $StateDir -Force | Out-Null }
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    Invoke-HrmExternal -Name "icacls.exe" -Arguments @($Path, "/inheritance:r", "/grant:r", ("{0}:(OI)(CI)F" -f $identity)) | Out-Null
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
