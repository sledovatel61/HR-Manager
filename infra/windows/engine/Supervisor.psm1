# Supervisor HR Manager: единственный управляющий процесс пилота.
#
# Ответственность supervisor'а (и только его):
#   - запустить и дождаться рабочей среды (Docker Desktop/Engine) — через
#     engine/Docker.psm1;
#   - поднять Compose-стек и дождаться готовности приложения;
#   - держать понятное состояние «Запускается / Готово / Ошибка» в
#     StateDir\supervisor.json для трея, диагностики и установщика;
#   - быть единственным в системе (именованный mutex + запись pid);
#   - запускать/останавливать приложение по команде трея;
#   - управлять автозапуском после входа в систему (ярлык в папке автозагрузки
#     пользователя) — настраиваемо и без прав администратора.
#
# Supervisor НЕ делает: не подменяет backend/frontend, не ходит в БД, не
# хранит секреты, не запускает несколько копий, не удаляет тома.

Set-StrictMode -Version 2.0

$script:SupervisorStateFile = "supervisor.json"
$script:SupervisorMutexName = "Local\HRManagerPilotSupervisor"
$script:ActionLockFile = "action.lock"
$script:AutostartShortcutName = "HR Manager (трей).lnk"
$script:LegacyAutostartNames = @("HR Manager.lnk", "HR Manager — автозапуск.lnk")

function Get-HrmSupervisorStateFile {
    param([string]$StateDir)
    return (Join-Path $StateDir $script:SupervisorStateFile)
}

function Set-HrmSupervisorState {
    # Состояние supervisor'а для трея и поддержки. Секретов здесь нет.
    param(
        [string]$StateDir,
        [string]$State,
        [string]$Message = "",
        [hashtable]$Extra = @{},
        [bool]$Busy = $false
    )
    $file = Get-HrmSupervisorStateFile $StateDir
    $previous = $null
    if (Test-Path $file) {
        try { $previous = Get-HrmJsonFile $file } catch { $previous = $null }
    }
    $merged = [ordered]@{}
    if ($null -ne $previous) {
        foreach ($property in $previous.PSObject.Properties) { $merged[$property.Name] = $property.Value }
    }
    $merged["state"] = $State
    $merged["message"] = (Redact-HrmText $Message)
    $merged["busy"] = $Busy
    $merged["pid"] = $PID
    $merged["updated_at"] = (Get-Date).ToString("o")
    foreach ($key in $Extra.Keys) { $merged[$key] = $Extra[$key] }
    Set-HrmJsonFile $StateDir $script:SupervisorStateFile $merged
    # Наружу НЕ отдаём $merged: вызывающие (цикл supervisor'а, трей, установщик)
    # возвращают свои объекты результата, и лишний объект в success stream
    # превращал результат в массив (под StrictMode — «The property 'ok' cannot
    # be found on this object» у читателя). Состояние читается через
    # Get-HrmSupervisorState.
    return
}

function Get-HrmSupervisorState {
    param([string]$StateDir)
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $file = Get-HrmSupervisorStateFile $StateDir
    if (-not (Test-Path $file)) {
        return [pscustomobject]@{ state = "unknown"; message = "Управляющий компонент ещё не запускался."; busy = $false; pid = 0 }
    }
    try {
        $data = Get-HrmJsonFile $file
        if ($null -eq $data) { throw "пустой файл состояния" }
        $state = "unknown"
        if ($data.PSObject.Properties["state"]) { $state = [string]$data.state }
        $message = ""
        if ($data.PSObject.Properties["message"]) { $message = [string]$data.message }
        $busy = $false
        if ($data.PSObject.Properties["busy"]) { $busy = [bool]$data.busy }
        $pid2 = 0
        if ($data.PSObject.Properties["pid"]) { $pid2 = [int]$data.pid }
        return [pscustomobject]@{ state = $state; message = $message; busy = $busy; pid = $pid2 }
    }
    catch {
        return [pscustomobject]@{ state = "unknown"; message = "Файл состояния повреждён."; busy = $false; pid = 0 }
    }
}

function Get-HrmSupervisorStatusText {
    # Понятный статус для трея и пользователя.
    param([string]$State)
    switch ($State) {
        "ready" { return "Готово" }
        "starting" { return "Запускается" }
        "stopped" { return "Остановлено" }
        "error" { return "Ошибка" }
        default { return "Проверяем состояние" }
    }
}

function Enter-HrmSupervisorLock {
    # Единственный supervisor на пользователя. Возвращает @{ acquired; mutex }.
    try {
        $mutex = New-Object System.Threading.Mutex($false, $script:SupervisorMutexName)
        $acquired = $false
        try { $acquired = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $acquired = $true }
        if (-not $acquired) {
            $mutex.Dispose()
            return [pscustomobject]@{ acquired = $false; mutex = $null }
        }
        return [pscustomobject]@{ acquired = $true; mutex = $mutex }
    }
    catch {
        # Mutex недоступен (экзотика): не блокируем работу пользователя.
        return [pscustomobject]@{ acquired = $true; mutex = $null }
    }
}

function Exit-HrmSupervisorLock {
    param($Lock)
    if ($null -eq $Lock) { return }
    try {
        if ($null -ne $Lock.mutex) {
            $Lock.mutex.ReleaseMutex()
            $Lock.mutex.Dispose()
        }
    }
    catch { }
}

function Get-HrmActionLockFile {
    param([string]$StateDir)
    return (Join-Path $StateDir $script:ActionLockFile)
}

function Enter-HrmActionLock {
    # Одна операция движка за раз (например, трей нажали дважды).
    param([string]$StateDir, [string]$Action)
    $file = Get-HrmActionLockFile $StateDir
    if (Test-Path $file) {
        $data = $null
        try { $data = Get-HrmJsonFile $file } catch { $data = $null }
        if ($null -ne $data) {
            $lockPid = 0
            if ($data.PSObject.Properties["pid"]) { $lockPid = [int]$data.pid }
            $startedAt = ""
            if ($data.PSObject.Properties["started_at"]) { $startedAt = [string]$data.started_at }
            $alive = $false
            if ($lockPid -gt 0) {
                try { $alive = ($null -ne (Get-Process -Id $lockPid -ErrorAction SilentlyContinue)) } catch { $alive = $false }
            }
            $fresh = $true
            if ($startedAt) {
                try { $fresh = (((Get-Date) - [datetime]::Parse($startedAt)).TotalMinutes -lt 60) } catch { $fresh = $true }
            }
            if ($alive -and $fresh) {
                $runningAction = ""
                if ($data.PSObject.Properties["action"]) { $runningAction = [string]$data.action }
                return [pscustomobject]@{ acquired = $false; message = ("Операция уже выполняется ({0}). Подождите." -f $runningAction) }
            }
        }
    }
    Set-HrmJsonFile $StateDir $script:ActionLockFile ([ordered]@{
            action = $Action
            pid = $PID
            started_at = (Get-Date).ToString("o")
        })
    return [pscustomobject]@{ acquired = $true; message = "" }
}

function Exit-HrmActionLock {
    param([string]$StateDir)
    $file = Get-HrmActionLockFile $StateDir
    if (Test-Path $file) { Remove-Item $file -Force -ErrorAction SilentlyContinue }
}

function Get-HrmAutostartShortcutPath {
    # Ярлык автозапуска в папке автозагрузки ТЕКУЩЕГО пользователя — без UAC.
    if ($env:HRM_AUTOSTART_DIR) { return (Join-Path $env:HRM_AUTOSTART_DIR $script:AutostartShortcutName) }
    $startup = [Environment]::GetFolderPath("Startup")
    if (-not $startup) {
        $startup = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
    }
    return (Join-Path $startup $script:AutostartShortcutName)
}

function Get-HrmTrayScriptPath {
    # Путь к hrm-tray.ps1 установленного приложения.
    param([string]$InstallDir = "")
    if (-not $InstallDir) { $InstallDir = Get-HrmInstallDefaultDirSafe }
    return (Join-Path $InstallDir "infra\windows\hrm-tray.ps1")
}

function New-HrmShortcut {
    # Создание ярлыка через WScript.Shell (без запуска внешних процессов).
    param([string]$Path, [string]$TargetPath, [string]$Arguments, [string]$WorkingDirectory, [string]$Description)
    $dir = Split-Path $Path -Parent
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    if ($env:HRM_AUTOSTART_MOCK -eq "1") {
        # Тестовый режим: ярлык не создаётся через COM, пишется маркер.
        Set-Content -Path $Path -Value ("mock-shortcut: {0} {1}" -f $TargetPath, $Arguments) -Encoding UTF8
        return
    }
    $shell = New-Object -ComObject WScript.Shell
    try {
        $shortcut = $shell.CreateShortcut($Path)
        $shortcut.TargetPath = $TargetPath
        $shortcut.Arguments = $Arguments
        if ($WorkingDirectory) { $shortcut.WorkingDirectory = $WorkingDirectory }
        if ($Description) { $shortcut.Description = $Description }
        $shortcut.WindowStyle = 7
        $shortcut.Save()
    }
    finally {
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
    }
}

function Get-HrmAutostartState {
    param([string]$StateDir = "")
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $stateFile = Join-Path $StateDir "autostart.json"
    $enabled = $false
    if (Test-Path $stateFile) {
        try {
            $data = Get-HrmJsonFile $stateFile
            if ($null -ne $data -and $data.PSObject.Properties["enabled"]) { $enabled = [bool]$data.enabled }
        }
        catch { $enabled = $false }
    }
    $shortcut = Get-HrmAutostartShortcutPath
    return [pscustomobject]@{ enabled = $enabled; shortcut_exists = (Test-Path $shortcut); shortcut = $shortcut }
}

function Remove-HrmLegacyAutostartEntries {
    # Убирает ярлыки автозапуска старого образца (-Action start), чтобы после
    # обновления не поднимались два автозапуска сразу.
    $startupDir = Split-Path (Get-HrmAutostartShortcutPath) -Parent
    foreach ($name in $script:LegacyAutostartNames) {
        $path = Join-Path $startupDir $name
        if (Test-Path $path) {
            Remove-Item -Path $path -Force -ErrorAction SilentlyContinue
            Write-HrmLog "info" ("Удалён устаревший ярлык автозапуска: {0}" -f $name)
        }
    }
}

function Enable-HrmAutostart {
    # Автозапуск supervisor'а после входа в систему. Прав администратора не требует.
    param([string]$InstallDir = "", [string]$StateDir = "")
    if (-not $InstallDir) { $InstallDir = Get-HrmInstallDefaultDirSafe }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $trayScript = Get-HrmTrayScriptPath $InstallDir
    if (-not (Test-Path $trayScript)) {
        throw ("Не найден управляющий компонент: {0}" -f $trayScript)
    }
    Remove-HrmLegacyAutostartEntries
    $arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -InstallDir "{1}" -StateDir "{2}"' -f $trayScript, $InstallDir, $StateDir
    New-HrmShortcut -Path (Get-HrmAutostartShortcutPath) -TargetPath "powershell.exe" -Arguments $arguments `
        -WorkingDirectory $InstallDir -Description "HR Manager — автозапуск после входа в систему"
    Set-HrmJsonFile $StateDir "autostart.json" ([ordered]@{ enabled = $true; updated_at = (Get-Date).ToString("o"); target = $trayScript })
    Write-HrmLog "info" "Автозапуск HR Manager включён (после входа в систему поднимется значок в трее)."
    return (Get-HrmAutostartState $StateDir)
}

function Disable-HrmAutostart {
    param([string]$StateDir = "")
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $shortcut = Get-HrmAutostartShortcutPath
    if (Test-Path $shortcut) { Remove-Item -Path $shortcut -Force -ErrorAction SilentlyContinue }
    Set-HrmJsonFile $StateDir "autostart.json" ([ordered]@{ enabled = $false; updated_at = (Get-Date).ToString("o") })
    Write-HrmLog "info" "Автозапуск HR Manager выключен."
    return (Get-HrmAutostartState $StateDir)
}

function Start-HrmSupervisor {
    # Запуск supervisor'а/трея как отдельного скрытого процесса.
    # Повторный запуск не создаёт второй supervisor: если трей уже работает,
    # просто обновляем состояние (трей сам это увидит).
    param([string]$InstallDir = "", [string]$StateDir = "")
    if (-not $InstallDir) { $InstallDir = Get-HrmInstallDefaultDirSafe }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $trayScript = Get-HrmTrayScriptPath $InstallDir
    if (-not (Test-Path $trayScript)) { throw ("Не найден управляющий компонент: {0}" -f $trayScript) }
    $existing = Get-HrmSupervisorProcess
    if ($null -ne $existing) {
        Write-HrmLog "info" "HR Manager уже работает в трее."
        return [pscustomobject]@{ started = $false; already_running = $true; pid = $existing.Id }
    }
    $arguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
        "-File", ('"{0}"' -f $trayScript),
        "-InstallDir", ('"{0}"' -f $InstallDir),
        "-StateDir", ('"{0}"' -f $StateDir)
    )
    Start-HrmDetached -FilePath "powershell.exe" -Arguments $arguments -Hidden | Out-Null
    Set-HrmSupervisorState -StateDir $StateDir -State "starting" -Message "HR Manager запускается…"
    Write-HrmLog "info" "Управляющий компонент HR Manager запущен (значок в трее)."
    return [pscustomobject]@{ started = $true; already_running = $false }
}

function Start-HrmSupervisorIfUserSession {
    # Запуск значка в трее только в реальной пользовательской сессии:
    # в неинтерактивном/тестовом режиме процесс не поднимается (иначе тесты
    # и автоматизация плодили бы окна и процессы).
    param([string]$InstallDir = "", [string]$StateDir = "")
    $fromInstaller = $false
    if (Get-Variable -Name HrmOpenBrowser -Scope Global -ErrorAction SilentlyContinue) { $fromInstaller = [bool]$global:HrmOpenBrowser }
    if (-not (Test-HrmInteractive) -and -not $fromInstaller) { return $false }
    Start-HrmSupervisor -InstallDir $InstallDir -StateDir $StateDir | Out-Null
    return $true
}

function Get-HrmSupervisorProcess {
    # Ищем процесс трея по командной строке (без секретов в командной строке).
    try {
        $processes = @(Get-CimInstance -ClassName Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction Stop)
        foreach ($process in $processes) {
            $commandLine = [string]$process.CommandLine
            if ($commandLine -match "hrm-tray\.ps1") {
                return [pscustomobject]@{ Id = [int]$process.ProcessId; CommandLine = $commandLine }
            }
        }
    }
    catch { }
    return $null
}

function Stop-HrmSupervisor {
    # Закрытие управляющего компонента: контейнеры при этом продолжают работу,
    # если не запрошена их остановка (см. трей: явное предупреждение).
    param([string]$InstallDir = "", [string]$StateDir = "")
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $process = Get-HrmSupervisorProcess
    if ($null -ne $process) {
        try { Stop-Process -Id $process.Id -Force -ErrorAction Stop } catch { }
    }
    Set-HrmSupervisorState -StateDir $StateDir -State "stopped" -Message "Управляющий компонент закрыт. Docker и контейнеры работают."
    Write-HrmLog "info" "Управляющий компонент HR Manager закрыт."
}

function Invoke-HrmSupervisorCycle {
    # Один полный цикл: среда → стек → готовность → состояние.
    # Никаких бесконечных повторов: каждая фаза ограничена таймаутом.
    param(
        [string]$InstallDir = "",
        [string]$StateDir = "",
        [int]$Port = 0,
        [switch]$AllowInstall,
        [switch]$Interactive,
        [int]$TimeoutSeconds = 300
    )
    if (-not $InstallDir) { $InstallDir = Get-HrmInstallDefaultDirSafe }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $record = Get-HrmInstallRecord $StateDir
    if ($null -eq $record) {
        $message = "HR Manager ещё не установлен."
        Set-HrmSupervisorState -StateDir $StateDir -State "error" -Message $message
        return [pscustomobject]@{ ok = $false; state = "error"; message = $message }
    }
    if ($record.PSObject.Properties["port"] -and $record.port) { $Port = [int]$record.port }
    $port = Get-HrmPort $Port
    $baseUrl = Get-HrmBaseUrl $port

    Set-HrmSupervisorState -StateDir $StateDir -State "starting" -Message "Подготавливаем рабочую среду…" -Busy $true -Extra @{ port = $port }
    try {
        $prepare = Invoke-HrmDockerPrepare -InstallDir $InstallDir -StateDir $StateDir -Port $port -AllowInstall:$AllowInstall -Interactive:$Interactive -TimeoutSeconds $TimeoutSeconds
        if (-not $prepare.ok) {
            Set-HrmSupervisorState -StateDir $StateDir -State "error" -Message $prepare.message -Busy $false -Extra @{ code = $prepare.state; port = $port }
            return [pscustomobject]@{ ok = $false; state = $prepare.state; message = $prepare.message; needs_reboot = $prepare.needs_reboot }
        }
        Set-HrmSupervisorState -StateDir $StateDir -State "starting" -Message "Запускаем HR Manager…" -Busy $true -Extra @{ port = $port }
        $stack = Start-HrmStack -InstallDir $InstallDir -StateDir $StateDir
        if (-not $stack.ok) {
            Set-HrmSupervisorState -StateDir $StateDir -State "error" -Message $stack.message -Busy $false -Extra @{ code = "stack"; port = $port }
            return [pscustomobject]@{ ok = $false; state = "stack"; message = $stack.message }
        }
        Wait-HrmReady $baseUrl $TimeoutSeconds
        Set-HrmSupervisorState -StateDir $StateDir -State "ready" -Message "HR Manager готов." -Busy $false -Extra @{ port = $port; url = $baseUrl }
        return [pscustomobject]@{ ok = $true; state = "ready"; message = "HR Manager готов."; url = $baseUrl }
    }
    catch {
        $message = Redact-HrmText $_.Exception.Message
        Set-HrmSupervisorState -StateDir $StateDir -State "error" -Message $message -Busy $false -Extra @{ code = "exception"; port = $port }
        return [pscustomobject]@{ ok = $false; state = "error"; message = $message }
    }
}
