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
    # (hrm-tray.ps1), и для каждого тика значка (интервал тика зависит от
    # состояния — см. Get-HrmTrayRefreshIntervalMs): расхождений между «что
    # видит пользователь» и «что делает компонент» быть не должно.
    #
    #   installing      — идёт установка/обновление (отметка мастера установки
    #                     свежая): значок живой, показывает ход, но не даёт
    #                     мешать работе мастера (дефект P2 ревью: раньше значок
    #                     выходил с ошибкой и до конца установки его не было);
    #   install_failed  — установка/обновление завершились ошибкой: значок
    #                     остаётся и показывает, что делать;
    #   install_stale   — отметка установки осталась, но установка не идёт и
    #                     приложения нет: честно просим запустить Setup.exe заново;
    #   busy            — операция движка УЖЕ идёт (живая блокировка действия,
    #                     свежая отметка мастера или busy с живым pid в
    #                     supervisor.json): значок показывает сохранённый ход
    #                     работы и НЕ порождает второй движок (регрессия D2:
    #                     каждый вход в Windows и каждый повторный запуск мастера
    #                     добавляли процесс, а compose-операции шли параллельно —
    #                     отсюда десятки powershell.exe и лишняя нагрузка на
    #                     Docker/WSL2);
    #   retry_exhausted — движок закончился ошибкой N раз ПОДРЯД: автоматические
    #                     повторы остановлены, показаны причина и действие.
    #                     Повтор возможен только по явному действию человека
    #                     (пункт меню сбрасывает счётчик). Без этого значок при
    #                     каждом входе в Windows снова запускал движок, который
    #                     снова отказывал по той же причине;
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
        [bool]$InstallDirReady = $false,
        [bool]$EngineOperationRunning = $false,
        [string]$EngineOperationReason = "",
        [int]$FailureCount = 0,
        [string]$FailureMessage = "",
        [int]$MaxConsecutiveFailures = 3
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
    if ($EngineOperationRunning) {
        # Операция движка идёт (её pid жив): показываем ход работы и не
        # запускаем второй движок. Значок остаётся живым и отзывчивым.
        $message = "Операция HR Manager уже выполняется — ожидаем завершения."
        if ($EngineOperationReason) { $message = ("Операция HR Manager уже выполняется ({0}) — ожидаем завершения." -f $EngineOperationReason) }
        return [pscustomobject]@{
            mode = "busy"; state = "starting"; busy = $true; start_engine = $false
            engine_action = ""; notify_event = ""; message = $message
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
    if ($FailureCount -ge $MaxConsecutiveFailures -and $MaxConsecutiveFailures -gt 0) {
        $reason = if ($FailureMessage) { $FailureMessage } else { "причина сохранена в состоянии приложения." }
        $message = ("Автоматические повторы запуска остановлены (подряд идущих отказов: {0}). Причина: {1} Создайте отчёт для поддержки (пункт меню) и передайте его разработчику; повторить запуск вручную можно там же." -f $FailureCount, $reason)
        return [pscustomobject]@{
            mode = "retry_exhausted"; state = "error"; busy = $false; start_engine = $false
            engine_action = ""; notify_event = ""; message = $message
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
    # Идёт ли уже операция движка (свой pid игнорируем): по живой блокировке
    # действия, свежей отметке мастера или busy с живым pid в supervisor.json.
    $inFlight = Test-HrmEngineOperationInFlight -StateDir $StateDir -IgnorePid $PID
    # Сколько раз ПОДРЯД движок закончился ошибкой и что он написал (причина).
    $failureCount = Get-HrmSupervisorFailureCount -StateDir $StateDir
    $failureMessage = ""
    $stored = Get-HrmSupervisorState -StateDir $StateDir
    if ($stored.state -eq "error") { $failureMessage = [string]$stored.message }
    return (Get-HrmTrayStartupPlan -HasRecord $hasRecord -MarkerStatus $marker.status -MarkerFresh $marker.fresh -MarkerMessage $marker.message -ResumePending $resumePending -InstallDirReady $installDirReady -EngineOperationRunning $inFlight.running -EngineOperationReason $inFlight.reason -FailureCount $failureCount -FailureMessage $failureMessage -MaxConsecutiveFailures (Get-HrmSupervisorMaxConsecutiveFailures))
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

function Limit-HrmBalloonText {
    # Пределы всплывающего уведомления Windows: заголовок — 63 символа, текст —
    # 255. Причина отказа теперь содержит подробности (какие каталоги плагинов
    # проверены, какая версия найдена), и длинный текст обрезается ЗДЕСЬ, а не
    # роняет обработчик значка на каждом обновлении. Полный текст остаётся в
    # пункте меню «Состояние» и в журнале.
    param([string]$Value = "", [int]$Max = 255)
    $text = [string]$Value
    if ($Max -le 0) { return "" }
    if ($text.Length -le $Max) { return $text }
    if ($Max -le 3) { return $text.Substring(0, $Max) }
    return ($text.Substring(0, $Max - 3) + "...")
}

function Get-HrmTrayBalloon {
    # Уведомление в трее: понятный русский текст без технических деталей,
    # обрезанный до пределов Windows (см. Limit-HrmBalloonText).
    param([string]$State, [string]$Message = "", [string]$Event = "")
    $balloon = Get-HrmTrayBalloonCore -State $State -Message $Message -Event $Event
    return [pscustomobject]@{
        title = (Limit-HrmBalloonText $balloon.title 63)
        text = (Limit-HrmBalloonText $balloon.text 255)
        kind = [string]$balloon.kind
    }
}

function Get-HrmTrayBalloonCore {
    # Содержимое уведомления (без обрезки): заголовок, текст, значок.
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
        # Причина отказа теперь подробная (перечень каталогов плагинов, найденная
        # версия, stderr пробы) и длиннее предела уведомления. Обрезается
        # ПРИЧИНА, а не весь текст: действие для человека обязано остаться в
        # уведомлении, иначе пользователь видел бы обрывок диагностики без
        # следующего шага (ровно тот симптом, из-за которого отказ выглядел
        # необъяснимым).
        $action = "Нажмите «Создать отчёт для поддержки»."
        $room = 255 - $action.Length - 1
        $reason = Limit-HrmBalloonText $Message $room
        $text = ("{0} {1}" -f $reason, $action).Trim()
        return [pscustomobject]@{ title = "HR Manager — нужна помощь"; text = $text; kind = "error" }
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

function Start-HrmTrayEngineProcess {
    # Порождение движка из значка — АТОМАРНО (дефект P5.1 дораздела PR #55).
    #
    # Раньше значок делал это двумя раздельными шагами:
    # Test-HrmEngineOperationInFlight -> Start-HrmEngineProcess. Проверка и
    # запуск не были атомарны, поэтому второй значок (два входа в Windows
    # подряд; повторный запуск Setup.exe, который поднимает значок и тут же
    # запускает -Action install) успевал пройти ту же проверку, и первые
    # секунды работали ДВА движка: две параллельные compose-операции, гонка за
    # порт и образы, процессы, которые никто не завершает.
    #
    # Теперь блокировка действия берётся ДО порождения (та же схема, что в
    # Start-HrmTrayAction) и передаётся потомку: в файле блокировки оказывается
    # pid процесса движка, и любой второй значок увидит «операция выполняется».
    # Потомок входит в эту же блокировку повторно (служебная отметка
    # action="engine") и снимает её сам по завершении.
    #
    # Возвращает @{ started; reason; message; pid; log }: reason — машинный
    # признак отказа (in_flight/locked/no_script/no_pid) для журнала и тестов.
    param(
        [Parameter(Mandatory = $true)][string]$Action,
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [string]$InstallDir = "",
        [string]$StateDir = ""
    )
    if (-not $InstallDir) { $InstallDir = Get-HrmInstallDefaultDirSafe }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    # Признаки идущей операции шире блокировки действия: свежая отметка мастера
    # установки/обновления и busy с живым чужим pid в supervisor.json.
    $inFlight = Test-HrmEngineOperationInFlight -StateDir $StateDir -IgnorePid $PID
    if ($inFlight.running) {
        return [pscustomobject]@{ started = $false; reason = "in_flight"; message = ("Операция HR Manager уже выполняется ({0})." -f $inFlight.reason); pid = 0; log = "" }
    }
    $lock = Enter-HrmActionLock -StateDir $StateDir -Action "engine"
    if (-not $lock.acquired) {
        return [pscustomobject]@{ started = $false; reason = "locked"; message = $lock.message; pid = 0; log = "" }
    }
    if (-not (Test-Path $ScriptPath)) {
        Exit-HrmActionLock -StateDir $StateDir
        return [pscustomobject]@{ started = $false; reason = "no_script"; message = "Не найден управляющий файл HR Manager. Переустановите Setup.exe."; pid = 0; log = "" }
    }
    $logFile = Get-HrmTimestampedLogFile -StateDir $StateDir -Action $Action
    $process = Start-HrmEngineProcess -ScriptPath $ScriptPath -Action $Action -InstallDir $InstallDir -StateDir $StateDir -LogFile $logFile
    $enginePid = 0
    if ($null -ne $process -and $process.PSObject.Properties["Id"]) { $enginePid = [int]$process.Id }
    if ($enginePid -le 0) {
        # pid потомка неизвестен: блокировку нельзя оставлять на значке, иначе
        # файл описывает процесс, который операцию не выполняет. Правило
        # Test-HrmTrayActionRunning трактует pid <= 0 так же — блокировка пуста.
        Exit-HrmActionLock -StateDir $StateDir
        return [pscustomobject]@{ started = $false; reason = "no_pid"; message = "Процесс HR Manager не запущен: не удалось получить идентификатор процесса. Повторите попытку из меню значка."; pid = 0; log = $logFile }
    }
    Update-HrmActionLockProcess -StateDir $StateDir -ProcessId $enginePid
    return [pscustomobject]@{ started = $true; reason = ""; message = ""; pid = $enginePid; log = $logFile }
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

function Get-HrmTrayEffectiveState {
    # Что значок ПОКАЗЫВАЕТ и ЗАПИСЫВАЕТ как своё состояние.
    # Чистая функция (без обращения к Docker/HTTP) — проверяется тестами.
    #
    # Правило: план значка НЕ затирает конкретную причину, которую записал
    # движок. Регрессия D7 (симптом пилотной машины): значок показывал
    # «Запускается» с текстом провала предполётной проверки, а движок при этом
    # уже ничего не делал — то есть состояние «starting» несло сообщение ошибки.
    # Поэтому если состояние в файле записано как error и это сделал ДРУГОЙ
    # (живой) процесс, значок показывает «Ошибка» с этой причиной.
    param(
        [string]$PlanState = "starting",
        [string]$PlanMessage = "",
        [bool]$PlanBusy = $false,
        [string]$StoredState = "",
        [string]$StoredMessage = "",
        [int]$StoredPid = 0,
        [int]$CurrentPid = 0
    )
    $state = $PlanState
    $message = $PlanMessage
    $storedIsError = ($StoredState -eq "error")
    if ($state -eq "error" -and -not $message -and $StoredMessage) {
        # Собственный план не знает причины — берём причину движка.
        $message = $StoredMessage
    }
    if ($state -ne "error" -and $storedIsError -and $StoredMessage -and $StoredPid -gt 0 -and $StoredPid -ne $CurrentPid) {
        # Причина записана движком, а план значка говорит «всё идёт по плану»:
        # причина важнее, состояние — «Ошибка».
        $state = "error"
        $message = $StoredMessage
    }
    $busy = $PlanBusy
    if ($state -eq "error") { $busy = $false }
    return [pscustomobject]@{ state = $state; message = $message; busy = $busy }
}

function Get-HrmTrayRefreshIntervalMs {
    # Интервал обновления значка (мс). Регрессия D4: раньше значок каждые 5
    # секунд ходил в `compose ps` + два HTTP-запроса — 12 обращений в минуту
    # ПОСТОЯННО. На подвешенном Docker CLI каждый такой тик оставлял висящий
    # процесс, и они накапливались (симптом «десятки powershell.exe»).
    #   * идёт операция (busy/действие движка/starting) — 5 с: нужен свежий ход
    #     работы, но обращения только к файлам состояния, не к Docker;
    #   * готово (ready) — 30 с: состояние меняется редко, достаточно заметить
    #     остановку контейнеров;
    #   * остановлено/ошибка — 60 с: само не изменится, меняется действием
    #     человека, которое и перерисовывает значок немедленно.
    # Критерий приёмки: в покое (ready) не более 4 проверок состояния
    # контейнеров в минуту (30 с → 2 проверки).
    param([string]$State = "starting", [bool]$Busy = $false, [bool]$ActionRunning = $false)
    if ($Busy -or $ActionRunning -or $State -eq "starting") { return 5000 }
    if ($State -eq "ready") { return 30000 }
    return 60000
}

function Invoke-HrmTrayRefresh {
    # ОДИН тик значка: что показать, уведомлять ли, идти ли в Docker, какой
    # следующий интервал. Вся логика тика вынесена из WinForms-обработчика в
    # чистую функцию, чтобы её можно было проверить тестами (сам интерфейс
    # тестируется только на живой машине).
    #
    # Возвращает: @{ state; message; busy; notify; event; probed; interval_ms;
    #                reason }.
    # probed=false означает «в Docker/HTTP не ходили» — тик читал только файлы
    # состояния. Именно это и есть защита от накопления висящих CLI-процессов.
    param(
        [string]$InstallDir = "",
        [string]$StateDir = "",
        [switch]$ForceProbe
    )
    if (-not $InstallDir) { $InstallDir = Get-HrmInstallDefaultDirSafe }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $plan = Get-HrmTrayCurrentPlan -StateDir $StateDir -InstallDir $InstallDir
    $stored = Get-HrmSupervisorState -StateDir $StateDir
    if ($plan.mode -ne "normal") {
        $message = $plan.message
        if ($plan.mode -eq "installing" -or $plan.mode -eq "resuming" -or $plan.mode -eq "busy") {
            # Пока операция идёт, показываем более подробный ход от движка
            # («Подготавливаем рабочую среду…», «Ожидаем запуск службы
            # контейнеров…»), если он уже записан. Ошибку движка план НЕ
            # затирает — это делает Get-HrmTrayEffectiveState.
            if ($stored.message -and ($stored.state -eq "starting" -or $stored.state -eq "error")) { $message = $stored.message }
        }
        $effective = Get-HrmTrayEffectiveState -PlanState $plan.state -PlanMessage $message -PlanBusy $plan.busy -StoredState $stored.state -StoredMessage $stored.message -StoredPid $stored.pid -CurrentPid $PID
        $interval = Get-HrmTrayRefreshIntervalMs -State $effective.state -Busy $effective.busy -ActionRunning $plan.busy
        return [pscustomobject]@{
            state = $effective.state; message = $effective.message; busy = $effective.busy
            notify = $true; event = $plan.notify_event; probed = $false
            interval_ms = $interval; reason = $plan.mode
        }
    }
    $actionRunning = (Test-HrmTrayActionRunning -StateDir $StateDir)
    $engineRunning = $false
    if (-not $actionRunning) { $engineRunning = [bool](Test-HrmEngineOperationInFlight -StateDir $StateDir).running }
    if (($actionRunning -or $engineRunning) -and -not $ForceProbe) {
        # Идёт операция движка: состояние берём из его файла и в Docker НЕ идём.
        # Дублирование опроса движком ничего не ускоряет, а висящий CLI
        # контейнеров остаётся висеть (регрессия D4).
        $effective = Get-HrmTrayEffectiveState -PlanState "starting" -PlanMessage $stored.message -PlanBusy $true -StoredState $stored.state -StoredMessage $stored.message -StoredPid $stored.pid -CurrentPid $PID
        $interval = Get-HrmTrayRefreshIntervalMs -State $effective.state -Busy $effective.busy -ActionRunning $true
        return [pscustomobject]@{
            state = $effective.state; message = $effective.message; busy = $effective.busy
            notify = $false; event = ""; probed = $false
            interval_ms = $interval; reason = $(if ($actionRunning) { "action-running" } else { "engine-running" })
        }
    }
    $snapshot = Get-HrmTraySnapshot -InstallDir $InstallDir -StateDir $StateDir
    $state = $snapshot.state
    $message = $snapshot.message
    if ($stored.state -eq "error" -and $state -ne "ready") {
        $state = "error"
        if ($stored.message) { $message = $stored.message }
    }
    $interval = Get-HrmTrayRefreshIntervalMs -State $state -Busy $false -ActionRunning $false
    return [pscustomobject]@{
        state = $state; message = $message; busy = $false
        notify = $true; event = ""; probed = $true
        interval_ms = $interval; reason = "probe"
    }
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
        # .NET Framework NotifyIcon accepts at most 63 UTF-16 characters.
        # Limit the value BEFORE assigning it; the full message stays in the menu.
        $tooltip = Get-HrmTrayTooltip -State $State -Message $Message
        if ($tooltip.Length -gt 63) { $tooltip = $tooltip.Substring(0, 60) + '...' }
        $icon.Text = $tooltip
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

    # Таймер создаётся ДО замыкания тика: тик сам меняет интервал (см.
    # Get-HrmTrayRefreshIntervalMs), а в Windows PowerShell 5.1 ссылка на
    # переменную в обработчике разрешается при исполнении, а не при создании.
    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = (Get-HrmTrayRefreshIntervalMs -State "starting")
    # Признак «тик ещё идёт» — защита от накладывающихся тиков (регрессия D4).
    $refreshState = @{ running = $false }

    $refresh = {
        # WinForms Timer ставит тики в очередь сообщений: если предыдущий тик не
        # закончился (типичный случай — CLI контейнеров висит на подвешенном
        # Docker), новый тик начинал ВТОРОЙ такой же поход, и процессы
        # накапливались. Пока тик идёт, следующие пропускаются; состояние
        # значка при этом не «залипает»: оно обновится на следующем тике.
        if ($refreshState.running) { return }
        $refreshState.running = $true
        try {
            $tick = Invoke-HrmTrayRefresh -InstallDir $InstallDir -StateDir $StateDir
            & $applyState $tick.state $tick.message $tick.busy $tick.notify $tick.event
            # Интервал зависит от состояния: в покое значок опрашивает редко.
            if ($tick.interval_ms -ne $timer.Interval) { $timer.Interval = [int]$tick.interval_ms }
        }
        catch {
            & $applyState "error" (Redact-HrmText $_.Exception.Message) $false $true
        }
        finally { $refreshState.running = $false }
    }

    $icon.ContextMenuStrip = $menu
    $icon.Visible = $true
    $icon.add_DoubleClick({ Invoke-HrmTrayMenuAction -ActionId "open" -InstallDir $InstallDir -StateDir $StateDir })
    & $refresh
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
        # Счётчик подряд идущих ОТКАЗОВ ДВИЖКА здесь не меняется: ручная
        # проверка состояния — не попытка запуска, и три проверки при
        # выключенном Docker не должны превращать значок в «повторы остановлены».
        Set-HrmSupervisorState -StateDir $StateDir -State $snapshot.state -Message $snapshot.message -FailureCount (Get-HrmSupervisorFailureCount -StateDir $StateDir)
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
    # Явное действие человека — новая попытка разрешена: счётчик подряд идущих
    # отказов обнуляется. Без этого в состоянии «автоматические повторы
    # остановлены» пользователь не смог бы повторить запуск из меню.
    if (Test-Path (Get-HrmSupervisorStateFile $StateDir)) {
        Reset-HrmSupervisorFailureCount -StateDir $StateDir | Out-Null
    }
    $result = Start-HrmTrayAction -Action $ActionId -InstallDir $InstallDir -StateDir $StateDir
    if (-not $result.started) { return $result.message }
    Set-HrmSupervisorState -StateDir $StateDir -State "starting" -Message "Выполняется действие: $ActionId" -Busy $true
    return "Действие запущено: $ActionId"
}
