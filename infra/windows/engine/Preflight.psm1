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
    $found = Invoke-HrmDocker -Arguments @("--version") -IgnoreExitCode
    if (Test-HrmResultTimedOut $found) {
        # Таймаут пробы — НЕ «Docker CLI не найден» (дефект P5.3 дораздела
        # PR #55): программа найдена и запущена, но не ответила за отведённое
        # время (код 124, см. Invoke-HrmExternal). Прежний текст при таймауте
        # отправлял человека переустанавливать Docker Desktop, хотя причина —
        # зависший CLI/ожидание инициализации WSL2, то есть действие другое.
        return [pscustomobject]@{
            Name = "docker"; Passed = $false
            Message = ("Docker CLI не ответил за {0} с: программа найдена и запущена, но ответ не получен (обычно это ожидание инициализации WSL2). Это не отсутствие Docker. Запустите Docker Desktop заново, дождитесь готовности и нажмите «Повторить»; если не помогает — создайте отчёт для поддержки." -f (Get-HrmResultTimeoutSeconds $found))
        }
    }
    if ($found.ExitCode -ne 0) {
        return [pscustomobject]@{ Name = "docker"; Passed = $false; Message = "Docker CLI не найден. Установите Docker Desktop только с официального сайта docker.com (лицензия принимается вручную)." }
    }
    return [pscustomobject]@{ Name = "docker"; Passed = $true; Message = ($found.Stdout.Trim()) }
}

function Test-HrmDockerDaemon {

    $override = Get-HrmPreflightOverrideResult "daemon"
    if ($null -ne $override) { return $override }
    $info = Invoke-HrmDocker -Arguments @("info", "--format", "{{.ServerVersion}}") -IgnoreExitCode
    if (Test-HrmResultTimedOut $info) {
        # То же правило, что и для пробы CLI (дефект P5.3): таймаут — это «не
        # ответил за N с», а не «демон не запущен». Различие важно для действия
        # человека: в первом случае ждать/перезапустить Docker Desktop, во
        # втором — запустить его впервые.
        return [pscustomobject]@{
            Name = "daemon"; Passed = $false
            Message = ("Демон Docker не ответил за {0} с: CLI запущен, но ответ не получен (обычно Docker Desktop ещё инициализирует WSL2). Это ожидание ответа, а не отсутствие работающего демона: заново запускать Docker Desktop не требуется. Дождитесь готовности Docker Desktop и нажмите «Повторить»; если ожидание затянулось — создайте отчёт для поддержки." -f (Get-HrmResultTimeoutSeconds $info))
        }
    }
    if ($info.ExitCode -ne 0) {
        return [pscustomobject]@{ Name = "daemon"; Passed = $false; Message = "Демон Docker не запущен (запустите Docker Desktop и подождите инициализацию WSL2)." }
    }
    return [pscustomobject]@{ Name = "daemon"; Passed = $true; Message = ("Docker engine " + $info.Stdout.Trim()) }
}

function Get-HrmComposePluginDirs {
    # Каталоги, в которых docker CLI (Windows) ищет плагин compose, по убыванию
    # приоритета. Источник — docker/cli#1534 «CLI Plugins Design»:
    #   1) %USERPROFILE%\.docker\cli-plugins      — пользовательский;
    #   2) %ProgramData%\Docker\cli-plugins       — машинный.
    # ВАЖНО про второй каталог: начиная с Docker CLI 29.2.0 он БОЛЬШЕ НЕ
    # ИЩЕТСЯ (docker/cli#6713, исправление GHSA-p436-gjf2-799p: каталог по
    # умолчанию не создаётся, и любой локальный пользователь мог положить туда
    # свой docker-compose.exe и получить повышение привилегий). Поэтому в отказе
    # мы честно перечисляем оба каталога и помечаем, что новый CLI второй
    # игнорирует — иначе инструкция «положите плагин в ProgramData» привела бы
    # пользователя к неработающему совету.
    # Каталог РЯДОМ с исполняемым файлом CLI (в отличие от Linux) не
    # просматривается — класть плагин туда бессмысленно. Имя файла плагина:
    # docker-compose.exe.
    $dirs = @()
    if ($env:USERPROFILE) {
        $dirs += [pscustomobject]@{
            path = (Join-Path $env:USERPROFILE ".docker\cli-plugins")
            searched_by_cli = $true
            note = "пользовательский каталог плагинов (ищется всеми версиями CLI)"
        }
    }
    if ($env:ProgramData) {
        $dirs += [pscustomobject]@{
            path = (Join-Path $env:ProgramData "Docker\cli-plugins")
            searched_by_cli = $false
            note = "машинный каталог: Docker CLI 29.2.0+ его не ищет (GHSA-p436-gjf2-799p)"
        }
    }
    return $dirs
}

function Get-HrmComposePluginFacts {
    # Что реально лежит в каталогах плагинов: существование каталога и имена
    # файлов docker-compose*. Используется в тексте отказа («где искали и что
    # нашли»), в диагностике и в отчёте поддержки. Ошибки чтения не бросаются:
    # отсутствие прав на ProgramData — обычный случай.
    $facts = @()
    foreach ($dir in (Get-HrmComposePluginDirs)) {
        $path = [string]$dir.path
        $exists = (Test-Path -LiteralPath $path)
        $files = @()
        if ($exists) {
            try {
                $files = @(Get-ChildItem -LiteralPath $path -Filter "docker-compose*" -File -ErrorAction Stop | ForEach-Object { [string]$_.Name })
            }
            catch { $files = @() }
        }
        $facts += [pscustomobject]@{
            path = $path
            exists = $exists
            files = $files
            file_count = $files.Count
            searched_by_cli = [bool]$dir.searched_by_cli
            note = [string]$dir.note
        }
    }
    return $facts
}

function Get-HrmComposeVersionVerdict {
    # ЧИСТАЯ классификация ответа пробы compose (без обращений к Docker и файлам,
    # поэтому проверяется тестом на фиксированных данных).
    #
    # Регрессия D5: раньше любой ненулевой код возврата превращался в один и тот
    # же текст «Плагин Docker Compose v2 не найден», и по нему было невозможно
    # отличить «CLI вообще не запустился» от «плагин не лежит ни в одном
    # каталоге» и от «плагин есть, но старый». На пилотной машине это выглядело
    # как «Docker сломался сам».
    #
    # Вердикты:
    #   cli_missing        — CLI контейнеров не найден/не запускается (код 127);
    #   plugin_missing     — CLI работает, команды compose в нём нет;
    #   version_unparsable — ответ получен, версию разобрать не удалось;
    #   version_old        — версия ниже требуемой;
    #   probe_failed       — другая ошибка CLI (демон, права, таймаут);
    #   ok                 — версия не ниже требуемой.
    #
    # Сравнение версий — через Compare-HrmCliVersion (Docker.psm1): прежнее
    # правило «major >= 2 -and minor >= 24» отвергало Compose 3.x/5.x
    # (major 5, minor 1 → «старый»), то есть сломалось бы на первом же
    # мажорном обновлении плагина.
    param(
        [int]$ExitCode = 0,
        [string]$Stdout = "",
        [string]$Stderr = "",
        [bool]$TimedOut = $false,
        [string]$MinimumVersion = "2.24"
    )
    if ($TimedOut) {
        return [pscustomobject]@{ verdict = "probe_failed"; version = ""; detail = "команда не завершилась за отведённое время (движок остановил её дерево процессов)" }
    }
    if ($ExitCode -eq 127) {
        return [pscustomobject]@{ verdict = "cli_missing"; version = ""; detail = "CLI контейнеров не найден или не запускается" }
    }
    $stdoutText = [string]$Stdout
    $stderrText = [string]$Stderr
    $version = ""
    if ($stdoutText -match "v?(\d+\.\d+(?:\.\d+)?(?:-[0-9A-Za-z\.\-]+)?)") { $version = $matches[1] }
    if (-not $version -and $stderrText -match "v?(\d+\.\d+(?:\.\d+)?(?:-[0-9A-Za-z\.\-]+)?)") { $version = $matches[1] }
    $firstLine = ($stdoutText -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -First 1)
    if (-not $firstLine) { $firstLine = "" }
    $firstLine = ([string]$firstLine).Trim()
    if ($ExitCode -ne 0) {
        # Так docker CLI отвечает, когда плагина нет: «'compose' is not a docker
        # command» (и варианты про неизвестную команду).
        if ($stderrText -match "(?i)(is not a docker command|unknown command|not a docker|compose.*not found|no such command)") {
            return [pscustomobject]@{ verdict = "plugin_missing"; version = $version; detail = $firstLine }
        }
        if ($stderrText -match "(?i)(plugin|cli-plugins)") {
            return [pscustomobject]@{ verdict = "plugin_missing"; version = $version; detail = $firstLine }
        }
        $errorLine = ($stderrText -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -First 1)
        if (-not $errorLine) { $errorLine = $firstLine }
        return [pscustomobject]@{ verdict = "probe_failed"; version = $version; detail = ([string]$errorLine).Trim() }
    }
    if (-not $version) {
        $detail = if ($firstLine) { $firstLine } else { "пустой ответ" }
        return [pscustomobject]@{ verdict = "version_unparsable"; version = ""; detail = $detail }
    }
    if ((Compare-HrmCliVersion $version $MinimumVersion) -lt 0) {
        return [pscustomobject]@{ verdict = "version_old"; version = $version; detail = ("найдена версия {0}, требуется {1} или новее" -f $version, $MinimumVersion) }
    }
    return [pscustomobject]@{ verdict = "ok"; version = $version; detail = $firstLine }
}

function Test-HrmComposeVersion {

    $override = Get-HrmPreflightOverrideResult "compose"
    if ($null -ne $override) { return $override }
    # Требуется Compose v2 (плагин docker compose), минимум 2.24 (тег !reset).
    #
    # Проверка НЕ ослабляется до предупреждения: без плагина compose стек не
    # поднимется вообще, а «предупреждение» оставило бы пользователя с вечным
    # «Запускается» и без причины (симптом пилотной машины 12.10.2026).
    # Отказ обязан называть ПРИЧИНУ и следующее действие.
    $minimumVersion = "2.24"
    $out = Invoke-HrmDocker -Arguments @("compose", "version", "--short") -IgnoreExitCode
    $timedOut = $false
    if ($out.PSObject.Properties["TimedOut"]) { $timedOut = [bool]$out.TimedOut }
    $verdict = Get-HrmComposeVersionVerdict -ExitCode ([int]$out.ExitCode) -Stdout ([string]$out.Stdout) -Stderr ([string]$out.Stderr) -TimedOut $timedOut -MinimumVersion $minimumVersion
    $pluginFacts = @(Get-HrmComposePluginFacts)
    $pluginDirsText = ""
    foreach ($fact in $pluginFacts) {
        $contentText = "пусто"
        if ($fact.file_count -gt 0) { $contentText = ($fact.files -join ", ") }
        $content = "каталога нет"
        if ($fact.exists) { $content = ("содержимое: {0}" -f $contentText) }
        if ($pluginDirsText) { $pluginDirsText += "; " }
        $pluginDirsText += ("{0} ({1}; {2})" -f $fact.path, $content, $fact.note)
    }
    $pluginFound = $false
    $userPluginDir = ""
    foreach ($fact in $pluginFacts) {
        if ($fact.file_count -gt 0) { $pluginFound = $true }
        if ($fact.searched_by_cli -and -not $userPluginDir) { $userPluginDir = [string]$fact.path }
    }
    if (-not $userPluginDir) { $userPluginDir = "%USERPROFILE%\.docker\cli-plugins" }
    $cliFacts = $null
    try { $cliFacts = Get-HrmDockerCliFacts } catch { $cliFacts = $null }
    $cliText = ""
    $cliVersionText = ""
    if ($null -ne $cliFacts) {
        $cliText = [string]$cliFacts.cli
        $cliVersionText = [string]$cliFacts.cli_version
    }
    $message = ""
    switch ($verdict.verdict) {
        "cli_missing" {
            $message = "CLI контейнеров не найден или не запускается — проверку Docker Compose выполнить нельзя. Установите Docker Desktop только с официального сайта docker.com (лицензия принимается вручную)."
            if ($cliText) { $message = ("CLI контейнеров не запускается ({0}). Установите или переустановите Docker Desktop с официального сайта docker.com." -f $cliText) }
        }
        "plugin_missing" {
            $message = ("Плагин Docker Compose v2 не найден: CLI контейнеров работает{0}, но команды compose в нём нет. Проверенные каталоги плагинов: {1}. Плагин ставится вместе с Docker Desktop — обновите его; если Docker Desktop не используется, положите docker-compose.exe в {2}. Каталог рядом с CLI не используется." -f $(if ($cliVersionText) { (" (версия {0})" -f $cliVersionText) } else { "" }), $pluginDirsText, $userPluginDir)
        }
        "version_unparsable" {
            $message = ("Не удалось разобрать версию Docker Compose из ответа: {0}. Проверенные каталоги плагинов: {1}." -f $verdict.detail, $pluginDirsText)
        }
        "version_old" {
            $message = ("Требуется Docker Compose {0} или новее (в файле пилота используется тег !reset); найдено: {1}. Обновите Docker Desktop — плагин compose обновляется вместе с ним." -f $minimumVersion, $verdict.version)
        }
        "probe_failed" {
            $message = ("Проверка Docker Compose не прошла: {0} (код возврата {1}). Запустите Docker Desktop и повторите." -f $verdict.detail, $out.ExitCode)
        }
        default {
            $message = ("Docker Compose {0}" -f $verdict.version)
        }
    }
    $passed = ($verdict.verdict -eq "ok")
    # Кроме Name/Passed/Message (их читают движок и отчёт о хосте) возвращаем
    # факты: причина отказа, версия, путь CLI, каталоги плагинов и stderr пробы.
    # Их используют диагностика и отчёт поддержки — без них «compose не найден»
    # на машине не воспроизвести.
    return [pscustomobject]@{
        Name = "compose"
        Passed = $passed
        Message = $message
        Reason = [string]$verdict.verdict
        Version = [string]$verdict.version
        MinimumVersion = $minimumVersion
        Cli = $cliText
        CliVersion = $cliVersionText
        ExitCode = [int]$out.ExitCode
        TimedOut = $timedOut
        ProbeStderr = (Redact-HrmText [string]$out.Stderr)
        PluginDirs = $pluginFacts
        PluginFound = $pluginFound
    }
}

function Test-HrmPortFree {
    param([int]$Port = 0)
    $override = Get-HrmPreflightOverrideResult "port"
    if ($null -ne $override) { return $override }
    $port = Get-HrmPort $Port
    $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($listeners) {
        # Порт занят СОБСТВЕННЫМ работающим пилотом — это норма: повторный
        # запуск/открытие приложения не должны падать на занятом собственном
        # порту. Чужое приложение (в т.ч. старый dev-стек) остаётся отказом:
        # его HTTP 200 не принадлежит целевому пилоту.
        if (Test-HrmPortServedByPilot -Port $port) {
            return [pscustomobject]@{ Name = "port"; Passed = $true; Message = "Порт $port занят собственным приложением HR Manager (пилот работает)." }
        }
        return [pscustomobject]@{ Name = "port"; Passed = $false; Message = "Порт $port уже занят (127.0.0.1:$port). Укажите другой порт параметром -Port." }
    }
    return [pscustomobject]@{ Name = "port"; Passed = $true; Message = "Порт $port свободен." }
}

function Test-HrmStateDirWritable {
    param([string]$StateDir)
    $override = Get-HrmPreflightOverrideResult "state_dir"
    if ($null -ne $override) { return $override }
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
    param([string]$Path, [long]$MinFreeMb = 5120)
    $override = Get-HrmPreflightOverrideResult "space"
    if ($null -ne $override) { return $override }
    # Порог свободного места на диске установки (по умолчанию 5 ГБ).
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
    param([string]$InstallDir, [string]$StateDir)
    $override = Get-HrmPreflightOverrideResult "config"
    if ($null -ne $override) { return $override }
    # Существующая конфигурация (повторная установка/обновление) валидна.
    $composeFiles = @(
        (Join-Path $InstallDir "infra\docker-compose.yml"),
        (Join-Path $InstallDir "infra\compose.pilot.yml")
    )
    foreach ($composeFile in $composeFiles) {
        if (-not (Test-Path $composeFile)) {
            return [pscustomobject]@{ Name = "config"; Passed = $false; Message = "Файл конфигурации не найден: $composeFile" }
        }
    }
    $args = @("compose", "--project-name", "hr-manager-pilot")
    $envFile = Get-HrmEnvFile $StateDir
    if (Test-Path $envFile) { $args += @("--env-file", $envFile) }
    foreach ($composeFile in $composeFiles) { $args += @("-f", $composeFile) }
    $args += @("config", "--quiet")
    $check = Invoke-HrmDocker -Arguments $args -IgnoreExitCode
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
        # Причина отказа попадает в САМО сообщение, а не только в журнал:
        # раньше пользователь видел «Предполётная проверка не пройдена: compose»
        # и не мог отличить «CLI не запустился» от «плагина нет ни в одном
        # каталоге» от «плагин старый» (симптом пилотной машины 12.10.2026).
        $details = @()
        foreach ($item in $failed) {
            $details += ("{0}: {1}" -f $item.Name, (Redact-HrmText [string]$item.Message))
        }
        $detailText = ($details -join " | ")
        # Текст редактится от секретов и обрезается: он уходит в supervisor.json,
        # в подсказку значка и во всплывающее уведомление (у последнего жёсткий
        # предел Windows). Полная причина остаётся в журнале и в отчёте для
        # поддержки.
        if ($detailText.Length -gt 300) { $detailText = $detailText.Substring(0, 297) + "..." }
        throw ("Предполётная проверка не пройдена: {0}. {1} Подробности — в отчёте для поддержки (diagnostics)." -f $list, $detailText)
    }
    return $results
}
