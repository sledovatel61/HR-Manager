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
# Маркер «мастер установки сейчас работает»: его пишет Setup.exe перед запуском
# движка и значка в трее, а движок удаляет, когда установка/обновление закончились.
function Get-HrmSetupMarkerFile { param([string]$StateDir) return (Join-Path $StateDir "setup-run.json") }
# Снимок ПРЕДЫДУЩЕЙ версии (файлы infra/backend/frontend/release.json) и копия
# нового релиза под управлением движка — нужны для согласованного отката и
# повторяемых фаз обновления (см. Update.psm1).
function Get-HrmPreviousSnapshotDir { param([string]$StateDir) return (Join-Path $StateDir "previous-snapshot") }
function Get-HrmReleaseStagingDir { param([string]$StateDir) return (Join-Path $StateDir "release-staging") }

# --- Журнал -----------------------------------------------------------------

function Format-HrmLogLine {
    # Чистая функция: готовая строка журнала (метка времени + редакция секретов).
    param([string]$Level, [string]$Message)
    $clean = Redact-HrmText $Message
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    return "[$stamp] [$Level] $clean"
}

function Write-HrmLog {
    # Журнал НЕ пишется в success stream. Раньше строки журнала уходили туда, из-за
    # чего они примешивались к возвращаемым значениям функций
    # (Start-HrmStack, Repair-HrmStack, Invoke-HrmDockerPrepare и др.): результат
    # становился массивом, и обращение к его свойству ($stack.ok) под StrictMode
    # давало "The property 'ok' cannot be found on this object".
    # Адресаты: файл журнала фоновой операции (HRM_LOG_FILE, его задаёт
    # Start-HrmEngineProcess) либо консоль/перенаправленный stdout.
    param([string]$Level, [string]$Message)
    $line = Format-HrmLogLine -Level $Level -Message $Message
    $logFile = $env:HRM_LOG_FILE
    if ($logFile) {
        try {
            # UTF-8 без BOM: файл журнала читается инструментами как обычный текст.
            [System.IO.File]::AppendAllText($logFile, $line + [Environment]::NewLine, (New-Object System.Text.UTF8Encoding($false)))
            return
        }
        catch { }
    }
    Write-Host $line
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
$script:MockDownload = $null
$script:MockProcessLaunch = $null
$script:MockProcessStart = $null

# --- Таймауты внешних команд (регрессия «десятки powershell.exe») ------------
# Раньше у Invoke-HrmExternal не было НИ таймаута, НИ порядка чтения труб:
# WaitForExit() без аргумента ждал вечно, а последовательное
# StandardOutput.ReadToEnd() -> StandardError.ReadToEnd() deadlock'ило родителя,
# когда ребёнок писал в stderr больше объёма буфера канала (~64 КБ — обычное
# дело для `compose build`). Один подвешенный CLI контейнеров оставлял процесс
# движка живым навсегда: пользователь перезапускал Setup/входил в Windows — и
# процессы накапливались (десятки powershell.exe + висящие docker.exe).
#
# Значения ниже — ПОТОЛОК ожидания конкретной команды, а не «лечение
# таймаутом»: существующие таймауты ожиданий движка (Docker Engine 300 с,
# readiness 180 с, цикл supervisor'а 300 с) НЕ изменены и не увеличены.
# Обоснование каждого — в infra/windows/README.md («Таймауты и отмена»).
$script:HrmExternalTimeoutProbeSeconds = 60     # короткие пробы: --version, info, compose version/ps/config, inspect, tag, images, wsl
$script:HrmExternalTimeoutComposeSeconds = 900  # compose up/down/run/exec/logs/stop: запуск стека, миграции, бэкап
$script:HrmExternalTimeoutBuildSeconds = 3600   # compose build: первая сборка образов (npm/pip) на пилотной машине
$script:HrmExternalTimeoutKillSeconds = 20      # остановка дерева процессов
$script:HrmExternalTimeoutDefaultSeconds = 120  # icacls, git, netsh и прочие команды
# Код возврата «команда не завершилась вовремя» (соглашение GNU timeout):
# отличается и от 127 (CLI не найден — см. Invoke-HrmDocker), и от кодов самой
# команды, поэтому вызывающие видят причину, а не «просто ошибку».
$script:HrmExternalExitCodeTimeout = 124
# Признак «дерево процессов уже останавливается» — защита от рекурсии:
# остановка дерева сама вызывает внешнюю команду, и её таймаут не должен
# запускать вторую остановку дерева.
$script:HrmKillingProcessTree = $false

function Set-HrmExternalMock {
    # Тестовый шов: подменяет ВСЕ внешние команды (docker/icacls/…).
    param([scriptblock]$Mock)
    $script:MockExternal = $Mock
}

function Clear-HrmExternalMock { $script:MockExternal = $null }

function Get-HrmSetupMarker {
    # Состояние мастера установки (файл пишет Setup.exe перед запуском движка и
    # значка в трее; движок обновляет/удаляет его по ходу установки).
    # Возвращает @{ status; message; fresh; age_seconds; file }.
    # «Свежесть» считается по времени изменения файла: так не нужно разбирать
    # дату из JSON, которую пишет мастер установки (Inno Setup).
    param([string]$StateDir = "", [int]$MaxAgeMinutes = 30)
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $file = Get-HrmSetupMarkerFile $StateDir
    $result = [pscustomobject]@{ status = ""; message = ""; fresh = $false; age_seconds = -1; file = $file }
    if (-not (Test-Path $file)) { return $result }
    $age = -1
    try { $age = [int]((Get-Date) - (Get-Item $file).LastWriteTime).TotalSeconds } catch { $age = -1 }
    $status = ""
    $message = ""
    try {
        $data = Get-HrmJsonFile $file
        if ($null -ne $data) {
            if ($data.PSObject.Properties["status"]) { $status = [string]$data.status }
            if ($data.PSObject.Properties["message"]) { $message = [string]$data.message }
        }
    }
    catch {
        # Повреждённый файл = незавершённая установка, а не «всё хорошо».
        $status = "failed"
        $message = "Файл состояния установки повреждён."
    }
    $limit = [int]($MaxAgeMinutes * 60)
    $fresh = ($age -ge 0 -and $age -le $limit)
    return [pscustomobject]@{ status = $status; message = $message; fresh = $fresh; age_seconds = $age; file = $file }
}

function Set-HrmSetupMarker {
    # Отметить ход установки/обновления: status = running | failed.
    # Сообщение пишется в редакции секретов — в трее и журнале не должно быть
    # ни паролей, ни путей к секретам.
    param([string]$StateDir = "", [string]$Status = "running", [string]$Message = "")
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Path $StateDir -Force | Out-Null }
    $data = [ordered]@{
        status = $Status
        message = (Redact-HrmText $Message)
        updated_at = (Get-Date).ToString("o")
    }
    Set-HrmJsonFile $StateDir "setup-run.json" $data
    # Время изменения файла — источник «свежести» для значка в трее.
    try { (Get-Item (Get-HrmSetupMarkerFile $StateDir)).LastWriteTime = (Get-Date) } catch { }
    return (Get-HrmSetupMarker -StateDir $StateDir)
}

function Clear-HrmSetupMarker {
    # Успешная установка завершена: отметка больше не нужна.
    param([string]$StateDir = "")
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $file = Get-HrmSetupMarkerFile $StateDir
    if (Test-Path $file) { Remove-Item -Path $file -Force -ErrorAction SilentlyContinue }
}

function Set-HrmHttpMock {
    # Тестовый шов: подменяет ВСЕ HTTP-вызовы (loopback).
    param([scriptblock]$Mock)
    $script:MockHttp = $Mock
}

function Clear-HrmHttpMock { $script:MockHttp = $null }

function Get-HrmExternalTimeoutSeconds {
    # Потолок ожидания внешней команды. Порядок выбора:
    #   1) явно запрошенный вызывающим -TimeoutSeconds;
    #   2) HRM_EXTERNAL_TIMEOUT_SECONDS (тестовый/аварийный шов);
    #   3) тип команды (сборка образов длиннее probes в десятки раз).
    # Аргументы сопоставляются ТОЧНЫМ элементом массива, а не поиском подстроки
    # в склеенной командной строке: путь к env-файлу или compose-файлу может
    # содержать слово «build», и команда получила бы чужой таймаут.
    param([string]$Name = "", [string[]]$Arguments = @(), [int]$Requested = 0)
    if ($Requested -gt 0) { return $Requested }
    $envValue = 0
    if ([int]::TryParse([string]$env:HRM_EXTERNAL_TIMEOUT_SECONDS, [ref]$envValue) -and $envValue -gt 0) { return $envValue }
    $leaf = [string]$Name
    try { $leaf = [string][System.IO.Path]::GetFileName(([string]$Name)) } catch { $leaf = [string]$Name }
    $leaf = $leaf.ToLowerInvariant()
    $items = @($Arguments | ForEach-Object { ([string]$_).ToLowerInvariant() })
    if ($leaf -like "docker*") {
        if ($items -contains "build") { return $script:HrmExternalTimeoutBuildSeconds }
        foreach ($long in @("up", "down", "run", "exec", "logs", "stop", "start", "restart", "pull", "push")) {
            if ($items -contains $long) { return $script:HrmExternalTimeoutComposeSeconds }
        }
        return $script:HrmExternalTimeoutProbeSeconds
    }
    if ($leaf -like "taskkill*") { return $script:HrmExternalTimeoutKillSeconds }
    if ($leaf -like "wsl*") { return $script:HrmExternalTimeoutProbeSeconds }
    return $script:HrmExternalTimeoutDefaultSeconds
}

function Get-HrmExternalKillGraceMs {
    # Сколько ждать завершения после остановки дерева (и сколько ждать дренаж
    # труб). Пропорционально таймауту команды, но не меньше 500 мс и не больше
    # 5 с: короткая проба не должна добавлять секунды ожидания, а сборка
    # образов — терять вывод.
    param([int]$TimeoutSeconds = 60)
    $grace = [int]($TimeoutSeconds * 100)
    if ($grace -lt 500) { $grace = 500 }
    if ($grace -gt 5000) { $grace = 5000 }
    return $grace
}

function Set-HrmProcessStartMock {
    # Тестовый шов СОЗДАНИЯ процесса внешней команды (не результата вызова:
    # результат подменяет Set-HrmExternalMock). Нужен, чтобы проверить сам
    # механизм таймаута/остановки дерева на двойнике процесса, который «висит»
    # или пишет много в stderr — настоящая машина при этом не трогается.
    param([scriptblock]$Mock)
    $script:MockProcessStart = $Mock
}

function Clear-HrmProcessStartMock { $script:MockProcessStart = $null }

function Start-HrmExternalProcessObject {
    # ЕДИНСТВЕННАЯ точка создания процесса внешней команды (статический контракт
    # «внешние процессы только через Invoke-HrmExternal» сохраняется: вызов
    # остаётся внутри Common.psm1 и мокабелен).
    param($StartInfo)
    if ($null -ne $script:MockProcessStart) { return (& $script:MockProcessStart -StartInfo $StartInfo) }
    return [System.Diagnostics.Process]::Start($StartInfo)
}

function Stop-HrmProcessTree {
    # Остановка ДЕРЕВА процессов, а не только корня: у CLI контейнеров есть
    # потомки (wsl.exe, docker-compose, buildx), и после убийства корня они
    # остались бы висеть — ровно тот симптом, из-за которого в диспетчере задач
    # жили десятки процессов. Внешняя команда идёт через Invoke-HrmExternal
    # (единственная мокабельная точка), поэтому тесты фиксируют сам факт
    # вызова с /T /F, не запуская настоящую программу.
    # Возвращает $true, если команда остановки отработала без ошибки.
    param([int]$ProcessId = 0, [string]$Reason = "")
    if ($ProcessId -le 0) { return $false }
    if ($script:HrmKillingProcessTree) { return $false }
    $script:HrmKillingProcessTree = $true
    try {
        $note = ("Останавливаем дерево процессов (pid {0}): {1}" -f $ProcessId, $Reason)
        $null = Write-HrmLog "warn" $note
        $kill = Invoke-HrmExternal -Name "taskkill.exe" -Arguments @("/PID", ([string]$ProcessId), "/T", "/F") -IgnoreExitCode
        return ($kill.ExitCode -eq 0)
    }
    catch {
        $null = Write-HrmLog "warn" ("Не удалось остановить дерево процессов (pid {0}): {1}" -f $ProcessId, (Redact-HrmText $_.Exception.Message))
        return $false
    }
    finally { $script:HrmKillingProcessTree = $false }
}

function Invoke-HrmExternal {
    # Запуск внешней команды. НИКОГДА не вызывайте docker/icacls/etc.
    # напрямую из движка — только через эту функцию (Pester мокает её).
    #
    # КОНТРАКТ (регрессия «вечное зависание движка»):
    #   * трубы читаются АСИНХРОННО и параллельно — последовательное
    #     ReadToEnd() stdout-потом-stderr deadlock'ило родителя при полном
    #     буфере stderr (~64 КБ);
    #   * ожидание ограничено (-TimeoutSeconds, иначе Get-HrmExternalTimeoutSeconds);
    #   * по таймауту дерево процессов останавливается (Stop-HrmProcessTree),
    #     результат получает ExitCode 124 и объяснение в Stderr, факт пишется в
    #     журнал; без -IgnoreExitCode — исключение с тем же текстом;
    #   * мок внешних команд вызывается с ПРЕЖНЕЙ сигнатурой (без -TimeoutSeconds):
    #     таймаут относится к настоящему запуску процесса, а подмена результата
    #     не должна ломать существующие моки тестов.
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string[]]$Arguments = @(),
        [string]$Stdin = "",
        [switch]$IgnoreExitCode,
        [int]$TimeoutSeconds = 0
    )
    if ($null -ne $script:MockExternal) {
        return & $script:MockExternal -Name $Name -Arguments $Arguments -Stdin $Stdin -IgnoreExitCode:$IgnoreExitCode
    }
    $timeout = Get-HrmExternalTimeoutSeconds -Name $Name -Arguments $Arguments -Requested $TimeoutSeconds
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
    $process = Start-HrmExternalProcessObject -StartInfo $psi
    $graceMs = Get-HrmExternalKillGraceMs -TimeoutSeconds $timeout
    $timedOut = $false
    $exitCode = $script:HrmExternalExitCodeTimeout
    $stdout = ""
    $stderr = ""
    try {
        # Ввод пишется асинхронно и ограничен по времени: синхронная запись в
        # незаполняемый ребёнком stdin заблокировала бы родителя так же надёжно,
        # как полное чтение труб (движок передаёт Stdin пустым, но контракт
        # обязан быть безопасным и для непустого значения).
        if ($Stdin) {
            $stdinAccepted = $true
            try { $stdinAccepted = $process.StandardInput.WriteAsync($Stdin).Wait($graceMs) }
            catch { $stdinAccepted = $false }
            if (-not $stdinAccepted) { $timedOut = $true }
        }
        try { $process.StandardInput.Close() } catch { }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $timedOut) {
            $exited = $false
            try { $exited = $process.WaitForExit([int]($timeout * 1000)) } catch { $exited = $true }
            if (-not $exited) { $timedOut = $true }
        }
        if ($timedOut) {
            # Сначала снимаем то, что уже прочитано (без ожидания), затем
            # останавливаем дерево: потомки CLI контейнеров (wsl, buildx)
            # обязаны умереть вместе с корнем.
            if ($stdoutTask.IsCompleted) { try { $stdout = [string]$stdoutTask.Result } catch { $stdout = "" } }
            if ($stderrTask.IsCompleted) { try { $stderr = [string]$stderrTask.Result } catch { $stderr = "" } }
            $childPid = 0
            try { $childPid = [int]$process.Id } catch { $childPid = 0 }
            $reason = ("команда не завершилась за {0} с" -f $timeout)
            $null = Stop-HrmProcessTree -ProcessId $childPid -Reason $reason
            $exitedAfterKill = $false
            try { $exitedAfterKill = $process.WaitForExit($graceMs) } catch { $exitedAfterKill = $true }
            if (-not $exitedAfterKill) {
                # taskkill мог не сработать (нет прав/нет такой команды):
                # корень процесса закрываем штатным Kill, иначе родитель висит.
                try { $process.Kill() } catch { }
                try { $null = $process.WaitForExit($graceMs) } catch { }
            }
            $message = ("Команда '{0}' не завершилась за {1} с — процесс и его потомки остановлены (код {2}). Это не нормальное завершение: среда контейнеров не отвечает или операция зависла. Повторите после перезапуска Docker Desktop; если повторяется — создайте отчёт для поддержки." -f $Name, $timeout, $script:HrmExternalExitCodeTimeout)
            $null = Write-HrmLog "error" (Redact-HrmText $message)
            if ($stderr) { $stderr = ($stderr.TrimEnd() + [Environment]::NewLine + $message) } else { $stderr = $message }
        }
        else {
            # Процесс завершился: дожидаемся дренажа труб (ограниченно) и
            # забираем код возврата.
            try { $null = $stdoutTask.Wait($graceMs) } catch { }
            try { $null = $stderrTask.Wait($graceMs) } catch { }
            if ($stdoutTask.IsCompleted) { try { $stdout = [string]$stdoutTask.Result } catch { $stdout = "" } }
            if ($stderrTask.IsCompleted) { try { $stderr = [string]$stderrTask.Result } catch { $stderr = "" } }
            try { $exitCode = [int]$process.ExitCode } catch { $exitCode = -1 }
        }
    }
    finally {
        try { $process.Dispose() } catch { }
    }
    $result = [pscustomobject]@{
        Name = $Name
        ExitCode = $exitCode
        Stdout = $stdout
        Stderr = $stderr
        TimedOut = $timedOut
        TimeoutSeconds = $timeout
    }
    if (-not $IgnoreExitCode -and $exitCode -ne 0) {
        throw ("Команда '{0}' завершилась с кодом {1}: {2}" -f $Name, $exitCode, (Redact-HrmText $stderr.Trim()))
    }
    return $result
}

function Test-HrmResultTimedOut {
    # Истекло ли ожидание у результата внешней команды или повышенного запуска.
    # Свойство читается через PSObject (тот же приём, что в
    # Invoke-HrmDockerCliVersionProbe): тестовые двойники (Set-HrmExternalMock,
    # Set-HrmProcessLaunchMock) возвращают объект без TimedOut, а под StrictMode
    # обращение к несуществующему свойству — ошибка. Отсутствие свойства
    # трактуется как «таймаута не было», то есть смысл прежних результатов
    # (код возврата, отмена UAC) не меняется.
    param($Result)
    if ($null -eq $Result) { return $false }
    if (-not $Result.PSObject.Properties["TimedOut"]) { return $false }
    return [bool]$Result.TimedOut
}

function Get-HrmResultTimeoutSeconds {
    # Какой потолок ожидания был применён к результату (0, если источник его не
    # сообщил). Тексты отказов обязаны называть ожидание честно, а не выдумывать
    # значение по умолчанию.
    param($Result)
    if ($null -eq $Result) { return 0 }
    if (-not $Result.PSObject.Properties["TimeoutSeconds"]) { return 0 }
    return [int]$Result.TimeoutSeconds
}

function Set-HrmDownloadMock {
    # Тестовый шов: подменяет скачивание файла (официальный установщик).
    param([scriptblock]$Mock)
    $script:MockDownload = $Mock
}

function Clear-HrmDownloadMock { $script:MockDownload = $null }

function Set-HrmProcessLaunchMock {
    # Тестовый шов: подменяет запуск процессов (Docker Desktop, установщик,
    # процесс движка). В тестах реальные программы не запускаются.
    param([scriptblock]$Mock)
    $script:MockProcessLaunch = $Mock
}

function Clear-HrmProcessLaunchMock { $script:MockProcessLaunch = $null }

function Save-HrmDownload {
    # Скачивание файла (только официальные источники, вызывающий обязан
    # проверить источник и подпись). Единственная точка скачивания — мокабельна.
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    # Каталог назначения создаём ДО мока: иначе тестовый шов вёл бы себя
    # иначе, чем настоящая загрузка (мок писал файл в несуществующий каталог,
    # установка Docker падала с «Не удалось скачать установщик»).
    $dir = Split-Path $Destination -Parent
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    if ($null -ne $script:MockDownload) {
        return (& $script:MockDownload -Uri $Uri -Destination $Destination)
    }
    $previous = $ProgressPreference
    $ProgressPreference = "SilentlyContinue"
    try {
        Invoke-WebRequest -Uri $Uri -OutFile $Destination -UseBasicParsing -TimeoutSec 600
    }
    finally { $ProgressPreference = $previous }
    if (-not (Test-Path $Destination)) {
        throw "Не удалось скачать файл: $Uri"
    }
    return $Destination
}

function Get-HrmFileSha256 {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return "" }
    return (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

# Признак «путь CLI контейнеров уже записан в журнал» (один раз на процесс).
$script:HrmDockerCliLogged = $false

function Resolve-HrmDockerCli {
    # Разрешение CLI контейнеров для фактических вызовов: резолвер живёт рядом с
    # детекцией Docker (Get-HrmDockerCliPath в Docker.psm1), поэтому «Docker
    # установлен» и «CLI можно вызвать» не расходятся. Путь пишется в постоянный
    # журнал один раз на процесс (без дампа окружения).
    $cli = Get-HrmDockerCliPath
    if ($cli -and -not $script:HrmDockerCliLogged) {
        $script:HrmDockerCliLogged = $true
        Write-HrmLog "info" ("CLI контейнеров: " + $cli)
        # Все НАЙДЕННЫЕ кандидаты пишутся в журнал рядом с выбранным: при двух
        # установках Docker Desktop (устаревшая машинная + актуальная
        # пользовательская) по журналу видно, какой CLI выбран и какой
        # проигнорирован — иначе расхождение версий выглядит как «Docker сломался
        # сам». Ошибка перечисления не должна ломать вызов команды.
        try {
            foreach ($candidate in (Get-HrmDockerCliFacts).candidates) {
                if ([string]$candidate.path -eq [string]$cli) { continue }
                Write-HrmLog "info" ("Найден ещё один CLI контейнеров (не выбран): {0} (версия файла {1}, источник {2})" -f ([string]$candidate.path), ([string]$candidate.file_version), ([string]$candidate.source))
            }
        }
        catch { }
    }
    return [string]$cli
}

function Invoke-HrmDocker {
    # ЕДИНСТВЕННАЯ точка запуска CLI контейнеров: имя разрешается в путь
    # (Get-HrmDockerCliPath), а не берётся из PATH процесса. Голое имя работало
    # только там, где каталог CLI попал в PATH: при пользовательской установке
    # Docker Desktop (без администратора) детекция сообщала «установлен», а
    # вызов падал с Win32Exception или таймаутом.
    # -TimeoutSeconds: потолок ожидания именно этого вызова (0 — политика
    # Get-HrmExternalTimeoutSeconds по типу команды).
    param([string[]]$Arguments = @(), [string]$Stdin = "", [switch]$IgnoreExitCode, [int]$TimeoutSeconds = 0)
    $cli = Resolve-HrmDockerCli
    if (-not $cli) {
        # Структурированный отказ вместо исключения Process.Start: вызывающие
        # проверяют ExitCode как и раньше (диагностика → missing, готовность
        # Engine → false, предполётная проверка → отказ с текстом), а сообщение
        # перечисляет проверенные места.
        $message = ("CLI контейнеров не найден. Проверенные места: {0}. Установите Docker Desktop только с официального сайта docker.com и повторите." -f ((Get-HrmDockerCliSearchPlaces) -join "; "))
        Write-HrmLog "error" $message
        $result = [pscustomobject]@{ Name = "docker"; ExitCode = 127; Stdout = ""; Stderr = $message; TimedOut = $false; TimeoutSeconds = 0 }
        # Контракт Invoke-HrmExternal сохранён: без -IgnoreExitCode ошибка —
        # исключение. Иначе вызовы вида `Invoke-HrmCompose … | Out-Null` молча
        # продолжали бы работу без движка контейнеров, то есть маскировали бы
        # проблему (раньше на этом месте было исключение Process.Start).
        if (-not $IgnoreExitCode) { throw $message }
        return $result
    }
    return Invoke-HrmExternal -Name $cli -Arguments $Arguments -Stdin $Stdin -IgnoreExitCode:$IgnoreExitCode -TimeoutSeconds $TimeoutSeconds
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
        Set-Content -Path $file -Value $Url -Encoding UTF8
        Protect-HrmFile $stateDir $file
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

# НАБЛЮДАТЕЛЬ КАНАЛА ОБНОВЛЕНИЙ (D8, ревизия стабильности 12.10.2026): здесь
# раньше жил Start-HrmChannelWatcherProcess — скрытый powershell.exe с
# блокирующим циклом. Функция НИКОГДА не вызывалась (ни движком, ни
# установщиком, ни треем), то есть наблюдатель в пилоте фактически не работал,
# а мёртвый спавнер был готовым источником ещё одного бессмертного процесса
# (ровно тот симптом, который устраняется этой ревизией). Спавнер удалён.
# Канал проверяется ЯВНО: hr-manager.ps1 -Action channel (один цикл) или
# `-Action channel -Watch` (блокирующий наблюдатель с точкой выхода —
# см. Start-HrmChannelWatch/Stop-HrmChannelWatch в Channel.psm1 и раздел
# «Канал обновлений» в infra/windows/README.md).

# --- Защита файлов состояния --------------------------------------------------

function Protect-HrmFile {
    # ACL: наследование выключено, полный доступ только текущему пользователю.
    param([string]$StateDir, [string]$Path)
    if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Path $StateDir -Force | Out-Null }
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    # (OI)(CI) are inheritance flags for a directory. Applied to a regular
    # file they create an inherit-only ACE, leaving the file itself unreadable
    # after /inheritance:r. This used to make atomically replaced state files
    # (notably secrets.json and update-journal.json) fail with Access Denied.
    $isDirectory = (Test-Path -LiteralPath $Path -PathType Container)
    $grant = if ($isDirectory) {
        "{0}:(OI)(CI)F" -f $identity
    }
    else {
        "{0}:F" -f $identity
    }
    Invoke-HrmExternal -Name "icacls.exe" -Arguments @($Path, "/inheritance:r", "/grant:r", $grant) | Out-Null
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

function Get-HrmWaitIntervalSeconds {
    # Интервал опроса для циклов ожидания. Тестовый шов: HRM_WAIT_INTERVAL_SECONDS
    # (движок на CI не должен спать реальные секунды в моках). Отрицательные и
    # нечисловые значения игнорируются — используется переданный по умолчанию.
    param([int]$Default = 3)
    $value = -1
    if ([int]::TryParse([string]$env:HRM_WAIT_INTERVAL_SECONDS, [ref]$value) -and $value -ge 0) { return $value }
    return $Default
}

function Start-HrmWaitSleep {
    # Пауза между попытками в циклах ожидания. Нулевой интервал — без сна:
    # это тестовый/быстрый режим (HRM_WAIT_INTERVAL_SECONDS=0), а не «спать 0 с»
    # (у Start-Sleep -Seconds 0 смысла нет, и цикл остаётся ограничен дедлайном).
    param([int]$Seconds = 3)
    if ($Seconds -gt 0) { Start-Sleep -Seconds $Seconds }
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
        Start-HrmWaitSleep -Seconds $IntervalSeconds
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

# --- Запуск процессов движка и внешних программ -------------------------------
# ЕДИНСТВЕННОЕ место, где допускается Start-Process/Process.Start: статический
# тест (static.tests.ps1) запрещает эти вызовы в остальных модулях движка.
# Никакие секреты сюда не передаются: только пути, действия и флаги.

function Start-HrmDetached {
    # Запуск внешней программы без ожидания завершения.
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [switch]$Hidden,
        [string]$WorkingDirectory = "",
        [switch]$RunAsAdmin
    )
    $startArgs = @{ FilePath = $FilePath }
    if ($Arguments.Count -gt 0) { $startArgs["ArgumentList"] = $Arguments }
    if ($WorkingDirectory) { $startArgs["WorkingDirectory"] = $WorkingDirectory }
    if ($Hidden) { $startArgs["WindowStyle"] = "Hidden" }
    if ($RunAsAdmin) { $startArgs["Verb"] = "RunAs" }
    if ($null -ne $script:MockProcessLaunch) {
        return (& $script:MockProcessLaunch -FilePath $FilePath -Arguments $Arguments -Mode $(if ($RunAsAdmin) { "elevated_detached" } else { "detached" }))
    }
    $process = Start-Process @startArgs -PassThru
    return $process
}

function Start-HrmElevatedAndWait {
    # Запуск программы с повышением прав (UAC) и ожиданием завершения.
    # Возвращает @{ Started; CanceledByUser; ExitCode; TimedOut }.
    # Отмена UAC пользователем — штатный результат, а не сбой движка.
    #
    # ОЖИДАНИЕ ОГРАНИЧЕНО (регрессия «вечное зависание»): установщик Docker
    # Desktop интерактивный (лицензию принимает человек), но WaitForExit() без
    # аргумента означал, что забытое окно установщика удерживает процесс движка
    # живым навсегда. По таймауту движок честно сообщает, что установщик не
    # завершился, и НЕ убивает его: прерванная установка Docker Desktop хуже
    # явного отказа (половинчатая установка, требующая ручного восстановления).
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = "",
        [int]$TimeoutSeconds = 3600
    )
    $startArgs = @{ FilePath = $FilePath; Verb = "RunAs"; PassThru = $true }
    if ($Arguments.Count -gt 0) { $startArgs["ArgumentList"] = $Arguments }
    if ($WorkingDirectory) { $startArgs["WorkingDirectory"] = $WorkingDirectory }
    if ($null -ne $script:MockProcessLaunch) {
        return (& $script:MockProcessLaunch -FilePath $FilePath -Arguments $Arguments -Mode "elevated_wait")
    }
    try {
        $process = Start-Process @startArgs
    }
    catch {
        return [pscustomobject]@{ Started = $false; CanceledByUser = $true; ExitCode = -1; TimedOut = $false }
    }
    $exited = $true
    try { $exited = $process.WaitForExit([int]($TimeoutSeconds * 1000)) } catch { $exited = $true }
    if (-not $exited) {
        $note = ("Повышенный процесс не завершился за {0} с: {1}" -f $TimeoutSeconds, $FilePath)
        $null = Write-HrmLog "warn" $note
        try { $process.Dispose() } catch { }
        return [pscustomobject]@{ Started = $true; CanceledByUser = $false; ExitCode = -1; TimedOut = $true }
    }
    $code = -1
    try { $code = [int]$process.ExitCode } catch { $code = -1 }
    try { $process.Dispose() } catch { }
    return [pscustomobject]@{ Started = $true; CanceledByUser = $false; ExitCode = $code; TimedOut = $false }
}

function Start-HrmEngineProcess {
    # Скрытый процесс движка (действие hr-manager.ps1) с журналом в StateDir.
    # Трей и установщик используют это, чтобы долгие операции не блокировали
    # интерфейс и не открывали пользователю консоль.
    param(
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [Parameter(Mandatory = $true)][string]$Action,
        [string]$InstallDir = "",
        [string]$StateDir = "",
        [string]$ExtraArguments = "",
        [string]$LogFile = ""
    )
    $arguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-NonInteractive",
        "-File", ('"{0}"' -f $ScriptPath),
        "-Action", $Action
    )
    if ($InstallDir) { $arguments += @("-InstallDir", ('"{0}"' -f $InstallDir)) }
    if ($StateDir) { $arguments += @("-StateDir", ('"{0}"' -f $StateDir)) }
    if ($ExtraArguments) { $arguments += $ExtraArguments.Split(" ") }
    $startArgs = @{
        FilePath = "powershell.exe"
        ArgumentList = $arguments
        WindowStyle = "Hidden"
        PassThru = $true
    }
    if ($LogFile) {
        $logDir = Split-Path $LogFile -Parent
        if ($logDir -and -not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
        $startArgs["RedirectStandardOutput"] = $LogFile
        $startArgs["RedirectStandardError"] = "$LogFile.err"
    }
    if ($null -ne $script:MockProcessLaunch) {
        return (& $script:MockProcessLaunch -FilePath "powershell.exe" -Arguments $arguments -Mode "engine" -LogFile $LogFile)
    }
    # Потомок наследует HRM_LOG_FILE и пишет свой журнал туда сам (Write-HrmLog),
    # поэтому окно можно держать скрытым; RedirectStandardOutput остаётся
    # подстраховкой для всего, что процесс печатает напрямую.
    if ($LogFile) { $env:HRM_LOG_FILE = $LogFile }
    try { return (Start-Process @startArgs) }
    finally { if ($LogFile) { Remove-Item Env:HRM_LOG_FILE -ErrorAction SilentlyContinue } }
}

function Get-HrmEngineLogsDir {
    param([string]$StateDir)
    return (Join-Path $StateDir "logs")
}

function Get-HrmTimestampedLogFile {
    # Путь журнала операции: StateDir\logs\<action>-<дата-время>.log
    param([string]$StateDir, [string]$Action)
    $stamp = (Get-Date).ToString("yyyyMMdd-HHmmss")
    return (Join-Path (Get-HrmEngineLogsDir $StateDir) ("{0}-{1}.log" -f $Action, $stamp))
}

function Get-HrmProcessRunning {
    # Проверка запущенного процесса по имени (без запуска внешних команд).
    param([string]$Name)
    if (-not $Name) { return $false }
    try {
        $processes = @(Get-Process -Name $Name -ErrorAction SilentlyContinue)
        return ($processes.Count -gt 0)
    }
    catch { return $false }
}
