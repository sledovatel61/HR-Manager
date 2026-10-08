# Установка, запуск/остановка/статус, открытие, удаление и возобновление.

Set-StrictMode -Version 2.0

function Get-HrmReleaseSha {
    # SHA релиза установленного снимка: release.json (пишет сборка установщика)
    # либо git-рев из checkout.
    param([string]$InstallDir, [string]$StateDir)
    $releaseFile = Join-Path $InstallDir "release.json"
    if (Test-Path $releaseFile) {
        $data = Get-HrmJsonFile $releaseFile
        if ($null -ne $data -and $data.release_sha) { return [string]$data.release_sha }
    }
    $git = Join-Path $InstallDir ".git"
    if (Test-Path $git) {
        $rev = Invoke-HrmExternal -Name "git.exe" -Arguments @("-C", $InstallDir, "rev-parse", "HEAD") -IgnoreExitCode
        if ($rev.ExitCode -eq 0) { return $rev.Stdout.Trim() }
    }
    return ""
}

function Get-HrmNormalizedPath {
    # Абсолютный путь для сравнения: без хвостовых разделителей, кроме корня
    # диска («C:\»), с единым разделителем. Нужен, потому что Setup передаёт
    # движку -SourceDir «{app}» — то есть источник и назначение формально разные
    # строки, а на диске один и тот же каталог.
    param([string]$Path)
    if (-not $Path) { return "" }
    $full = [System.IO.Path]::GetFullPath($Path)
    $trimmed = $full.TrimEnd([char[]]@('\', '/'))
    if (-not $trimmed) { return $full }
    if ($trimmed -match '^[A-Za-z]:$') { return ($trimmed + '\') }
    return $trimmed
}

function Test-HrmSamePath {
    # Один и тот же каталог (регистр в Windows не важен).
    param([string]$Left, [string]$Right)
    $l = Get-HrmNormalizedPath $Left
    $r = Get-HrmNormalizedPath $Right
    if (-not $l -or -not $r) { return $false }
    return [string]::Equals($l, $r, [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-HrmPathInside {
    # $Child лежит внутри $Parent (или совпадает с ним).
    param([string]$Parent, [string]$Child)
    $p = Get-HrmNormalizedPath $Parent
    $c = Get-HrmNormalizedPath $Child
    if (-not $p -or -not $c) { return $false }
    if ([string]::Equals($p, $c, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    $prefix = $p
    if (-not $prefix.EndsWith('\')) { $prefix = $prefix + '\' }
    return $c.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Assert-HrmSnapshotComplete {
    # Снимок приложения полон: по infra\compose.pilot.yml движок собирает стек,
    # backend/ и frontend/ нужны для сборки образов. Проверка выполняется ДО
    # подмены файлов и ПОСЛЕ копирования: иначе установка могла бы «успешно»
    # завершиться с пустым или половинчатым снимком.
    param([string]$Dir, [string]$Label = "снимок")
    $missing = @()
    if (-not (Test-Path -Path (Join-Path $Dir "infra\compose.pilot.yml") -PathType Leaf)) { $missing += "infra\compose.pilot.yml" }
    foreach ($name in @("backend", "frontend")) {
        if (-not (Test-Path -Path (Join-Path $Dir $name) -PathType Container)) { $missing += $name }
    }
    if ($missing.Count -gt 0) {
        throw ("{0} неполный ({1}): нет {2}." -f $Label, $Dir, ($missing -join ", "))
    }
    return $true
}

function Copy-HrmSnapshot {
    # Копирует снимок приложения (infra/, backend/, frontend/, release.json)
    # из SourceDir в InstallDir. Не трогает .git каталог назначения.
    #
    # ГРАНИЦЫ (дефект P1 первичной установки):
    #   * одинаковые пути — так работает Setup.exe: он сам раскладывает файлы в
    #     {app} и запускает движок с -SourceDir «{app}». Раньше функция удаляла
    #     каталог назначения перед копированием и в этом случае уничтожала сам
    #     снимок (удаляла источник), после чего копировать было нечего. Теперь
    #     одинаковые пути — не копирование, а проверка снимка на месте: ничего
    #     не удаляется;
    #   * вложенные пути (источник внутри назначения или назначение внутри
    #     источника) — отказ ДО любых изменений файлов: копирование каталога
    #     внутрь себя зациклилось бы, а предварительная очистка удалила бы
    #     источник;
    #   * обычное копирование идёт через временный каталог рядом с назначением,
    #     поэтому источник никогда не оказывается местом очистки;
    #   * любая ошибка копирования или неполный результат — исключение:
    #     установка не может «успешно» завершиться с наполовину скопированным
    #     снимком (раньше ошибки Copy-Item не останавливали установку).
    param([string]$SourceDir, [string]$InstallDir)
    $sourceFull = Get-HrmNormalizedPath $SourceDir
    $installFull = Get-HrmNormalizedPath $InstallDir
    if (-not $sourceFull -or -not $installFull) {
        throw "Copy-HrmSnapshot: не заданы -SourceDir и -InstallDir."
    }
    if (Test-HrmSamePath $sourceFull $installFull) {
        Assert-HrmSnapshotComplete -Dir $installFull -Label "Каталог установки" | Out-Null
        $null = Write-HrmLog "info" "Снимок приложения уже разложен в каталог установки — копирование не требуется."
        return
    }
    if (Test-HrmPathInside -Parent $installFull -Child $sourceFull) {
        throw ("Каталог релиза ({0}) находится внутри каталога установки ({1}): копирование снимка внутрь самого себя запрещено — файлы не изменены." -f $sourceFull, $installFull)
    }
    if (Test-HrmPathInside -Parent $sourceFull -Child $installFull) {
        throw ("Каталог установки ({0}) находится внутри каталога релиза ({1}): копирование снимка в собственный подкаталог запрещено — файлы не изменены." -f $installFull, $sourceFull)
    }
    Assert-HrmSnapshotComplete -Dir $sourceFull -Label "Каталог релиза" | Out-Null

    $components = @("infra", "backend", "frontend")
    if (-not (Test-Path $installFull)) { New-Item -ItemType Directory -Path $installFull -Force | Out-Null }
    $staging = Join-Path $installFull (".hrm-snapshot-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
    $copyRoot = Join-Path $staging "new"
    try {
        New-Item -ItemType Directory -Path $copyRoot -Force -ErrorAction Stop | Out-Null
        foreach ($name in ($components + "release.json")) {
            $source = Join-Path $sourceFull $name
            if (-not (Test-Path $source)) {
                if ($name -eq "release.json") { continue }
                throw ("В каталоге релиза нет {0}: {1}." -f $name, $sourceFull)
            }
            # -ErrorAction Stop: ошибка копирования обязана остановить установку,
            # а не оставить «успешный» статус при неполном снимке.
            Copy-Item -Path $source -Destination (Join-Path $copyRoot $name) -Recurse -Force -ErrorAction Stop
        }
        Assert-HrmSnapshotComplete -Dir $copyRoot -Label "Временная копия снимка" | Out-Null
        # Подмена только после успешной копии и проверки. Источник уже не
        # является местом очистки, поэтому удаление каталогов назначения не
        # может уничтожить снимок релиза.
        foreach ($name in $components) {
            $destination = Join-Path $installFull $name
            if (Test-Path $destination) { Remove-Item -Path $destination -Recurse -Force -ErrorAction Stop }
            Move-Item -Path (Join-Path $copyRoot $name) -Destination $destination -Force -ErrorAction Stop
        }
        $releaseSource = Join-Path $sourceFull "release.json"
        if (Test-Path $releaseSource) {
            Move-Item -Path (Join-Path $copyRoot "release.json") -Destination (Join-Path $installFull "release.json") -Force -ErrorAction Stop
        }
        Assert-HrmSnapshotComplete -Dir $installFull -Label "Каталог установки" | Out-Null
        $null = Write-HrmLog "info" ("Снимок приложения разложен из {0}." -f $sourceFull)
    }
    finally {
        if (Test-Path $staging) { Remove-Item -Path $staging -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

function Install-HrmApp {
    # Основной сценарий: повторный запуск распознаёт существующую установку
    # и только открывает/восстанавливает её.
    # -AllowDockerInstall: разрешено предложить/выполнить установку Docker Desktop
    # официальным установщиком (UAC и лицензию Docker принимает человек).
    param(
        [string]$SourceDir = "",
        [string]$InstallDir = "",
        [string]$StateDir = "",
        [int]$Port = 0,
        [switch]$AllowDockerInstall
    )
    if (-not $SourceDir) {
        # Каталог снимка — ближайший родитель, содержащий infra/compose.pilot.yml
        # (для checkout это корень репозитория, для установки — каталог HRManager).
        $probe = $PSScriptRoot
        for ($i = 0; $i -lt 4; $i++) {
            if (Test-Path (Join-Path $probe "infra\compose.pilot.yml")) { break }
            $probe = Split-Path $probe -Parent
        }
        $SourceDir = $probe
    }
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $port = Get-HrmPort $Port

    $existing = Get-HrmInstallRecord $StateDir
    # Идентичность установленной версии читается БЕЗОПАСНО (Get-HrmInstallRecordField):
    # в записи СТАРОЙ установки может не быть release_sha. Если своего
    # идентификатора у записи нет, он берётся из описания снимка, который мастер
    # установки создал ДО замены файлов: без идентификатора обновление не смогло
    # бы ни отличить новую версию от прежней, ни доказать откат.
    $existingSha = [string](Get-HrmInstallRecordField -Record $existing -Field "release_sha")
    $previousVersionFromSnapshot = ""
    if ($null -ne $existing -and -not $existingSha) {
        $snapshotForUpdate = Resolve-HrmPreviousSnapshot -StateDir $StateDir -ReleaseSha ""
        if ($snapshotForUpdate.usable) {
            $existingSha = [string](Get-HrmSnapshotField -Metadata $snapshotForUpdate.metadata -Field "release_sha")
            $previousVersionFromSnapshot = [string](Get-HrmSnapshotField -Metadata $snapshotForUpdate.metadata -Field "version")
            if ($existingSha) {
                Write-HrmLog "info" ("Запись установки без идентификатора версии — прежняя версия взята из снимка мастера: {0}." -f $existingSha)
            }
        }
    }
    if ($null -ne $existing) {
        # B6: повторный запуск новой версии Setup.exe = обновление поверх
        # Существующая установка: проверяем, отличается ли версия в SourceDir
        $sourceReleaseSha = ""
        $sourceVersion = ""
        try {
            $srcReleaseFile = Join-Path $SourceDir "release.json"
            if (Test-Path $srcReleaseFile) {
                $srcData = Get-HrmJsonFile $srcReleaseFile
                if ($null -ne $srcData -and $srcData.release_sha) { $sourceReleaseSha = [string]$srcData.release_sha }
                if ($null -ne $srcData -and $srcData.PSObject.Properties["version"] -and $srcData.version) { $sourceVersion = [string]$srcData.version }
            }
        } catch {}
        $installedSha = $existingSha
        # Идентичность может отсутствовать у ОЧЕНЬ старой установки (в записи и в
        # release.json нет release_sha — «Правило прежней идентичности»). Тогда
        # обновление всё равно обязано состояться, но доказательством «это другая
        # версия» служит ВЕРСИЯ прежней версии из проверенного снимка мастера.
        $legacyUpdate = $false
        if (-not $installedSha -and $sourceReleaseSha -and $previousVersionFromSnapshot -and $sourceVersion -and
            ($previousVersionFromSnapshot -ne $sourceVersion)) {
            $legacyUpdate = $true
        }
        $installedLabel = if ($installedSha) { $installedSha } else { ("версия " + $previousVersionFromSnapshot + " без идентификатора") }
        if (($sourceReleaseSha -and $installedSha -and $sourceReleaseSha -ne $installedSha) -or $legacyUpdate) {
            Write-HrmLog "info" ("Существующая установка найдена: {0}" -f $installedLabel)
            $preview = Get-HrmUpdatePreview -ReleaseDir $SourceDir -InstallDir $InstallDir -StateDir $StateDir
            foreach ($line in (Format-HrmUpdatePreview $preview)) { Write-HrmLog "info" $line }
            Write-HrmLog "info" ("Обнаружена новая версия {0} — запускаю обновление с бэкапом и откатом..." -f $sourceReleaseSha)
            $releaseDirForUpdate = $SourceDir
            $tempRelease = ""
            try {
                $fullSource = [System.IO.Path]::GetFullPath($SourceDir).TrimEnd('\','/')
                $fullInstall = [System.IO.Path]::GetFullPath($InstallDir).TrimEnd('\','/')
                $isSame = ($fullSource -eq $fullInstall)
            } catch { $isSame = $false }
            if ($isSame) {
                # Installer уже перезаписал InstallDir новой версией; делаем временную копию для Update потока
                $tempRelease = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM-update-" + [Guid]::NewGuid().ToString("N").Substring(0,8))
                New-Item -ItemType Directory -Path $tempRelease -Force | Out-Null
                Copy-HrmSnapshot $InstallDir $tempRelease
                $releaseDirForUpdate = $tempRelease
            }
            try {
                Update-HrmApp -ReleaseDir $releaseDirForUpdate -InstallDir $InstallDir -StateDir $StateDir
            } finally {
                if ($tempRelease -and (Test-Path $tempRelease)) { Remove-Item $tempRelease -Recurse -Force -ErrorAction SilentlyContinue }
            }
            Start-HrmFirstRun -InstallDir $InstallDir -StateDir $StateDir -Port $port
            Start-HrmSupervisorIfUserSession -InstallDir $InstallDir -StateDir $StateDir | Out-Null
            Set-HrmSupervisorState -StateDir $StateDir -State "ready" -Message "Обновление завершено."
            return
        }
        Write-HrmLog "info" ("Существующая установка найдена: {0}" -f $installedLabel)
        Write-HrmLog "info" "Повторный запуск установки не меняет данные и секреты."
        if (-not $installedSha) {
            # Идентификатора версии нет даже в снимке мастера: обновление с
            # доказанным откатом невозможно. Говорим честно и запускаем то, что
            # установлено, — вместо исключения PropertyNotFound.
            Write-HrmLog "warn" "Запись установки без идентификатора версии и без проверенного снимка: обновление с откатом недоказуемо — запускаю установленную версию как есть."
        }
        $existingPort = [int](Get-HrmInstallRecordField -Record $existing -Field "port" -Default 0)
        if ($existingPort -le 0) { $existingPort = $port }
        $prepare = Invoke-HrmDockerPrepare -InstallDir $InstallDir -StateDir $StateDir -Port $existingPort -AllowInstall:$AllowDockerInstall -Interactive:($AllowDockerInstall -or (Test-HrmInteractive))
        if (-not $prepare.ok) {
            Set-HrmSupervisorState -StateDir $StateDir -State "error" -Message $prepare.message
            throw $prepare.message
        }
        if (Test-HrmComposeRunning $InstallDir $StateDir) {
            Write-HrmLog "info" "Приложение уже запущено."
        }
        else {
            Write-HrmLog "info" "Запускаю приложение…"
            $stack = Start-HrmStack $InstallDir $StateDir
            if (-not $stack.ok) { throw $stack.message }
            Wait-HrmReady (Get-HrmBaseUrl $existingPort)
        }
        $null = Write-HrmPilotEnv $StateDir (Get-HrmReleaseSha $InstallDir $StateDir) $existingPort
        Start-HrmFirstRun -InstallDir $InstallDir -StateDir $StateDir -Port $existingPort
        Start-HrmSupervisorIfUserSession -InstallDir $InstallDir -StateDir $StateDir | Out-Null
        Set-HrmSupervisorState -StateDir $StateDir -State "ready" -Message "HR Manager запущен."
        return
    }
    # --- Первичная установка (записи установки нет) ---
    Assert-HrmPreflight -InstallDir $InstallDir -StateDir $StateDir -Port $port -SkipCompose
    Initialize-HrmStateDir $StateDir
    # Подготовка рабочей среды: Docker Desktop (при разрешении — официальный
    # установщик), WSL2/виртуализация, ожидание Engine. Без Docker дальше нельзя.
    $prepare = Invoke-HrmDockerPrepare -InstallDir $InstallDir -StateDir $StateDir -Port $port -AllowInstall:$AllowDockerInstall -Interactive:($AllowDockerInstall -or (Test-HrmInteractive))
    if (-not $prepare.ok) {
        Set-HrmSupervisorState -StateDir $StateDir -State "error" -Message $prepare.message
        if ($prepare.needs_install) {
            Write-HrmLog "warn" $prepare.message
            foreach ($line in (Get-HrmDockerInstallGuide)) { Write-HrmLog "info" $line }
        }
        throw $prepare.message
    }
    Protect-HrmFile $StateDir $StateDir
    # Файл ввода первого запуска (если его записал мастер установки)
    # тоже защищается ACL — фамилия владельца не должна читаться другими
    # пользователями машины.
    $inputFile = Get-HrmInputFile $StateDir
    if (Test-Path $inputFile) { Protect-HrmFile $StateDir $inputFile }
    if (-not (Test-Path $InstallDir)) { New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null }
    Copy-HrmSnapshot $SourceDir $InstallDir
    # Снимок обязан быть полным: с этого каталога движок собирает стек.
    Assert-HrmSnapshotComplete -Dir $InstallDir -Label "Каталог установки" | Out-Null

    # Лицензия: публичный ключ проверки — внешний локальный файл
    # StateDir\license_public_key.b64 (см. Secrets.psm1\Install-HrmLicensePublicKey).
    $null = Install-HrmLicensePublicKey -SourceDir $SourceDir -InstallDir $InstallDir -StateDir $StateDir

    $releaseSha = Get-HrmReleaseSha $InstallDir $StateDir
    # Версия снимка — для предпросмотра обновления и отчёта «текущая → новая».
    $installedVersion = ""
    $releaseFile = Join-Path $InstallDir "release.json"
    if (Test-Path $releaseFile) {
        $releaseData = Get-HrmJsonFile $releaseFile
        if ($null -ne $releaseData -and $releaseData.PSObject.Properties["version"] -and $releaseData.version) {
            $installedVersion = [string]$releaseData.version
        }
    }
    Set-HrmInstallRecord $StateDir @{
        release_sha = $releaseSha
        version = $installedVersion
        install_dir = $InstallDir
        state_dir = $StateDir
        port = $port
        installed_at = (Get-Date).ToString("o")
        pilot_created = $false
    }

    $null = Write-HrmPilotEnv $StateDir $releaseSha $port
    # Перепроверяем конфигурацию уже с env-файлом.
    Assert-HrmPreflight -InstallDir $InstallDir -StateDir $StateDir -Port $port | Out-Null

    $stack = Start-HrmStack $InstallDir $StateDir
    if (-not $stack.ok) { throw $stack.message }
    Wait-HrmReady (Get-HrmBaseUrl $port)
    Start-HrmFirstRun -InstallDir $InstallDir -StateDir $StateDir -Port $port
    Start-HrmSupervisorIfUserSession -InstallDir $InstallDir -StateDir $StateDir | Out-Null
    Set-HrmSupervisorState -StateDir $StateDir -State "ready" -Message "HR Manager готов."
    Write-HrmLog "info" ("Установка завершена. Приложение: {0}" -f (Get-HrmBaseUrl $port))
}

function Start-HrmApp {
    param([string]$InstallDir = "", [string]$StateDir = "")
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $record = Get-HrmInstallRecord $StateDir
    if ($null -eq $record) { throw "Установка не найдена. Выполните -Action install." }
    $port = [int](Get-HrmInstallRecordField -Record $record -Field "port" -Default 0)
    if ($port -le 0) { $port = Get-HrmPort }
    $prepare = Invoke-HrmDockerPrepare -InstallDir $InstallDir -StateDir $StateDir -Port $port
    if (-not $prepare.ok) {
        Set-HrmSupervisorState -StateDir $StateDir -State "error" -Message $prepare.message
        throw $prepare.message
    }
    Assert-HrmPreflight -InstallDir $InstallDir -StateDir $StateDir -Port $port | Out-Null
    $stack = Start-HrmStack $InstallDir $StateDir
    if (-not $stack.ok) { throw $stack.message }
    Wait-HrmReady (Get-HrmBaseUrl $port)
    # Управляющий компонент (значок в трее) — единственный supervisor: повторный
    # запуск ничего не дублирует. Наблюдатель канала обновлений живёт внутри
    # supervisor'а, а не в консольном процессе.
    if (Test-HrmInteractive) {
        Start-HrmSupervisorIfUserSession -InstallDir $InstallDir -StateDir $StateDir | Out-Null
    }
    Set-HrmSupervisorState -StateDir $StateDir -State "ready" -Message "HR Manager готов."
    Write-HrmLog "info" ("Приложение запущено: {0}" -f (Get-HrmBaseUrl $port))
}

function Stop-HrmApp {
    param([string]$InstallDir = "", [string]$StateDir = "")
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    if (-not (Get-HrmInstallRecord $StateDir)) { throw "Установка не найдена." }
    Stop-HrmChannelWatch -StateDir $StateDir
    if (Test-HrmComposeRunning $InstallDir $StateDir) {
        Stop-HrmStack $InstallDir $StateDir
    }
    else {
        Write-HrmLog "info" "Приложение не запущено."
    }
}

function Get-HrmAppStatus {
    param([string]$InstallDir = "", [string]$StateDir = "")
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $record = Get-HrmInstallRecord $StateDir
    $port = [int](Get-HrmInstallRecordField -Record $record -Field "port" -Default 0)
    if ($port -le 0) { $port = Get-HrmPort }
    $baseUrl = Get-HrmBaseUrl $port
    $running = Test-HrmComposeRunning $InstallDir $StateDir
    $frontend = if ($running) { Test-HrmFrontendReady $baseUrl } else { $false }
    $backend = if ($running) { Test-HrmBackendReady $baseUrl } else { $false }
    $ops = if ($backend) { Get-HrmOpsStatus $baseUrl } else { $null }

    $lines = @()
    $lines += "HR Manager — статус"
    $lines += ("Установлено:      {0}" -f $(if ($record) { [string](Get-HrmInstallRecordField -Record $record -Field "release_sha") } else { "нет" }))
    $lines += ("Контейнеры:       {0}" -f $(if ($running) { "запущены" } else { "остановлены" }))
    $lines += ("Фронтенд:         {0}" -f $(if ($frontend) { "готов ($baseUrl)" } else { "недоступен" }))
    $lines += ("Бэкенд:           {0}" -f $(if ($backend) { "готов" } else { "недоступен" }))
    if ($null -ne $ops) {
        $lines += ("Версия в работе:  {0}" -f $ops.release_sha)
        $lines += ("Миграции:         {0} (ожидается {1})" -f $ops.migrations.current_revision, $ops.migrations.expected_revision)
    }
    $lines | ForEach-Object { Protect-HrmOutput $_ }
}

function Open-HrmApp {
    param([string]$InstallDir = "", [string]$StateDir = "")
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $record = Get-HrmInstallRecord $StateDir
    $port = [int](Get-HrmInstallRecordField -Record $record -Field "port" -Default 0)
    if ($port -le 0) { $port = Get-HrmPort }
    if (-not (Test-HrmComposeRunning $InstallDir $StateDir)) {
        Write-HrmLog "info" "Приложение не запущено — запускаю…"
        $guard = Enter-HrmActionLock -StateDir $StateDir -Action "open"
        if (-not $guard.acquired) {
            Write-HrmLog "info" $guard.message
            return
        }
        try {
            $prepare = Invoke-HrmDockerPrepare -InstallDir $InstallDir -StateDir $StateDir -Port $port
            if (-not $prepare.ok) { throw $prepare.message }
            $stack = Start-HrmStack $InstallDir $StateDir
            if (-not $stack.ok) { throw $stack.message }
            Wait-HrmReady (Get-HrmBaseUrl $port)
        }
        finally { Exit-HrmActionLock -StateDir $StateDir }
    }
    Invoke-HrmOpenBrowser (Get-HrmBaseUrl $port)
}

function Remove-HrmApp {
    # Удаление: контейнеры снимаются, тома Postgres/бэкапов СОХРАНЯЮТСЯ.
    # Docker Desktop и WSL2 никогда не удаляются. -PurgeData удаляет данные
    # только после точной фразы, свежего бэкапа и deep verification. Бэкапы сохраняются.
    param(
        [string]$InstallDir = "",
        [string]$StateDir = "",
        [bool]$PurgeData = $false
    )
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    if (-not (Get-HrmInstallRecord $StateDir)) {
        Write-HrmLog "info" "Установка не найдена — удалять нечего."
        return
    }
    Stop-HrmChannelWatch -StateDir $StateDir
    Stop-HrmSupervisor -InstallDir $InstallDir -StateDir $StateDir
    if ($PurgeData) {
        # Фраза-подтверждение. Неинтерактивно фраза берётся из
        # HRM_PURGE_CONFIRMATION (документировано в infra/windows/README.md).
        $typed = ""
        if ($env:HRM_PURGE_CONFIRMATION) { $typed = $env:HRM_PURGE_CONFIRMATION }
        if (-not $typed) {
            $typed = Invoke-HrmPrompt -Prompt "Удаление данных необратимо. Напечатайте точно: УДАЛИТЬ ДАННЫЕ HR MANAGER" -Default ""
        }
        if ($typed -ne "УДАЛИТЬ ДАННЫЕ HR MANAGER") {
            throw "Фраза подтверждения не совпала — данные не удалены."
        }
        $offer = Invoke-HrmConfirmationPrompt -Prompt "Создать и проверить обязательный шифрованный бэкап перед удалением?" -Default $true
        if (-not $offer) { throw "Отказ от обязательного бэкапа — purge отменён, данные сохранены." }
        # Keep the database available until a fresh encrypted backup AND the
        # scheduler's deep integrity check succeed. Never accept an old backup
        # or a successful oneshot without verification as permission to purge.
        $backup = Invoke-HrmCompose $InstallDir $StateDir @("run", "--rm", "-e", "BACKUP_REASON=pre-uninstall backup", "backup", "oneshot") -IgnoreExitCode
        if ($backup.ExitCode -ne 0) { throw "Бэкап не создан — purge запрещён." }
        $check = Invoke-HrmCompose $InstallDir $StateDir @("run", "--rm", "backup", "check") -IgnoreExitCode
        if ($check.ExitCode -ne 0) { throw "Бэкап не прошёл deep verification — purge запрещён." }
        Write-HrmLog "info" "Свежий шифрованный бэкап создан и проверен; pilot_backups сохраняется."
    }
    # Always down without -v, also when only stopped containers remain. A failed
    # down must not be followed by volume deletion (including under test mocks).
    $down = Invoke-HrmCompose $InstallDir $StateDir @("down", "--remove-orphans") -IgnoreExitCode
    if ($down.ExitCode -ne 0) { throw "Стек не остановлен — удаление данных запрещено." }
    if ($PurgeData) {
        Remove-HrmPilotDataVolume
        Write-HrmLog "info" "Том данных удалён. Том бэкапов и каталог состояния сохранены."
    }
    # Каталоги состояния/установки удаляет деинсталлятор Inno Setup;
    # движок оставляет их, чтобы повторная установка восстановила данные.
    Write-HrmLog "info" "Приложение удалено. Docker Desktop и WSL2 не тронуты."
}

function Resume-HrmOperation {
    # Возобновление прерванной операции (после перезагрузки/UAC):
    # если есть журнал обновления — продолжает обновление; иначе просто
    # поднимает стек и доводит первый запуск.
    param([string]$InstallDir = "", [string]$StateDir = "")
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $journal = Get-HrmUpdateJournal $StateDir
    if (Test-Path $journal) {
        $data = Get-HrmJsonFile $journal
        if ($null -ne $data -and $data.phase) {
            Write-HrmLog "info" ("Найден прерванный процесс обновления (фаза {0}) — продолжаю." -f $data.phase)
            Update-HrmApp -ReleaseDir ([string]$data.release_dir) -InstallDir $InstallDir -StateDir $StateDir
            return
        }
    }
    $pending = Get-HrmPendingDockerOperation $StateDir
    if ($null -ne $pending) {
        $kind = ""
        if ($pending.PSObject.Properties["kind"]) { $kind = [string]$pending.kind }
        Write-HrmLog "info" "Найдена незавершённая установка рабочей среды — продолжаю автоматически."
        $null = Set-HrmSetupMarker -StateDir $StateDir -Status "running" -Message "Продолжаем установку HR Manager…"
        Set-HrmSupervisorState -StateDir $StateDir -State "starting" -Message "Подготавливаем рабочую среду…" -Busy $true
        $prepare = Invoke-HrmDockerPrepare -InstallDir $InstallDir -StateDir $StateDir -AllowInstall -Interactive
        if (-not $prepare.ok) {
            Set-HrmSupervisorState -StateDir $StateDir -State "error" -Message $prepare.message
            throw $prepare.message
        }
        if ($kind -eq "reboot") { Clear-HrmPendingDockerOperation $StateDir }
        # Записи об установке может ещё НЕ быть: перезагрузка после установки
        # Docker прерывает ПЕРВИЧНУЮ установку до её записи. Продолжаем её —
        # файлы снимка уже разложены мастером установки, поэтому запускаем
        # обычную установку из каталога установки (одинаковые пути безопасны:
        # снимок проверяется, а не копируется поверх самого себя).
        $record = Get-HrmInstallRecord $StateDir
        if ($null -eq $record -and (Test-Path (Join-Path $InstallDir "infra\compose.pilot.yml"))) {
            Write-HrmLog "info" "Продолжаю первичную установку HR Manager (файлы уже на месте)."
            Install-HrmApp -SourceDir $InstallDir -InstallDir $InstallDir -StateDir $StateDir -AllowDockerInstall
            Clear-HrmSetupMarker -StateDir $StateDir
            return
        }
    }
    # Записи об установке может ещё НЕ быть: перезагрузка Windows или закрытие
    # мастера прерывают ПЕРВИЧНУЮ установку до её записи. Файлы снимка уже
    # разложены мастером установки — продолжаем установку из каталога установки
    # (одинаковые пути безопасны: снимок проверяется, а не копируется поверх
    # самого себя). Так «после перезагрузки HR Manager продолжит сам» становится
    # правдой и для первичной установки, а не только для обновления.
    $recordForResume = Get-HrmInstallRecord $StateDir
    if ($null -eq $recordForResume -and (Test-Path (Join-Path $InstallDir "infra\compose.pilot.yml"))) {
        Write-HrmLog "info" "Продолжаю первичную установку HR Manager (файлы уже на месте)."
        $null = Set-HrmSetupMarker -StateDir $StateDir -Status "running" -Message "Продолжаем установку HR Manager…"
        Set-HrmSupervisorState -StateDir $StateDir -State "starting" -Message "Подготавливаем рабочую среду…" -Busy $true
        Install-HrmApp -SourceDir $InstallDir -InstallDir $InstallDir -StateDir $StateDir -AllowDockerInstall
        Clear-HrmSetupMarker -StateDir $StateDir
        return
    }
    Write-HrmLog "info" "Прерванных операций нет; поднимаю приложение."
    Start-HrmApp -InstallDir $InstallDir -StateDir $StateDir
    $port = Get-HrmPort
    $record = Get-HrmInstallRecord $StateDir
    if ($null -ne $record -and $record.PSObject.Properties["port"] -and $record.port) { $port = [int]$record.port }
    Start-HrmFirstRun -InstallDir $InstallDir -StateDir $StateDir -Port $port
}
