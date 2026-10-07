# HR Manager — значок в системном трее (управляющий компонент пилота).
#
# Запускается:
#   - ярлыком автозапуска после входа в Windows (ярлык создаёт движок):
#     powershell.exe -WindowStyle Hidden -File hrm-tray.ps1
#   - действием движка: hr-manager.ps1 -Action tray
#
# Что делает: показывает состояние («Запускается / Готово / Ошибка») и меню
# («Открыть HR Manager», «Проверить состояние», «Перезапустить приложение»,
# «Создать отчёт для поддержки», «Остановить приложение», автозапуск, «Выйти»).
# Всю работу выполняет движок hr-manager.ps1 отдельным скрытым процессом.
#
# ЕДИНСТВЕННОСТЬ: именованный mutex «Local\HRManagerPilotSupervisor» — повторный
# запуск не создаёт второй supervisor, а тихо завершается.
#
# Секретов в этом процессе нет: только пути и действия.

#requires -Version 5.1

[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [string]$StateDir = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$script:EngineDir = Join-Path $PSScriptRoot "engine"
foreach ($module in @("Common", "Secrets", "Preflight", "Compose", "Bootstrap", "Update",
        "Diagnostics", "Install", "Crypto", "Channel", "Lan", "SupportBundle",
        "Docker", "Supervisor", "Tray")) {
    Import-Module (Join-Path $script:EngineDir "$module.psm1") -Force -ErrorAction Stop
}

$global:HrmNonInteractive = $true
$global:HrmOpenBrowser = $true

if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
if (-not $StateDir) { $StateDir = Get-HrmStateDir }

# Единственный supervisor на пользователя. Второй запуск — тихий выход.
$lock = Enter-HrmSupervisorLock
if (-not $lock.acquired) {
    exit 0
}

try {
    Set-HrmSupervisorState -StateDir $StateDir -State "starting" -Message "Запускаем HR Manager…"
    $record = Get-HrmInstallRecord $StateDir
    if ($null -eq $record) {
        Set-HrmSupervisorState -StateDir $StateDir -State "error" -Message "HR Manager не установлен."
        exit 1
    }
    # Автозапуск: включён по умолчанию после установки, отключается пользователем
    # из меню трея (StateDir\autostart.json — источник истины).
    $autostart = Get-HrmAutostartState -StateDir $StateDir
    if (-not $autostart.shortcut_exists -and -not $autostart.enabled) {
        try { Enable-HrmAutostart -InstallDir $InstallDir -StateDir $StateDir | Out-Null } catch { }
    }
    # Полный цикл подготовки и запуска — отдельным скрытым процессом, чтобы
    # значок в трее оставался отзывчивым.
    $engineScript = Join-Path $PSScriptRoot "hr-manager.ps1"
    $logFile = Get-HrmTimestampedLogFile -StateDir $StateDir -Action "supervise"
    Start-HrmEngineProcess -ScriptPath $engineScript -Action "supervise" -InstallDir $InstallDir -StateDir $StateDir -LogFile $logFile | Out-Null
    Start-HrmTrayUi -InstallDir $InstallDir -StateDir $StateDir | Out-Null
}
catch {
    $message = Redact-HrmText $_.Exception.Message
    try { Set-HrmSupervisorState -StateDir $StateDir -State "error" -Message $message } catch { }
    Write-HrmLog "error" ("Управляющий компонент остановлен: " + $message)
    exit 1
}
finally {
    Exit-HrmSupervisorLock $lock
}
exit 0
