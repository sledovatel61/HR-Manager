# ===========================================================================
# Preflight: всё, что должно быть в порядке ДО изменений на машине.
# Каждая проверка возвращает объект {Ok; Code; Message; Hint} с русским
# человекочитаемым сообщением и конкретным действием. Никаких секретов.
# ===========================================================================

function script:Test-HrmPowerShellVersion {
    $major = $PSVersionTable.PSVersion.Major
    if ($major -lt 5) {
        return [pscustomobject]@{
            Ok = $false; Code = "ps_version"
            Message = "Нужен PowerShell 5.1 или новее (найден $major)."
            Hint  = "Обновите Windows до актуального состояния (Параметры → Обновление и безопасность)."
        }
    }
    return [pscustomobject]@{ Ok = $true; Code = "ps_version"; Message = ("PowerShell " + [string]$PSVersionTable.PSVersion); Hint = "" }
}

function script:Test-HrmWindowsOs {
    param([switch]$AllowNonWindows)
    if (-not (Test-HrmWindows)) {
        if ($AllowNonWindows) {
            return [pscustomobject]@{ Ok = $true; Code = "os"; Message = "не-Windows (только тестовый контур)"; Hint = "" }
        }
        return [pscustomobject]@{
            Ok = $false; Code = "os"
            Message = "HR Manager для локального пилота устанавливается только на Windows 10/11 (64-bit)."
            Hint  = "Запустите установщик на рабочем компьютере с Windows."
        }
    }
    $build = 0
    try { $build = [Environment]::OSVersion.Version.Build } catch { $build = 0 }
    # Windows 10 = 10240+; 11 — тоже 10.x по версии. Младшие отклоняются.
    if ($build -gt 0 -and $build -lt 10240) {
        return [pscustomobject]@{
            Ok = $false; Code = "os"
            Message = "Требуется Windows 10 или Windows 11 (версия сборки $build слишком старая)."
            Hint  = "Обновите Windows до поддерживаемой версии."
        }
    }
    return [pscustomobject]@{ Ok = $true; Code = "os"; Message = "Windows build $build"; Hint = "" }
}

function script:Test-HrmDocker {
    <# Проверяет CLI docker и доступность daemon-а. Возвращает
       daemonStatus: "up" | "down" | "missing". #>
    try {
        $probe = Invoke-HrmTool -FilePath "docker" -Arguments @("version", "--format", "{{.Server.Version}}")
    } catch { $probe = [pscustomobject]@{ ExitCode = 1; StdOut = @(); StdErr = @(); StdOutText = "" } }
    if ($probe.ExitCode -ne 0) {
        try {
            $which = Invoke-HrmTool -FilePath "docker" -Arguments @("--version")
        } catch { $which = [pscustomobject]@{ ExitCode = 1; StdOut = @(); StdErr = @(); StdOutText = "" } }
        if ($which.ExitCode -ne 0) {
            return [pscustomobject]@{
                Ok = $false; Code = "docker_missing"; daemonStatus = "missing"
                Message = "На компьютере не обнаружен Docker Desktop — он нужен для работы HR Manager."
                Hint  = "Установщик может скачать и запустить официальный установщик Docker Desktop; после установки и перезагрузки продолжение автоматически."
            }
        }
        return [pscustomobject]@{
            Ok = $false; Code = "docker_daemon"; daemonStatus = "down"
            Message = "Docker Desktop запущен, но его служба недоступна."
            Hint  = "Откройте Docker Desktop и дождитесь статуса «Engine running», затем повторите."
        }
    }
    return [pscustomobject]@{
        Ok = $true; Code = "docker"; daemonStatus = "up"
        Message = "Docker engine: $($probe.StdOutText.Trim())"; Hint = ""
    }
}

function script:Test-HrmComposeV2 {
    $probe = Invoke-HrmTool -FilePath "docker" -Arguments @("compose", "version", "--short")
    if ($probe.ExitCode -ne 0) {
        return [pscustomobject]@{
            Ok = $false; Code = "compose"
            Message = "Нет поддержки «docker compose» v2 (требуется v$($script:HrmMinComposeMajor).$($script:HrmMinComposeMinor)+)."
            Hint  = "Обновите Docker Desktop до актуальной версии (в ней Compose устанавливается автоматически)."
        }
    }
    $version = $probe.StdOutText.Trim()
    if ($version -match '^v?(\d+)\.(\d+)') {
        $major = [int]$Matches[1]; $minor = [int]$Matches[2]
        if ($major -lt $script:HrmMinComposeMajor -or ($major -eq $script:HrmMinComposeMajor -and $minor -lt $script:HrmMinComposeMinor)) {
            return [pscustomobject]@{
                Ok = $false; Code = "compose"
                Message = "Docker Compose $version слишком старый — нужна версия $script:HrmMinComposeMajor.$script:HrmMinComposeMinor или новее."
                Hint  = "Обновите Docker Desktop (Launch → Check for updates), затем повторите установку."
            }
        }
    } else {
        return [pscustomobject]@{
            Ok = $false; Code = "compose"
            Message = "Не удалось определить версию «docker compose»."
            Hint  = "Полностью перезапустите Docker Desktop и повторите установку."
        }
    }
    return [pscustomobject]@{ Ok = $true; Code = "compose"; Message = "Docker Compose $version"; Hint = "" }
}

function script:Test-HrmPorts {
    param([Parameter(Mandatory)][int[]]$Ports)
    foreach ($port in $Ports) {
        if (-not (Test-HrmPortAvailable -Port $port)) {
            return [pscustomobject]@{
                Ok = $false; Code = "port"
                Message = "Порт $port на этом компьютере уже занят другим приложением."
                Hint  = "Закройте программу, занявшую порт, или завершите установку в другом порту (по умолчанию 8081)."
            }
        }
    }
    return [pscustomobject]@{ Ok = $true; Code = "port"; Message = "порты $($Ports -join ', ') свободны"; Hint = "" }
}

function script:Test-HrmStateWritable {
    param([Parameter(Mandatory)][string]$StateRoot)
    try {
        if (-not (Test-Path $StateRoot)) { New-Item -ItemType Directory -Path $StateRoot -Force | Out-Null }
        $probe = Join-Path $StateRoot ".write-probe"
        [IO.File]::WriteAllText($probe, "ok")
        Remove-Item $probe -Force
        return [pscustomobject]@{ Ok = $true; Code = "state"; Message = "каталог состояния доступен"; Hint = "" }
    } catch {
        return [pscustomobject]@{
            Ok = $false; Code = "state"
            Message = "Нет доступа на запись к каталогу: $StateRoot"
            Hint  = "Убедитесь, что диск не заполнен и каталог принадлежит вашему пользователю Windows."
        }
    }
}

function script:Test-HrmDiskSpace {
    param([Parameter(Mandatory)][string]$Path)
    $freeMb = Get-HrmDiskFreeMb -Path $Path
    if ($freeMb -lt $script:HrmMinFreeDiskMb) {
        return [pscustomobject]@{
            Ok = $false; Code = "disk"
            Message = "На диске свободно ${freeMb} МБ, а требуется не менее $script:HrmMinFreeDiskMb МБ."
            Hint  = "Освободите место (документированный порог: $script:HrmMinFreeDiskMb МБ) и повторите установку."
        }
    }
    return [pscustomobject]@{ Ok = $true; Code = "disk"; Message = "свободно ${freeMb} МБ"; Hint = "" }
}

function script:Test-HrmExistingConfig {
    <# Если конфигурация уже есть — она должна быть целой; движок НИКОГДА
       не перезаписывает повреждённую конфигурацию молча (fail closed). #>
    param([Parameter(Mandatory)][string]$StateRoot)
    $configPath = Join-Path $StateRoot "config.json"
    $secretsPath = Join-Path $StateRoot "secrets.json"
    if (-not (Test-Path $configPath)) {
        return [pscustomobject]@{ Ok = $true; Code = "config"; Message = "новая установка"; Hint = "" }
    }
    try {
        $config = Read-HrmJson $configPath
        if (-not $config -or -not $config.port -or -not $config.composeProject) { throw "структура повреждена" }
        if (-not (Test-Path $secretsPath)) { throw "файл секретов отсутствует" }
        $secrets = Read-HrmJson $secretsPath
        foreach ($field in @("secretKey", "postgresPassword", "backupEncKey", "backupKeyId")) {
            if (-not $secrets.$field) { throw "секрет $field отсутствует" }
        }
        return [pscustomobject]@{ Ok = $true; Code = "config"; Message = "существующая конфигурация цела"; Hint = "" }
    } catch {
        return [pscustomobject]@{
            Ok = $false; Code = "config"
            Message = "Существующая конфигурация повреждена ( $_ )."
            Hint  = "Движок не перезаписывает её автоматически: восстановьте каталог из резервной копии или выполните явное удаление данных через установщик."
        }
    }
}

function script:Invoke-HrmPreflight {
    param(
        [Parameter(Mandatory)][string]$StateRoot,
        [int]$Port = 8081,
        [switch]$SkipDocker,
        [switch]$AllowNonWindows
    )
    $checks = @()
    $checks += Test-HrmPowerShellVersion
    $checks += Test-HrmWindowsOs -AllowNonWindows:$AllowNonWindows
    $checks += Test-HrmStateWritable -StateRoot $StateRoot
    $checks += Test-HrmExistingConfig -StateRoot $StateRoot
    $checks += Test-HrmDiskSpace -Path $StateRoot
    $checks += Test-HrmPorts -Ports @($Port)
    if (-not $SkipDocker) {
        $docker = Test-HrmDocker
        $checks += $docker
        if ($docker.Ok) { $checks += Test-HrmComposeV2 }
    }
    $failed = @($checks | Where-Object { -not $_.Ok })
    return [pscustomobject]@{
        Ok     = ($failed.Count -eq 0)
        Checks = $checks
        Failed = $failed
    }
}

function script:Get-HrmDockerDesktopManifest {
    <# Официальный источник Docker Desktop: версия и SHA-256 закреплены в
       репозитории (docs-файл), скачивание проверяется по хэшу. Лицензия
       НЕ принимается за пользователя — запускается официальный installer. #>
    param([Parameter(Mandatory)][string]$RepoRoot)
    $path = Join-Path $RepoRoot "docker-desktop.json"
    if (-not (Test-Path $path)) {
        throw (New-HrmError "Отсутствует infra/windows/docker-desktop.json — источник Docker Desktop не настроен." "" 2)
    }
    $manifest = Read-HrmJson $path
    foreach ($field in @("version", "url", "sha256")) {
        if (-not $manifest.$field) {
            throw (New-HrmError "docker-desktop.json: пустое поле '$field' — источник Docker Desktop не настроен (fail-closed)." "Значения закрепляет релиз-инженер в репозитории." 2)
        }
    }
    return $manifest
}
