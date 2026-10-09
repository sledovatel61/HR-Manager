#requires -Version 5.1
<#
    CI-смоук «чистая установка → обновление поверх → удаление» для HR Manager.

    Почему это файл репозитория, а не текст шага workflow. Раннер пишет
    временный скрипт шага в UTF-8 БЕЗ BOM, а Windows PowerShell 5.1 читает такие
    файлы в ANSI-кодировке: кириллица в тексте шага превращается в мусор, и
    шаг падал, не оставив ни одной аннотации (логи шагов из среды сопровождения
    не читаются). Здесь файл лежит в репозитории с BOM, поэтому кириллица
    читается корректно, а сам скрипт проверяется и локально
    (infra/windows/tests/static.tests.ps1: BOM + разбор PowerShell 5.1), и в
    движковом прогоне CI.

    Диагностика идёт АННОТАЦИЯМИ ::notice/::error (их видно через check-run
    API). При сбое в аннотацию попадают: причина, код возврата мастера, решение
    мастера о снимке прежней версии и хвост журнала Inno (/LOG=) — журнал
    мастера единственный рассказывает, почему установка остановилась ДО
    перезаписи файлов.

    Секретов здесь нет: пути, коды возврата, SHA-256 и число файлов.
#>
[CmdletBinding()]
param(
    # Каталог репозитория; по умолчанию — три уровня вверх от файла.
    [string]$RepoRoot = "",
    # Каталоги пилотной установки (совпадают с defaults installer.iss).
    [string]$InstallDir = "",
    [string]$StateDir = ""
)

$ErrorActionPreference = "Stop"

function Format-HrmCommandText {
    # GitHub-команды: сообщение с переводом строки обрывается на первой строке,
    # а знак % ломает разбор команды. Длинный текст обрезаем с конца: важное —
    # в хвосте.
    param([string]$Text, [int]$Limit = 3500)
    if (-not $Text) { return "" }
    $flat = $Text.Replace("%", "%25").Replace("`r", "%0D").Replace("`n", "%0A")
    if ($flat.Length -gt $Limit) { $flat = $flat.Substring($flat.Length - $Limit) }
    return $flat
}

function Write-HrmNotice {
    param([string]$Title, [string]$Message)
    Write-Host ("::notice title=" + $Title + "::" + (Format-HrmCommandText -Text $Message -Limit 2000))
}

function Write-HrmError {
    param([string]$Title, [string]$Message)
    Write-Host ("::error title=" + $Title + "::" + (Format-HrmCommandText -Text $Message))
}

function Read-HrmJsonFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function Get-HrmJsonProperty {
    # Отсутствующее свойство — это провал проверки, а не «значение по
    # умолчанию»: именно verified/origin/release_sha доказывают, что снимок
    # прежней версии сделан ДО перезаписи файлов.
    param($Object, [string]$Name)
    if ($null -eq $Object) { throw ("нет объекта для чтения свойства " + $Name) }
    if (-not $Object.PSObject.Properties[$Name]) { throw ("в файле нет свойства " + $Name) }
    return $Object.$Name
}

function Write-HrmJsonFile {
    # Как installer/build.ps1: UTF-8 без BOM (файл читает движок).
    param([string]$Path, [string]$Json)
    [System.IO.File]::WriteAllText($Path, $Json, (New-Object System.Text.UTF8Encoding($false)))
}

function Get-HrmSmokeStateDump {
    # Сводка состояния движка для аннотации: supervisor.json / setup-run.json /
    # update-result.json (движок пишет их уже отредактированными, без секретов).
    param([string]$StateDir)
    $parts = @()
    foreach ($name in @("supervisor.json", "setup-run.json", "update-result.json")) {
        $file = Join-Path $StateDir $name
        if (-not (Test-Path -LiteralPath $file)) { continue }
        try {
            $data = Read-HrmJsonFile $file
            if ($null -eq $data) { continue }
            $fields = @()
            foreach ($prop in $data.PSObject.Properties) {
                $value = [string]$prop.Value
                if ($value.Length -gt 160) { $value = $value.Substring(0, 160) + "…" }
                $fields += ($prop.Name + "=" + $value)
            }
            $parts += ($name + ": " + ($fields -join "; "))
        } catch { $parts += ($name + ": нечитаем") }
    }
    if ($parts.Count -eq 0) { return "нет файлов состояния" }
    return ($parts -join " | ")
}

function Invoke-HrmSetupProcess {
    # /LOG=<файл>: журнал Inno нужен, чтобы сбой был виден причиной, а не
    # «exit code 1». Код возврата обязателен к проверке: остановка мастера до
    # перезаписи файлов (гейт снимка) обязана быть видна автоматике.
    # Сторожевой таймаут: зависший мастер (или зависший под ним движок/Docker
    # на раннере) не должен подвешивать смоук часами — по таймауту процессы
    # убиваются, а в ошибку попадает сводка состояния движка.
    param([string]$Exe, [string]$LogPath, [string]$StateDir, [int]$TimeoutMinutes = 40)
    Remove-Item -LiteralPath $LogPath -Force -ErrorAction SilentlyContinue
    $arguments = @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", ('/LOG="{0}"' -f $LogPath))
    $process = Start-Process -FilePath $Exe -ArgumentList $arguments -PassThru
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    while (-not $process.HasExited -and (Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
    }
    if (-not $process.HasExited) {
        try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch { }
        foreach ($engine in @(Get-CimInstance -ClassName Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue)) {
            $cmd = [string]$engine.CommandLine
            if ($cmd -match "hr-manager\.ps1|hrm-tray\.ps1|hrm-snapshot\.ps1") {
                try { Stop-Process -Id ([int]$engine.ProcessId) -Force -ErrorAction SilentlyContinue } catch { }
            }
        }
        throw ("мастер не завершился за " + $TimeoutMinutes + " минут (возможно, зависание движка/Docker на раннере) — процесс остановлен; состояние движка: " + (Get-HrmSmokeStateDump -StateDir $StateDir))
    }
    return [int]$process.ExitCode
}

function Write-HrmSetupLogTail {
    # Хвост журнала мастера — в аннотацию: логи шага читать из среды
    # сопровождения нельзя, а причина остановки мастера есть только там.
    param([string]$Path, [string]$Title, [int]$Lines = 25)
    if (-not (Test-Path -LiteralPath $Path)) {
        Write-HrmError $Title ("журнал мастера не найден: " + $Path)
        return
    }
    $tail = @(Get-Content -LiteralPath $Path -Encoding UTF8 -Tail $Lines)
    Write-HrmError $Title ("журнал " + $Path + "`n" + ($tail -join "`n"))
}

function Assert-HrmSetupLog {
    param([string]$Path, [string]$Pattern, [string]$Message)
    if (-not (Test-Path -LiteralPath $Path)) { throw ($Message + " (журнала нет: " + $Path + ")") }
    $text = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    if ($text -notmatch $Pattern) {
        throw ($Message + " (в журнале мастера нет строки по образцу: " + $Pattern + ")")
    }
}

function Show-HrmSnapshotDecision {
    # Файл диагностики пишет мастер (installer.iss). На чистой установке
    # каталога состояния ещё нет, и решение видно только в журнале мастера.
    param([string]$Directory, [string]$Label)
    $file = Join-Path $Directory "setup-snapshot.json"
    $data = Read-HrmJsonFile $file
    if ($null -eq $data) {
        Write-HrmNotice $Label ("файла диагностики снимка нет: " + $file)
        return $null
    }
    $summary = "schema=" + [string]$data.schema + " needed=" + [string]$data.needed +
        " status=" + [string]$data.status + " reason=" + [string]$data.reason +
        " run_ok=" + [string]$data.run_ok + " exit=" + [string]$data.run_exit +
        " stop=" + [string]$data.stop + " at=" + [string]$data.at
    Write-HrmNotice $Label $summary
    return $data
}

function Get-HrmSmokeDockerEngineOs {
    # OSType работающего Docker Engine: 'linux' — Linux-движок (Docker Desktop),
    # 'windows' — Windows-движок (Windows-контейнеры), '' — движка нет вовсе
    # (или зонд не уложился в таймаут). Диагностика раннера настоящим
    # `docker info` — движок HR Manager не запускается. Зонд — в отдельной
    # job с таймаутом 30 с: зависший daemon не должен подвешивать смоук.
    $probe = Start-Job -ScriptBlock {
        try {
            $output = (& docker.exe info --format "{{.OSType}}" 2>$null)
            if ($LASTEXITCODE -ne 0) { return "" }
            return ([string]$output).Trim()
        } catch { return "" }
    }
    if (-not (Wait-Job $probe -Timeout 30)) {
        # Daemon не ответил за 30 с — это отдельный случай «движок не
        # недоступен», а «не отвечает»: заметно в notice.
        $script:SmokeDockerProbeTimeout = $true
        Remove-Job $probe -Force -ErrorAction SilentlyContinue
        return ""
    }
    $result = Receive-Job $probe
    Remove-Job $probe -Force -ErrorAction SilentlyContinue
    if ($null -eq $result) { return "" }
    return ([string]$result).Trim()
}

function Test-HrmSmokeDockerRefusal {
    # Отказ движка на Docker-гейте обязан быть узнаваемым: известные
    # формулировки (каждая — честный отказ, а не «успех» и не посторонний сбой).
    param([string]$Message)
    if (-not $Message) { return $false }
    $patterns = @("Linux-движок", "Linux-контейнер", "Docker Desktop", "Docker Engine", "Предполётная проверка")
    foreach ($pattern in $patterns) {
        if ($Message.Contains($pattern)) { return $true }
    }
    return $false
}

function Get-HrmSmokeEngineState {
    # Состояние движка (supervisor.json) после остановки: state + message.
    param([string]$StateDir)
    $file = Join-Path $StateDir "supervisor.json"
    if (-not (Test-Path -LiteralPath $file)) { return $null }
    try {
        $data = Read-HrmJsonFile $file
        if ($null -eq $data) { return $null }
        return [pscustomobject]@{
            state = [string](Get-HrmJsonProperty -Object $data -Name "state")
            message = [string](Get-HrmJsonProperty -Object $data -Name "message")
        }
    } catch { return $null }
}

function Get-HrmSmokeUpdateResult {
    # Результат обновления движка (update-result.json) после отказа гейта.
    param([string]$StateDir)
    $file = Join-Path $StateDir "update-result.json"
    if (-not (Test-Path -LiteralPath $file)) { return $null }
    try { return (Read-HrmJsonFile $file) } catch { return $null }
}

try {
    if (-not $RepoRoot) {
        $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
    }
    if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA "Programs\HRManager" }
    if (-not $StateDir) { $StateDir = Join-Path $env:LOCALAPPDATA "HRManager" }

    Write-HrmNotice "Silent smoke" ("старт: скрипт=" + $PSCommandPath + " каталог установки=" + $InstallDir)

    # --- ДО запуска мастера: доступен ли Linux-движок Docker на раннере? ------
    # Стек HR Manager — Linux-контейнеры. Без Linux-движка движок обязан
    # остановиться на Docker-гейте с понятным отказом (а не «успехом»), и смоук
    # проверяет именно это — честный ограниченный режим вместо маскировки.
    $script:SmokeDockerProbeTimeout = $false
    $engineOs = Get-HrmSmokeDockerEngineOs
    $linuxEngine = ($engineOs -eq "linux")
    if ($linuxEngine) {
        Write-HrmNotice "Silent smoke" "режим: полный — Docker Engine OSType=linux (установка, обновление, удаление со стеком)"
    }
    else {
        $probeNote = if ($script:SmokeDockerProbeTimeout) { "зонд docker info не уложился в 30 с (daemon не отвечает)" } else { "OSType='" + $engineOs + "'" }
        Write-HrmNotice "Silent smoke" ("режим: ограниченный — Linux-движка нет (" + $probeNote + "). Мастер запускается, и движок обязан остановиться на Docker-гейте с ожидаемым отказом (а не «успехом»). НЕ покрыто на этом раннере: сборка образов, запуск стека, готовность, первый запуск, миграции, backup-ворота.")
    }

    $setup = Get-ChildItem -Path (Join-Path $RepoRoot "installer\output") -Filter "HR-Manager-Setup-*.exe" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $setup) { throw "в installer\output нет HR-Manager-Setup-*.exe" }
    Write-HrmNotice "Silent smoke" ("мастер: " + $setup.Name + " (" + $setup.Length + " байт)")

    # --- 1. Чистая установка -------------------------------------------------
    # Снимка не будет (сохранять нечего), и мастер обязан продолжить установку:
    # решение видно в журнале Inno (/LOG) строкой «HRM: snapshot decision».
    $installLog = Join-Path $env:TEMP "hrm-smoke-install.log"
    $installCode = Invoke-HrmSetupProcess -Exe $setup.FullName -LogPath $installLog -StateDir $StateDir -TimeoutMinutes 40
    Assert-HrmSetupLog -Path $installLog -Pattern "HRM: snapshot decision needed=0" `
        -Message "мастер не сообщил, что на чистой установке сохранять нечего"
    if ($linuxEngine) {
        if ($installCode -ne 0) {
            Write-HrmSetupLogTail -Path $installLog -Title "Silent smoke: чистая установка не прошла"
            throw ("чистая установка завершилась с кодом " + $installCode + "; состояние движка: " + (Get-HrmSmokeStateDump -StateDir $StateDir))
        }
        Write-HrmNotice "Silent smoke: установка" "exit=0"
    }
    else {
        # Ограниченный режим: движок обязан остановиться на Docker-гейте с
        # ожидаемым отказом (а не «успехом»). Inno Setup НЕ пробрасывает код
        # возврата [Run]-записи в код мастера, поэтому вердикт движка читаем
        # из его файлов состояния. Авторитет — setup-run.json: его пишет
        # мастер (status=running) и затем движок (status=failed), порядок
        # детерминирован, гонки с треем нет. supervisor.json — сверка
        # по возможности (трей тоже пишет его при старте).
        $marker = Read-HrmJsonFile (Join-Path $StateDir "setup-run.json")
        if ($null -eq $marker) { throw "нет отметки установки (setup-run.json) — движок не запускался или не записал отказ" }
        if ([string](Get-HrmJsonProperty -Object $marker -Name "status") -ne "failed") {
            throw ("движок не записал отказ честно: setup-run.json status=" + [string]$marker.status + " (мастер exit=" + $installCode + ")")
        }
        $markerMessage = [string](Get-HrmJsonProperty -Object $marker -Name "message")
        if (-not (Test-HrmSmokeDockerRefusal $markerMessage)) {
            Write-HrmSetupLogTail -Path $installLog -Title "Silent smoke: неожиданный отказ движка"
            throw ("движок остановился не на Docker-гейте: " + $markerMessage)
        }
        $engineState = Get-HrmSmokeEngineState -StateDir $StateDir
        $supervisorNote = "нет supervisor.json"
        if ($null -ne $engineState) {
            $supervisorNote = ("supervisor.json state=" + $engineState.state + " message=" + $engineState.message)
        }
        Write-HrmNotice "Silent smoke: установка (ограниченный режим)" ("мастер exit=" + $installCode + " (Inno не пробрасывает код [Run]); движок остановился на Docker-гейте: " + $markerMessage + "; " + $supervisorNote)
    }

    $engine = Join-Path $InstallDir "infra\windows\hr-manager.ps1"
    if (-not (Test-Path -LiteralPath $engine)) { throw "в каталоге установки нет движка" }
    $uninstaller = Join-Path $InstallDir "unins000.exe"
    $uninstallAvailable = Test-Path -LiteralPath $uninstaller
    if ($linuxEngine -and -not $uninstallAvailable) { throw "нет деинсталлятора" }
    if (-not $linuxEngine -and -not $uninstallAvailable) {
        Write-HrmNotice "Silent smoke" "деинсталлятор не создан — фаза удаления не покрыта на этом раннере"
    }

    # --- 2. «Прежняя версия» и обновление поверх -----------------------------
    # Идентификаторы обязаны отличаться от пакета: иначе снимок прежней версии
    # делать запрещено (files_replaced) — так мастер защищается от «отката» к
    # сбойной сборке.
    $previousSha = ("a" * 40)
    $releasePath = Join-Path $InstallDir "release.json"
    $release = Read-HrmJsonFile $releasePath
    $newSha = [string](Get-HrmJsonProperty -Object $release -Name "release_sha")
    if (-not $newSha) { throw "в release.json пакета нет release_sha" }
    $release.release_sha = $previousSha
    $release.version = "0.0.1"
    Write-HrmJsonFile -Path $releasePath -Json ($release | ConvertTo-Json -Depth 5)
    # Дата назад: пакетный release.json должен быть новее своего прежнего.
    (Get-Item -LiteralPath $releasePath).LastWriteTime = (Get-Date).AddDays(-1)
    $record = [ordered]@{
        version = "0.0.1"
        release_sha = $previousSha
        port = 8080
        installed_at = (Get-Date).AddDays(-1).ToString("o")
    }
    Write-HrmJsonFile -Path (Join-Path $StateDir "installed.json") -Json ($record | ConvertTo-Json)
    Remove-Item -LiteralPath (Join-Path $StateDir "previous-snapshot.json") -Force -ErrorAction SilentlyContinue

    $upgradeLog = Join-Path $env:TEMP "hrm-smoke-upgrade.log"
    $upgradeCode = Invoke-HrmSetupProcess -Exe $setup.FullName -LogPath $upgradeLog -StateDir $StateDir -TimeoutMinutes 40
    $decision = Show-HrmSnapshotDecision -Directory $StateDir -Label "Silent smoke: снимок"
    if ($linuxEngine) {
        if ($upgradeCode -ne 0) {
            Write-HrmSetupLogTail -Path $upgradeLog -Title "Silent smoke: обновление не прошло"
            throw ("обновление поверх установки завершилось с кодом " + $upgradeCode + "; состояние движка: " + (Get-HrmSmokeStateDump -StateDir $StateDir))
        }
        Write-HrmNotice "Silent smoke: обновление" "exit=0"
    }
    else {
        # Ограниченный режим: движок обязан остановиться на Docker-гейте
        # обновления (T1) и честно записать отказ. Inno не пробрасывает код
        # [Run] в код мастера — вердикт в файлах движка: update-result.json
        # (только движок) и setup-run.json (мастер → движок, без гонки).
        $result = Get-HrmSmokeUpdateResult -StateDir $StateDir
        if ($null -eq $result) { throw "движок не записал результат обновления (update-result.json) — обновление не дошло до гейта?" }
        if ([string](Get-HrmJsonProperty -Object $result -Name "status") -ne "failed") {
            throw ("обновление без Linux-движка не отмечено как failed (status=" + [string]$result.status + ") — возможен замаскированный успех")
        }
        $resultMessage = [string](Get-HrmJsonProperty -Object $result -Name "message")
        if (-not (Test-HrmSmokeDockerRefusal $resultMessage)) {
            Write-HrmSetupLogTail -Path $upgradeLog -Title "Silent smoke: неожиданный отказ движка"
            throw ("движок остановился не на Docker-гейте: " + $resultMessage)
        }
        $marker = Read-HrmJsonFile (Join-Path $StateDir "setup-run.json")
        if ($null -eq $marker -or [string](Get-HrmJsonProperty -Object $marker -Name "status") -ne "failed") {
            throw "движок не записал отказ в отметке установки (setup-run.json не failed)"
        }
        Write-HrmNotice "Silent smoke: обновление (ограниченный режим)" ("мастер exit=" + $upgradeCode + " (Inno не пробрасывает код [Run]); снимок подтверждён; движок остановился на Docker-гейте: " + $resultMessage)
    }

    # Снимок обязан быть сделан ДО перезаписи: подтверждён, сделан мастером и
    # содержит файлы ПРЕЖНЕЙ версии, а {app} уже перезаписан новым релизом.
    $descriptor = Read-HrmJsonFile (Join-Path $StateDir "previous-snapshot.json")
    if ($null -eq $descriptor) { throw "снимок прежней версии не создан (previous-snapshot.json)" }
    if ((Get-HrmJsonProperty -Object $descriptor -Name "verified") -ne $true) {
        throw "снимок прежней версии не подтверждён (verified <> true)"
    }
    $origin = [string](Get-HrmJsonProperty -Object $descriptor -Name "origin")
    if ($origin -ne "installer") { throw ("снимок сделан не мастером установки: origin=" + $origin) }
    $descriptorSha = [string](Get-HrmJsonProperty -Object $descriptor -Name "release_sha")
    if ($descriptorSha -ne $previousSha) { throw ("снимок сделан не из прежней версии: " + $descriptorSha) }
    $files = [int](Get-HrmJsonProperty -Object $descriptor -Name "files")
    if ($files -le 0) { throw "в снимке прежней версии нет ни одного файла" }
    $snapshotRelease = Read-HrmJsonFile (Join-Path $StateDir "previous-snapshot\release.json")
    $snapshotSha = [string](Get-HrmJsonProperty -Object $snapshotRelease -Name "release_sha")
    if ($snapshotSha -ne $previousSha) { throw ("в снимке нет файлов прежней версии: " + $snapshotSha) }
    $nowSha = [string](Get-HrmJsonProperty -Object (Read-HrmJsonFile $releasePath) -Name "release_sha")
    if ($nowSha -ne $newSha) { throw ("мастер не заменил файлы программы: " + $nowSha) }
    if ($null -ne $decision) {
        $status = [string](Get-HrmJsonProperty -Object $decision -Name "status")
        if ($status -ne "verified") { throw ("решение мастера о снимке не подтверждено: status=" + $status) }
        $needed = [string](Get-HrmJsonProperty -Object $decision -Name "needed")
        if ($needed -ne "1" -and $needed -ne "True") { throw ("мастер не считал снимок нужным: needed=" + $needed) }
        $stopped = [string](Get-HrmJsonProperty -Object $decision -Name "stop")
        if ($stopped -eq "1" -or $stopped -eq "True") { throw "мастер сообщил об остановке при успешном обновлении" }
    }
    Write-HrmNotice "Silent smoke: снимок" ("verified=true origin=installer файлов=" + $files +
        " прежний=" + $previousSha.Substring(0, 12) + " новый=" + $newSha.Substring(0, 12))

    # --- 3. Удаление ---------------------------------------------------------
    # Программу удаляем; каталог состояния и тома данных остаются (их хранит
    # движок и человек, а не мастер). В ограниченном режиме — только если
    # мастер успел создать деинсталлятор (фаза удаления объявляется непокрытой,
    # а не подменяется успехом).
    if ($uninstallAvailable) {
        $uninstallLog = Join-Path $env:TEMP "hrm-smoke-uninstall.log"
        $uninstallCode = Invoke-HrmSetupProcess -Exe $uninstaller -LogPath $uninstallLog -StateDir $StateDir -TimeoutMinutes 15
        if ($uninstallCode -ne 0) {
            Write-HrmSetupLogTail -Path $uninstallLog -Title "Silent smoke: удаление не прошло"
            throw ("удаление завершилось с кодом " + $uninstallCode)
        }
        if (Test-Path -LiteralPath $engine) { throw "после удаления файлы программы остались на месте" }
        Write-HrmNotice "Silent smoke: удаление" "exit=0"
    }

    $modeLabel = if ($linuxEngine) { "полный" } else { "ограниченный (без Linux-движка)" }
    Write-HrmNotice "Silent smoke" ("все фазы пройдены (режим: " + $modeLabel + ")")
    exit 0
}
catch {
    Write-HrmError "Silent smoke" ("сбой: " + $_.Exception.Message)
    exit 1
}
