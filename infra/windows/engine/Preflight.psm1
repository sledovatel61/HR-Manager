# Честная предполётная проверка: система, Docker, порты, место, конфигурация.
# Ничего не «соглашается» за пользователя молча: каждая проверка возвращает
# понятный результат, а отказ — точную причину.

Set-StrictMode -Version 2.0

# --- Тестовый шов ------------------------------------------------------------
# Переопределения результатов проверок (используются только тестами движка;
# без переопределения работают честные реальные проверки).

$script:PreflightOverride = $null

function Set-HrmPreflightOverride {
    param([hashtable]$Override)
    $script:PreflightOverride = $Override
}

function Clear-HrmPreflightOverride { $script:PreflightOverride = $null }

function Get-HrmPreflightOverrideResult {
    # Возвращает готовый результат по ключу или $null, если нет переопределения.
    param([string]$Key)
    if ($null -eq $script:PreflightOverride) { return $null }
    if (-not $script:PreflightOverride.ContainsKey($Key)) { return $null }
    $value = $script:PreflightOverride[$Key]
    if ($value -is [bool]) {
        return [pscustomobject]@{ Name = $Key; Passed = $value; Message = ("override: " + $value) }
    }
    return [pscustomobject]@{ Name = $Key; Passed = $true; Message = ("override: " + $value) }
}

function Test-HrmWindowsVersion {

    $override = Get-HrmPreflightOverrideResult "windows"
    if ($null -ne $override) { return $override }
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
    if ($null -eq $os) {
        return [pscustomobject]@{ Name = "windows"; Passed = $false; Message = "Не удалось определить версию Windows." }
    }
    $ok = ($os.Name -match "Windows 10|Windows 11") -or ([int]$os.Version.Split(".")[0] -ge 10)
    if ($ok) {
        return [pscustomobject]@{ Name = "windows"; Passed = $true; Message = $os.Caption }
    }
    return [pscustomobject]@{ Name = "windows"; Passed = $false; Message = "Требуются Windows 10/11 x64; найдено: $($os.Caption)" }
}

function Test-HrmPowerShellVersion {

    $override = Get-HrmPreflightOverrideResult "powershell"
    if ($null -ne $override) { return $override }
    $ok = $PSVersionTable.PSVersion.Major -ge 5
    if (-not $ok) {
        return [pscustomobject]@{ Name = "powershell"; Passed = $false; Message = "Требуется Windows PowerShell 5.1+; найдено: $($PSVersionTable.PSVersion)" }
    }
    $bitness = if ([Environment]::Is64BitProcess) { "x64" } else { "x86" }
    if (-not [Environment]::Is64BitProcess) {
        return [pscustomobject]@{ Name = "powershell"; Passed = $false; Message = "Требуется 64-разрядный PowerShell." }
    }
    return [pscustomobject]@{ Name = "powershell"; Passed = $true; Message = "PowerShell $($PSVersionTable.PSVersion) ($bitness)" }
}

function Test-HrmDockerCli {

    $override = Get-HrmPreflightOverrideResult "docker"
    if ($null -ne $override) { return $override }
    $found = Invoke-HrmExternal -Name "docker.exe" -Arguments @("--version") -IgnoreExitCode
    if ($found.ExitCode -ne 0) {
        return [pscustomobject]@{ Name = "docker"; Passed = $false; Message = "Docker CLI не найден. Установите Docker Desktop только с официального сайта docker.com (лицензия принимается вручную)." }
    }
    return [pscustomobject]@{ Name = "docker"; Passed = $true; Message = ($found.Stdout.Trim()) }
}

function Test-HrmDockerDaemon {

    $override = Get-HrmPreflightOverrideResult "daemon"
    if ($null -ne $override) { return $override }
    $info = Invoke-HrmExternal -Name "docker.exe" -Arguments @("info", "--format", "{{.ServerVersion}}") -IgnoreExitCode
    if ($info.ExitCode -ne 0) {
        return [pscustomobject]@{ Name = "daemon"; Passed = $false; Message = "Демон Docker не запущен (запустите Docker Desktop и подождите инициализацию WSL2)." }
    }
    return [pscustomobject]@{ Name = "daemon"; Passed = $true; Message = ("Docker engine " + $info.Stdout.Trim()) }
}

function Test-HrmComposeVersion {

    $override = Get-HrmPreflightOverrideResult "compose"
    if ($null -ne $override) { return $override }
    # Требуется Compose v2 (плагин docker compose), минимум 2.24 (тег !reset).
    $out = Invoke-HrmExternal -Name "docker.exe" -Arguments @("compose", "version", "--short") -IgnoreExitCode
    if ($out.ExitCode -ne 0 -or -not $out.Stdout) {
        return [pscustomobject]@{ Name = "compose"; Passed = $false; Message = "Плагин Docker Compose v2 не найден (`docker compose version`)." }
    }
    $text = ($out.Stdout -split "`n")[0].Trim()
    $match = [regex]::Match($text, "v?(\d+)\.(\d+)\.(\d+)")
    if (-not $match.Success) {
        return [pscustomobject]@{ Name = "compose"; Passed = $false; Message = "Не удалось разобрать версию Compose: $text" }
    }
    $major = [int]$match.Groups[1].Value
    $minor = [int]$match.Groups[2].Value
    $ok = ($major -ge 2) -and ($minor -ge 24)
    if (-not $ok) {
        return [pscustomobject]@{ Name = "compose"; Passed = $false; Message = "Требуется Docker Compose v2.24+; найдено: $text (обновите Docker Desktop)." }
    }
    return [pscustomobject]@{ Name = "compose"; Passed = $true; Message = "Docker Compose $text" }
}

function Test-HrmPortFree {

    $override = Get-HrmPreflightOverrideResult "port"
    if ($null -ne $override) { return $override }
    param([int]$Port = 0)
    $port = Get-HrmPort $Port
    $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($listeners) {
        return [pscustomobject]@{ Name = "port"; Passed = $false; Message = "Порт $port уже занят (127.0.0.1:$port). Укажите другой порт параметром -Port." }
    }
    return [pscustomobject]@{ Name = "port"; Passed = $true; Message = "Порт $port свободен." }
}

function Test-HrmStateDirWritable {

    $override = Get-HrmPreflightOverrideResult "state_dir"
    if ($null -ne $override) { return $override }
    param([string]$StateDir)
    try {
        if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Path $StateDir -Force | Out-Null }
        $probe = Join-Path $StateDir ".write-probe"
        Set-Content -Path $probe -Value "ok"
        Remove-Item $probe -Force
        return [pscustomobject]@{ Name = "state_dir"; Passed = $true; Message = "Каталог состояния доступен для записи: $StateDir" }
    }
    catch {
        return [pscustomobject]@{ Name = "state_dir"; Passed = $false; Message = "Каталог состояния недоступен для записи: $StateDir" }
    }
}

function Test-HrmFreeSpace {

    $override = Get-HrmPreflightOverrideResult "space"
    if ($null -ne $override) { return $override }
    # Порог свободного места на диске установки (по умолчанию 5 ГБ).
    param([string]$Path, [long]$MinFreeMb = 5120)
    $drive = (Split-Path -Qualifier $Path)
    $psDrive = Get-PSDrive -Name ($drive.TrimEnd(":")) -ErrorAction SilentlyContinue
    if ($null -eq $psDrive) {
        return [pscustomobject]@{ Name = "space"; Passed = $false; Message = "Не удалось проверить свободное место на $drive" }
    }
    $freeMb = [long]($psDrive.Free / 1MB)
    if ($freeMb -lt $MinFreeMb) {
        return [pscustomobject]@{ Name = "space"; Passed = $false; Message = "Свободно $freeMb МБ — меньше порога $MinFreeMb МБ." }
    }
    return [pscustomobject]@{ Name = "space"; Passed = $true; Message = "Свободно $freeMb МБ." }
}

function Test-HrmConfigFiles {

    $override = Get-HrmPreflightOverrideResult "config"
    if ($null -ne $override) { return $override }
    # Существующая конфигурация (повторная установка/обновление) валидна.
    param([string]$InstallDir, [string]$StateDir)
    $composeFile = Join-Path $InstallDir "infra\compose.pilot.yml"
    if (-not (Test-Path $composeFile)) {
        return [pscustomobject]@{ Name = "config"; Passed = $false; Message = "Файл конфигурации не найден: $composeFile" }
    }
    $args = @("compose", "--project-name", "hr-manager-pilot")
    $envFile = Get-HrmEnvFile $StateDir
    if (Test-Path $envFile) { $args += @("--env-file", $envFile) }
    $args += @("-f", $composeFile, "config", "--quiet")
    $check = Invoke-HrmExternal -Name "docker.exe" -Arguments $args -IgnoreExitCode
    if ($check.ExitCode -ne 0) {
        return [pscustomobject]@{ Name = "config"; Passed = $false; Message = "Конфигурация невалидна: " + (Redact-HrmText $check.Stderr.Trim()) }
    }
    return [pscustomobject]@{ Name = "config"; Passed = $true; Message = "Конфигурация compose валидна." }
}

function Invoke-HrmPreflight {
    # Полный набор проверок. Возвращает массив результатов; в конце — сводка.
    param([string]$InstallDir, [string]$StateDir, [int]$Port = 0, [switch]$SkipCompose)
    $results = @()
    $results += Test-HrmWindowsVersion
    $results += Test-HrmPowerShellVersion
    $results += Test-HrmDockerCli
    $results += Test-HrmDockerDaemon
    $results += Test-HrmComposeVersion
    $results += Test-HrmPortFree -Port $Port
    $results += Test-HrmStateDirWritable -StateDir $StateDir
    $results += Test-HrmFreeSpace -Path $StateDir
    if (-not $SkipCompose) {
        $results += Test-HrmConfigFiles -InstallDir $InstallDir -StateDir $StateDir
    }
    return $results
}

function Assert-HrmPreflight {
    # Печатает результаты и бросает исключение при провале.
    param([string]$InstallDir, [string]$StateDir, [int]$Port = 0, [switch]$SkipCompose)
    $results = Invoke-HrmPreflight -InstallDir $InstallDir -StateDir $StateDir -Port $Port -SkipCompose:$SkipCompose
    foreach ($result in $results) {
        $mark = if ($result.Passed) { "[OK]" } else { "[FAIL]" }
        Write-HrmLog "info" ("{0} {1}: {2}" -f $mark, $result.Name, (Redact-HrmText $result.Message))
    }
    $failed = @($results | Where-Object { -not $_.Passed })
    if ($failed.Count -gt 0) {
        $list = ($failed | ForEach-Object { $_.Name }) -join ", "
        throw "Предполётная проверка не пройдена: $list. Запустите diagnostics для деталей."
    }
    return $results
}
