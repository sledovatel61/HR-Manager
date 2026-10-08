# Значок HR Manager в системном трее: состояние приложения и меню действий.
#
# Ответственность трея: показать состояние и выполнить ДЕЙСТВИЕ. Вся работа
# выполняется движком (hr-manager.ps1) отдельным скрытым процессом; трей не
# ходит в БД, не хранит секреты и не подменяет backend.
#
# Модель меню и состояние вынесены в чистые функции (Get-HrmTrayMenuModel,
# Get-HrmTrayIconKind, Get-HrmTrayTooltip, Get-HrmTrayExitPrompt) — их
# проверяют тесты без GUI. Сам интерфейс (Start-HrmTrayUi) использует
# WinForms NotifyIcon, который есть в Windows 10/11 «из коробки».

Set-StrictMode -Version 2.0

$script:TrayRequiredMenuActions = @("open", "check", "restart", "support-bundle", "stop", "exit")

function Get-HrmTrayStartupPlan {
    # Что должен делать значок ПРЯМО СЕЙЧАС. Одна точка правды и для запуска
    # (hrm-tray.ps1), и для обновления значка каждые 5 секунд — расхождений
    # между «что видит пользователь» и «что делает компонент» быть не должно.
    #
    #   installing      — идёт установка/обновление (отметка мастера установки
    #                     свежая): значок живой, показывает ход, но не даёт
    #                     мешать работе мастера (дефект P2 ревью: раньше значок
    #                     выходил с ошибкой и до конца установки его не было);
    #   install_failed  — установка/обновление завершились ошибкой: значок
    #                     остаётся и показывает, что делать;
    #   install_stale   — отметка установки осталась, но установка не идёт и
    #                     приложения нет: честно просим запустить Setup.exe заново;
    #   resuming        — есть незавершённое обновление/подготовка среды: значок
    #                     продолжает операцию (resume) — «после перезагрузки
    #                     HR Manager продолжит сам»;
    #   not_installed   — приложения нет (значок в каталоге установки, поэтому
    #                     это редкий случай) — честная ошибка;
    #   normal          — обычная работа: поднять среду и показать состояние.
    param(
        [bool]$HasRecord = $false,
        [string]$MarkerStatus = "",
        [bool]$MarkerFresh = $false,
        [string]$MarkerMessage = "",
        [bool]$ResumePending = $false,
        [bool]$InstallDirReady = $false
    )
    if ($MarkerStatus -eq "failed") {
        $message = if ($MarkerMessage) { $MarkerMessage } else { "Установка HR Manager не завершилась." }
        return [pscustomobject]@{
            mode = "install_failed"; state = "error"; busy = $false; start_engine = $false
            engine_action = ""; notify_event = "install"; message = $message
        }
    }
    if ($MarkerStatus -and $MarkerStatus -ne "failed" -and $MarkerFresh) {
        $message = if ($MarkerMessage) { $MarkerMessage } else { "Устанавливаем HR Manager…" }
        return [pscustomobject]@{
            mode = "installing"; state = "starting"; busy = $true; start_engine = $false
            engine_action = ""; notify_event = "install"; message = $message
        }
    }
    if ($MarkerStatus -and $MarkerStatus -ne "failed" -and -not $HasRecord) {
        if ($InstallDirReady) {
            # Отметка устарела (перезагрузка/закрытие мастера), но файлы
            # установки уже разложены: движок сам доведёт первичную установку —
            # человеку не нужно искать Setup.exe и запускать его заново.
            return [pscustomobject]@{
                mode = "resuming"; state = "starting"; busy = $true; start_engine = $true
                engine_action = "resume"; notify_event = "install"; message = "Продолжаем установку HR Manager…"
            }
        }
        return [pscustomobject]@{
            mode = "install_stale"; state = "error"; busy = $false; start_engine = $false
            engine_action = ""; notify_event = "install"
            message = "Установка не завершилась. Запустите Setup.exe заново — данные и настройки не пострадали."
        }
    }
    if ($ResumePending) {
        return [pscustomobject]@{
            mode = "resuming"; state = "starting"; busy = $true; start_engine = $true
            engine_action = "resume"; notify_event = "install"; message = "Продолжаем установку HR Manager…"
        }
    }
    if (-not $HasRecord) {
        return [pscustomobject]@{
            mode = "not_installed"; state = "error"; busy = $false; start_engine = $false
            engine_action = ""; notify_event = ""; message = "HR Manager не установлен."
        }
    }
    return [pscustomobject]@{
        mode = "normal"; state = "starting"; busy = $false; start_engine = $true
        engine_action = "supervise"; notify_event = ""; message = "Запускаем HR Manager…"
    }
}

function Get-HrmTrayCurrentPlan {
    # Планировщик по РЕАЛЬНЫМ файлам состояния: отметка установки, запись об
    # установке, незавершённое обновление (журнал обновления), отложенная
    # подготовка среды после перезагрузки.
    param([string]$StateDir = "", [string]$InstallDir = "")
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    if (-not $InstallDir) { $InstallDir = Get-HrmInstallDefaultDirSafe }
    $marker = Get-HrmSetupMarker -StateDir $StateDir
    $hasRecord = ($null -ne (Get-HrmInstallRecord $StateDir))
    # Файлы установки уже разложены мастером ({app})\infra\compose.pilot.yml —
    # значит первичную установку можно довести без повторного запуска Setup.exe.
    $installDirReady = (Test-Path (Join-Path $InstallDir "infra\compose.pilot.yml"))
    $resumePending = $false
    $journal = Get-HrmUpdateJournal $StateDir
    if (Test-Path $journal) {
        $phase = ""
        try {
            $data = Get-HrmJsonFile $journal
            if ($null -ne $data -and $data.PSObject.Properties["phase"]) { $phase = [string]$data.phase }
        }
        catch { $phase = "" }
        # Откат завершён (phase=rollback) — это НЕ незавершённая работа:
        # автоматически повторять обновление нельзя, решает владелец.
        if ($phase -and $phase -ne "rollback") { $resumePending = $true }
    }
    if (-not $resumePending -and $null -ne (Get-HrmPendingDockerOperation $StateDir)) { $resumePending = $true }
    return (Get-HrmTrayStartupPlan -HasRecord $hasRecord -MarkerStatus $marker.status -MarkerFresh $marker.fresh -MarkerMessage $marker.message -ResumePending $resumePending -InstallDirReady $installDirReady)
}

function Get-HrmTrayIconKind {
    # ready | starting | error | stopped
    param([string]$State, [bool]$Busy = $false)
    if ($Busy) { return "starting" }
    switch ($State) {
        "ready" { return "ready" }
        "starting" { return "starting" }
        "error" { return "error" }
        "stopped" { return "stopped" }
        default { return "starting" }
    }
}

function Get-HrmTrayTooltip {
    param([string]$State, [string]$Message = "")
    $label = Get-HrmSupervisorStatusText $State
    $text = ("HR Manager — {0}" -f $label)
    if ($Message) { $text = "$text`n$Message" }
    return $text
}

function Get-HrmTrayMenuModel {
    # Модель меню: пункт, действие и доступность. Проверяется тестами.
    param(
        [string]$State = "unknown",
        [bool]$Busy = $false,
        [bool]$AutostartEnabled = $false,
        [string]$Message = ""
    )
    $ready = ($State -eq "ready")
    $label = Get-HrmSupervisorStatusText $State
    $statusDetail = if ($Message) { $Message } else { "HR Manager" }
    $model = @()
    $model += [pscustomobject]@{ id = "status"; text = ("Состояние: {0}" -f $label); action = ""; enabled = $false; separator = $false }
    foreach ($line in ($statusDetail -split "`n")) {
        if (-not $line) { continue }
        $model += [pscustomobject]@{ id = "status-detail"; text = ("   " + $line); action = ""; enabled = $false; separator = $false }
    }
    $model += [pscustomobject]@{ id = "open"; text = "Открыть HR Manager"; action = "open"; enabled = ($ready -and -not $Busy); separator = $true }
    $model += [pscustomobject]@{ id = "check"; text = "Проверить состояние"; action = "check"; enabled = (-not $Busy); separator = $false }
    $model += [pscustomobject]@{ id = "restart"; text = "Перезапустить приложение"; action = "restart"; enabled = (-not $Busy); separator = $false }
    $model += [pscustomobject]@{ id = "support-bundle"; text = "Создать отчёт для поддержки"; action = "support-bundle"; enabled = (-not $Busy); separator = $false }
    $model += [pscustomobject]@{ id = "stop"; text = "Остановить приложение"; action = "stop"; enabled = (-not $Busy); separator = $true }
    $autostartText = if ($AutostartEnabled) { "Автозапуск после входа в Windows: включён" } else { "Автозапуск после входа в Windows: выключен" }
    $model += [pscustomobject]@{ id = "autostart"; text = $autostartText; action = "autostart"; enabled = (-not $Busy); separator = $false }
    $model += [pscustomobject]@{ id = "exit"; text = "Выйти"; action = "exit"; enabled = $true; separator = $true }
    return $model
}

function Test-HrmTrayMenuModel {
    # Контракт меню: все обязательные действия присутствуют и подписаны.
    param($Model)
    $actions = @($Model | Where-Object { $_.action } | Select-Object -ExpandProperty action)
    foreach ($required in $script:TrayRequiredMenuActions) {
        if ($actions -notcontains $required) { return $false }
    }
    return $true
}

function Get-HrmTrayExitPrompt {
    # Явное предупреждение о последствиях закрытия управляющего компонента.
    return [pscustomobject]@{
        title = "Закрыть значок HR Manager?"
        text = ("Если закрыть значок, HR Manager продолжит работать в фоне, а данные останутся на месте. " +
            "Docker и контейнеры при этом не останавливаются.`n`n" +
            "«Да» — закрыть только значок (приложение продолжит работу).`n" +
            "«Нет» — остановить HR Manager и закрыть значок (данные сохранятся).`n" +
            "«Отмена» — оставить как есть.")
    }
}

function Get-HrmTrayStopPrompt {
    return [pscustomobject]@{
        title = "Остановить HR Manager?"
        text = ("Приложение перестанет открываться, пока вы снова его не запустите. `n" +
            "Данные, пользователи, вложения, лицензия и резервные копии СОХРАНЯЮТСЯ.`n" +
            "Docker Desktop останавливать не нужно.")
    }
}

function Get-HrmTrayBalloon {
    # Уведомление в трее: понятный русский текст без технических деталей.
    param([string]$State, [string]$Message = "", [string]$Event = "")
    if ($Event -eq "action-start") {
        return [pscustomobject]@{ title = "HR Manager"; text = $Message; kind = "info" }
    }
    if ($Event -eq "action-done") {
        return [pscustomobject]@{ title = "HR Manager"; text = $Message; kind = "info" }
    }
    if ($Event -eq "install") {
        # Установка/обновление: понятный заголовок и ход операции.
        return [pscustomobject]@{ title = "HR Manager устанавливается"; text = $Message; kind = "info" }
    }
    if ($State -eq "ready") {
        return [pscustomobject]@{ title = "HR Manager готов"; text = "Приложение открывается в браузере. Значок остаётся в трее."; kind = "info" }
    }
    if ($State -eq "error") {
        return [pscustomobject]@{ title = "HR Manager — нужна помощь"; text = ("{0} Нажмите «Создать отчёт для поддержки»." -f $Message); kind = "error" }
    }
    if ($State -eq "starting") {
        return [pscustomobject]@{ title = "HR Manager запускается"; text = "HR Manager запускается: подготавливаем рабочую среду. Это может занять несколько минут."; kind = "info" }
    }
    return [pscustomobject]@{ title = "HR Manager"; text = $Message; kind = "info" }
}

function Get-HrmTraySnapshot {
    # Быстрый снимок состояния для значка: контейнеры + готовность приложения.
    param([string]$InstallDir = "", [string]$StateDir = "")
    if (-not $InstallDir) { $InstallDir = Get-HrmInstallDefaultDirSafe }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $record = Get-HrmInstallRecord $StateDir
    $port = Get-HrmPort
    if ($null -ne $record -and $record.PSObject.Properties["port"] -and $record.port) { $port = [int]$record.port }
    $baseUrl = Get-HrmBaseUrl $port
    if ($null -eq $record) {
        return [pscustomobject]@{ state = "error"; message = "HR Manager не установлен."; url = $baseUrl; port = $port }
    }
    try {
        $stack = Get-HrmStackState -InstallDir $InstallDir -StateDir $StateDir
    }
    catch {
        return [pscustomobject]@{ state = "error"; message = "Не удалось проверить состояние приложения."; url = $baseUrl; port = $port }
    }
    if ($stack.state -eq "running") {
        $ready = (Test-HrmBackendReady $baseUrl) -and (Test-HrmFrontendReady $baseUrl)
        if ($ready) {
            return [pscustomobject]@{ state = "ready"; message = "Приложение готово."; url = $baseUrl; port = $port }
        }
        return [pscustomobject]@{ state = "starting"; message = "Приложение почти готово."; url = $baseUrl; port = $port }
    }
    if ($stack.state -eq "stopped" -or $stack.state -eq "absent") {
        return [pscustomobject]@{ state = "stopped"; message = "Приложение остановлено."; url = $baseUrl; port = $port }
    }
    return [pscustomobject]@{ state = "starting"; message = $stack.message; url = $baseUrl; port = $port }
}

function Get-HrmTrayEngineScript {
    param([string]$InstallDir = "")
    if (-not $InstallDir) { $InstallDir = Get-HrmInstallDefaultDirSafe }
    return (Join-Path $InstallDir "infra\windows\hr-manager.ps1")
}

function Start-HrmTrayAction {
    # Запуск действия движка отдельным скрытым процессом + файл-блокировка,
    # чтобы два нажатия не создали две параллельные операции.
    param(
        [Parameter(Mandatory = $true)][string]$Action,
        [string]$InstallDir = "",
        [string]$StateDir = ""
    )
    if (-not $InstallDir) { $InstallDir = Get-HrmInstallDefaultDirSafe }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $lock = Enter-HrmActionLock -StateDir $StateDir -Action $Action
    if (-not $lock.acquired) {
        return [pscustomobject]@{ started = $false; message = $lock.message }
    }
    $scriptPath = Get-HrmTrayEngineScript $InstallDir
    if (-not (Test-Path $scriptPath)) {
        Exit-HrmActionLock -StateDir $StateDir
        return [pscustomobject]@{ started = $false; message = "Не найден управляющий файл HR Manager. Переустановите Setup.exe." }
    }
    $logFile = Get-HrmTimestampedLogFile -StateDir $StateDir -Action $Action
    $process = Start-HrmEngineProcess -ScriptPath $scriptPath -Action $Action -InstallDir $InstallDir -StateDir $StateDir -LogFile $logFile
    Update-HrmActionLockProcess -StateDir $StateDir -ProcessId ([int]$process.Id)
    return [pscustomobject]@{ started = $true; message = ""; pid = [int]$process.Id; log = $logFile }
}

function Update-HrmActionLockProcess {
    # Блокировка хранит pid процесса-потомка: пока он жив, операция идёт.
    param([string]$StateDir, [int]$ProcessId)
    Set-HrmJsonFile $StateDir (Split-Path (Get-HrmActionLockFile $StateDir) -Leaf) ([ordered]@{
            action = "engine"
            pid = $ProcessId
            started_at = (Get-Date).ToString("o")
        })
}

function Test-HrmTrayActionRunning {
    # Идёт ли сейчас действие движка (по pid из файла-блокировки).
    param([string]$StateDir)
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $file = Get-HrmActionLockFile $StateDir
    if (-not (Test-Path $file)) { return $false }
    $data = $null
    try { $data = Get-HrmJsonFile $file } catch { return $false }
    if ($null -eq $data) { return $false }
    $lockPid = 0
    if ($data.PSObject.Properties["pid"]) { $lockPid = [int]$data.pid }
    if ($lockPid -le 0) { Exit-HrmActionLock -StateDir $StateDir; return $false }
    try {
        $alive = ($null -ne (Get-Process -Id $lockPid -ErrorAction SilentlyContinue))
    }
    catch { $alive = $false }
    if (-not $alive) {
        Exit-HrmActionLock -StateDir $StateDir
        return $false
    }
    return $true
}

function Get-HrmTrayLastSupportBundle {
    param([string]$StateDir)
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $file = Join-Path $StateDir "last-support-bundle.json"
    if (-not (Test-Path $file)) { return $null }
    try { return (Get-HrmJsonFile $file) } catch { return $null }
}

function Start-HrmTrayUi {
    # Интерфейс значка. Запускается только из hrm-tray.ps1 (после проверки
    # единственности supervisor'а).
    param([string]$InstallDir = "", [string]$StateDir = "")
    if (-not $InstallDir) { $InstallDir = Get-HrmInstallDefaultDirSafe }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing

    $icon = New-Object System.Windows.Forms.NotifyIcon
    $menu = New-Object System.Windows.Forms.ContextMenuStrip
    # Изменяемое состояние в хэш-таблице: обработчики WinForms не разделяют
    # локальные переменные функции между вызовами.
    $viewState = @{ last = "" }

    $applyState = {
        param([string]$State, [string]$Message, [bool]$Busy, [bool]$Notify, [string]$Event = "")
        $kind = Get-HrmTrayIconKind -State $State -Busy $Busy
        switch ($kind) {
            "ready" { $icon.Icon = [System.Drawing.SystemIcons]::Application }
            "starting" { $icon.Icon = [System.Drawing.SystemIcons]::Information }
            "stopped" { $icon.Icon = [System.Drawing.SystemIcons]::Warning }
            default { $icon.Icon = [System.Drawing.SystemIcons]::Error }
        }
        $icon.Text = (Get-HrmTrayTooltip -State $State -Message $Message)
        if ($icon.Text.Length -gt 120) { $icon.Text = $icon.Text.Substring(0, 120) }
        $autostart = Get-HrmAutostartState -StateDir $StateDir
        $model = Get-HrmTrayMenuModel -State $State -Busy $Busy -AutostartEnabled ([bool]$autostart.enabled) -Message $Message
        $menu.Items.Clear()
        foreach ($item in $model) {
            if ($item.separator) { [void]$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) }
            $entry = New-Object System.Windows.Forms.ToolStripMenuItem
            $entry.Text = $item.text
            $entry.Enabled = [bool]$item.enabled
            if ($item.action) {
                $actionId = $item.action
                $entry.add_Click({ Invoke-HrmTrayMenuAction -ActionId $actionId -InstallDir $InstallDir -StateDir $StateDir }.GetNewClosure())
            }
            [void]$menu.Items.Add($entry)
        }
        if ($Notify -and $State -ne $viewState.last) {
            $balloon = Get-HrmTrayBalloon -State $State -Message $Message -Event $Event
            $icon.ShowBalloonTip(7000, $balloon.title, $balloon.text, [System.Windows.Forms.ToolTipIcon]::Info)
        }
        $viewState.last = $State
    }

    $refresh = {
        try {
            $plan = Get-HrmTrayCurrentPlan -StateDir $StateDir -InstallDir $InstallDir
            if ($plan.mode -ne "normal") {
                $message = $plan.message
                if ($plan.mode -eq "installing" -or $plan.mode -eq "resuming") {
                    # Пока идёт установка/обновление, показываем более подробный
                    # ход от движка («Подготавливаем рабочую среду…», «Ожидаем
                    # запуск службы контейнеров…»), если он уже записан.
                    $stored = Get-HrmSupervisorState -StateDir $StateDir
                    if ($stored.message -and ($stored.state -eq "starting" -or $stored.state -eq "error")) { $message = $stored.message }
                }
                & $applyState $plan.state $message $plan.busy $true $plan.notify_event
                return
            }
            $running = Test-HrmTrayActionRunning -StateDir $StateDir
            $stored = Get-HrmSupervisorState -StateDir $StateDir
            if ($running) {
                & $applyState "starting" $stored.message $true $false
                return
            }
            $snapshot = Get-HrmTraySnapshot -InstallDir $InstallDir -StateDir $StateDir
            $state = $snapshot.state
            $message = $snapshot.message
            if ($stored.state -eq "error" -and $state -ne "ready") {
                $state = "error"
                if ($stored.message) { $message = $stored.message }
            }
            & $applyState $state $message $false $true
        }
        catch {
            & $applyState "error" (Redact-HrmText $_.Exception.Message) $false $true
        }
    }

    $icon.ContextMenuStrip = $menu
    $icon.Visible = $true
    $icon.add_DoubleClick({ Invoke-HrmTrayMenuAction -ActionId "open" -InstallDir $InstallDir -StateDir $StateDir })
    & $refresh
    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 5000
    $timer.add_Tick($refresh)
    $timer.Start()
    [System.Windows.Forms.Application]::Run()
    $timer.Stop()
    $icon.Visible = $false
    $icon.Dispose()
    return 0
}

function Invoke-HrmTrayMenuAction {
    # Обработчик пункта меню. Возвращает текст уведомления (для тестов).
    param(
        [Parameter(Mandatory = $true)][string]$ActionId,
        [string]$InstallDir = "",
        [string]$StateDir = "",
        [switch]$NonInteractive
    )
    if (-not $InstallDir) { $InstallDir = Get-HrmInstallDefaultDirSafe }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }

    if ($ActionId -eq "check") {
        $snapshot = Get-HrmTraySnapshot -InstallDir $InstallDir -StateDir $StateDir
        $label = Get-HrmSupervisorStatusText $snapshot.state
        Set-HrmSupervisorState -StateDir $StateDir -State $snapshot.state -Message $snapshot.message
        return ("Состояние HR Manager: {0}. {1}" -f $label, $snapshot.message)
    }
    if ($ActionId -eq "exit") {
        # Явное предупреждение о последствиях. Значок закрывается, приложение
        # продолжает работать (Docker и контейнеры не останавливаются).
        $text = ""
        if (-not $NonInteractive) {
            $prompt = Get-HrmTrayExitPrompt
            $answer = [System.Windows.Forms.MessageBox]::Show($prompt.text, $prompt.title, [System.Windows.Forms.MessageBoxButtons]::YesNoCancel, [System.Windows.Forms.MessageBoxIcon]::Question)
            if ($answer -eq [System.Windows.Forms.DialogResult]::Cancel) { return "Отменено" }
            if ($answer -eq [System.Windows.Forms.DialogResult]::No) {
                $stop = Stop-HrmApp -InstallDir $InstallDir -StateDir $StateDir
                $text = "HR Manager остановлен, данные сохранены."
                Set-HrmSupervisorState -StateDir $StateDir -State "stopped" -Message $text
                [System.Windows.Forms.Application]::Exit()
                return $text
            }
            $text = "Значок закрыт. HR Manager продолжает работать."
        }
        else {
            $text = "Значок закрыт (неинтерактивный режим)."
        }
        Set-HrmSupervisorState -StateDir $StateDir -State "stopped" -Message $text
        [System.Windows.Forms.Application]::Exit()
        return $text
    }
    if ($ActionId -eq "autostart") {
        $state = Get-HrmAutostartState -StateDir $StateDir
        if ($state.enabled) {
            Disable-HrmAutostart -StateDir $StateDir | Out-Null
            return "Автозапуск выключен: HR Manager не будет запускаться автоматически."
        }
        Enable-HrmAutostart -InstallDir $InstallDir -StateDir $StateDir | Out-Null
        return "Автозапуск включён: HR Manager поднимется после входа в Windows."
    }
    if ($ActionId -eq "stop") {
        if (-not $NonInteractive) {
            $prompt = Get-HrmTrayStopPrompt
            $answer = [System.Windows.Forms.MessageBox]::Show($prompt.text, $prompt.title, [System.Windows.Forms.MessageBoxButtons]::YesNo, [System.Windows.Forms.MessageBoxIcon]::Warning)
            if ($answer -ne [System.Windows.Forms.DialogResult]::Yes) { return "Отменено" }
        }
    }
    $result = Start-HrmTrayAction -Action $ActionId -InstallDir $InstallDir -StateDir $StateDir
    if (-not $result.started) { return $result.message }
    Set-HrmSupervisorState -StateDir $StateDir -State "starting" -Message "Выполняется действие: $ActionId" -Busy $true
    return "Действие запущено: $ActionId"
}
