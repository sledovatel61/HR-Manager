# Поведенческие тесты управляющего компонента (supervisor) и значка в трее:
# состояния «Запускается / Готово / Ошибка», меню, единственность, автозапуск,
# отсутствие дублей операций. Реальная машина не затрагивается.

param([string]$HarnessPath = $PSScriptRoot)

. (Join-Path $HarnessPath "test-harness.ps1")

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
    # Симулируем «занятый машинный мьютекс»: занимаем имя ПО УМОЛЧАНИЮ напрямую.
    $busy = New-Object System.Threading.Mutex($false, "Local\HRManagerPilotSupervisor")
    $held = $false
    try { $held = $busy.WaitOne(0) } catch { $held = $false }
    Assert-HrmTrue $held "не удалось занять машинный мьютекс для симуляции"
    try {
        # Контекстный мьютекс (уникальное имя из env) свободен — первый захват успешен.
        $lock = Enter-HrmSupervisorLock
        Assert-HrmTrue $lock.acquired "уникальный мьютекс контекста должен быть свободен даже при занятом машинном"
        Exit-HrmSupervisorLock $lock
        # Без переопределения занятый машинный мьютекс даёт отказ (контракт единственности).
        Remove-Item Env:HRM_SUPERVISOR_MUTEX -ErrorAction SilentlyContinue
        $lock2 = Enter-HrmSupervisorLock
        Assert-HrmFalse $lock2.acquired "занятый машинный мьютекс должен давать отказ без переопределения"
        Exit-HrmSupervisorLock $lock2
    }
    finally {
        if ($held) { try { $busy.ReleaseMutex() } catch { } }
        $busy.Dispose()
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

Write-Host ("Supervisor: {0} пройдено, {1} провалено" -f $global:HRM_TestPassed, $global:HRM_TestFailed)
