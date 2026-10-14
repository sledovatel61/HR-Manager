# Сбор фактов с пилотной машины Windows (Этап 1 диагностики стабильности установки).
#
# ЗАЧЕМ. Отказ установки на пилотной машине 12.10.2026 выглядел так: значок в трее
# «Состояние: Запускается», сообщение «Предпроверка не пройдена: compose…», в
# диспетчере задач десятки процессов powershell.exe, vmmemWSL ≈ 1.8 ГБ. По одному
# тексту отказа невозможно отличить «CLI контейнеров не запустился» от «плагина
# compose нет ни в одном каталоге» от «плагин есть, но старый», а без списка
# процессов нельзя сказать, кто именно их породил. Этот скрипт снимает ВСЕ факты,
# по которым движок принимает решения, одним запуском и складывает их в JSON.
#
# ЧТО СОБИРАЕТ
#   1. все найденные CLI контейнеров (пути, версии файлов, даты, корень установки)
#      и результат `--version` для КАЖДОГО кандидата (код возврата, stdout, stderr,
#      признак таймаута);
#   2. какой CLI выбран движком и по какому правилу (Select-HrmDockerCliCandidate);
#   3. `docker compose version --short` и `docker compose version` — с ПОЛНЫМ
#      stderr и кодом возврата, плюс вердикт движка (cli_missing / plugin_missing /
#      version_unparsable / version_old / probe_failed / ok);
#   4. каталоги плагинов compose: существование и содержимое (имена, размеры, даты)
#      — %USERPROFILE%\.docker\cli-plugins и %ProgramData%\Docker\cli-plugins
#      (второй не ищется Docker CLI 29.2.0 и новее, GHSA-p436-gjf2-799p);
#   5. `docker info` (состояние демона, OSType, версии);
#   6. `wsl --status`, `wsl --list --verbose`, `wsl --version`;
#   7. память: процессы vmmem/vmmemWSL (рабочий набор), память хоста, топ процессов
#      по памяти — факт для оценки «1.8 ГБ это норма или утечка»;
#   8. ВСЕ процессы powershell.exe/pwsh.exe через Get-CimInstance Win32_Process:
#      Id, ParentProcessId, CreationDate, CommandLine (отредактировано от секретов),
#      рабочий набор — ответ на вопрос «кто наплодил процессы и кто их держит»;
#   9. контейнеры: `docker compose ls --all`, `docker compose ps -a` (проект
#      hr-manager-pilot, если каталог установки на месте) и `docker ps -a`;
#  10. каталог состояния %LOCALAPPDATA%\HRManager: список файлов (имя, размер, дата),
#      содержимое JSON-состояний (supervisor.json, installed.json, setup-run.json,
#      action.lock, update-journal.json, update-result.json, channel.json,
#      autostart.json) и хвосты файлов журнала.
#
# ЧЕГО НЕ ДЕЛАЕТ (принципиально)
#   * ничего не устанавливает, не обновляет, не останавливает и НЕ УДАЛЯЕТ:
#     ни контейнеров, ни томов, ни Docker Desktop, ни WSL (`wsl --shutdown` не
#     вызывается), ни файлов пользователя;
#   * не читает содержимое secrets.json, pilot.env, license_public_key.b64 и
#     first-run-input.json — только их имена, размеры и даты (там секреты и
#     персональные данные);
#   * весь текст проходит редакцию секретов движка (Redact-HrmText);
#   * каждая внешняя команда ограничена таймаутом: на «подвешенной» машине
#     скрипт завершится, а не зависнет вместе с ней.
#
# ЗАПУСК (на пилотной машине, Windows PowerShell 5.1, от обычного пользователя —
# администратор НЕ нужен; права важны именно такие, в которых работает движок):
#   powershell -NoProfile -ExecutionPolicy Bypass -File infra\windows\tools\Collect-HrmPilotFacts.ps1
#
# Необязательные параметры:
#   -OutFile <путь>            куда положить отчёт (по умолчанию — каталог состояния);
#   -StateDir <путь>           каталог состояния (по умолчанию %LOCALAPPDATA%\HRManager);
#   -InstallDir <путь>         каталог установки (по умолчанию — как у движка);
#   -CommandTimeoutSeconds N   потолок на одну внешнюю команду (по умолчанию 30);
#   -LogTailLines N            сколько строк журнала показать (по умолчанию 40);
#   -SkipDocker                не вызывать CLI контейнеров вовсе (только файлы и процессы).
#
# Результат: JSON-файл (путь печатается) + короткая человекочитаемая сводка.
# Файл содержит пути машины и имя пользователя в них — публикуйте его только
# разработчику HR Manager, не выкладывайте в открытые тикеты.
#
# Коды возврата: 0 — отчёт собран; 1 — отчёт не собран (ошибка записи/импорта).

#requires -Version 5.1

param(
    [string]$OutFile = "",
    [string]$StateDir = "",
    [string]$InstallDir = "",
    [int]$CommandTimeoutSeconds = 30,
    [int]$LogTailLines = 40,
    [switch]$SkipDocker
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$script:FactsVersion = "1.0.0"
# Файлы каталога состояния, содержимое которых читать НЕЛЬЗЯ (секреты и
# персональные данные). Для них снимаются только метаданные.
$script:ForbiddenContentFiles = @(
    "secrets.json", "pilot.env", "license_public_key.b64", "first-run-input.json",
    "first-run-url.txt", "lan.json"
)
# JSON-состояния, содержимое которых полезно для диагностики (после редакции).
$script:StateJsonFiles = @(
    "supervisor.json", "installed.json", "setup-run.json", "action.lock",
    "update-journal.json", "update-result.json", "channel.json",
    "autostart.json", "last-support-bundle.json"
)

function Limit-FactText {
    # Редакция секретов + обрезка: отчёт должен оставаться читаемым, а длинные
    # выводы CLI (docker info) не должны превращать его в мегабайты.
    param([string]$Value = "", [int]$MaxChars = 8000)
    $text = [string]$Value
    try { $text = Redact-HrmText $text } catch { }
    $text = $text -replace "`r", ""
    if ($MaxChars -le 0) { return $text }
    if ($text.Length -le $MaxChars) { return $text }
    return ($text.Substring(0, $MaxChars) + ("... [обрезано, всего символов: {0}]" -f $text.Length))
}

function Invoke-FactCommand {
    # Одна внешняя команда как факт: код возврата, stdout, stderr, признак
    # таймаута. Команда идёт через единственную точку движка
    # (Invoke-HrmExternal), поэтому у неё тот же контракт: ограниченное ожидание,
    # асинхронное чтение труб и остановка ДЕРЕВА процессов по таймауту.
    param(
        [string]$Name,
        [string[]]$Arguments = @(),
        [int]$TimeoutSeconds = 30,
        [int]$MaxOutputChars = 8000
    )
    $fact = [ordered]@{
        command = $Name
        arguments = @($Arguments)
        exit_code = $null
        timed_out = $false
        stdout = ""
        stderr = ""
        error = ""
    }
    try {
        $result = Invoke-HrmExternal -Name $Name -Arguments $Arguments -IgnoreExitCode -TimeoutSeconds $TimeoutSeconds
        $fact["exit_code"] = [int]$result.ExitCode
        if ($result.PSObject.Properties["TimedOut"]) { $fact["timed_out"] = [bool]$result.TimedOut }
        $fact["stdout"] = (Limit-FactText ([string]$result.Stdout) $MaxOutputChars)
        $fact["stderr"] = (Limit-FactText ([string]$result.Stderr) $MaxOutputChars)
    }
    catch {
        # Команда не запустилась вовсе (нет файла, нет прав): это тоже факт.
        $fact["error"] = (Limit-FactText $_.Exception.Message 2000)
    }
    return $fact
}

function Get-FactFileItem {
    # Метаданные файла/каталога (без содержимого).
    param([string]$Path)
    $item = [ordered]@{ path = [string]$Path; exists = $false; is_directory = $false; size_bytes = 0; last_write = "" }
    try {
        if (Test-Path -LiteralPath $Path) {
            $entry = Get-Item -LiteralPath $Path -ErrorAction Stop
            $item["exists"] = $true
            $item["is_directory"] = [bool]$entry.PSIsContainer
            if (-not $entry.PSIsContainer) { $item["size_bytes"] = [int64]$entry.Length }
            $item["last_write"] = $entry.LastWriteTime.ToString("o")
        }
    }
    catch { $item["error"] = (Limit-FactText $_.Exception.Message 500) }
    return $item
}

# --- Импорт модулей движка ------------------------------------------------------
# Факты снимаются ТЕМИ ЖЕ функциями, которыми их снимает движок: если резолвер CLI
# или классификация compose изменятся, отчёт изменится вместе с ними (иначе
# диагностика начнёт врать про собственный продукт).
$script:EngineDir = Join-Path (Split-Path $PSScriptRoot -Parent) "engine"
foreach ($module in @("Common", "Secrets", "Preflight", "Compose", "Bootstrap", "Update",
        "Diagnostics", "Install", "Crypto", "Channel", "Lan", "SupportBundle",
        "Docker", "Supervisor", "Tray")) {
    $modulePath = Join-Path $script:EngineDir ("$module.psm1")
    if (-not (Test-Path $modulePath)) { throw ("Модуль движка не найден: {0}" -f $modulePath) }
    Import-Module $modulePath -Force -ErrorAction Stop
}
# Отчёт не должен ничего менять и ничего не спрашивает: неинтерактивный режим,
# браузер не открывается.
$env:HRM_NONINTERACTIVE = "1"
$env:HRM_OPEN_BROWSER = "0"

if (-not $StateDir) { $StateDir = Get-HrmStateDir }
if (-not $InstallDir) {
    try { $InstallDir = Get-HrmDefaultInstallDir } catch { $InstallDir = "" }
}

$report = [ordered]@{
    schema = "hr-manager.pilot-facts"
    facts_version = $script:FactsVersion
    generated_at = (Get-Date).ToString("o")
    generator = "Collect-HrmPilotFacts.ps1"
    command_timeout_seconds = $CommandTimeoutSeconds
    docker_probed = (-not $SkipDocker)
}

# --- 1. Система и PowerShell ---------------------------------------------------
$system = [ordered]@{
    os_caption = ""
    os_version = ""
    os_build = ""
    os_architecture = ""
    powershell_version = [string]$PSVersionTable.PSVersion
    process_is_64bit = [bool][Environment]::Is64BitProcess
    host_total_memory_mb = 0
    host_free_memory_mb = 0
}
try {
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    $system["os_caption"] = [string]$os.Caption
    $system["os_version"] = [string]$os.Version
    $system["os_build"] = [string]$os.BuildNumber
    $system["os_architecture"] = [string]$os.OSArchitecture
    if ($os.TotalVisibleMemorySize) { $system["host_total_memory_mb"] = [int][Math]::Round(([double]$os.TotalVisibleMemorySize / 1024), 0) }
    if ($os.FreePhysicalMemory) { $system["host_free_memory_mb"] = [int][Math]::Round(([double]$os.FreePhysicalMemory / 1024), 0) }
}
catch { $system["error"] = (Limit-FactText $_.Exception.Message 500) }
$report["system"] = $system

# --- 2. CLI контейнеров: все кандидаты и проба каждого -------------------------
$cliSection = [ordered]@{
    chosen = ""
    chosen_reason = ""
    chosen_source = ""
    chosen_file_version = ""
    desktop_path = ""
    desktop_install_root = ""
    searched_dirs = @()
    candidates = @()
    probes = @()
}
try {
    $cliFacts = Get-HrmDockerCliFacts
    $cliSection["chosen"] = [string]$cliFacts.cli
    $cliSection["chosen_reason"] = [string]$cliFacts.chosen_reason
    $cliSection["chosen_source"] = [string]$cliFacts.cli_source
    $cliSection["chosen_file_version"] = [string]$cliFacts.cli_version
    $cliSection["desktop_path"] = [string]$cliFacts.desktop_path
    $cliSection["desktop_install_root"] = [string]$cliFacts.desktop_install_root
    $cliSection["searched_dirs"] = @($cliFacts.searched_dirs)
    foreach ($candidate in $cliFacts.candidates) {
        $cliSection["candidates"] += [ordered]@{
            path = [string]$candidate.path
            source = [string]$candidate.source
            file_version = [string]$candidate.file_version
            file_last_write = [string]$candidate.file_last_write
            install_root = [string]$candidate.install_root
            desktop_root_match = [bool]$candidate.desktop_root_match
        }
    }
}
catch { $cliSection["error"] = (Limit-FactText $_.Exception.Message 1000) }
if (-not $SkipDocker) {
    # Проба КАЖДОГО кандидата (движок так не делает намеренно: на горячем пути
    # это лишние процессы). Здесь это главная ценность отчёта: видно, какой CLI
    # вообще отвечает и какой версией.
    foreach ($candidate in $cliSection["candidates"]) {
        $probe = Invoke-FactCommand -Name ([string]$candidate.path) -Arguments @("--version") -TimeoutSeconds $CommandTimeoutSeconds -MaxOutputChars 2000
        $probe["candidate_path"] = [string]$candidate.path
        $cliSection["probes"] += $probe
    }
}
$report["docker_cli"] = $cliSection

# --- 3. Плагин compose: проба, вердикт движка, каталоги плагинов ---------------
$composeSection = [ordered]@{
    version_short = $null
    version_full = $null
    verdict = ""
    passed = $false
    message = ""
    reason = ""
    version = ""
    plugin_dirs = @()
    plugin_found = $false
}
if (-not $SkipDocker) {
    $composeCli = Get-HrmDockerCliPath
    if ($composeCli) {
        $composeSection["version_short"] = (Invoke-FactCommand -Name $composeCli -Arguments @("compose", "version", "--short") -TimeoutSeconds $CommandTimeoutSeconds -MaxOutputChars 2000)
        $composeSection["version_full"] = (Invoke-FactCommand -Name $composeCli -Arguments @("compose", "version") -TimeoutSeconds $CommandTimeoutSeconds -MaxOutputChars 2000)
    }
    else {
        $composeSection["version_short"] = [ordered]@{
            skipped = $true
            reason = "CLI контейнеров не найден — команду compose выполнить нельзя (это и есть причина отказа cli_missing)"
        }
    }
    try {
        $probe = Test-HrmComposeVersion
        $composeSection["passed"] = [bool]$probe.Passed
        $composeSection["message"] = (Limit-FactText ([string]$probe.Message) 4000)
        if ($probe.PSObject.Properties["Reason"]) { $composeSection["reason"] = [string]$probe.Reason }
        if ($probe.PSObject.Properties["Version"]) { $composeSection["version"] = [string]$probe.Version }
        if ($probe.PSObject.Properties["PluginFound"]) { $composeSection["plugin_found"] = [bool]$probe.PluginFound }
    }
    catch { $composeSection["error"] = (Limit-FactText $_.Exception.Message 1000) }
}
# Содержимое каталогов плагинов с размерами и датами (полезнее списка имён):
# по дате видно, откуда плагин приехал — из Docker Desktop или положен вручную.
foreach ($dir in (Get-HrmComposePluginDirs)) {
    $path = [string]$dir.path
    $entries = @()
    if (Test-Path -LiteralPath $path) {
        try {
            foreach ($file in @(Get-ChildItem -LiteralPath $path -File -ErrorAction Stop)) {
                $fileVersion = ""
                try { if ($file.VersionInfo -and $file.VersionInfo.ProductVersion) { $fileVersion = [string]$file.VersionInfo.ProductVersion } } catch { }
                $entries += [ordered]@{
                    name = [string]$file.Name
                    size_bytes = [int64]$file.Length
                    last_write = $file.LastWriteTime.ToString("o")
                    product_version = $fileVersion
                }
            }
        }
        catch { $entries += [ordered]@{ error = (Limit-FactText $_.Exception.Message 500) } }
    }
    $composeSection["plugin_dirs"] += [ordered]@{
        path = $path
        exists = (Test-Path -LiteralPath $path)
        searched_by_cli = [bool]$dir.searched_by_cli
        note = [string]$dir.note
        entries = $entries
    }
}
$report["compose_plugin"] = $composeSection

# --- 4. Демон Docker и WSL2 ----------------------------------------------------
$daemon = [ordered]@{ info = $null; version = $null }
if (-not $SkipDocker) {
    $cli = Get-HrmDockerCliPath
    if ($cli) {
        $daemon["info"] = (Invoke-FactCommand -Name $cli -Arguments @("info") -TimeoutSeconds $CommandTimeoutSeconds -MaxOutputChars 12000)
        $daemon["version"] = (Invoke-FactCommand -Name $cli -Arguments @("version") -TimeoutSeconds $CommandTimeoutSeconds -MaxOutputChars 4000)
    }
    else {
        $daemon["error"] = "CLI контейнеров не найден: docker info/version не вызывались."
    }
}
$report["docker_daemon"] = $daemon

$wsl = [ordered]@{ status = $null; list = $null; version = $null }
if (-not $SkipDocker) {
    # Только ЧТЕНИЕ состояния WSL2. `wsl --shutdown`, `--unregister`, `--install`
    # не вызываются: окружение пользователя принадлежит пользователю.
    $wsl["status"] = (Invoke-FactCommand -Name "wsl.exe" -Arguments @("--status") -TimeoutSeconds $CommandTimeoutSeconds -MaxOutputChars 4000)
    $wsl["list"] = (Invoke-FactCommand -Name "wsl.exe" -Arguments @("--list", "--verbose") -TimeoutSeconds $CommandTimeoutSeconds -MaxOutputChars 4000)
    $wsl["version"] = (Invoke-FactCommand -Name "wsl.exe" -Arguments @("--version") -TimeoutSeconds $CommandTimeoutSeconds -MaxOutputChars 2000)
}
$report["wsl"] = $wsl

# --- 5. Память: vmmemWSL и топ процессов ---------------------------------------
$memory = [ordered]@{ vmmem_processes = @(); vmmem_total_mb = 0; top_processes = @() }
try {
    foreach ($process in @(Get-Process -ErrorAction Stop)) {
        if (([string]$process.ProcessName) -match "(?i)^vmmem") {
            $workingSetMb = 0
            try { $workingSetMb = [int][Math]::Round(([double]$process.WorkingSet64 / 1MB), 0) } catch { }
            $memory["vmmem_processes"] += [ordered]@{
                name = [string]$process.ProcessName
                id = [int]$process.Id
                working_set_mb = $workingSetMb
                start_time = $(try { $process.StartTime.ToString("o") } catch { "" })
            }
            $memory["vmmem_total_mb"] = [int]$memory["vmmem_total_mb"] + $workingSetMb
        }
    }
    $top = @(Get-Process -ErrorAction Stop | Sort-Object -Property WorkingSet64 -Descending | Select-Object -First 12)
    foreach ($process in $top) {
        $workingSetMb = 0
        try { $workingSetMb = [int][Math]::Round(([double]$process.WorkingSet64 / 1MB), 0) } catch { }
        $memory["top_processes"] += [ordered]@{
            name = [string]$process.ProcessName
            id = [int]$process.Id
            working_set_mb = $workingSetMb
        }
    }
}
catch { $memory["error"] = (Limit-FactText $_.Exception.Message 500) }
$report["memory"] = $memory

# --- 6. Все процессы PowerShell (кто наплодил и кто держит) --------------------
$processSection = [ordered]@{
    powershell_count = 0
    powershell = @()
    docker_process_count = 0
    docker_processes = @()
}
try {
    # Win32_Process, а не Get-Process: нужна командная строка и время создания —
    # по ним видно, кто породил процесс (мастер установки, значок, автозапуск)
    # и сколько он уже жив.
    $all = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    foreach ($entry in $all) {
        $name = [string]$entry.Name
        if ($name -match "(?i)^(powershell|pwsh)(\.exe)?$") {
            $commandLine = ""
            if ($entry.CommandLine) { $commandLine = (Limit-FactText ([string]$entry.CommandLine) 1200) }
            $created = ""
            try {
                if ($entry.CreationDate) {
                    # Get-CimInstance отдаёт DateTime, Get-WmiObject — строку WMI:
                    # поддерживаем оба варианта (на пилотной машине может быть
                    # любой из них в зависимости от способа запуска).
                    if ($entry.CreationDate -is [datetime]) { $created = ([datetime]$entry.CreationDate).ToString("o") }
                    else { $created = ([Management.ManagementDateTimeConverter]::ToDateTime([string]$entry.CreationDate)).ToString("o") }
                }
            }
            catch { }
            $workingSetMb = 0
            try { $workingSetMb = [int][Math]::Round(([double]$entry.WorkingSetSize / 1MB), 0) } catch { }
            $processSection["powershell"] += [ordered]@{
                id = [int]$entry.ProcessId
                parent_id = [int]$entry.ParentProcessId
                name = $name
                creation_date = $created
                working_set_mb = $workingSetMb
                command_line = $commandLine
            }
            $processSection["powershell_count"] = [int]$processSection["powershell_count"] + 1
        }
        if ($name -match "(?i)^(docker|com\.docker\.backend|com\.docker\.build|docker-desktop)(\.exe)?$") {
            $processSection["docker_processes"] += [ordered]@{
                id = [int]$entry.ProcessId
                parent_id = [int]$entry.ParentProcessId
                name = $name
            }
            $processSection["docker_process_count"] = [int]$processSection["docker_process_count"] + 1
        }
    }
}
catch { $processSection["error"] = (Limit-FactText $_.Exception.Message 800) }
$report["processes"] = $processSection

# --- 7. Контейнеры и compose-проекты -------------------------------------------
$containers = [ordered]@{ compose_projects = $null; compose_ps = $null; docker_ps = $null }
if (-not $SkipDocker) {
    $cli = Get-HrmDockerCliPath
    if ($cli) {
        $containers["compose_projects"] = (Invoke-FactCommand -Name $cli -Arguments @("compose", "ls", "--all") -TimeoutSeconds $CommandTimeoutSeconds -MaxOutputChars 4000)
        $containers["docker_ps"] = (Invoke-FactCommand -Name $cli -Arguments @("ps", "-a", "--format", "{{.Names}}|{{.Image}}|{{.State}}|{{.Status}}") -TimeoutSeconds $CommandTimeoutSeconds -MaxOutputChars 6000)
        # `docker compose ps -a` — через функцию движка: она подставляет имя
        # проекта и env-файл так же, как это делает установка (иначе вывод
        # относился бы к другому проекту и вводил в заблуждение).
        $composeFiles = @()
        if ($InstallDir) {
            $composeFiles += (Join-Path $InstallDir "infra\docker-compose.yml")
            $composeFiles += (Join-Path $InstallDir "infra\compose.pilot.yml")
        }
        $haveComposeFiles = $true
        foreach ($file in $composeFiles) { if (-not (Test-Path -LiteralPath $file)) { $haveComposeFiles = $false } }
        if ($haveComposeFiles) {
            try {
                $ps = Invoke-HrmCompose $InstallDir $StateDir @("ps", "-a") -IgnoreExitCode
                $containers["compose_ps"] = [ordered]@{
                    exit_code = [int]$ps.ExitCode
                    stdout = (Limit-FactText ([string]$ps.Stdout) 6000)
                    stderr = (Limit-FactText ([string]$ps.Stderr) 4000)
                }
            }
            catch { $containers["compose_ps"] = [ordered]@{ error = (Limit-FactText $_.Exception.Message 800) } }
        }
        else {
            $containers["compose_ps"] = [ordered]@{
                skipped = $true
                reason = "в каталоге установки нет compose-файлов пилота (приложение не установлено или каталог указан неверно)"
                install_dir = [string]$InstallDir
            }
        }
        try {
            $stack = Get-HrmStackState -InstallDir $InstallDir -StateDir $StateDir
            $containers["stack_state"] = [ordered]@{
                state = [string]$stack.state
                total = [int]$stack.total
                running = [int]$stack.running
                unhealthy = [int]$stack.unhealthy
                message = (Limit-FactText ([string]$stack.message) 500)
            }
        }
        catch { $containers["stack_state"] = [ordered]@{ error = (Limit-FactText $_.Exception.Message 800) } }
    }
    else {
        $containers["error"] = "CLI контейнеров не найден: состояние контейнеров не снималось."
    }
}
$report["containers"] = $containers

# --- 8. Каталог состояния: файлы, состояния, хвосты журналов --------------------
$stateSection = [ordered]@{
    state_dir = [string]$StateDir
    exists = (Test-Path -LiteralPath $StateDir)
    files = @()
    states = @()
    logs = @()
}
if ($stateSection["exists"]) {
    try {
        foreach ($entry in @(Get-ChildItem -LiteralPath $StateDir -File -ErrorAction Stop | Sort-Object -Property Name)) {
            $stateSection["files"] += [ordered]@{
                name = [string]$entry.Name
                size_bytes = [int64]$entry.Length
                last_write = $entry.LastWriteTime.ToString("o")
                content_collected = ($script:StateJsonFiles -contains [string]$entry.Name)
            }
        }
    }
    catch { $stateSection["files_error"] = (Limit-FactText $_.Exception.Message 500) }
    foreach ($name in $script:StateJsonFiles) {
        $path = Join-Path $StateDir $name
        if (-not (Test-Path -LiteralPath $path)) { continue }
        $parsed = $null
        try { $parsed = Get-HrmJsonFile $path } catch { $parsed = $null }
        if ($null -ne $parsed) {
            try {
                $json = ($parsed | ConvertTo-Json -Depth 6 -Compress)
                $stateSection["states"] += [ordered]@{ name = $name; json = (Limit-FactText $json 6000) }
            }
            catch { $stateSection["states"] += [ordered]@{ name = $name; error = (Limit-FactText $_.Exception.Message 500) } }
        }
        else {
            $stateSection["states"] += [ordered]@{ name = $name; error = "файл не читается или не является JSON" }
        }
    }
    $logDir = Join-Path $StateDir "logs"
    if (Test-Path -LiteralPath $logDir) {
        try {
            $logFiles = @(Get-ChildItem -LiteralPath $logDir -File -ErrorAction Stop | Sort-Object -Property LastWriteTime -Descending | Select-Object -First 6)
            foreach ($logFile in $logFiles) {
                $tail = @()
                try { $tail = @(Get-Content -LiteralPath $logFile.FullName -Tail $LogTailLines -Encoding UTF8 -ErrorAction Stop) } catch { }
                $stateSection["logs"] += [ordered]@{
                    name = [string]$logFile.Name
                    size_bytes = [int64]$logFile.Length
                    last_write = $logFile.LastWriteTime.ToString("o")
                    tail_lines = [int]$LogTailLines
                    tail = (Limit-FactText (($tail -join "`n")) 8000)
                }
            }
        }
        catch { $stateSection["logs_error"] = (Limit-FactText $_.Exception.Message 500) }
    }
}
$stateSection["forbidden_content_files"] = @($script:ForbiddenContentFiles)
$report["state_dir"] = $stateSection

# --- 9. Состояние движка глазами значка и supervisor'а -------------------------
$engine = [ordered]@{ install_dir = [string]$InstallDir }
try { $engine["install_dir_files"] = (Get-FactFileItem (Join-Path $InstallDir "infra\compose.pilot.yml")) } catch { }
try { $engine["supervisor"] = (Get-HrmSupervisorState -StateDir $StateDir) } catch { }
try { $engine["supervisor_failure_count"] = (Get-HrmSupervisorFailureCount -StateDir $StateDir) } catch { }
try {
    $plan = Get-HrmTrayCurrentPlan -StateDir $StateDir -InstallDir $InstallDir
    $engine["tray_plan"] = [ordered]@{
        mode = [string]$plan.mode
        state = [string]$plan.state
        busy = [bool]$plan.busy
        start_engine = [bool]$plan.start_engine
        engine_action = [string]$plan.engine_action
        message = (Limit-FactText ([string]$plan.message) 1000)
    }
}
catch { $engine["tray_plan_error"] = (Limit-FactText $_.Exception.Message 800) }
try {
    $inFlight = Test-HrmEngineOperationInFlight -StateDir $StateDir -IgnorePid $PID
    $engine["operation_in_flight"] = [ordered]@{
        running = [bool]$inFlight.running
        reason = [string]$inFlight.reason
        pid = [int]$inFlight.pid
    }
}
catch { $engine["operation_in_flight_error"] = (Limit-FactText $_.Exception.Message 800) }
try { $engine["action_lock"] = (Get-FactFileItem (Get-HrmActionLockFile $StateDir)) } catch { }
try { $engine["channel_watch_pid"] = (Get-FactFileItem (Get-HrmChannelPidFile $StateDir)) } catch { }
$report["engine"] = $engine

# --- Запись отчёта и сводка -----------------------------------------------------
$stamp = (Get-Date).ToString("yyyyMMdd-HHmmss")
if (-not $OutFile) {
    $targetDir = $StateDir
    if (-not (Test-Path -LiteralPath $targetDir)) { $targetDir = [System.IO.Path]::GetTempPath() }
    $OutFile = Join-Path $targetDir ("hrm-pilot-facts-" + $stamp + ".json")
}
$json = ""
try { $json = ($report | ConvertTo-Json -Depth 10) } catch { $json = "" }
if (-not $json) { throw "не удалось собрать отчёт в JSON" }
# UTF-8 без BOM: отчёт читают как обычный текст (jq, редакторы, backend-инструменты).
[System.IO.File]::WriteAllText($OutFile, $json, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "== Факты пилотной машины HR Manager =="
Write-Host ("Отчёт: {0}" -f $OutFile)
Write-Host ("Система: {0} (сборка {1}), PowerShell {2}, 64-бит: {3}" -f $system["os_caption"], $system["os_build"], $system["powershell_version"], $system["process_is_64bit"])
Write-Host ("Память хоста: всего {0} МБ, свободно {1} МБ; vmmem* занимает {2} МБ" -f $system["host_total_memory_mb"], $system["host_free_memory_mb"], $memory["vmmem_total_mb"])
Write-Host ("Процессов powershell/pwsh: {0}; процессов docker*: {1}" -f $processSection["powershell_count"], $processSection["docker_process_count"])
Write-Host ("Выбранный CLI контейнеров: {0}" -f $(if ($cliSection["chosen"]) { $cliSection["chosen"] } else { "не найден" }))
Write-Host ("Правило выбора: {0}" -f $cliSection["chosen_reason"])
Write-Host ("Кандидатов CLI найдено: {0}" -f @($cliSection["candidates"]).Count)
foreach ($probe in $cliSection["probes"]) {
    Write-Host ("  --version: {0} → код {1}{2}" -f $probe["candidate_path"], $probe["exit_code"], $(if ([bool]$probe["timed_out"]) { " (ТАЙМАУТ)" } else { "" }))
}
Write-Host ("Плагин compose: {0}" -f $(if ([bool]$composeSection["passed"]) { ("найден, версия " + [string]$composeSection["version"]) } else { ("НЕ ПРОЙДЕНА (" + [string]$composeSection["reason"] + ")") }))
if ($composeSection["message"]) { Write-Host ("Сообщение движка: {0}" -f $composeSection["message"]) }
foreach ($dir in $composeSection["plugin_dirs"]) {
    Write-Host ("  каталог плагинов: {0} (существует: {1}; ищется CLI: {2})" -f $dir["path"], $dir["exists"], $dir["searched_by_cli"])
}
Write-Host ("Состояние supervisor: {0}; подряд идущих отказов: {1}" -f $(if ($engine["supervisor"]) { [string]$engine["supervisor"].state } else { "нет файла" }), $(if ($null -ne $engine["supervisor_failure_count"]) { $engine["supervisor_failure_count"] } else { "?" }))
if ($engine["tray_plan"]) {
    Write-Host ("План значка: {0} (состояние {1}, запускать движок: {2})" -f $engine["tray_plan"]["mode"], $engine["tray_plan"]["state"], $engine["tray_plan"]["start_engine"])
}
if ($engine["operation_in_flight"]) {
    Write-Host ("Операция движка идёт: {0} ({1})" -f $engine["operation_in_flight"]["running"], $engine["operation_in_flight"]["reason"])
}
Write-Host ""
Write-Host "Отчёт не содержит секретов: содержимое secrets.json, pilot.env, license_public_key.b64 и first-run-input.json не читалось, остальной текст прошёл редакцию."
Write-Host "Передайте файл разработчику HR Manager вместе с описанием того, что вы видели на экране."
exit 0
