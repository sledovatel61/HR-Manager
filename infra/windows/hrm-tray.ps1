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
        "Diagnostics", "Install", "Snapshot", "Crypto", "Channel", "Lan", "SupportBundle",
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

    # Что делать значку: идёт установка, установка упала/прервалась, уже идёт
    # операция движка (порождать вторую нельзя), автоматические повторы
    # исчерпаны или начинается обычная работа.
    $plan = Get-HrmTrayCurrentPlan -StateDir $StateDir -InstallDir $InstallDir
    $stored = Get-HrmSupervisorState -StateDir $StateDir
    # Значок НЕ затирает причину, которую записал движок: состояние «Запускается»
    # с текстом провала предполётной проверки (симптом пилотной машины) получалось
    # именно так — план значка перезаписывал сохранённую ошибку, а движок при этом
    # уже ничего не делал. Теперь: если ошибку записал другой (живой) процесс,
    # значок показывает «Ошибка» с этой причиной и действием для человека.
    $effective = Get-HrmTrayEffectiveState -PlanState $plan.state -PlanMessage $plan.message -PlanBusy $plan.busy -StoredState $stored.state -StoredMessage $stored.message -StoredPid $stored.pid -CurrentPid $PID
    $failureCount = -1
    if ($effective.state -eq "error" -and $stored.state -eq "error") {
        # Причина уже посчитана движком: значок её не удваивает (иначе каждый
        # перезапуск значка приближал бы «автоматические повторы остановлены»).
        $failureCount = (Get-HrmSupervisorFailureCount -StateDir $StateDir)
    }
    Set-HrmSupervisorState -StateDir $StateDir -State $effective.state -Message $effective.message -Busy $effective.busy -FailureCount $failureCount
    if ($plan.mode -eq "not_installed" -and -not $plan.open_available) {
        # «Не установлено» и файлы приложения отсутствуют: это не отказ движка,
        # а отсутствие приложения. Не накручиваем счётчик и завершаем значок.
        # Если Setup уже скопировал файлы, open_available оставляет трею живым:
        # пользователь сможет вручную установить Docker и продолжить настройку
        # пунктом «Открыть HR Manager», не переустанавливая Setup.exe.
        Set-HrmSupervisorState -StateDir $StateDir -State "error" -Message $plan.message -FailureCount 0
        exit 1
    }
    if ($plan.start_engine) {
        # Проверка ПЕРЕД порождением процесса. Критерий приёмки: два подряд входа
        # в Windows (или повторный запуск Setup.exe, который поднимает значок и
        # тут же запускает -Action install) дают ОДИН цикл движка. Признаки
        # идущей операции: живая блокировка действия, свежая отметка мастера
        # установки/обновления, busy с живым чужим pid в supervisor.json.
        # Второй движок — это второй compose build/up поверх первого: гонка за
        # порт и образы, перегрузка Docker/WSL и процессы, которые никто не
        # завершает (симптом пилотной машины: десятки powershell.exe).
        $inFlight = Test-HrmEngineOperationInFlight -StateDir $StateDir -IgnorePid $PID
        if ($inFlight.running) {
            Write-HrmLog "info" ("Движок не запускается: операция HR Manager уже выполняется ({0})." -f $inFlight.reason)
        }
        else {
            # Полный цикл подготовки и запуска (или продолжение прерванной
            # операции) — отдельным скрытым процессом, чтобы значок в трее
            # оставался отзывчивым. Порождение АТОМАРНО: блокировка действия
            # берётся до запуска процесса и передаётся потомку
            # (Start-HrmTrayEngineProcess, дефект P5.1 дораздела PR #55) —
            # иначе второй значок проходил ту же проверку и первые секунды
            # работали два движка.
            $engineScript = Join-Path $PSScriptRoot "hr-manager.ps1"
            $engine = Start-HrmTrayEngineProcess -Action $plan.engine_action -ScriptPath $engineScript -InstallDir $InstallDir -StateDir $StateDir
            if (-not $engine.started) {
                Write-HrmLog "warn" ("Движок не запускается: " + $engine.message)
            }
        }
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
