# ============================================================================
# HR Manager — локальный пилот (Windows, фаза 12): общие примитивы движка.
#
# Это ВНУТРЕННИЙ automation-движок: обычный пользователь его не запускает —
# его вызывает графический установщик (Setup.exe) и ярлыки. Вся логика
# установки/запуска/обновления/диагностики живёт здесь и вызывается с
# параметрами ТОЛЬКО через защищённые файлы состояния. Секреты, фамилии и
# токены никогда не попадают в командную строку процессов.
#
# Совместимость: Windows PowerShell 5.1 и PowerShell 7 (pwsh). Никакого
# синтаксиса 7-only (?? / тернарный). Тесты: infra/windows/tests.
# ============================================================================

Set-StrictMode -Version 2.0

class HrmEngineException : System.Exception {
    [string]$Hint = ""
    [int]$ExitCode = 1
    HrmEngineException([string]$message, [string]$hint, [int]$exitCode) : base($message) {
        $this.Hint = $hint
        $this.ExitCode = $exitCode
    }
}

# Каталог движка (infra/windows) — для VBS-ярлыков и RunOnce-резюме.
$script:HrmEngineDir = Split-Path -Parent $PSScriptRoot
# Реестр тестовых подмен (mockable command runner). Тесты ставят
# $script:HrmTestHooks["ToolOverride"] = { scriptblock } в том же scope.
$script:HrmTestHooks = @{}

$script:HrmProduct = "HR Manager"
$script:HrmProfileName = "hr-manager-pilot"
$script:HrmMinComposeMajor = 2
$script:HrmMinComposeMinor = 24
# Минимальное свободное место на диске состояния, МБ (документированный
# порог: образы ~1,2 ГБ + развёртывание БД + зашифрованные backup-копии).
$script:HrmMinFreeDiskMb = 10240
$script:HrmConfirmPhrase = "УДАЛИТЬ ДАННЫЕ HR MANAGER"

function script:Get-HrmStateRoot {
    <# Точный каталог локального состояния. TestMode (и все тесты) всегда
       работают в изолированном временном каталоге и НЕ трогают реальную
       установку пользователя. #>
    param([switch]$TestMode)
    if ($TestMode -or $env:HRMGR_TEST_STATE_DIR) {
        if ($env:HRMGR_TEST_STATE_DIR) { return (Join-Path $env:HRMGR_TEST_STATE_DIR "state") }
        return (Join-Path ([IO.Path]::Combine([System.IO.Path]::GetTempPath(), "hrmgr-test-state")) "state")
    }
    $local = [Environment]::GetFolderPath("LocalApplicationData")
    return [IO.Path]::Combine($local, "HR Manager", "state")
}

function script:Get-HrmAppRoot {
    <# Каталог установленных release-файлов (копирует Inno Setup). Тестовый
       контур (HRMGR_TEST_STATE_DIR) — временный каталог рядом с состоянием,
       реальные пути пользователя не затрагиваются. #>
    param([switch]$TestMode)
    if ($env:HRMGR_TEST_STATE_DIR) {
        return Join-Path $env:HRMGR_TEST_STATE_DIR "app"
    }
    $local = [Environment]::GetFolderPath("LocalApplicationData")
    return [IO.Path]::Combine($local, "HR Manager", "app")
}

function script:Write-HrmLog {
    param(
        [Parameter(Mandatory)][string]$StateRoot,
        [Parameter(Mandatory)][string]$Message,
        [ValidateSet("info", "warn", "error")][string]$Level = "info",
        [string[]]$Redact = @()
    )
    $dir = Join-Path $StateRoot "logs"
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $line = "[{0}] {1}: {2}" -f (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ"), $Level.ToUpperInvariant(), $Message
    foreach ($secret in $Redact) {
        if ($secret) { $line = $line.Replace($secret, "***") }
    }
    Add-Content -Path (Join-Path $dir "hr-manager.log") -Value $line -Encoding UTF8
}

function script:Write-HrmProgress {
    <# GUI читает этот файл (progress.json) и показывает понятные этапы без
       потока технических логов. #>
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string]$StateRoot,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Phase,
        [string]$Detail = ""
    )
    if (-not $StateRoot) { return }
    if (-not (Test-Path $StateRoot)) { New-Item -ItemType Directory -Path $StateRoot -Force | Out-Null }
    $payload = [ordered]@{
        phase     = $Phase
        detail    = $Detail
        at        = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    } | ConvertTo-Json -Compress
    [IO.File]::WriteAllText((Join-Path $StateRoot "progress.json"), $payload, (New-Object Text.UTF8Encoding($false)))
}

function script:New-HrmError {
    <# Единый формат ошибки: русское сообщение, действие и код возврата.
       Коды: 1 общ, 2 preflight, 3 docker, 4 update/rollback, 5 конфигурация
       повреждена/ACL, 6 места недостаточно, 7 не подтверждено удаление. #>
    param(
        [Parameter(Mandatory)][string]$Message,
        [string]$Hint = "",
        [int]$ExitCode = 1
    )
    return New-Object HrmEngineException ($Message, $Hint, $ExitCode)
}

function script:Test-HrmWindows {
    if ($env:OS -eq "Windows_NT") { return $true }
    try { return [Environment]::OSVersion.Version.Major -ge 0 -and $IsWindows } catch { return $false }
}

function script:Set-HrmSecretFileAcl {
    <# Максимум, что позволяют штатные средства: на Windows — явный ACL
       «только текущий пользователь» (наследование снято), на не-Windows
       (только тестовый контур) — chmod 600. Возвращает $true, только если
       права реально удалось ограничить — вызывающий код обязан fail closed. #>
    param([Parameter(Mandatory)][string]$Path)
    if (Test-HrmWindows) {
        try {
            $user = "{0}\{1}" -f [Environment]::UserDomainName, [Environment]::UserName
            $acl = Get-Acl -Path $Path
            $acl.SetAccessRuleProtection($true, $false)
            $acl.Access | ForEach-Object { $acl.RemoveAccessRule($_) } | Out-Null
            $rights = [Security.AccessControl.FileSystemRights]::FullControl
            $rule = New-Object Security.AccessControl.FileSystemAccessRule(
                $user, $rights, "Allow")
            $acl.AddAccessRule($rule)
            Set-Acl -Path $Path -AclObject $acl
            $verify = Get-Acl -Path $Path
            $identities = $verify.Access | Where-Object { $_.AccessControlType -eq "Allow" } |
                ForEach-Object { $_.IdentityReference.Translate([Security.Principal.NTAccount]).Value.ToLowerInvariant() } |
                Sort-Object -Unique
            if ($identities.Count -ne 1 -or $identities[0] -ne $user.ToLowerInvariant()) { return $false }
            return $true
        } catch {
            return $false
        }
    }
    # Не-Windows исполняется ТОЛЬКО в тестовом контуре (CI/Linux-разработка):
    # эквивалентная защита chmod 600 штатным средством.
    try {
        if (Test-Path /bin/chmod) { & /bin/chmod 600 $Path; if ($LASTEXITCODE -ne 0) { return $false } }
        return $true
    } catch { return $false }
}

function script:Write-HrmPrivateFile {
    <# Атомарная запись файла с ограничением доступа ДО записи содержимого
       (файл создаётся пустым, ACL применяется сразу, затем — содержимое). #>
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Content
    )
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    [IO.File]::WriteAllText($Path, "", (New-Object Text.UTF8Encoding($false)))
    if (-not (Set-HrmSecretFileAcl -Path $Path)) {
        Remove-Item -Path $Path -Force -ErrorAction SilentlyContinue
        throw (New-HrmError "Не удалось безопасно ограничить доступ к файлу секретов." `
                "Проверьте, что каталог состояния принадлежит вашему пользователю (не общий диск/сеть)." 5)
    }
    [IO.File]::WriteAllText($Path, $Content, (New-Object Text.UTF8Encoding($false)))
}

function script:Read-HrmJson {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    $raw = [IO.File]::ReadAllText($Path)
    if (-not $raw.Trim()) { return $null }
    return ($raw | ConvertFrom-Json)
}

function script:Write-HrmJson {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Object)
    $json = $Object | ConvertTo-Json -Depth 10
    [IO.File]::WriteAllText($Path, $json, (New-Object Text.UTF8Encoding($false)))
}

function script:Get-HrmConfig {
    param([Parameter(Mandatory)][string]$StateRoot)
    return Read-HrmJson (Join-Path $StateRoot "config.json")
}

function script:Set-HrmConfig {
    param([Parameter(Mandatory)][string]$StateRoot, [Parameter(Mandatory)]$Config)
    Write-HrmJson (Join-Path $StateRoot "config.json") $Config
}

# --- вызов внешней команды (docker CLI / curl-аналог) ------------------------

function script:ConvertTo-HrmCommandLine {
    <# Экранирование аргументов под правила Win32 CommandLineToArgvW:
       обёртка в кавычки, " внутри — удвоение, \ перед " — эскейп. Только
       для fallback без ArgumentList; спец-символы шелла не интерпретируются. #>
    param([string[]]$Arguments)
    $parts = foreach ($a in $Arguments) {
        $s = [string]$a
        if ($s -notmatch '[\s"]') { $s; continue }
        $escaped = ""
        $backslashes = 0
        foreach ($ch in $s.ToCharArray()) {
            if ($ch -eq '\') { $backslashes++; continue }
            if ($ch -eq '"') {
                $escaped += ('\' * ($backslashes * 2 + 1)) + '"'
            } else {
                $escaped += ('\' * $backslashes) + $ch
            }
            $backslashes = 0
        }
        $escaped += '\' * ($backslashes * 2)
        '"' + $escaped + '"'
    }
    return ($parts -join " ")
}

function script:Invoke-HrmTool {
    <# Единственная точка запуска внешних процессов. Возвращает объект
       { ExitCode; StdOut; StdErr; StdOutText }. Аргументы передаются списком
       без шелла (массив → прямая команда). В тестовом режиме подменяется
       хуком $script:HrmTestHooks.ToolOverride (mockable command runner). #>
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = "",
        [string]$StdInText = $null
    )
    if ($script:HrmTestHooks.Contains("ToolOverride")) {
        return (& $script:HrmTestHooks["ToolOverride"] $FilePath $Arguments $StdInText)
    }
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    if ($psi.psobject.Properties.Name -contains "ArgumentList") {
        # PowerShell 7 / .NET Core: безопасная передача аргументов списком.
        foreach ($a in $Arguments) { $psi.ArgumentList.Add([string]$a) }
    } elseif ($Arguments.Count -gt 0) {
        # Windows PowerShell 5.1 (.NET Framework): экранирование по правилам
        # Win32 (кавычки/двойные кавычки) — никакого шелл-интерпретирования.
        $psi.Arguments = ConvertTo-HrmCommandLine -Arguments $Arguments
    }
    if ($WorkingDirectory) { $psi.WorkingDirectory = $WorkingDirectory }
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = ($null -ne $StdInText)
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.StandardOutputEncoding = [Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [Text.Encoding]::UTF8
    $proc = [Diagnostics.Process]::Start($psi)
    if ($null -ne $StdInText) {
        $proc.StandardInput.Write($StdInText)
        $proc.StandardInput.Close()
    }
    $out = $proc.StandardOutput.ReadToEnd()
    $err = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()
    return [pscustomobject]@{
        ExitCode   = $proc.ExitCode
        StdOut     = ($out -split "`r?`n" | Where-Object { $_ -ne "" })
        StdErr     = ($err -split "`r?`n" | Where-Object { $_ -ne "" })
        StdOutText = $out
    }
}

function script:Invoke-HrmCompose {
    <# Единая сборка команд docker compose для пилотного профиля. #>
    param(
        [Parameter(Mandatory)][string]$AppDir,
        [Parameter(Mandatory)][string]$StateRoot,
        [Parameter(Mandatory)][AllowEmptyString()][string[]]$ComposeArgs,
        [string]$StdInText = $null
    )
    $config = Get-HrmConfig $StateRoot
    $project = "hr-manager-pilot"
    if ($config -and $config.composeProject) { $project = $config.composeProject }
    $infra = Join-Path $AppDir "infra"
    $arguments = @(
        "compose",
        "-p", $project,
        "-f", (Join-Path $infra "docker-compose.yml"),
        "-f", (Join-Path $infra "compose.pilot.yml"),
        "--env-file", (Join-Path $StateRoot "pilot.env")
    ) + @($ComposeArgs)
    return Invoke-HrmTool -FilePath "docker" -Arguments $arguments `
        -WorkingDirectory $AppDir -StdInText $StdInText
}

function script:Invoke-HrmHttpJson {
    <# GET локального API как JSON. Timeout всегда ограничен — GUI/engine
       никогда не ждёт вечно. Возвращает $null при любой сетевой ошибке. #>
    param([Parameter(Mandatory)][string]$Url, [int]$TimeoutSec = 5)
    if ($script:HrmTestHooks.Contains("HttpOverride")) {
        return (& $script:HrmTestHooks["HttpOverride"] $Url)
    }
    try {
        $request = [Net.HttpWebRequest]::Create($Url)
        $request.Timeout = $TimeoutSec * 1000
        $request.ReadWriteTimeout = $TimeoutSec * 1000
        $request.UserAgent = "HR-Manager-launcher"
        $response = $request.GetResponse()
        try {
            $stream = $response.GetResponseStream()
            $reader = New-Object IO.StreamReader($stream)
            $text = $reader.ReadToEnd()
            $json = $null
            if ($text) { $json = $text | ConvertFrom-Json }
            return [pscustomobject]@{ Json = $json; StatusCode = [int]$response.StatusCode }
        } finally { $response.Dispose() }
    } catch {
        $code = 0
        try { if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode } } catch { }
        return [pscustomobject]@{ Json = $null; StatusCode = $code }
    }
}

function script:Test-HrmPortAvailable {
    <# Порт свободен, если его можно забиндить на 127.0.0.1. #>
    param([Parameter(Mandatory)][int]$Port)
    try {
        $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        $listener.Stop()
        return $true
    } catch {
        return $false
    }
}

function script:Get-HrmDiskFreeMb {
    param([Parameter(Mandatory)][string]$Path)
    $root = ([IO.Path]::GetPathRoot($Path))
    if (-not $root) { $root = $Path }
    if (Test-HrmWindows) {
        try {
            $drive = New-Object IO.DriveInfo($root.TrimEnd('\'))
            return [math]::Floor($drive.AvailableFreeSpace / 1MB)
        } catch { return -1 }
    }
    try {
        $di = New-Object IO.DriveInfo($root)
        return [math]::Floor($di.AvailableFreeSpace / 1MB)
    } catch { return -1 }
}

function script:New-HrmRandomHex {
    <# Криптостойкий hex-секрет указанной длины (байт). #>
    param([Parameter(Mandatory)][int]$Bytes)
    $buf = New-Object byte[] $Bytes
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($buf) } finally { $rng.Dispose() }
    return (-join ($buf | ForEach-Object { $_.ToString("x2") }))
}

function script:New-HrmPairingCode {
    <# 6-значный код из алфавита без неоднозначных символов (0/O, 1/I). #>
    $alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    $buf = New-Object byte[] 6
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($buf) } finally { $rng.Dispose() }
    $code = -join ($buf | ForEach-Object { $alphabet[$_ % $alphabet.Length] })
    return $code
}

function script:Protect-HrmText {
    <# Санитизация: известные секретные значения заменяются полностью,
       плюс regex-страховка для password/token/secret/key= значений, email и
       телефонных последовательностей. Применяется к логам и diagnostics. #>
    param([AllowEmptyString()][string]$Text, [string[]]$Secrets = @())
    if ($null -eq $Text) { return "" }
    $out = $Text
    foreach ($secret in $Secrets) {
        if ($secret -and $secret.Length -ge 6) { $out = $out.Replace($secret, "***") }
    }
    $out = [regex]::Replace($out, '(?i)(password|passwd|secret|token|api[_-]?key|authorization|cookie)\s*[=:]\s*\S+', '$1=***')
    $out = [regex]::Replace($out, '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '***@***')
    $out = [regex]::Replace($out, '(\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}', '***')
    $out = [regex]::Replace($out, '(?i)(postgresql?|postgres|amqp|redis)://[^\s"'']+', 'connection-string=***')
    return $out
}

function script:Test-HrmSecretValueAbsent {
    <# Утилита тестов: ни одно секретное значение не должно встречаться в
       тексте (вывод/аргументы/файлы) — строгое сравнение подстрок. #>
    param([AllowEmptyString()][string]$Text, [string[]]$Secrets)
    if ($null -eq $Text) { $Text = "" }
    foreach ($secret in $Secrets) {
        if ($secret -and $Text.Contains($secret)) { return $false }
    }
    return $true
}

function script:Get-HrmComposeFiles {
    param([Parameter(Mandatory)][string]$AppDir)
    return @(
        (Join-Path $AppDir "infra\docker-compose.yml"),
        (Join-Path $AppDir "infra\compose.pilot.yml")
    )
}

function script:Test-HrmReleaseIntegrity {
    <# Проверка целостности вложенных release-файлов по SHA256SUMS.txt из
       манифеста. Возвращает список проблем (пусто = ОК). #>
    param([Parameter(Mandatory)][string]$ReleaseDir)
    $problems = @()
    $manifestPath = Join-Path $ReleaseDir "release-manifest.json"
    $sumsPath = Join-Path $ReleaseDir "SHA256SUMS.txt"
    if (-not (Test-Path $manifestPath)) { $problems += "release-manifest.json отсутствует"; return ,$problems }
    if (-not (Test-Path $sumsPath)) { $problems += "SHA256SUMS.txt отсутствует"; return ,$problems }
    $manifest = Read-HrmJson $manifestPath
    if ($manifest.product -ne "hr-manager-pilot") { $problems += "release-manifest.json: чужой продукт"; }
    foreach ($line in ([IO.File]::ReadAllLines($sumsPath))) {
        if (-not $line.Trim()) { continue }
        $parts = $line -split '\s+', 2
        if ($parts.Count -lt 2) { $problems += "SHA256SUMS.txt: повреждённая строка"; continue }
        $expectedHash = $parts[0].ToLowerInvariant()
        $rel = $parts[1].TrimStart('*').Replace('\', '/').ToLowerInvariant()
        $full = Join-Path $ReleaseDir ($rel.Replace('/', [IO.Path]::DirectorySeparatorChar))
        if (-not (Test-Path $full)) { $problems += "файл отсутствует: $rel"; continue }
        $actual = (Get-FileHash -Algorithm SHA256 -Path $full).Hash.ToLowerInvariant()
        if ($actual -ne $expectedHash) { $problems += "хэш не совпал: $rel" }
    }
    return ,$problems
}

function script:Get-HrmReleaseManifest {
    param([Parameter(Mandatory)][string]$ReleaseDir)
    return Read-HrmJson (Join-Path $ReleaseDir "release-manifest.json")
}
