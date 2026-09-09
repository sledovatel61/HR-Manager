# ===========================================================================
# Обновление релиза (§10 контракта). Порядок жёсткий:
#   блокировка (single-instance lock)
#   → предпроверки + проверка целостности вложенного релиза (файл-лист +
#     хэши + источник)
#   → docker compose build НОВЫХ образов (старые контейнеры ещё работают)
#   → резервная копия ДО любых миграций (deep-подтверждение, не «успех запуска»)
#   → swap файлов (прежний комплект — в appRoot\previous для отката кода)
#   → остановка контейнеров (данные и тома не трогаются)
#   → alembic upgrade head (backend-консоль; идемпотентна; advisory lock в БД)
#   → запуск новой версии + ожидание готовности
#   → post-check (health/миграции/worker/backup, версия в /ops/status)
#   → снятие блокировки.
# Откат = ТОЛЬКО код/контейнеры на предыдущий релиз; миграции никогда не
# откатываются (политика forward-only); сбой сохраняет resume-точку, повтор
# безопасного шага идемпотентно продолжает с того же этапа.
# ===========================================================================

function script:Get-HrmHistoryPath { param([string]$StateRoot) return (Join-Path $StateRoot "update-history.json") }

function script:Add-HrmHistoryEntry {
    param([Parameter(Mandatory)][string]$StateRoot, [Parameter(Mandatory)][hashtable]$Entry)
    $path = Get-HrmHistoryPath -StateRoot $StateRoot
    $history = @()
    if (Test-Path $path) {
        $raw = Read-HrmJson $path
        if ($raw -and $raw.entries) { $history = @($raw.entries) }
    }
    $Entry["at"] = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $history += $Entry
    # Устойчивая форма: всегда объект-обёртка (иначе массив из одного элемента
    # ConvertTo-Json сериализует объектом и читатель сломается).
    Write-HrmJson $path @{ entries = @($history) }
}

function script:Acquire-HrmUpdateLock {
    param([Parameter(Mandatory)][string]$StateRoot)
    $lockPath = Join-Path $StateRoot "update.lock"
    if (Test-Path $lockPath) {
        $info = Read-HrmJson $lockPath
        $ageMinutes = 9999
        try {
            $started = [DateTime]::ParseExact([string]$info.startedAt, "yyyy-MM-ddTHH:mm:ssZ", [Globalization.CultureInfo]::InvariantCulture)
            $ageMinutes = [int](((Get-Date).ToUniversalTime() - $started).TotalMinutes)
        } catch { }
        if ($ageMinutes -lt 120) {
            throw (New-HrmError "Другое обновление уже выполняется — дождитесь его завершения." "Откройте «Диагностика HR Manager», чтобы увидеть прогресс." 4)
        }
        # Старше 2 часов — сирота прерванной сессии; перехват (операции идемпотентны).
        Write-HrmLog -StateRoot $StateRoot -Message "перехвачен устаревший update.lock (${ageMinutes} мин)" -Level warn
    }
    Write-HrmJson $lockPath ([ordered]@{ startedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ"); pid = $PID })
    return $lockPath
}

function script:Release-HrmUpdateLock {
    param([string]$LockPath)
    if ($LockPath -and (Test-Path $LockPath)) { Remove-Item $LockPath -Force -ErrorAction SilentlyContinue }
}

function script:Get-HrmBackupMarkers {
    param([string]$StateRoot)
    $dir = Join-Path $StateRoot "backup"
    if (-not (Test-Path $dir)) { return @() }
    return @(Get-ChildItem -Path $dir -Filter "*.sql.gz" -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
}

function script:Invoke-HrmBackupOnce {
    <# Ручной запуск backup-сервиса ONESHOT=1 — тот же код, что и в контуре.
       Deep-успех = свежий файл копии + подтверждение в выводе (не просто
       «контейнер завершился с кодом 0»). #>
    param([Parameter(Mandatory)][string]$AppDir, [Parameter(Mandatory)][string]$StateRoot)
    $before = @(Get-HrmBackupMarkers -StateRoot $StateRoot)
    $result = Invoke-HrmCompose -AppDir $AppDir -StateRoot $StateRoot -ComposeArgs @("run", "--rm", "backup")
    if ($result.ExitCode -ne 0) { return $false }
    $after = @(Get-HrmBackupMarkers -StateRoot $StateRoot)
    if ($after.Count -le $before.Count) { return $false }
    if ($result.StdOutText -notmatch "backup created") { return $false }
    if ($result.StdOutText -notmatch "deep check ok") { return $false }
    return $true
}

function script:Write-HrmUpdateStage {
    param([string]$StateRoot, [string]$Stage)
    if ($Stage) {
        Write-HrmJson (Join-Path $StateRoot "update-stage.json") ([ordered]@{ stage = $Stage })
    } else {
        Remove-Item (Join-Path $StateRoot "update-stage.json") -Force -ErrorAction SilentlyContinue
    }
}

function script:Get-HrmUpdateStage {
    param([string]$StateRoot)
    $p = Join-Path $StateRoot "update-stage.json"
    if (-not (Test-Path $p)) { return "" }
    $s = Read-HrmJson $p
    if ($s -and $s.stage) { return [string]$s.stage }
    return ""
}

function script:Invoke-HrmUpdate {
    param(
        [Parameter(Mandatory)][string]$StateRoot,
        [Parameter(Mandatory)][string]$NewReleaseDir,
        [switch]$AllowNonWindows
    )
    $appRoot = Get-HrmAppRoot
    $config = Get-HrmConfig $StateRoot
    if (-not $config) {
        throw (New-HrmError "Обновление невозможно: HR Manager ещё не установлен." "Сначала выполните установку через Setup.exe." 5)
    }
    $oldReleaseDir = [string]$config.releaseDir
    if (-not $oldReleaseDir) { $oldReleaseDir = $appRoot }
    $oldConfigSnapshot = $config.PSObject.Copy()
    $oldVersion = [string]$config.releaseVersion

    Write-HrmProgress -StateRoot $StateRoot -Phase "update-check" -Detail "Проверяем новый релиз"
    $newManifest = Get-HrmReleaseManifest $NewReleaseDir
    if (-not $newManifest) {
        throw (New-HrmError "В новом релизе нет release-manifest.json." "Используйте официальный установочный комплект." 5)
    }
    $problems = @(Test-HrmReleaseIntegrity -ReleaseDir $NewReleaseDir)
    if ($problems.Count -gt 0) {
        throw (New-HrmError ("Новый релиз не прошёл проверку целостности: " + ($problems -join "; ")) "Скачайте установщик заново (источник — официальный релиз)." 4)
    }
    if ($newManifest.releaseSha -eq $config.releaseSha) {
        Write-HrmLog -StateRoot $StateRoot -Message "обновление пропущено: приложение уже на версии $($newManifest.version)"
        return [pscustomobject]@{ Ok = $true; Skipped = $true }
    }

    $lockPath = Acquire-HrmUpdateLock -StateRoot $StateRoot
    $stage = "build"
    $resumedStage = Get-HrmUpdateStage -StateRoot $StateRoot
    $stageOrder = @("build", "backup", "swap", "stop", "migrate", "start", "verify")
    if ($resumedStage -and ($stageOrder -contains $resumedStage)) { $stage = $resumedStage }
    try {
        if ($stage -eq "build") {
            Write-HrmProgress -StateRoot $StateRoot -Phase "update-build" -Detail "Собираем новые образы (текущая версия продолжает работать)"
            $build = Invoke-HrmCompose -AppDir $NewReleaseDir -StateRoot $StateRoot -ComposeArgs @("build")
            if ($build.ExitCode -ne 0) {
                throw (New-HrmError "Сборка новых образов не удалась — приложение остаётся на текущей версии." "Повторите безопасный шаг или откройте диагностику." 4)
            }
            $stage = "backup"; Write-HrmUpdateStage -StateRoot $StateRoot -Stage $stage
        }

        if ($stage -eq "backup") {
            Write-HrmProgress -StateRoot $StateRoot -Phase "update-backup" -Detail "Резервная копия ДО любых изменений структуры"
            if (-not (Invoke-HrmBackupOnce -AppDir $oldReleaseDir -StateRoot $StateRoot)) {
                throw (New-HrmError "Резервное копирование перед обновлением не подтверждено — обновление не начато, данные не изменены." "Проверьте диск и контейнеры, повторите безопасный шаг." 4)
            }
            $stage = "swap"; Write-HrmUpdateStage -StateRoot $StateRoot -Stage $stage
        }

        if ($stage -eq "swap") {
            Write-HrmProgress -StateRoot $StateRoot -Phase "update-code" -Detail "Устанавливаем файлы новой версии (данные не трогаем)"
            $prevDir = Join-Path $appRoot "previous"
            if (Test-Path $prevDir) { Remove-Item $prevDir -Recurse -Force }
            New-Item -ItemType Directory -Path $prevDir -Force | Out-Null
            Get-ChildItem $appRoot -Force | Where-Object { $_.Name -notin @("previous", "release-staging", "releases") } | ForEach-Object {
                Move-Item $_.FullName -Destination $prevDir -Force
            }
            $staging = Join-Path $appRoot "release-staging"
            if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
            New-Item -ItemType Directory -Path $staging -Force | Out-Null
            Copy-Item -Path (Join-Path $NewReleaseDir "*") -Destination $staging -Recurse -Force
            Get-ChildItem $staging | ForEach-Object {
                Move-Item $_.FullName -Destination (Join-Path $appRoot $_.Name) -Force
            }
            Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
            $config | Add-Member -NotePropertyName releaseDir -NotePropertyValue $appRoot -Force
            $config | Add-Member -NotePropertyName releaseVersion -NotePropertyValue [string]$newManifest.version -Force
            $config | Add-Member -NotePropertyName releaseSha -NotePropertyValue [string]$newManifest.releaseSha -Force
            Set-HrmConfig -StateRoot $StateRoot -Config $config
            Write-HrmPilotEnv -StateRoot $StateRoot -Secrets (Ensure-HrmSecrets -StateRoot $StateRoot) -Config $config
            $stage = "stop"; Write-HrmUpdateStage -StateRoot $StateRoot -Stage $stage
        }

        if ($stage -eq "stop") {
            Write-HrmProgress -StateRoot $StateRoot -Phase "update-stop" -Detail "Останавливаем текущую версию (тома с данными не затрагиваются)"
            $down = Invoke-HrmCompose -AppDir $appRoot -StateRoot $StateRoot -ComposeArgs @("stop")
            if ($down.ExitCode -ne 0) {
                throw (New-HrmError "Не удалось остановить текущую версию — миграции не запускались, данные целы." "Повторите безопасный шаг." 4)
            }
            $stage = "migrate"; Write-HrmUpdateStage -StateRoot $StateRoot -Stage $stage
        }

        if ($stage -eq "migrate") {
            Write-HrmProgress -StateRoot $StateRoot -Phase "update-migrate" -Detail "Применяем миграции (только вперёд, под блокировкой БД)"
            if (-not (Invoke-HrmMigrate -AppDir $appRoot -StateRoot $StateRoot)) {
                throw (New-HrmError "Миграции не применились — новая версия не запущена; выполняется откат кода на предыдущую версию (данные целы)." "" 4)
            }
            $stage = "start"; Write-HrmUpdateStage -StateRoot $StateRoot -Stage $stage
        }

        if ($stage -eq "start") {
            Write-HrmProgress -StateRoot $StateRoot -Phase "update-start" -Detail "Запускаем новую версию"
            $up = Invoke-HrmCompose -AppDir $appRoot -StateRoot $StateRoot -ComposeArgs @("up", "-d")
            if ($up.ExitCode -ne 0) { throw (New-HrmError "Не удалось запустить новую версию — выполняется откат кода." "" 4) }
            $stage = "verify"; Write-HrmUpdateStage -StateRoot $StateRoot -Stage $stage
        }

        Write-HrmProgress -StateRoot $StateRoot -Phase "update-verify" -Detail "Проверяем новую версию перед подтверждением"
        if (-not (Wait-HrmReady -StateRoot $StateRoot -WaitSeconds 90)) {
            throw (New-HrmError "Новая версия не ответила на проверку готовности — выполняется откат кода (данные не тронуты)." "" 4)
        }
        $ops = Get-HrmOpsStatus -StateRoot $StateRoot
        if (-not $ops -or $ops.status -ne "ok" -or $ops.release_sha -ne $newManifest.releaseSha) {
            throw (New-HrmError "Post-проверка не подтвердила новую версию (health/миграции/версия) — выполняется откат кода." "" 4)
        }
        Write-HrmUpdateStage -StateRoot $StateRoot -Stage ""
        Add-HrmHistoryEntry -StateRoot $StateRoot -Entry @{
            action = "update"; result = "ok"; from = $oldVersion; to = [string]$newManifest.version
        }
        Write-HrmProgress -StateRoot $StateRoot -Phase "done" -Detail "Обновление завершено"
        Write-HrmLog -StateRoot $StateRoot -Message "обновление успешно: $oldVersion -> $($newManifest.version)"
        return [pscustomobject]@{ Ok = $true; Version = [string]$newManifest.version }
    } catch {
        $err = $_
        Add-HrmHistoryEntry -StateRoot $StateRoot -Entry @{
            action = "update"; result = "failed"; stage = $stage
            error  = (Protect-HrmText -Text ([string]$err.Exception.Message))
        }
        # Откат кода имеет смысл, когда файлы уже заменены (swap и далее).
        if ([Array]::IndexOf($stageOrder, $stage) -ge [Array]::IndexOf($stageOrder, "swap")) {
            try {
                Invoke-HrmRollbackToPrevious -StateRoot $StateRoot -OldConfig $oldConfigSnapshot
                # Сбой оставил stage-точку: повтор безопасного шага продолчит
                # с помеченного этапа; resume-файл говорит об откате честно.
                Write-HrmJson (Join-Path $StateRoot "resume.json") ([ordered]@{
                    stage = "update-failed"
                    note  = "обновление отменено, приложение возвращено на предыдущую версию; миграции не откатываются"
                })
                Write-HrmProgress -StateRoot $StateRoot -Phase "update-rollback" -Detail "Код возвращён на предыдущую версию; данные не изменены"
            } catch {
                Write-HrmLog -StateRoot $StateRoot -Message "откат кода не удался; resume-точка сохранена — повторите безопасный шаг" -Level error
            }
        }
        throw $err
    } finally {
        Release-HrmUpdateLock -LockPath $lockPath
    }
}

function script:Invoke-HrmRollbackToPrevious {
    <# Откат = код/контейнеры на предыдущий релиз. Миграции НЕ откатываются
       (forward-only): если миграция уже применена, структура остаётся новой —
       предыдущий образ совместим с ней в пилотных релизах, иначе — повторное
       обновление. Предыдущий комплект файлов лежит в appRoot\previous. #>
    param(
        [Parameter(Mandatory)][string]$StateRoot,
        $OldConfig
    )
    $appRoot = Get-HrmAppRoot
    $prevDir = Join-Path $appRoot "previous"
    if (-not (Test-Path (Join-Path $prevDir "release-manifest.json"))) {
        throw (New-HrmError "Откат невозможен: предыдущий комплект файлов не найден. Приложение остаётся в текущем состоянии, данные не изменены." "Восстановите работоспособность повторным безопасным шагом или повторной установкой релиза." 4)
    }
    Write-HrmProgress -StateRoot $StateRoot -Phase "update-rollback" -Detail "Откатываем код на предыдущую версию (данные и структура БД не трогаем)"
    # Текущие (сбойные) файлы выбрасываем, прежние возвращаем.
    Get-ChildItem $appRoot -Force | Where-Object { $_.Name -ne "previous" -and $_.Name -ne "releases" } | ForEach-Object {
        Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
    Get-ChildItem $prevDir -Force | ForEach-Object {
        Move-Item $_.FullName -Destination (Join-Path $appRoot $_.Name) -Force
    }
    Remove-Item $prevDir -Recurse -Force -ErrorAction SilentlyContinue
    if ($OldConfig) { Set-HrmConfig -StateRoot $StateRoot -Config $OldConfig }
    Invoke-HrmCompose -AppDir $appRoot -StateRoot $StateRoot -ComposeArgs @("up", "-d") | Out-Null
    Wait-HrmReady -StateRoot $StateRoot -WaitSeconds 60 | Out-Null
    # Сбойный цикл обновления полностью разобран: начинаем с чистого листа.
    Write-HrmUpdateStage -StateRoot $StateRoot -Stage ""
    Write-HrmLog -StateRoot $StateRoot -Message "откат кода выполнен; миграции не откатывались (политика forward-only)"
    return $true
}
