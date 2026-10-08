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
    # Автозапуск: включён по умолчанию после установки, отключается пользователем
    # из меню трея (StateDir\autostart.json — источник истины). Явное «выключено»
    # больше НЕ переигрывается: значок только приводит ярлык в соответствие с
    # выбором человека — в том числе если мастер установки положил ярлык заново
    # при повторной установке или обновлении.
    try { Sync-HrmAutostart -InstallDir $InstallDir -StateDir $StateDir | Out-Null }
    catch { Write-HrmLog "warn" ("Автозапуск не настроен автоматически: " + (Redact-HrmText $_.Exception.Message)) }

    # Что делать значку: идёт установка, установка упала/прервалась, нужно
    # продолжить операцию или начинается обычная работа.
    $plan = Get-HrmTrayCurrentPlan -StateDir $StateDir -InstallDir $InstallDir
    Set-HrmSupervisorState -StateDir $StateDir -State $plan.state -Message $plan.message -Busy $plan.busy
    if ($plan.mode -eq "not_installed") {
        Set-HrmSupervisorState -StateDir $StateDir -State "error" -Message $plan.message
        exit 1
    }
    if ($plan.start_engine) {
        # Полный цикл подготовки и запуска (или продолжение прерванной операции) —
        # отдельным скрытым процессом, чтобы значок в трее оставался отзывчивым.
        $engineScript = Join-Path $PSScriptRoot "hr-manager.ps1"
        $logFile = Get-HrmTimestampedLogFile -StateDir $StateDir -Action $plan.engine_action
        Start-HrmEngineProcess -ScriptPath $engineScript -Action $plan.engine_action -InstallDir $InstallDir -StateDir $StateDir -LogFile $logFile | Out-Null
    }
    else {
        $note = ("Значок работает в состоянии «{0}»: {1}" -f $plan.mode, $plan.message)
        Write-HrmLog "info" $note
    }
    # Во время установки значок НЕ выходит: он показывает ход и остаётся
    # доступным, если установка не удалась (дефект P2 ревью).
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
