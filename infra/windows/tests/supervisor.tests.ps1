# Поведенческие тесты управляющего компонента (supervisor) и значка в трее:
# состояния «Запускается / Готово / Ошибка», меню, единственность, автозапуск,
# отсутствие дублей операций. Реальная машина не затрагивается.

param([string]$HarnessPath = $PSScriptRoot)

. (Join-Path $HarnessPath "test-harness.ps1")

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path

Write-Host "== Supervisor и значок в трее =="

function New-HrmSupervisorTestContext {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows = $true; powershell = $true; docker = $true; daemon = $true; compose = "v2.29.7 (mock)"; port = $true; state_dir = $true; space = $true; config = $true }
    Set-HrmDockerOverride @{ desktop = "engine_ready"; engine = $true; wsl = "ok"; virtualization = "enabled"; free_mb = 20480; port_free = $true; admin = $false; reboot = $false }
    # Уникальное имя мьютекса на контекст: тест единственности не должен
    # зависеть от живых процессов машины (машино-широкий Local\-мьютекс
    # могут держать установки владельца сутками — провал supervisor.tests.ps1:123).
    $env:HRM_SUPERVISOR_MUTEX = "Local\HRManagerPilotSupervisorTest-" + [guid]::NewGuid().ToString("N")
    return $world
}

Test-Case "состояния supervisor'а читаются как «Запускается», «Готово», «Ошибка»" {
    New-HrmSupervisorTestContext
    $state = Get-HrmTestStateDir
    Set-HrmSupervisorState -StateDir $state -State "starting" -Message "Запускаем HR Manager…"
    $snapshot = Get-HrmSupervisorState -StateDir $state
    Assert-HrmEqual "starting" $snapshot.state "состояние не сохранилось"
    Assert-HrmEqual "Запускается" (Get-HrmSupervisorStatusText "starting") "нет понятного статуса"
    Assert-HrmEqual "Готово" (Get-HrmSupervisorStatusText "ready") "нет понятного статуса"
    Assert-HrmEqual "Ошибка" (Get-HrmSupervisorStatusText "error") "нет понятного статуса"
    Set-HrmSupervisorState -StateDir $state -State "ready" -Message "HR Manager готов."
    $snapshot = Get-HrmSupervisorState -StateDir $state
    Assert-HrmEqual "ready" $snapshot.state "состояние не переключилось"
    Assert-HrmTrue (Test-Path (Get-HrmSupervisorStateFile $state)) "нет файла состояния"
}

Test-Case "состояние supervisor'а не содержит секретов (редакция)" {
    New-HrmSupervisorTestContext
    $state = Get-HrmTestStateDir
    $secret = Get-HrmSecret $state "HRM_SIGNING_KEY"
    Set-HrmSupervisorState -StateDir $state -State "error" -Message ("Сбой: " + $secret)
    $file = Get-HrmSupervisorStateFile $state
    $text = Get-Content -Path $file -Raw -Encoding UTF8
    Assert-HrmNotContains $text $secret "секрет попал в состояние supervisor'а"
    # ConvertTo-Json экранирует < > как \u003c/\u003e: читатель JSON видит
    # <redacted>, поэтому проверяем обе формы.
    Assert-HrmContainsRedacted $text "секрет должен быть отредактирован"
}

Test-Case "меню трея содержит все обязательные пункты" {
    New-HrmSupervisorTestContext
    $model = Get-HrmTrayMenuModel -State "ready" -Busy $false -AutostartEnabled $true -Message "Приложение готово."
    Assert-HrmTrue (Test-HrmTrayMenuModel $model) "меню не проходит контракт"
    $texts = ($model | ForEach-Object { $_.text }) -join "|"
    foreach ($required in @("Открыть HR Manager", "Проверить состояние", "Перезапустить приложение",
            "Создать отчёт для поддержки", "Остановить приложение", "Выйти")) {
        Assert-HrmContains $texts $required ("нет пункта меню: " + $required)
    }
    Assert-HrmContains $texts "Состояние: Готово" "состояние не видно в меню"
    $open = @($model | Where-Object { $_.action -eq "open" })[0]
    Assert-HrmTrue $open.enabled "«Открыть» должно быть доступно в состоянии ready"
    $firstRunModel = Get-HrmTrayMenuModel -State "error" -Busy $false -AutostartEnabled $false -Message "Docker Desktop не установлен." -OpenAvailable $true
    $firstRunOpen = @($firstRunModel | Where-Object { $_.action -eq "open" })[0]
    Assert-HrmTrue $firstRunOpen.enabled "после отказа первой установки пункт «Открыть» должен позволять повторить настройку"
    $blockedFirstRun = Get-HrmTrayMenuModel -State "error" -Busy $true -OpenAvailable $true
    $blockedOpen = @($blockedFirstRun | Where-Object { $_.action -eq "open" })[0]
    Assert-HrmFalse $blockedOpen.enabled "повторная первичная настройка не должна обходить блокировку движка"
}

Test-Case "E.5: после отказа Docker из трея доступен повтор первичной настройки с браузером" {
    New-HrmSupervisorTestContext
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $infra = Join-Path $install "infra"
    $windowsDir = Join-Path $infra "windows"
    New-Item -ItemType Directory -Path $windowsDir -Force | Out-Null
    Set-Content -Path (Join-Path $infra "compose.pilot.yml") -Value "services: {}" -Encoding UTF8
    Set-Content -Path (Join-Path $windowsDir "hr-manager.ps1") -Value "# test stub" -Encoding ASCII
    Set-HrmSetupMarker -StateDir $state -Status "failed" -Message "Настройка не завершена; проверьте Docker Desktop." | Out-Null
    $dockerReason = "Docker Desktop отсутствует; Docker Engine не готов. Подробности в журнале."
    Set-HrmSupervisorState -StateDir $state -State "error" -Message $dockerReason

    $plan = Get-HrmTrayCurrentPlan -StateDir $state -InstallDir $install
    Assert-HrmEqual "install_failed" ([string]$plan.mode) "первый отказ не распознан треем"
    Assert-HrmEqual $dockerReason ([string]$plan.message) "fallback Setup скрыл точную причину Docker из состояния движка"
    Assert-HrmTrue ([bool]$plan.open_available) "после отказа Setup не оставил в трее путь продолжения"
    $tick = Invoke-HrmTrayRefresh -InstallDir $install -StateDir $state
    Assert-HrmTrue ([bool]$tick.open_available) "обновление значка потеряло доступность повторной настройки"
    $model = Get-HrmTrayMenuModel -State $tick.state -Busy $tick.busy -OpenAvailable $tick.open_available
    $open = @($model | Where-Object { $_.action -eq "open" })[0]
    Assert-HrmTrue ([bool]$open.enabled) "пункт «Открыть HR Manager» недоступен после отказа Docker"

    $global:HRM_TrayProcessArguments = @()
    Set-HrmProcessLaunchMock {
        param($FilePath, $Arguments, $Mode, $LogFile)
        $global:HRM_TrayProcessArguments = @($Arguments)
        return [pscustomobject]@{ Id = 4321 }
    }
    $result = Invoke-HrmTrayMenuAction -ActionId "open" -InstallDir $install -StateDir $state
    Assert-HrmContains $result "Действие запущено: open" "нажатие в трее не запустило повтор настройки"
    Assert-HrmTrue ($global:HRM_TrayProcessArguments -contains "-NonInteractive") "движок трея должен остаться неинтерактивным"
    Assert-HrmTrue ($global:HRM_TrayProcessArguments -contains "-OpenBrowser") "неинтерактивный движок трея не получил явное разрешение открыть браузер"
    Assert-HrmEqual "-OpenBrowser" (Get-HrmTrayEngineExtraArguments -Action "resume" -StateDir $state) "первичное продолжение из трея должно открыть страницу настройки"
    Exit-HrmActionLock -StateDir $state
}

Test-Case "во время операции пункты меню недоступны (нет дублей действий)" {
    New-HrmSupervisorTestContext
    $model = Get-HrmTrayMenuModel -State "starting" -Busy $true -AutostartEnabled $false
    foreach ($item in @($model | Where-Object { $_.action -and $_.action -ne "exit" })) {
        Assert-HrmFalse $item.enabled ("пункт должен быть недоступен во время операции: " + $item.text)
    }
    $exit = @($model | Where-Object { $_.action -eq "exit" })[0]
    Assert-HrmTrue $exit.enabled "«Выйти» должно быть доступно всегда"
}

Test-Case "иконка трея отражает состояние" {
    New-HrmSupervisorTestContext
    Assert-HrmEqual "ready" (Get-HrmTrayIconKind -State "ready" -Busy $false) "ready"
    Assert-HrmEqual "starting" (Get-HrmTrayIconKind -State "starting" -Busy $false) "starting"
    Assert-HrmEqual "error" (Get-HrmTrayIconKind -State "error" -Busy $false) "error"
    Assert-HrmEqual "stopped" (Get-HrmTrayIconKind -State "stopped" -Busy $false) "stopped"
    Assert-HrmEqual "starting" (Get-HrmTrayIconKind -State "ready" -Busy $true) "занятость приоритетнее"
    $tooltip = Get-HrmTrayTooltip -State "ready" -Message "Приложение готово."
    Assert-HrmContains $tooltip "Готово" "подсказка не описывает состояние"
}

Test-Case "закрытие значка предупреждает о последствиях (данные и контейнеры сохраняются)" {
    New-HrmSupervisorTestContext
    $prompt = Get-HrmTrayExitPrompt
    Assert-HrmContains $prompt.text "данные останутся на месте" "нет предупреждения о данных"
    Assert-HrmContains $prompt.text "Docker" "нет предупреждения о Docker"
    Assert-HrmContains $prompt.text "Отмена" "нет варианта отмены"
    $stop = Get-HrmTrayStopPrompt
    Assert-HrmContains $stop.text "СОХРАНЯЮТСЯ" "остановка должна явно обещать сохранение данных"
}

Test-Case "уведомления трея понятны и не содержат технических команд" {
    New-HrmSupervisorTestContext
    $balloon = Get-HrmTrayBalloon -State "error" -Message "Не удалось запустить приложение."
    Assert-HrmContains $balloon.text "Создать отчёт для поддержки" "нет кнопки-подсказки"
    $starting = Get-HrmTrayBalloon -State "starting"
    Assert-HrmContains $starting.text "запускается" "нет пояснения про запуск"
    foreach ($forbidden in @("docker compose", "PowerShell", "контейнер")) {
        Assert-HrmNotContains ($balloon.text + $starting.text) $forbidden ("техническая формулировка: " + $forbidden)
    }
}

Test-Case "две операции движка одновременно невозможны (файл-блокировка)" {
    New-HrmSupervisorTestContext
    $state = Get-HrmTestStateDir
    $first = Enter-HrmActionLock -StateDir $state -Action "restart"
    Assert-HrmTrue $first.acquired "первая операция должна получить блокировку"
    $second = Enter-HrmActionLock -StateDir $state -Action "support-bundle"
    Assert-HrmFalse $second.acquired "вторая операция не должна стартовать параллельно"
    Assert-HrmContains $second.message "уже выполняется" "нужно понятное сообщение"
    Exit-HrmActionLock -StateDir $state
    $third = Enter-HrmActionLock -StateDir $state -Action "support-bundle"
    Assert-HrmTrue $third.acquired "после завершения блокировка должна освободиться"
    Exit-HrmActionLock -StateDir $state
    Assert-HrmFalse (Test-HrmTrayActionRunning -StateDir $state) "завершённая операция не должна считаться активной"
}

Test-Case "повторный запуск supervisor'а не создаёт второй процесс (единственность)" {
    New-HrmSupervisorTestContext
    $state = Get-HrmTestStateDir
    Set-HrmSupervisorState -StateDir $state -State "ready" -Message "HR Manager готов."
    $lock = Enter-HrmSupervisorLock
    Assert-HrmTrue $lock.acquired "первый supervisor должен получить блокировку"
    # Второй процесс получил бы $false от WaitOne(0); в одном процессе проверяем
    # контракт функции и запись pid.
    $snapshot = Get-HrmSupervisorState -StateDir $state
    Assert-HrmEqual $PID $snapshot.pid "состояние должно содержать pid supervisor'а"
    Exit-HrmSupervisorLock $lock
}

Test-Case "мьютекс переопределяется через env: единственность не зависит от живых процессов машины" {
    # Регрессия R16 (T5): Enter-HrmSupervisorLock брал машино-широкий
    # Local\HRManagerPilotSupervisor, и первый захват в тесте падал, когда
    # мьютекс держал живой процесс от установки. Имя переопределяется через
    # HRM_SUPERVISOR_MUTEX — контекст задаёт уникальное имя.
    New-HrmSupervisorTestContext
    # Чистая функция выбора имени: env переопределяет, иначе — умолчание.
    $env:HRM_SUPERVISOR_MUTEX = "Local\HRManagerPilotSupervisorTest-probe"
    Assert-HrmEqual "Local\HRManagerPilotSupervisorTest-probe" (Get-HrmSupervisorMutexName) "env не переопределил имя мьютекса"
    Remove-Item Env:HRM_SUPERVISOR_MUTEX -ErrorAction SilentlyContinue
    Assert-HrmEqual "Local\HRManagerPilotSupervisor" (Get-HrmSupervisorMutexName) "имя по умолчанию потеряно"
    # «Занятый машинный мьютекс» держим ОТДЕЛЬНЫМ ПРОЦЕССОМ (фоновая job):
    # .NET Mutex ре-ентерабелен для своего потока, поэтому занять его в том же
    # потоке и пронаблюдать отказ невозможно (а Start-ThreadJob в PowerShell
    # 5.1 не входит в поставку). Local\-мьютекс общий для процессов сессии.
    $holder = Start-Job -ScriptBlock {
        $m = New-Object System.Threading.Mutex($false, "Local\HRManagerPilotSupervisor")
        $null = $m.WaitOne(0)
        Start-Sleep -Seconds 60
    }
    try {
        # Ждём, пока процесс job действительно захватит мьютекс (зонд свободным захватом).
        $heldByOther = $false
        for ($i = 0; $i -lt 50 -and -not $heldByOther; $i++) {
            $probe = New-Object System.Threading.Mutex($false, "Local\HRManagerPilotSupervisor")
            $got = $false
            try { $got = $probe.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $got = $true }
            if ($got) { try { $probe.ReleaseMutex() } catch { } } else { $heldByOther = $true }
            $probe.Dispose()
            if (-not $heldByOther) { Start-Sleep -Milliseconds 200 }
        }
        Assert-HrmTrue $heldByOther "не удалось дождаться занятого машинного мьютекса"
        # Без переопределения занятый машинный мьютекс даёт отказ (контракт единственности).
        $lock2 = Enter-HrmSupervisorLock
        Assert-HrmFalse $lock2.acquired "занятый машинный мьютекс должен давать отказ без переопределения"
        Exit-HrmSupervisorLock $lock2
        # С переопределением (уникальное имя контекста): первый захват успешен
        # даже когда машинный мьютекс занят живым потоком.
        $env:HRM_SUPERVISOR_MUTEX = "Local\HRManagerPilotSupervisorTest-" + [guid]::NewGuid().ToString("N")
        $lock = Enter-HrmSupervisorLock
        Assert-HrmTrue $lock.acquired "уникальный мьютекс контекста должен быть свободен даже при занятом машинном"
        Exit-HrmSupervisorLock $lock
    }
    finally {
        Remove-Job $holder -Force -ErrorAction SilentlyContinue
    }
}

Test-Case "автозапуск включается и выключается, старый ярлык -Action start удаляется" {
    New-HrmSupervisorTestContext
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $autostartDir = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM-autostart-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Path $autostartDir -Force | Out-Null
    $env:HRM_AUTOSTART_DIR = $autostartDir
    $env:HRM_AUTOSTART_MOCK = "1"
    # Устанавливаем «компонент», на который указывает ярлык автозапуска.
    $trayDir = Join-Path $install "infra\windows"
    New-Item -ItemType Directory -Path $trayDir -Force | Out-Null
    Set-Content -Path (Join-Path $trayDir "hrm-tray.ps1") -Value "# stub" -Encoding ASCII
    # Устаревший автозапуск старого образца.
    Set-Content -Path (Join-Path $autostartDir "HR Manager.lnk") -Value "legacy" -Encoding ASCII

    $enabled = Enable-HrmAutostart -InstallDir $install -StateDir $state
    Assert-HrmTrue $enabled.enabled "автозапуск должен включиться"
    Assert-HrmFalse (Test-Path (Join-Path $autostartDir "HR Manager.lnk")) "старый ярлык автозапуска должен быть удалён"
    $shortcut = Join-Path $autostartDir "HR Manager (трей).lnk"
    Assert-HrmTrue (Test-Path $shortcut) "нет ярлыка автозапуска supervisor'а"
    $content = Get-Content -Path $shortcut -Raw -Encoding UTF8
    Assert-HrmContains $content "hrm-tray.ps1" "ярлык должен запускать supervisor"
    Assert-HrmNotContains $content "-Action start" "ярлык не должен запускать консольный start"

    $disabled = Disable-HrmAutostart -StateDir $state
    Assert-HrmFalse $disabled.enabled "автозапуск должен выключаться"
    Assert-HrmFalse (Test-Path $shortcut) "ярлык автозапуска должен быть удалён"
    Remove-Item $autostartDir -Recurse -Force -ErrorAction SilentlyContinue
}

Test-Case "полный цикл supervisor'а: среда → стек → готовность → состояние ready" {
    $world = New-HrmSupervisorTestContext
    $install = Get-HrmTestInstallDir
    $state = Get-HrmTestStateDir
    $source = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM-src-sup-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Path $source -Force | Out-Null
    New-HrmFakeSnapshot -Root $source -ReleaseSha ("c" * 40)
    Install-HrmApp -SourceDir $source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    $result = Invoke-HrmSupervisorCycle -InstallDir $install -StateDir $state
    Assert-HrmTrue $result.ok "цикл supervisor'а должен завершиться успешно"
    $snapshot = Get-HrmSupervisorState -StateDir $state
    Assert-HrmEqual "ready" $snapshot.state "состояние должно быть ready"
    Assert-HrmFalse $snapshot.busy "операция должна быть завершена"
    Remove-Item $source -Recurse -Force -ErrorAction SilentlyContinue
}

Test-Case "ошибка среды: состояние «Ошибка» и понятное сообщение вместо падения" {
    New-HrmSupervisorTestContext
    $install = Get-HrmTestInstallDir
    $state = Get-HrmTestStateDir
    $source = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM-src-err-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Path $source -Force | Out-Null
    New-HrmFakeSnapshot -Root $source -ReleaseSha ("d" * 40)
    Install-HrmApp -SourceDir $source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    # Docker перестал отвечать.
    Set-HrmDockerOverride @{ desktop = "installed_stopped"; desktop_path = ""; engine = $false; wsl = "ok"; virtualization = "enabled" }
    $result = Invoke-HrmSupervisorCycle -InstallDir $install -StateDir $state -TimeoutSeconds 2
    Assert-HrmFalse $result.ok "цикл не должен считаться успешным"
    Assert-HrmEqual "error" (Get-HrmSupervisorState -StateDir $state).state "состояние должно быть error"
    Assert-HrmTrue (@("engine_timeout", "engine_not_ready") -contains $result.state) "неожиданный код: $($result.state)"
    Remove-Item $source -Recurse -Force -ErrorAction SilentlyContinue
}

Test-Case "незапущенный supervisor: состояние unknown, а не выдуманное «готово»" {
    Initialize-HrmTestEngine
    $snapshot = Get-HrmSupervisorState -StateDir (Get-HrmTestStateDir)
    Assert-HrmEqual "unknown" $snapshot.state "неизвестное состояние должно быть честным"
    Assert-HrmEqual "Проверяем состояние" (Get-HrmSupervisorStatusText $snapshot.state) "нет понятного статуса unknown"
}

Write-Host "== Ревизия стабильности установщика (12.10.2026): единственность цикла, блокировка, значок =="

function Get-HrmOtherLivePid {
    # Живой pid ДРУГОГО процесса (не тестового). По нему проверяется «операция
    # движка идёт» без запуска настоящих процессов: блокировка действия и
    # признак busy в supervisor.json опираются на живость pid.
    $others = @(Get-Process | Where-Object { $_.Id -gt 4 -and $_.Id -ne $PID } | Select-Object -First 1 -ExpandProperty Id)
    if ($others.Count -eq 0) { return 0 }
    return [int]$others[0]
}

function Set-HrmTestActionLock {
    # Запись блокировки действия от имени другого процесса (для тестов).
    param([string]$StateDir, [string]$Action, [int]$ProcessId, [int]$AgeMinutes = 0)
    Set-HrmJsonFile $StateDir "action.lock" ([ordered]@{
            action = $Action
            pid = $ProcessId
            started_at = (Get-Date).AddMinutes(-$AgeMinutes).ToString("o")
        })
}

Test-Case "D2: второй параллельный цикл движка не начинается (мьютекс цикла) и не идёт в Docker" {
    $world = New-HrmSupervisorTestContext
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    # Уникальное имя на тест: как и для HRM_SUPERVISOR_MUTEX, проверка не должна
    # зависеть от живых процессов машины.
    $cycleMutex = "Local\HRManagerPilotEngineCycleTest-" + [guid]::NewGuid().ToString("N")
    $env:HRM_ENGINE_CYCLE_MUTEX = $cycleMutex
    # Мьютекс .NET ре-ентерабелен для своего потока, поэтому «чужой» владелец —
    # отдельный процесс (фоновая job), как в тесте единственности supervisor'а.
    $holder = Start-Job -ScriptBlock {
        param($name)
        $m = New-Object System.Threading.Mutex($false, $name)
        $null = $m.WaitOne(0)
        Start-Sleep -Seconds 40
    } -ArgumentList $cycleMutex
    try {
        $heldByOther = $false
        for ($i = 0; $i -lt 50 -and -not $heldByOther; $i++) {
            $probe = New-Object System.Threading.Mutex($false, $cycleMutex)
            $got = $false
            try { $got = $probe.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $got = $true }
            if ($got) { try { $probe.ReleaseMutex() } catch { } } else { $heldByOther = $true }
            $probe.Dispose()
            if (-not $heldByOther) { Start-Sleep -Milliseconds 200 }
        }
        Assert-HrmTrue $heldByOther "не удалось дождаться занятого мьютекса цикла движка"
        Set-HrmSupervisorState -StateDir $state -State "starting" -Message "Запускаем HR Manager…" -Busy $true
        $callsBefore = @($world.Calls).Count
        $result = Invoke-HrmSupervisorCycle -InstallDir $install -StateDir $state
        Assert-HrmFalse $result.ok "второй цикл не должен считаться успешным"
        Assert-HrmEqual "busy" ([string]$result.state) "второй цикл обязан честно сообщить «уже выполняется»"
        Assert-HrmTrue ([bool]$result.already_running) "нет признака «операция уже идёт»"
        Assert-HrmContains ([string]$result.message) "уже выполняется" "сообщение не объясняет отказ"
        Assert-HrmEqual $callsBefore (@($world.Calls).Count) "второй цикл сходил в Docker: параллельная compose-операция"
        # Состояние настоящей операции не перезаписано отказом второй.
        $snapshot = Get-HrmSupervisorState -StateDir $state
        Assert-HrmEqual "starting" ([string]$snapshot.state) "отказ второго цикла перезаписал состояние первого"
    }
    finally {
        Stop-Job $holder -ErrorAction SilentlyContinue
        Remove-Job $holder -Force -ErrorAction SilentlyContinue
    }
}

Test-Case "D2: цикл не начинается, пока идёт операция движка (живая блокировка действия)" {
    $world = New-HrmSupervisorTestContext
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $otherPid = Get-HrmOtherLivePid
    Assert-HrmTrue ($otherPid -gt 0) "на машине нет другого живого процесса для проверки"
    Set-HrmTestActionLock -StateDir $state -Action "install" -ProcessId $otherPid
    $callsBefore = @($world.Calls).Count
    $result = Invoke-HrmSupervisorCycle -InstallDir $install -StateDir $state
    Assert-HrmEqual "busy" ([string]$result.state) "цикл стартовал поверх идущей установки"
    Assert-HrmTrue ([bool]$result.already_running) "нет признака «операция уже идёт»"
    Assert-HrmContains ([string]$result.message) "уже выполняется" "сообщение не объясняет отказ"
    Assert-HrmEqual $callsBefore (@($world.Calls).Count) "цикл сходил в Docker при идущей установке"
    # Чужая блокировка не снята: она принадлежит живому процессу.
    Assert-HrmTrue (Test-Path (Get-HrmActionLockFile $state)) "чужая блокировка действия снята"
    Exit-HrmActionLock -StateDir $state
}

Test-Case "D2: значок не порождает второй движок, пока операция идёт (план «busy»)" {
    New-HrmSupervisorTestContext
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Set-HrmInstallRecord $state @{ release_sha = ("1" * 40); port = 8080 }
    $otherPid = Get-HrmOtherLivePid
    Set-HrmTestActionLock -StateDir $state -Action "supervise" -ProcessId $otherPid
    $plan = Get-HrmTrayCurrentPlan -StateDir $state -InstallDir $install
    Assert-HrmEqual "busy" ([string]$plan.mode) "идущая операция не распознана значком"
    Assert-HrmFalse ([bool]$plan.start_engine) "значок породил бы второй скрытый процесс движка"
    Assert-HrmEqual "starting" ([string]$plan.state) "в ожидании операции значок показывает «Запускается»"
    Assert-HrmContains ([string]$plan.message) "уже выполняется" "сообщение значка не объясняет ожидание"
    # Тот же вывод по supervisor.json: busy + живой чужой pid.
    Exit-HrmActionLock -StateDir $state
    Set-HrmJsonFile $state "supervisor.json" ([ordered]@{
            state = "starting"; message = "Ожидаем запуск службы контейнеров…"; busy = $true
            pid = $otherPid; failure_count = 0; updated_at = (Get-Date).ToString("o")
        })
    $plan2 = Get-HrmTrayCurrentPlan -StateDir $state -InstallDir $install
    Assert-HrmEqual "busy" ([string]$plan2.mode) "busy с живым чужим pid не распознан"
    Assert-HrmFalse ([bool]$plan2.start_engine) "значок породил бы второй движок поверх работающего"
}

Test-Case "D3: блокировка действия не «протухает», пока процесс операции жив" {
    New-HrmSupervisorTestContext
    $state = Get-HrmTestStateDir
    $otherPid = Get-HrmOtherLivePid
    # Свежая блокировка живого процесса: отказ с прежним понятным текстом и БЕЗ
    # признака «зависла» (порог затянувшейся операции — 60 минут).
    Set-HrmTestActionLock -StateDir $state -Action "update" -ProcessId $otherPid -AgeMinutes 5
    $fresh = Enter-HrmActionLock -StateDir $state -Action "restart"
    Assert-HrmFalse ([bool]$fresh.acquired) "две операции стартовали одновременно"
    Assert-HrmContains ([string]$fresh.message) "уже выполняется" "сообщение не объясняет отказ"
    Assert-HrmFalse ([bool]$fresh.stalled) "свежая операция помечена зависшей"
    Exit-HrmActionLock -StateDir $state
    # Операция идёт уже 2 часа: прежний код считал блокировку протухшей (60 мин)
    # и запускал вторую операцию поверх живой — второй параллельный compose.
    Set-HrmTestActionLock -StateDir $state -Action "update" -ProcessId $otherPid -AgeMinutes 120
    $lock = Enter-HrmActionLock -StateDir $state -Action "restart"
    Assert-HrmFalse ([bool]$lock.acquired) "блокировка «протухла» по возрасту при живом процессе операции"
    Assert-HrmContains ([string]$lock.message) "не завершилась" "в отказе нет объяснения затянувшейся операции"
    Assert-HrmContains ([string]$lock.message) "НЕ запускается" "в отказе не сказано, что вторая операция не начинается"
    Assert-HrmContains ([string]$lock.message) "диспетчере задач" "в отказе нет действия для человека"
    Assert-HrmTrue (Test-Path (Get-HrmActionLockFile $state)) "блокировка снята при живом процессе операции"
    Assert-HrmTrue ([bool]$lock.stalled) "затянувшаяся операция не помечена как зависшая"
    Assert-HrmEqual $otherPid ([int]$lock.pid) "в отказе нет pid зависшей операции"
    # Затянувшаяся операция видна и в состоянии: «Ошибка» + код + действие.
    $supervisor = Get-HrmSupervisorState -StateDir $state
    Assert-HrmEqual "error" ([string]$supervisor.state) "зависшая операция не показана как ошибка"
    Assert-HrmContains ([string]$supervisor.message) "не завершилась" "в состоянии нет объяснения"
    Assert-HrmContains ([string]$supervisor.message) "диспетчере задач" "в состоянии нет действия для человека"
    $raw = Get-HrmJsonFile (Get-HrmSupervisorStateFile $state)
    Assert-HrmEqual "action_stalled" ([string]$raw.code) "в состоянии нет кода причины"
    Assert-HrmEqual $otherPid ([int]$raw.lock_pid) "в состоянии нет pid зависшей операции"
    # Мёртвый процесс операции блокировку освобождает.
    Exit-HrmActionLock -StateDir $state
    Set-HrmTestActionLock -StateDir $state -Action "update" -ProcessId 999999 -AgeMinutes 120
    $afterDead = Enter-HrmActionLock -StateDir $state -Action "restart"
    Assert-HrmTrue ([bool]$afterDead.acquired) "блокировка мёртвого процесса не снята"
    Exit-HrmActionLock -StateDir $state
}

Test-Case "D10: процесс операции входит в свою блокировку повторно — «Открыть HR Manager» из меню работает" {
    New-HrmSupervisorTestContext
    $state = Get-HrmTestStateDir
    # Значок блокирует действие и передаёт блокировку порождённому процессу движка
    # (action="engine", pid процесса движка). Движок, выполняя действие, входит в
    # ту же блокировку повторно — раньше он отказывал сам себе, и пункт меню
    # «Открыть HR Manager» не открывал приложение (Start-HrmApp, Install.psm1).
    Set-HrmTestActionLock -StateDir $state -Action "engine" -ProcessId $PID
    $reentrant = Enter-HrmActionLock -StateDir $state -Action "open"
    Assert-HrmTrue ([bool]$reentrant.acquired) "движок не смог войти в блокировку, переданную ему значком"
    Assert-HrmTrue ([bool]$reentrant.reentrant) "повторный вход не помечен"
    Assert-HrmTrue (Test-Path (Get-HrmActionLockFile $state)) "блокировка снята при повторном входе"
    # Та же операция тем же процессом — тоже повторный вход.
    $same = Enter-HrmActionLock -StateDir $state -Action "engine"
    Assert-HrmTrue ([bool]$same.acquired) "повторный вход для той же операции не сработал"
    # Чужое действие — по-прежнему отказ (блокировка защищает от двух операций).
    Set-HrmTestActionLock -StateDir $state -Action "restart" -ProcessId $PID
    $other = Enter-HrmActionLock -StateDir $state -Action "support-bundle"
    Assert-HrmFalse ([bool]$other.acquired) "две разные операции прошли одновременно"
    Exit-HrmActionLock -StateDir $state
}

Test-Case "P5.1: значок порождает движок атомарно — блокировка берётся до запуска и передаётся потомку" {
    # До исправления (e298202) значок делал два раздельных шага:
    # Test-HrmEngineOperationInFlight -> Start-HrmEngineProcess. Второй значок
    # (два входа в Windows подряд, повторный запуск Setup.exe) успевал пройти ту
    # же проверку, и первые секунды работали два движка: две параллельные
    # compose-операции, гонка за порт и образы.
    $world = New-HrmSupervisorTestContext
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    # Управляющий файл значка лежит рядом с hr-manager.ps1; здесь достаточно
    # заглушки — существование пути проверяется до порождения.
    New-Item -ItemType Directory -Path $install -Force | Out-Null
    $engineScript = Join-Path $install "hr-manager.ps1"
    Set-Content -Path $engineScript -Value "# stub" -Encoding ASCII
    # pid потомка — ЖИВОЙ pid другого процесса: блокировка с мёртвым pid
    # считается пустой, и тест не доказывал бы защиту от второго движка.
    $childPid = Get-HrmOtherLivePid
    Assert-HrmTrue ($childPid -gt 0) "не найден живой pid другого процесса для проверки"
    $global:HRM_Spawns = 0
    $global:HRM_LockAtSpawn = @()
    # Скриптблоки моков вызываются из модуля движка, поэтому данные теста — в
    # глобальной области видимости (см. New-HrmMockWorld в test-harness.ps1).
    $global:HRM_SpawnState = $state
    $global:HRM_SpawnChildPid = $childPid
    Set-HrmProcessLaunchMock {
        param($FilePath, $Arguments, $Mode, $LogFile)
        $global:HRM_Spawns = $global:HRM_Spawns + 1
        # Снимок блокировки В МОМЕНТ порождения: именно он доказывает, что
        # проверка и запуск стали одним действием.
        $lockFile = Get-HrmActionLockFile $global:HRM_SpawnState
        $lockNow = $null
        if (Test-Path $lockFile) { $lockNow = Get-HrmJsonFile $lockFile }
        $lockAction = ""
        $lockOwner = 0
        if ($null -ne $lockNow) {
            if ($lockNow.PSObject.Properties["action"]) { $lockAction = [string]$lockNow.action }
            if ($lockNow.PSObject.Properties["pid"]) { $lockOwner = [int]$lockNow.pid }
        }
        $global:HRM_LockAtSpawn += , ([pscustomobject]@{
                exists = ($null -ne $lockNow)
                action = $lockAction
                pid = $lockOwner
                mode = [string]$Mode
            })
        return (New-HrmFakeExternalProcess -ProcessId $global:HRM_SpawnChildPid -Hangs $false -ExitCode 0)
    }
    $engine = Start-HrmTrayEngineProcess -Action "supervise" -ScriptPath $engineScript -InstallDir $install -StateDir $state
    Assert-HrmTrue ([bool]$engine.started) "значок не породил движок"
    Assert-HrmEqual 1 $global:HRM_Spawns "порождено больше одного процесса движка"
    Assert-HrmEqual "engine" ([string]$global:HRM_LockAtSpawn[0].mode) "движок порождён в обход единой точки запуска процессов"
    Assert-HrmTrue ([bool]$global:HRM_LockAtSpawn[0].exists) "процесс порождён без блокировки действия: проверка и запуск не атомарны"
    Assert-HrmEqual "engine" ([string]$global:HRM_LockAtSpawn[0].action) "блокировка до порождения не помечена служебным действием"
    Assert-HrmEqual $PID ([int]$global:HRM_LockAtSpawn[0].pid) "до порождения блокировка принадлежит не значку"
    # Передача потомку: блокировка описывает процесс движка, пока он жив.
    $lockData = Get-HrmJsonFile (Get-HrmActionLockFile $state)
    Assert-HrmEqual "engine" ([string]$lockData.action) "блокировка не передана потомку"
    Assert-HrmEqual $childPid ([int]$lockData.pid) "в блокировке не pid процесса движка"
    Assert-HrmEqual $childPid ([int]$engine.pid) "в результате порождения нет pid потомка"
    Assert-HrmTrue ([bool](Test-HrmEngineOperationInFlight -StateDir $state).running) "второй значок не увидит идущую операцию движка"
    # Критерий приёмки: второй значок НЕ порождает второй движок.
    $second = Start-HrmTrayEngineProcess -Action "supervise" -ScriptPath $engineScript -InstallDir $install -StateDir $state
    Assert-HrmFalse ([bool]$second.started) "второй значок породил второй движок"
    Assert-HrmEqual 1 $global:HRM_Spawns "при живой блокировке порождён ещё один процесс движка"
    Assert-HrmEqual "in_flight" ([string]$second.reason) "причина отказа значка не различима для журнала"
    Assert-HrmTrue ($second.message.Length -gt 0) "у отказа значка нет текста"
    Exit-HrmActionLock -StateDir $state
    # Входная точка значка не порождает движок в обход блокировки действия.
    $trayEntry = Get-Content -Path (Join-Path $script:RepoRoot "infra\windows\hrm-tray.ps1") -Raw -Encoding UTF8
    Assert-HrmContains $trayEntry "Start-HrmTrayEngineProcess" "значок не использует атомарное порождение движка"
    Assert-HrmNotContains $trayEntry "Start-HrmEngineProcess" "значок порождает движок напрямую, минуя блокировку действия"
}

Test-Case "P5.2: повторный вход перезаписывает блокировку на себя — снятие в конце операции не удаляет чужую запись" {
    # До исправления (e298202) повторный вход принимал живую блокировку со своим
    # pid и служебной отметкой action="engine" КАК ЕСТЬ: процесс выполнял
    # операцию по записи, созданной другим процессом (значком) и в другой момент
    # времени, а завершающий Exit-HrmActionLock удалял эту запись. Защита от
    # второй операции исчезала ровно в тот момент, когда первая ещё шла, а порог
    # «зависла» считался от решения значка, а не от начала работы.
    $world = New-HrmSupervisorTestContext
    $state = Get-HrmTestStateDir
    # Значок поставил отметку пять минут назад, pid потомка передать не успел.
    Set-HrmTestActionLock -StateDir $state -Action "engine" -ProcessId $PID -AgeMinutes 5
    $entered = Enter-HrmActionLock -StateDir $state -Action "supervise"
    Assert-HrmTrue ([bool]$entered.acquired) "повторный вход в переданную блокировку отказал"
    Assert-HrmTrue ([bool]$entered.reentrant) "повторный вход не помечен"
    Assert-HrmEqual $PID ([int]$entered.pid) "в результате повторного входа не свой pid"
    Assert-HrmEqual 0 ([int]$entered.age_minutes) "возраст блокировки не сброшен на момент входа"
    $lockData = Get-HrmJsonFile (Get-HrmActionLockFile $state)
    Assert-HrmEqual $PID ([int]$lockData.pid) "владелец блокировки не стал явным"
    $ageSeconds = ((Get-Date) - [datetime]::Parse([string]$lockData.started_at)).TotalSeconds
    Assert-HrmTrue ($ageSeconds -lt 60) "отсчёт «зависла» не начат заново для выполняющего процесса"
    # Служебная отметка значка сохраняется: по ней другие процессы понимают, что
    # работает именно движок, и по ней же разрешён повторный вход для той же
    # операции (контракт D10).
    Assert-HrmEqual "engine" ([string]$lockData.action) "служебная отметка значка потеряна при повторном входе"
    $again = Enter-HrmActionLock -StateDir $state -Action "supervise"
    Assert-HrmTrue ([bool]$again.acquired) "повторный вход для той же операции не сработал"
    Assert-HrmTrue ([bool]$again.reentrant) "повторный вход для той же операции не помечен"
    # Чужая ЖИВАЯ блокировка по-прежнему не пускает вторую операцию: перезапись
    # возможна только для своего pid (правило «зависла» не ослаблено).
    $otherPid = Get-HrmOtherLivePid
    Assert-HrmTrue ($otherPid -gt 0) "не найден живой pid другого процесса для проверки"
    Set-HrmTestActionLock -StateDir $state -Action "engine" -ProcessId $otherPid -AgeMinutes 0
    $foreign = Enter-HrmActionLock -StateDir $state -Action "supervise"
    Assert-HrmFalse ([bool]$foreign.acquired) "чужая живая блокировка пропущена"
    Assert-HrmFalse ([bool]$foreign.reentrant) "чужая блокировка помечена как повторный вход"
    $foreignLock = Get-HrmJsonFile (Get-HrmActionLockFile $state)
    Assert-HrmEqual $otherPid ([int]$foreignLock.pid) "чужая блокировка перезаписана на себя"
    Assert-HrmEqual "engine" ([string]$foreignLock.action) "чужая блокировка переписана на запрашиваемое действие"
    Exit-HrmActionLock -StateDir $state
}

Test-Case "D4: значок не ходит в Docker при идущей операции и опрашивает редко в покое" {
    $world = New-HrmSupervisorTestContext
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Set-HrmInstallRecord $state @{ release_sha = ("1" * 40); port = 8080 }
    $otherPid = Get-HrmOtherLivePid
    Set-HrmTestActionLock -StateDir $state -Action "supervise" -ProcessId $otherPid
    $world.Calls = @()
    $tick = Invoke-HrmTrayRefresh -InstallDir $install -StateDir $state
    Assert-HrmFalse ([bool]$tick.probed) "значок сходил в Docker при идущей операции движка"
    Assert-HrmEqual 0 (@($world.Calls).Count) "при идущей операции значок опрашивает Docker"
    Assert-HrmEqual 5000 ([int]$tick.interval_ms) "при идущей операции интервал должен остаться коротким"
    Assert-HrmEqual "starting" ([string]$tick.state) "в ожидании операции значок показывает «Запускается»"
    Exit-HrmActionLock -StateDir $state
    # В покое (приложение готово) значок опрашивает состояние, но редко: 30 с —
    # это 2 проверки в минуту при критерии «не больше 4 в минуту».
    $world.Running = $true
    $world.Calls = @()
    $restTick = Invoke-HrmTrayRefresh -InstallDir $install -StateDir $state
    Assert-HrmTrue ([bool]$restTick.probed) "в покое значок обязан проверить состояние приложения"
    Assert-HrmEqual "ready" ([string]$restTick.state) "готовое приложение не распознано"
    Assert-HrmEqual 30000 ([int]$restTick.interval_ms) "в покое интервал не увеличен (прежние 5 с — 12 опросов в минуту)"
    Assert-HrmTrue (@($world.Calls).Count -ge 1) "в покое значок не опросил состояние контейнеров"
    # Правило интервалов — чистая функция: значок в покое даёт не больше 4
    # опросов состояния контейнеров в минуту.
    Assert-HrmEqual 5000 (Get-HrmTrayRefreshIntervalMs -State "starting") "при запуске интервал должен быть коротким"
    Assert-HrmEqual 5000 (Get-HrmTrayRefreshIntervalMs -State "ready" -Busy $true) "при идущей операции интервал должен быть коротким"
    Assert-HrmEqual 30000 (Get-HrmTrayRefreshIntervalMs -State "ready") "в покое интервал 30 с"
    Assert-HrmEqual 60000 (Get-HrmTrayRefreshIntervalMs -State "stopped") "в остановке интервал 60 с"
    Assert-HrmEqual 60000 (Get-HrmTrayRefreshIntervalMs -State "error") "в ошибке интервал 60 с"
    Assert-HrmTrue ((60000 / (Get-HrmTrayRefreshIntervalMs -State "ready")) -le 4) "критерий «не больше 4 опросов в минуту» нарушен"
}

Test-Case "D7: значок показывает «Ошибка» с причиной движка, а не вечное «Запускается»" {
    New-HrmSupervisorTestContext
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $enginePid = Get-HrmOtherLivePid
    $reason = "Плагин Docker Compose v2 не найден: проверены каталоги плагинов cli-plugins."
    Set-HrmJsonFile $state "supervisor.json" ([ordered]@{
            state = "error"; message = $reason; busy = $false
            pid = $enginePid; failure_count = 1; updated_at = (Get-Date).ToString("o")
        })
    Set-HrmInstallRecord $state @{ release_sha = ("1" * 40); port = 8080 }
    # Чистая функция: план значка («Запускается») не затирает причину движка.
    $effective = Get-HrmTrayEffectiveState -PlanState "starting" -PlanMessage "Запускаем HR Manager…" -PlanBusy $false -StoredState "error" -StoredMessage $reason -StoredPid $enginePid -CurrentPid $PID
    Assert-HrmEqual "error" ([string]$effective.state) "значок показал «Запускается» вместо ошибки движка"
    Assert-HrmContains ([string]$effective.message) "Compose" "причина отказа потеряна"
    Assert-HrmFalse ([bool]$effective.busy) "в ошибке значок не должен считаться занятым"
    # Запись этого же процесса план не перезаписывает (иначе значок сам себя
    # «заклинил» бы в ошибке после собственного сообщения).
    $own = Get-HrmTrayEffectiveState -PlanState "starting" -PlanMessage "Запускаем HR Manager…" -PlanBusy $true -StoredState "error" -StoredMessage "старая причина" -StoredPid $PID -CurrentPid $PID
    Assert-HrmEqual "starting" ([string]$own.state) "собственная запись значка перезаписала его план"
    # План «Ошибка» без текста берёт причину из состояния.
    $fromStored = Get-HrmTrayEffectiveState -PlanState "error" -PlanMessage "" -StoredState "error" -StoredMessage "причина из файла" -StoredPid 0 -CurrentPid $PID
    Assert-HrmEqual "причина из файла" ([string]$fromStored.message) "причина из состояния не подставлена"
    # Сквозная проверка: тик значка на тех же файлах даёт «Ошибка» + причину.
    # Операция движка здесь уже завершена (он записал ошибку и вышел), поэтому
    # тик вправе опросить состояние — но опрашивает редко (60 с в ошибке).
    $tick = Invoke-HrmTrayRefresh -InstallDir $install -StateDir $state
    Assert-HrmEqual "error" ([string]$tick.state) "тик значка показал не ошибку"
    Assert-HrmContains ([string]$tick.message) "Compose" "тик значка потерял причину"
    Assert-HrmEqual 60000 ([int]$tick.interval_ms) "в ошибке значок опрашивает состояние слишком часто"
}

Test-Case "P2: начало новой попытки НЕ стирает историю отказов — три настоящих прохода движка дают retry_exhausted" {
    # Регрессия P2 (дораздел PR #55). Кейс выше проверяет счётчик синтетикой
    # («три записи error подряд без единого starting») и проходил даже тогда, когда
    # в реальном потоке порог был недостижим: каждая попытка движка НАЧИНАЕТСЯ
    # записью состояния «starting» (цикл, установка, продолжение операции, значок,
    # диспетчер), а прежнее правило Set-HrmSupervisorState обнуляло счётчик любым
    # не-error состоянием. Счётчик ходил 0 → 1 → 0 → 1, значок на каждом входе в
    # Windows снова порождал движок, и отказ по одной и той же причине повторялся
    # бесконечно. Здесь — настоящая последовательность «план значка → цикл движка →
    # отказ» три раза подряд.
    $world = New-HrmSupervisorTestContext
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Set-HrmInstallRecord $state @{ release_sha = ("1" * 40); port = 8080 }
    # Причина отказа постоянная (сценарий пилотной машины — Docker Desktop
    # недоступен): каждая попытка заканчивается одинаково, поэтому счётчик обязан
    # расти, а не обнуляться началом следующей попытки.
    $world.DockerMissing = $true
    Set-HrmDockerOverride @{ desktop = "not_installed"; desktop_path = ""; port_free = $true }
    $threshold = Get-HrmSupervisorMaxConsecutiveFailures
    for ($attempt = 1; $attempt -le $threshold; $attempt++) {
        # 1) Значок решает, запускать ли движок. До порога план обычный.
        $plan = Get-HrmTrayCurrentPlan -StateDir $state -InstallDir $install
        Assert-HrmEqual "normal" ([string]$plan.mode) ("попытка {0}: значок не собирался запускать движок" -f $attempt)
        Assert-HrmTrue ([bool]$plan.start_engine) ("попытка {0}: движок не запускается до достижения порога" -f $attempt)
        # 2) Настоящий проход движка: цикл пишет «starting», отказывает и пишет
        #    «error» (ровно то, что делает процесс, порождённый значком).
        $cycle = Invoke-HrmSupervisorCycle -InstallDir $install -StateDir $state
        Assert-HrmFalse ([bool]$cycle.ok) ("попытка {0}: цикл не отказал при недоступном Docker" -f $attempt)
        Assert-HrmEqual "error" ([string](Get-HrmSupervisorState -StateDir $state).state) ("попытка {0}: отказ не записан в состояние" -f $attempt)
        # 3) История отказов переживает начало попытки: счётчик равен числу
        #    сделанных попыток. На прежнем коде здесь всегда было 1.
        Assert-HrmEqual $attempt (Get-HrmSupervisorFailureCount -StateDir $state) ("попытка {0}: начало попытки обнулило счётчик подряд идущих отказов" -f $attempt)
    }
    # 4) Порог достигнут — значок перестаёт порождать движок и показывает причину.
    $exhausted = Get-HrmTrayCurrentPlan -StateDir $state -InstallDir $install
    Assert-HrmEqual "retry_exhausted" ([string]$exhausted.mode) "после трёх настоящих отказов значок снова запускает движок"
    Assert-HrmFalse ([bool]$exhausted.start_engine) "при исчерпании повторов значок порождает новый процесс движка"
    Assert-HrmEqual "error" ([string]$exhausted.state) "исчерпание повторов не видно как «Ошибка»"
    Assert-HrmContains ([string]$exhausted.message) "повторы запуска остановлены" "нет объяснения остановки повторов"
    Assert-HrmContains ([string]$exhausted.message) "отчёт для поддержки" "в сообщении нет действия для человека"
    # 5) Явное действие человека разрешает новую попытку (счётчик сбрасывается).
    Reset-HrmSupervisorFailureCount -StateDir $state | Out-Null
    $afterReset = Get-HrmTrayCurrentPlan -StateDir $state -InstallDir $install
    Assert-HrmEqual "normal" ([string]$afterReset.mode) "после действия человека попытка не разрешена"
    Assert-HrmTrue ([bool]$afterReset.start_engine) "после сброса счётчика значок не запускает движок"
    # 6) Промежуточные состояния («starting», «stopped») счётчик сохраняют, а успех
    #    («ready») — обнуляет: это единственные два способа обнуления.
    Set-HrmSupervisorState -StateDir $state -State "error" -Message "причина отказа"
    Set-HrmSupervisorState -StateDir $state -State "error" -Message "причина отказа"
    Assert-HrmEqual 2 (Get-HrmSupervisorFailureCount -StateDir $state) "счётчик не считает отказы"
    Set-HrmSupervisorState -StateDir $state -State "starting" -Message "Подготавливаем рабочую среду…" -Busy $true
    Assert-HrmEqual 2 (Get-HrmSupervisorFailureCount -StateDir $state) "запись «starting» обнулила счётчик отказов"
    Set-HrmSupervisorState -StateDir $state -State "stopped" -Message "Приложение остановлено."
    Assert-HrmEqual 2 (Get-HrmSupervisorFailureCount -StateDir $state) "запись «stopped» обнулила счётчик отказов"
    Set-HrmSupervisorState -StateDir $state -State "ready" -Message "HR Manager готов."
    Assert-HrmEqual 0 (Get-HrmSupervisorFailureCount -StateDir $state) "успех не сбросил счётчик отказов"
}

Test-Case "D4/D7: значок останавливает бесконечные повторные запуски после подряд идущих отказов" {
    New-HrmSupervisorTestContext
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Set-HrmInstallRecord $state @{ release_sha = ("1" * 40); port = 8080 }
    $reason = "Плагин Docker Compose v2 не найден."
    $threshold = Get-HrmSupervisorMaxConsecutiveFailures
    for ($i = 0; $i -lt $threshold; $i++) {
        Set-HrmSupervisorState -StateDir $state -State "error" -Message $reason
    }
    Assert-HrmEqual $threshold (Get-HrmSupervisorFailureCount -StateDir $state) "счётчик подряд идущих отказов не работает"
    $plan = Get-HrmTrayCurrentPlan -StateDir $state -InstallDir $install
    Assert-HrmEqual "retry_exhausted" ([string]$plan.mode) "значок продолжал бы запускать движок бесконечно"
    Assert-HrmFalse ([bool]$plan.start_engine) "при исчерпании повторов значок снова запускает движок"
    Assert-HrmEqual "error" ([string]$plan.state) "исчерпание повторов должно быть видно как «Ошибка»"
    Assert-HrmContains ([string]$plan.message) "повторы запуска остановлены" "нет объяснения остановки повторов"
    Assert-HrmContains ([string]$plan.message) "Compose" "в сообщении нет причины отказа"
    Assert-HrmContains ([string]$plan.message) "отчёт для поддержки" "в сообщении нет действия для человека"
    $tick = Invoke-HrmTrayRefresh -InstallDir $install -StateDir $state
    Assert-HrmEqual 60000 ([int]$tick.interval_ms) "в ошибке значок опрашивает слишком часто"
    Assert-HrmFalse ([bool]$tick.probed) "в ошибке значок не должен ходить в Docker"
    # Успех сбрасывает счётчик.
    Set-HrmSupervisorState -StateDir $state -State "ready" -Message "HR Manager готов."
    Assert-HrmEqual 0 (Get-HrmSupervisorFailureCount -StateDir $state) "успех не сбросил счётчик отказов"
    # Явное действие человека (пункт меню) разрешает новую попытку.
    for ($i = 0; $i -lt $threshold; $i++) {
        Set-HrmSupervisorState -StateDir $state -State "error" -Message $reason
    }
    Reset-HrmSupervisorFailureCount -StateDir $state | Out-Null
    Assert-HrmEqual 0 (Get-HrmSupervisorFailureCount -StateDir $state) "сброс счётчика не сработал"
    $afterReset = Get-HrmTrayCurrentPlan -StateDir $state -InstallDir $install
    Assert-HrmEqual "normal" ([string]$afterReset.mode) "после сброса счётчика значок не запускает движок"
    Assert-HrmTrue ([bool]$afterReset.start_engine) "после действия человека попытка не разрешена"
    # Ручная проверка состояния счётчик не накручивает (это не попытка запуска).
    Set-HrmSupervisorState -StateDir $state -State "ready" -Message "HR Manager готов."
    $world = New-HrmMockWorld
    $world.Running = $false
    $null = Invoke-HrmTrayMenuAction -ActionId "check" -InstallDir $install -StateDir $state
    $null = Invoke-HrmTrayMenuAction -ActionId "check" -InstallDir $install -StateDir $state
    Assert-HrmEqual 0 (Get-HrmSupervisorFailureCount -StateDir $state) "ручная проверка состояния накрутила счётчик отказов"
}

Test-Case "D5/D7: длинная причина отказа не вытесняет действие из уведомления значка" {
    New-HrmSupervisorTestContext
    # Причина отказа по compose подробная (перечень каталогов плагинов) и длиннее
    # предела уведомления Windows (255 символов). Обрезаться должна ПРИЧИНА:
    # действие «Создать отчёт для поддержки» обязано остаться в тексте, иначе
    # пользователь видел бы обрывок диагностики без следующего шага.
    $longReason = "Плагин Docker Compose v2 не найден: проверены каталоги плагинов " + ("C:\Users\owner\.docker\cli-plugins (содержимое: пусто); " * 8)
    Assert-HrmTrue ($longReason.Length -gt 255) "тестовая причина короче предела уведомления — случай не проверяется"
    $balloon = Get-HrmTrayBalloon -State "error" -Message $longReason
    Assert-HrmTrue ([int]$balloon.text.Length -le 255) ("текст уведомления длиннее предела Windows: " + [string]$balloon.text.Length)
    Assert-HrmTrue ([int]$balloon.title.Length -le 63) ("заголовок уведомления длиннее предела Windows: " + [string]$balloon.title.Length)
    Assert-HrmContains ([string]$balloon.text) "Создать отчёт для поддержки" "длинная причина вытеснила действие для человека"
    Assert-HrmContains ([string]$balloon.text) "Плагин Docker Compose" "в уведомлении не осталось начала причины"
    Assert-HrmContains ([string]$balloon.text) "..." "обрезка причины не обозначена"
    foreach ($forbidden in @("docker compose", "PowerShell", "контейнер")) {
        Assert-HrmNotContains ([string]$balloon.text) $forbidden ("техническая формулировка в уведомлении: " + $forbidden)
    }
    # Короткая причина не обрезается и сохраняет действие.
    $short = Get-HrmTrayBalloon -State "error" -Message "Не удалось запустить приложение."
    Assert-HrmContains ([string]$short.text) "Не удалось запустить приложение." "короткая причина изменена"
    Assert-HrmContains ([string]$short.text) "Создать отчёт для поддержки" "в коротком уведомлении нет действия"
}

Write-Host ("Supervisor: {0} пройдено, {1} провалено" -f $global:HRM_TestPassed, $global:HRM_TestFailed)
