# ===========================================================================
# Действия движка: install / start / stop / status / update / diagnostics /
# uninstall / repair / resume. Идемпотентны, неинтерактивны (никаких prompt в
# installer/CI/test mode), ошибки — по-русски с ненулевым кодом возврата.
# ===========================================================================

function script:Start-HrmStack {
    <# Поднимает контур и ждёт готовности frontend на loopback. #>
    param(
        [Parameter(Mandatory)][string]$AppDir,
        [Parameter(Mandatory)][string]$StateRoot,
        [int]$WaitSeconds = 240
    )
    $result = Invoke-HrmCompose -AppDir $AppDir -StateRoot $StateRoot -ComposeArgs @("up", "-d", "--wait", "--wait-timeout", "$WaitSeconds")
    if ($result.ExitCode -ne 0) { return $false }
    return (Wait-HrmReady -StateRoot $StateRoot -WaitSeconds 60)
}

function script:Wait-HrmReady {
    param(
        [Parameter(Mandatory)][string]$StateRoot,
        [int]$WaitSeconds = 60
    )
    $config = Get-HrmConfig $StateRoot
    $port = 8081
    if ($config -and $config.port) { $port = [int]$config.port }
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while ((Get-Date) -lt $deadline) {
        $res = Invoke-HrmHttpJson -Url "http://127.0.0.1:$port/api/health" -TimeoutSec 5
        if ($res -and $res.StatusCode -eq 200 -and $res.Json -and $res.Json.status -eq "ok") { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function script:Invoke-HrmMigrate {
    param([Parameter(Mandatory)][string]$AppDir, [Parameter(Mandatory)][string]$StateRoot)
    $result = Invoke-HrmCompose -AppDir $AppDir -StateRoot $StateRoot -ComposeArgs @("run", "--rm", "backend", "alembic", "upgrade", "head")
    return $result.ExitCode -eq 0
}

function script:Get-HrmOpsStatus {
    param([Parameter(Mandatory)][string]$StateRoot)
    $config = Get-HrmConfig $StateRoot
    $port = 8081
    if ($config -and $config.port) { $port = [int]$config.port }
    $res = Invoke-HrmHttpJson -Url "http://127.0.0.1:$port/api/ops/status" -TimeoutSec 8
    if (-not $res -or $res.StatusCode -ne 200) { return $null }
    return $res.Json
}

function script:Get-HrmAppDirFromConfig {
    param($Config)
    if ($Config -and $Config.releaseDir) { return [string]$Config.releaseDir }
    return (Get-HrmAppRoot)
}

function script:Test-HrmOwnerClaimed {
    param([Parameter(Mandatory)][string]$StateRoot)
    $config = Get-HrmConfig $StateRoot
    if (-not $config) { return $false }
    $res = Invoke-HrmHttpJson -Url "http://127.0.0.1:$($config.port)/api/setup/first-run/state" -TimeoutSec 6
    return ($res -and $res.StatusCode -eq 200 -and $res.Json -and [bool]$res.Json.pilot_owner_exists)
}

function script:Set-HrmConfigValue {
    <# Точечное обновление config.json без пересоздания объекта (PS 5.1
       не умеет spread-литералы). #>
    param(
        [Parameter(Mandatory)][string]$StateRoot,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)]$Value
    )
    $config = Get-HrmConfig $StateRoot
    if (-not $config) { return }
    $config | Add-Member -NotePropertyName $Name -NotePropertyValue $Value -Force
    Set-HrmConfig -StateRoot $StateRoot -Config $config
}

function script:Test-HrmSurname {
    <# Валидация фамилии (тот же контракт, что и в GUI): 2–60 символов,
       буквы (включая кириллицу), дефис, апостроф, пробел. #>
    param([AllowEmptyString()][string]$Surname)
    $value = $Surname.Trim()
    if ($value.Length -lt 2 -or $value.Length -gt 60) {
        return "Фамилия должна содержать от 2 до 60 символов."
    }
    if ($value -notmatch "^[\p{L}][\p{L}'’ \-]*$") {
        return "Фамилия может содержать только буквы, пробел, дефис и апостроф."
    }
    return ""
}

function script:New-HrmFirstRunPairing {
    <# Выдача одноразового кода first-run. Фамилия и код передаются ТОЛЬКО
       через STDIN в `docker compose exec -T backend python -m app.cli
       pilot-pairing issue` — никогда в командной строке. Когда владелец уже
       создан, повторный install ничего не создаёт и не ротит. #>
    param(
        [Parameter(Mandatory)][string]$AppDir,
        [Parameter(Mandatory)][string]$StateRoot,
        [string]$InputFile = ""
    )
    $config = Get-HrmConfig $StateRoot
    $res = Invoke-HrmHttpJson -Url "http://127.0.0.1:$($config.port)/api/setup/first-run/state" -TimeoutSec 8
    if ($res -and $res.StatusCode -eq 200 -and $res.Json) {
        if ($res.Json.pilot_owner_exists) {
            Set-HrmConfigValue -StateRoot $StateRoot -Name "ownerClaimed" -Value $true
            return [pscustomobject]@{ code = ""; ttlMinutes = 0; reused = $true }
        }
        if ($res.Json.pending -and (-not $InputFile)) {
            $pairFile = Read-HrmJson (Join-Path $StateRoot "pairing.json")
            if ($pairFile) {
                return [pscustomobject]@{ code = [string]$pairFile.code; ttlMinutes = [int]$pairFile.ttlMinutes; reused = $true }
            }
        }
    }
    $surname = ""
    $workRole = "hr"
    if ($InputFile -and (Test-Path $InputFile)) {
        $installerInput = Read-HrmJson $InputFile
        $surname = [string]$installerInput.surname
        if ($installerInput.workRole) { $workRole = [string]$installerInput.workRole }
        # Файл входа НЕ удаляем здесь: при сбое выдачи кода повтор безопасного
        # шага должен иметь те же данные. Удаляем после успешной выдачи.
    }
    $problems = Test-HrmSurname -Surname $surname
    if ($problems) { throw (New-HrmError $problems "Проверьте ввод в окне установщика." 2) }
    if (@("hr", "manager", "admin") -notcontains $workRole) {
        throw (New-HrmError "Неизвестный рабочий режим: $workRole" "Выберите HR, Руководителя или Администратора." 2)
    }
    $pairingCode = New-HrmPairingCode
    $ttl = 15
    $payload = (@{ code = $pairingCode; surname = $surname; work_role = $workRole } | ConvertTo-Json -Compress)
    $result = Invoke-HrmCompose -AppDir $AppDir -StateRoot $StateRoot `
        -ComposeArgs @("exec", "-T", "backend", "python", "-m", "app.cli", "pilot-pairing", "issue") `
        -StdInText $payload
    if ($result.ExitCode -ne 0) {
        throw (New-HrmError "Backend не принял выдачу кода первого входа." "Откройте «Диагностика HR Manager» и повторите безопасный шаг." 1)
    }
    if ($InputFile -and (Test-Path $InputFile)) { Remove-Item $InputFile -Force -ErrorAction SilentlyContinue }
    if ($workRole) { Set-HrmConfigValue -StateRoot $StateRoot -Name "workRole" -Value $workRole }
    $pairPath = Join-Path $StateRoot "pairing.json"
    Write-HrmPrivateFile -Path $pairPath -Content (@{
        code       = $pairingCode
        issuedAt   = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        ttlMinutes = $ttl
        workRole   = $workRole
    } | ConvertTo-Json -Compress)
    return [pscustomobject]@{ code = $pairingCode; ttlMinutes = $ttl; reused = $false }
}

function script:Install-HrmShortcuts {
    <# Ярлыки создаёт движок ПОСЛЕ smoke-проверки (контракт GUI). Цель —
       wscript + VBS-обёртка (штатный компонент Windows): скрытый запуск
       движка, никакого видимого терминала. #>
    param([Parameter(Mandatory)][string]$StateRoot)
    if ($env:HRMGR_TEST_STATE_DIR) {
        $binDir = Join-Path $env:HRMGR_TEST_STATE_DIR "bin"
    } else {
        $binDir = [IO.Path]::Combine([Environment]::GetFolderPath("LocalApplicationData"), "HR Manager", "bin")
    }
    if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir -Force | Out-Null }
    $enginePath = Join-Path $script:HrmEngineDir "hr-manager.ps1"
    $vbsLines = @(
        "' HR Manager launcher: скрытый запуск локального контура (без терминала)."
        "Option Explicit"
        "Dim sh, action, cmd"
        'Set sh = CreateObject("WScript.Shell")'
        'action = LCase(Trim(Replace(WScript.Arguments.Named("action"), Chr(34), "")))'
        'If action = "" Then action = "ensure-running"'
        'cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " & Chr(34) & ENGINEPATH & Chr(34) & " -Action " & action & " -FromShortcut"'
        'sh.Run cmd, 0, False'
    )
    $safeEngine = $enginePath.Replace("'", "''")
    $vbsLines[6] = $vbsLines[6].Replace("ENGINEPATH", "'" + $safeEngine + "'")
    [IO.File]::WriteAllText((Join-Path $binDir "hr-manager-launch.vbs"), ($vbsLines -join [Environment]::NewLine), (New-Object Text.UTF8Encoding($false)))

    if (-not (Test-HrmWindows)) { return $false }  # в тестовом контуре ярлыки не создаём
    $shell = New-Object -ComObject WScript.Shell
    $targets = @(
        @{ Name = "HR Manager"; Action = "ensure-running" },
        @{ Name = "Диагностика HR Manager"; Action = "diagnostics" }
    )
    $folders = @((Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs\HR Manager"),
                 [Environment]::GetFolderPath("Desktop"))
    foreach ($folder in $folders) {
        if (-not (Test-Path $folder)) { New-Item -ItemType Directory -Path $folder -Force | Out-Null }
    }
    foreach ($t in $targets) {
        foreach ($folder in $folders) {
            $lnk = $shell.CreateShortcut((Join-Path $folder ($t.Name + ".lnk")))
            $lnk.TargetPath = "$env:WINDIR\System32\wscript.exe"
            $lnk.Arguments = """{0}\hr-manager-launch.vbs"" /action:{1}" -f $binDir, $t.Action
            $lnk.WorkingDirectory = $binDir
            $lnk.Description = $t.Name
            $lnk.Save()
        }
    }
    return $true
}

function script:Invoke-HrmPrepareDocker {
    <# «Docker Desktop отсутствует»: скачиваем ОФИЦИАЛЬНЫЙ установщик по
       закрепленным в репозитории версии и SHA-256, проверяем хэш и запускаем
       его. Лицензию принимает сам пользователь — движок её не принимает.
       Сохраняем resume-точку и одноразовое продолжение (HKCU RunOnce). #>
    param([Parameter(Mandatory)][string]$StateRoot, [Parameter(Mandatory)][string]$RepoRoot)
    $manifest = Get-HrmDockerDesktopManifest -RepoRoot $RepoRoot
    $target = Join-Path ([System.IO.Path]::GetTempPath()) "DockerDesktopInstaller.exe"
    Write-HrmProgress -StateRoot $StateRoot -Phase "docker" -Detail "Docker Desktop $($manifest.version) — официальный установитель"
    $need = $true
    if (Test-Path $target) {
        if ((Get-FileHash -Algorithm SHA256 -Path $target).Hash.ToLowerInvariant() -eq $manifest.sha256) { $need = $false }
    }
    if ($need) {
        try {
            (New-Object Net.WebClient).DownloadFile($manifest.url, $target)
        } catch {
            throw (New-HrmError "Не удалось скачать Docker Desktop с официального сайта." "Проверьте интернет-соединение и повторите безопасный шаг." 3)
        }
    }
    $hash = (Get-FileHash -Algorithm SHA256 -Path $target).Hash.ToLowerInvariant()
    if ($hash -ne $manifest.sha256) {
        Remove-Item $target -Force -ErrorAction SilentlyContinue
        throw (New-HrmError "Контрольная сумма Docker Desktop не совпала с закреплённой в релизе — загрузка отклонена." "Повторите безопасный шаг." 3)
    }
    $resume = [ordered]@{
        stage  = "await-docker"
        note   = "после установки Docker Desktop (возможен перезапуск Windows) продолжение автоматически"
        savedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    }
    Write-HrmJson (Join-Path $StateRoot "resume.json") $resume
    Register-HrmResumeRunOnce -StateRoot $StateRoot
    Write-HrmLog -StateRoot $StateRoot -Message "запущен официальный установщик Docker Desktop; resume-точка сохранена"
    throw (New-HrmError "Нужен Docker Desktop: откройте скачанный установщик, примите лицензию и перезагрузите компьютер при просьбе — установка продолжится сама." "Дождитесь значка Docker Desktop в трее." 3)
}

function script:Register-HrmResumeRunOnce {
    <# Одноразовое продолжение после перезагрузки: только HKCU\...\RunOnce,
       без администратора. Повторно не срабатывает (RunOnce самосъёмный). #>
    param([Parameter(Mandatory)][string]$StateRoot)
    if (-not (Test-HrmWindows)) { return }
    try {
        $enginePath = Join-Path $script:HrmEngineDir "hr-manager.ps1"
        $command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$enginePath`" -Action resume"
        New-Item -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce" -Force -ErrorAction SilentlyContinue | Out-Null
        Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce" -Name "HRManagerSetupResume" -Value $command
    } catch {
        Write-HrmLog -StateRoot $StateRoot -Message "не удалось зарегистрировать RunOnce: продолжение доступно повторным запуском Setup.exe" -Level warn
    }
}

function script:Invoke-HrmResume {
    <# Продолжение с сохранённой точки: идемпотентно доделывает install
       (владельца повторно не создаёт — это гарантирует install), снимает
       свою RunOnce-метку и удаляет resume-файл. #>
    param([Parameter(Mandatory)][string]$AppDir, [Parameter(Mandatory)][string]$StateRoot)
    $resume = Read-HrmJson (Join-Path $StateRoot "resume.json")
    if (-not $resume) {
        Write-HrmLog -StateRoot $StateRoot -Message "resume: точка продолжения не найдена — делать ничего не нужно"
        return [pscustomobject]@{ Ok = $true; Resumed = $false }
    }
    if (Test-HrmWindows) {
        Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce" -Name "HRManagerSetupResume" -ErrorAction SilentlyContinue
    }
    $result = Invoke-HrmInstall -AppDir $AppDir -StateRoot $StateRoot -AllowNonWindows:(-not (Test-HrmWindows))
    Remove-Item (Join-Path $StateRoot "resume.json") -Force -ErrorAction SilentlyContinue
    return [pscustomobject]@{ Ok = $true; Resumed = $true; Result = $result }
}

function script:Invoke-HrmInstall {
    <# Полный сценарий установки (см. контракт §2): preflight → секреты →
       целостность релиза → config/pilot.env → build+up → миграции → smoke →
       pairing (один владелец) → ярлыки → готовность. Повторный запуск
       идемпотентен: секреты, роль и владелец не меняются, данные не
       удаляются. InputFile — JSON от GUI-установщика {surname, workRole}. #>
    param(
        [Parameter(Mandatory)][string]$AppDir,
        [Parameter(Mandatory)][string]$StateRoot,
        [string]$InputFile = "",
        [int]$Port = 8081,
        [switch]$SkipDocker,
        [switch]$AllowNonWindows,
        [string]$DockerManifestDir = ""
    )
    Write-HrmProgress -StateRoot $StateRoot -Phase "preflight" -Detail "Проверяем систему перед установкой"
    $pre = Invoke-HrmPreflight -StateRoot $StateRoot -Port $Port -SkipDocker:$SkipDocker -AllowNonWindows:$AllowNonWindows
    if (-not $pre.Ok) {
        $dockerMissing = @($pre.Failed | Where-Object { $_.Code -eq "docker_missing" })
        if ($dockerMissing.Count -gt 0 -and $DockerManifestDir) {
            return Invoke-HrmPrepareDocker -StateRoot $StateRoot -RepoRoot $DockerManifestDir
        }
        $first = $pre.Failed[0]
        $exit = 2
        if ($first.Code -eq "disk") { $exit = 6 } elseif ($first.Code -eq "config") { $exit = 5 }
        throw (New-HrmError $first.Message $first.Hint $exit)
    }

    $problems = Test-HrmReleaseIntegrity -ReleaseDir $AppDir
    if (@($problems).Count -gt 0) {
        throw (New-HrmError ("Файлы установочного комплекта повреждены: " + ($problems -join "; ")) `
                "Запустите Setup.exe заново или возьмите актуальный установщик из официального релиза." 5)
    }

    Write-HrmProgress -StateRoot $StateRoot -Phase "secrets" -Detail "Создаём локальные ключи — вводить и видеть их не нужно"
    $secrets = Ensure-HrmSecrets -StateRoot $StateRoot

    $existing = Get-HrmConfig $StateRoot
    if ($existing) {
        $config = $existing
    } else {
        $config = New-HrmInstallConfig -ReleaseDir $AppDir -Port $Port -TestMode:$AllowNonWindows
    }
    # Порт обновляем только если он ещё не зафиксирован (reinstall не меняет).
    if (-not $config.port) {
        $config | Add-Member -NotePropertyName port -NotePropertyValue $Port -Force
    }
    Set-HrmConfig -StateRoot $StateRoot -Config $config
    Write-HrmPilotEnv -StateRoot $StateRoot -Secrets ([pscustomobject]$secrets) -Config $config

    Write-HrmProgress -StateRoot $StateRoot -Phase "build" -Detail "Собираем контейнеры — это может занять несколько минут"
    $build = Invoke-HrmCompose -AppDir $AppDir -StateRoot $StateRoot -ComposeArgs @("build")
    if ($build.ExitCode -ne 0) {
        Write-HrmLog -StateRoot $StateRoot -Message "docker compose build завершился с ошибкой" -Level error
        throw (New-HrmError "Не удалось собрать контейнеры HR Manager." "Нажмите «Повторить безопасный шаг» — сборка идемпотентна." 1)
    }

    Write-HrmProgress -StateRoot $StateRoot -Phase "start" -Detail "Запускаем приложение и готовим локальную базу"
    $stackUp = Invoke-HrmCompose -AppDir $AppDir -StateRoot $StateRoot -ComposeArgs @("up", "-d")
    if ($stackUp.ExitCode -ne 0) {
        throw (New-HrmError "Контейнеры не запустились." "Откройте «Диагностика HR Manager» и повторите безопасный шаг." 1)
    }
    if (-not (Wait-HrmReady -StateRoot $StateRoot -WaitSeconds 120)) {
        throw (New-HrmError "HR Manager не ответил на локальной проверке после запуска." "Откройте «Диагностика HR Manager» и повторите безопасный шаг." 1)
    }
    if (-not (Invoke-HrmMigrate -AppDir $AppDir -StateRoot $StateRoot)) {
        throw (New-HrmError "Не удалось применить структуру локальной базы данных." "Откройте «Диагностика HR Manager»." 1)
    }

    Write-HrmProgress -StateRoot $StateRoot -Phase "smoke" -Detail "Проверяем готовность сервисов"
    if (-not (Wait-HrmReady -StateRoot $StateRoot -WaitSeconds 60)) {
        throw (New-HrmError "Проверка готовности не прошла: приложение не отвечает." "Нажмите «Открыть диагностику» и повторите безопасный шаг." 1)
    }

    Write-HrmProgress -StateRoot $StateRoot -Phase "pairing" -Detail "Готовим безопасный первый вход"
    $pairing = New-HrmFirstRunPairing -AppDir $AppDir -StateRoot $StateRoot -InputFile $InputFile

    Write-HrmProgress -StateRoot $StateRoot -Phase "shortcuts" -Detail "Создаём ярлыки «HR Manager» и «Диагностика HR Manager»"
    Install-HrmShortcuts -StateRoot $StateRoot | Out-Null

    Write-HrmProgress -StateRoot $StateRoot -Phase "done" -Detail "Готово — введите код подтверждения в открывшейся странице"
    Write-HrmLog -StateRoot $StateRoot -Message "install завершён (installId=$($config.installId), версия $($config.releaseVersion))"
    return [pscustomobject]@{
        Ok                      = $true
        Url                     = "http://127.0.0.1:$($config.port)/first-run"
        PairingCode             = $pairing.code
        PairingExpiresInMinutes = $pairing.ttlMinutes
        Reused                  = [bool]$pairing.reused
    }
}

function script:Invoke-HrmStart {
    param([Parameter(Mandatory)][string]$AppDir, [Parameter(Mandatory)][string]$StateRoot, [int]$WaitSeconds = 240)
    $config = Get-HrmConfig $StateRoot
    if (-not $config) {
        throw (New-HrmError "HR Manager ещё не установлен на этом компьютере." "Запустите «HR Manager Setup.exe»." 5)
    }
    $ready = Start-HrmStack -AppDir $AppDir -StateRoot $StateRoot -WaitSeconds $WaitSeconds
    if (-not $ready) { throw (New-HrmError "Приложение не вышло в готовность после запуска." "Откройте «Диагностика HR Manager»." 1) }
    Set-HrmConfigValue -StateRoot $StateRoot -Name "ownerClaimed" -Value ([bool](Test-HrmOwnerClaimed -StateRoot $StateRoot))
    return [pscustomobject]@{ Ok = $true; Url = "http://127.0.0.1:$($config.port)" }
}

function script:Invoke-HrmEnsureRunning {
    <# Пункт ярлыка «HR Manager»: запустить контур (если нужно) и открыть
       приложение. Идемпотентен, без терминала. #>
    param([Parameter(Mandatory)][string]$AppDir, [Parameter(Mandatory)][string]$StateRoot)
    $config = Get-HrmConfig $StateRoot
    if (-not $config) { throw (New-HrmError "HR Manager ещё не установлен." "Запустите «HR Manager Setup.exe»." 5) }
    $status = Get-HrmStatusInternal -StateRoot $StateRoot
    if ($status.appState -notin @("running", "degraded")) {
        Start-HrmStack -AppDir $AppDir -StateRoot $StateRoot | Out-Null
        $status = Get-HrmStatusInternal -StateRoot $StateRoot
    }
    $claimed = [bool]$config.ownerClaimed
    if (-not $claimed) {
        $claimed = Test-HrmOwnerClaimed -StateRoot $StateRoot
        if ($claimed) { Set-HrmConfigValue -StateRoot $StateRoot -Name "ownerClaimed" -Value $true }
    }
    $url = "http://127.0.0.1:$($config.port)"
    if (-not $claimed) { $url = "$url/first-run" }
    if ((Test-HrmWindows) -and -not $env:HRMGR_TEST_STATE_DIR) {
        Start-Process $url | Out-Null
    }
    return [pscustomobject]@{ Ok = $true; Url = $url; Running = ($status.appState -in @("running", "degraded")) }
}

function script:Invoke-HrmStop {
    param([Parameter(Mandatory)][string]$AppDir, [Parameter(Mandatory)][string]$StateRoot)
    $result = Invoke-HrmCompose -AppDir $AppDir -StateRoot $StateRoot -ComposeArgs @("stop")
    Write-HrmLog -StateRoot $StateRoot -Message "остановка контура завершена (код $($result.ExitCode)); данные сохранены"
    return [pscustomobject]@{ Ok = ($result.ExitCode -eq 0) }
}

function script:Get-HrmStatusInternal {
    <# Единый агрегированный статус (§6 контракта): docker отсутствует/daemon
       не запущен; stopped/starting/running/degraded; база; head миграций;
       worker; backup; версии; необязательные каналы. Без секретов и PII. #>
    param([Parameter(Mandatory)][string]$StateRoot)
    $config = Get-HrmConfig $StateRoot
    $docker = Test-HrmDocker
    $result = [ordered]@{
        docker       = "ok"
        appState     = "not-installed"
        database     = "unknown"
        migrations   = "unknown"
        worker       = "unknown"
        backup       = "unknown"
        installed    = $null
        running      = $null
        versionMatch = $null
        integrations = [ordered]@{ telegram = "unknown"; email = "unknown" }
        problems     = @()
    }
    if (-not $docker.Ok) {
        $result.docker = $docker.daemonStatus
        if ($docker.daemonStatus -eq "missing") { $result.problems += "Docker Desktop не установлен" }
        else { $result.problems += "Не запущен Docker Desktop (служба недоступна)" }
        return $result
    }
    if (-not $config) { return $result }
    $ops = Get-HrmOpsStatus -StateRoot $StateRoot
    if (-not $ops) {
        $runningCheck = Invoke-HrmCompose -AppDir (Get-HrmAppDirFromConfig $config) -StateRoot $StateRoot -ComposeArgs @("ps", "-q", "frontend")
        if ($runningCheck.ExitCode -eq 0 -and $runningCheck.StdOutText.Trim()) { $result.appState = "starting" }
        else { $result.appState = "stopped" }
        return $result
    }
    if ($ops.status -eq "ok") { $result.appState = "running" } else { $result.appState = "degraded" }
    if ($ops.database -and $ops.database.status -eq "ok") { $result.database = "ok" } else {
        $result.database = "down"; $result.problems += "База данных недоступна"
    }
    if ($ops.migrations -and $null -ne $ops.migrations.ok) {
        if ($ops.migrations.ok) { $result.migrations = "match" }
        else { $result.migrations = "drift"; $result.problems += "Структура базы не совпадает с версией приложения" }
    }
    if ($ops.notifications -and $null -ne $ops.notifications.worker_alive) {
        if ([bool]$ops.notifications.worker_alive) { $result.worker = "ok" }
        else { $result.worker = "stale"; $result.problems += "Worker уведомлений не отвечает (heartbeat просрочен)" }
    }
    if ($ops.backup) {
        if (-not $ops.backup.available) { $result.backup = "missing"; $result.problems += "Резервных копий ещё нет" }
        elseif ($ops.backup.ok -ne $true) { $result.backup = "failed"; $result.problems += "Последняя проверка резервной копии неуспешна" }
        elseif ($null -ne $ops.backup.age_seconds -and [double]$ops.backup.age_seconds -gt 26 * 3600) { $result.backup = "stale"; $result.problems += "Резервная копия устарела" }
        else { $result.backup = "ok" }
    }
    $result.installed = $config.releaseVersion
    $result.running = $ops.release_sha
    if ($config.releaseSha -and $ops.release_sha) {
        $result.versionMatch = ($config.releaseSha -eq $ops.release_sha)
        if (-not $result.versionMatch) { $result.problems += "Установленная и запущенная версии различаются" }
    }
    # Необязательные каналы читаем из окружения контейнера (флаги-булевы,
    # без значений секретов): честное «не настроено» вместо выдумывания.
    $envProbe = Invoke-HrmCompose -AppDir (Get-HrmAppDirFromConfig $config) -StateRoot $StateRoot `
        -ComposeArgs @("exec", "-T", "backend", "sh", "-c", "echo ${TELEGRAM_ENABLED:-false}|${SMTP_ENABLED:-false}")
    if ($envProbe.ExitCode -eq 0 -and $envProbe.StdOutText -match "(\w+)\|(\w+)") {
        $result.integrations.telegram = $(if ($Matches[1] -eq "true") { "configured" } else { "not_configured" })
        $result.integrations.email = $(if ($Matches[2] -eq "true") { "configured" } else { "not_configured" })
    }
    return $result
}

function script:Invoke-HrmStatus {
    param([Parameter(Mandatory)][string]$StateRoot)
    return Get-HrmStatusInternal -StateRoot $StateRoot
}

function script:Invoke-HrmDiagnostics {
    <# Санитаризованный отчёт для поддержки: версии, compose-состояние, коды
       health, хвосты логов. Запрещены: env-значения, connection strings,
       cookies, токены, пароли, email/телефоны, ФИО, тексты сообщений.
       Перед записью — контрольная проверка отсутствия секретов (fail closed). #>
    param(
        [Parameter(Mandatory)][string]$AppDir,
        [Parameter(Mandatory)][string]$StateRoot,
        [switch]$OpenFolder
    )
    $secrets = @()
    $secretsPath = Join-Path $StateRoot "secrets.json"
    if (Test-Path $secretsPath) {
        $s = Read-HrmJson $secretsPath
        $secrets = @([string]$s.secretKey, [string]$s.postgresPassword, [string]$s.backupEncKey)
    }
    $status = Get-HrmStatusInternal -StateRoot $StateRoot
    $sb = New-Object Text.StringBuilder
    [void]$sb.AppendLine("HR Manager — диагностика ($((Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm')) UTC)")
    [void]$sb.AppendLine("Состояние: $($status.appState); docker: $($status.docker); база: $($status.database); миграции: $($status.migrations); worker: $($status.worker); backup: $($status.backup)")
    [void]$sb.AppendLine("Версия установлена: $($status.installed); запущено: $($status.running)")
    [void]$sb.AppendLine("Интеграции: telegram=$($status.integrations.telegram), email=$($status.integrations.email) (необязательные)")
    if (@($status.problems).Count -gt 0) { [void]$sb.AppendLine("Проблемы: " + ($status.problems -join "; ")) }
    $ps = Invoke-HrmCompose -AppDir $AppDir -StateRoot $StateRoot -ComposeArgs @("ps", "--format", "json")
    if ($ps.ExitCode -eq 0) {
        [void]$sb.AppendLine("")
        [void]$sb.AppendLine("Состояние контейнеров:")
        foreach ($line in $ps.StdOut) {
            if (-not $line.Trim()) { continue }
            try {
                $svc = $line | ConvertFrom-Json
                [void]$sb.AppendLine("  $($svc.Service): $($svc.State) ($($svc.Health))")
            } catch { [void]$sb.AppendLine("  $line") }
        }
    }
    foreach ($svc in @("backend", "frontend", "worker", "backup")) {
        $logs = Invoke-HrmCompose -AppDir $AppDir -StateRoot $StateRoot -ComposeArgs @("logs", "--tail", "40", "--no-color", $svc)
        if ($logs.ExitCode -eq 0 -and $logs.StdOutText.Trim()) {
            [void]$sb.AppendLine("")
            [void]$sb.AppendLine("Последние строки ($svc):")
            foreach ($line in ($logs.StdOutText -split "`r?`n")) {
                [void]$sb.AppendLine("  " + (Protect-HrmText -Text $line -Secrets $secrets))
            }
        }
    }
    $dir = Join-Path $StateRoot "logs"
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $report = $sb.ToString()
    if (-not (Test-HrmSecretValueAbsent -Text $report -Secrets $secrets)) {
        throw (New-HrmError "Диагностика остановлена: в отчёт попало секретное значение." "Сообщите разработчикам (код DIAG-LEAK)." 1)
    }
    $path = Join-Path $dir ("diagnostics-{0}.txt" -f (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss"))
    [IO.File]::WriteAllText($path, $report, (New-Object Text.UTF8Encoding($false)))
    Write-HrmJson (Join-Path $StateRoot "status.json") $status
    if ($OpenFolder -and (Test-HrmWindows)) { Start-Process explorer.exe "/select,`"$path`"" | Out-Null }
    return [pscustomobject]@{ Ok = $true; Path = $path; Status = $status }
}
