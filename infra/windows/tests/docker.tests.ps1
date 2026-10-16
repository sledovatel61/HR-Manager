# Поведенческие тесты диагностики и запуска Docker Desktop (0.15.0).
# Реальная машина не затрагивается: Docker/процессы подменяются моками
# (Set-HrmDockerOverride, Set-HrmProcessLaunchMock, Set-HrmExternalMock,
# Set-HrmHttpMock). Установка Docker Desktop пользователю остаётся ручной.

param([string]$HarnessPath = $PSScriptRoot)

. (Join-Path $HarnessPath "test-harness.ps1")

Write-Host "== Docker Desktop: диагностика, ручная установка, ожидание Engine =="

function New-HrmDockerTestContext {
    # Изолированные каталоги + мир моков + базовые предусловия.
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    $env:HRM_DESKTOP_DIR = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM-desktop-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Path $env:HRM_DESKTOP_DIR -Force | Out-Null
    return $world
}

Test-Case "Docker Desktop не установлен: prepare отказывает с официальным URL и не запускает installer" {
    $world = New-HrmDockerTestContext
    Set-HrmDockerOverride @{ desktop = "not_installed"; desktop_path = ""; wsl = "ok"; virtualization = "enabled"; free_mb = 20480; port_free = $true }
    $state = Get-HrmTestStateDir
    $result = Invoke-HrmDockerPrepare -InstallDir (Get-HrmTestInstallDir) -StateDir $state -Port 8080
    Assert-HrmFalse $result.ok "prepare не должен считаться успешным без Docker"
    Assert-HrmEqual "docker_missing" $result.state "неверное состояние"
    Assert-HrmTrue $result.needs_install "должна быть отмечена необходимость ручной установки"
    Assert-HrmContains $result.message "Docker Desktop" "сообщение должно называть Docker Desktop"
    Assert-HrmContains $result.message "Docker Engine" "сообщение должно называть Docker Engine"
    Assert-HrmContains $result.message "https://www.docker.com/products/docker-desktop/" "нужна действующая официальная ссылка"
    Assert-HrmNotContains $result.message "Установить Docker Desktop" "сообщение не должно обещать несуществующую кнопку"
    Assert-HrmFalse (Test-Path (Join-Path $state "docker-pending.json")) "Docker-specific pending state не должен создаваться"
    # Бесконечного цикла нет: один проход, состояние зафиксировано.
    $steps = Get-HrmSetupProgressPlan -StateDir $state
    $dockerStep = @($steps | Where-Object { $_.key -eq "docker_app" })[0]
    Assert-HrmEqual "fail" $dockerStep.status "шаг «Ищем Docker Desktop» должен быть fail"
}

Test-Case "Docker Desktop установлен, но не запущен: движок запускает его и ждёт Engine" {
    $world = New-HrmDockerTestContext
    # Счётчик запусков принадлежит тесту: инициализируем его явно, иначе
    # StrictMode упадёт на несуществующей глобальной переменной.
    $global:HRM_DockerLaunches = @()
    Set-HrmProcessLaunchMock {
        param($FilePath, $Arguments, $Mode, $LogFile)
        $global:HRM_DockerLaunches += [pscustomobject]@{ FilePath = $FilePath; Mode = $Mode }
        return [pscustomobject]@{ Id = 4321 }
    }
    $desktopExe = Join-Path $env:HRM_DESKTOP_DIR "Docker Desktop.exe"
    Set-Content -Path $desktopExe -Value "stub" -Encoding ASCII
    # Готовность Engine НЕ подменяем: она должна подтвердиться настоящей
    # проверкой через мок docker info (иначе вызова docker не было бы вовсе).
    Set-HrmDockerOverride @{ desktop = "installed_stopped"; desktop_path = $desktopExe; wsl = "ok"; virtualization = "enabled" }
    $world = New-HrmMockWorld
    $result = Start-HrmDockerDesktop -TimeoutSeconds 5
    Assert-HrmTrue $result.started "движок должен посчитать Docker Desktop запущенным"
    Assert-HrmTrue ($global:HRM_DockerLaunches.Count -ge 1) "Docker Desktop должен быть запущен движком"
    $calls = @(Get-HrmWorldCallArgs $world)
    Assert-HrmTrue ($calls.Count -ge 1) "ожидался вызов docker (проверка Engine)"
}

Test-Case "Docker Engine ещё не готов: таймаут без бесконечного цикла и понятное сообщение" {
    New-HrmDockerTestContext
    $world = New-HrmMockWorld
    $world.SuspendEngine = $true
    Set-HrmDockerOverride @{ desktop = "starting"; desktop_path = ""; engine = $false; wsl = "ok"; virtualization = "enabled" }
    $ready = Wait-HrmDockerEngine -TimeoutSeconds 2 -IntervalSeconds 1
    Assert-HrmFalse $ready "ожидание должно завершиться по таймауту"
    $state = Get-HrmTestStateDir
    $result = Invoke-HrmDockerPrepare -InstallDir (Get-HrmTestInstallDir) -StateDir $state -Port 8080 -TimeoutSeconds 2
    Assert-HrmFalse $result.ok "prepare не должен считаться успешным"
    Assert-HrmTrue (@("engine_timeout", "engine_not_ready") -contains $result.state) "неожиданное состояние: $($result.state)"
    Assert-HrmContains $result.message "Повторить" "сообщение должно предлагать повтор"
}

Test-Case "Docker sidecar отсутствует: инструкция показывает только официальный сайт" {
    New-HrmDockerTestContext
    $installDir = Get-HrmTestInstallDir
    $guide = @(Get-HrmDockerInstallGuide -InstallDir $installDir) -join "`n"
    Assert-HrmContains $guide "https://www.docker.com/products/docker-desktop/" "нужна ссылка на официальный сайт"
    Assert-HrmNotContains $guide "DockerDesktopInstaller.exe" "при отсутствии sidecar не следует показывать локальный путь"
    Assert-HrmContains $guide "лицензи" "лицензию Docker принимает человек"
    Assert-HrmNotContains $guide "Нажмите «Установить Docker Desktop»" "инструкция не должна ссылаться на несуществующую кнопку"
}

Test-Case "Docker sidecar с совпавшим SHA256 показывается для ручного запуска и никогда не стартует сам" {
    New-HrmDockerTestContext
    $installDir = Get-HrmTestInstallDir
    $dockerDir = Join-Path $installDir "docker"
    New-Item -ItemType Directory -Path $dockerDir -Force | Out-Null
    $sidecar = Join-Path $dockerDir "DockerDesktopInstaller.exe"
    Set-Content -LiteralPath $sidecar -Value "test placeholder" -Encoding ASCII
    $sidecarHash = Get-HrmFileSha256 -Path $sidecar
    $release = [ordered]@{
        release_sha = ("a" * 40)
        docker_desktop_installer = [ordered]@{
            available = $true
            sha256 = $sidecarHash
            source_url = "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"
            automatically_launched = $false
        }
    }
    $release | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $installDir "release.json") -Encoding UTF8
    Set-HrmDockerOverride @{ desktop = "not_installed"; desktop_path = ""; wsl = "ok"; virtualization = "enabled"; free_mb = 20480; port_free = $true }
    $global:HRM_DockerLaunches = @()
    Set-HrmProcessLaunchMock {
        param($FilePath, $Arguments, $Mode, $LogFile)
        $global:HRM_DockerLaunches += $FilePath
        return [pscustomobject]@{ Id = 4321 }
    }
    $guide = @(Get-HrmDockerInstallGuide -InstallDir $installDir) -join "`n"
    Assert-HrmContains $guide $sidecar "локальный путь можно показать только для sidecar с совпавшим SHA256"
    Assert-HrmContains $guide "https://www.docker.com/products/docker-desktop/" "официальный URL должен оставаться видимым"
    Assert-HrmContains $guide "не запускает" "HR Manager обязан явно сказать, что не запускает sidecar"
    $result = Invoke-HrmDockerPrepare -InstallDir $installDir -StateDir (Get-HrmTestStateDir) -Port 8080
    Assert-HrmFalse $result.ok "отсутствие Docker Engine должно оставаться отказом и при sidecar"
    Assert-HrmEqual 0 $global:HRM_DockerLaunches.Count "Docker installer sidecar был запущен автоматически"
}

Test-Case "Docker sidecar с несовпавшим или отсутствующим pin скрывается без ошибки hash-проверки" {
    New-HrmDockerTestContext
    $installDir = Get-HrmTestInstallDir
    $state = Get-HrmTestStateDir
    $dockerDir = Join-Path $installDir "docker"
    New-Item -ItemType Directory -Path $dockerDir -Force | Out-Null
    $sidecar = Join-Path $dockerDir "DockerDesktopInstaller.exe"
    Set-Content -LiteralPath $sidecar -Value "tampered placeholder" -Encoding ASCII
    $release = [ordered]@{
        release_sha = ("b" * 40)
        docker_desktop_installer = [ordered]@{
            available = $true
            sha256 = ("0" * 64)
            source_url = "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"
            automatically_launched = $false
        }
    }
    $release | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $installDir "release.json") -Encoding UTF8
    $env:HRM_LOG_FILE = Join-Path $state "sidecar-integrity.log"
    New-Item -ItemType Directory -Path $state -Force | Out-Null
    Set-HrmDockerOverride @{ desktop = "not_installed"; desktop_path = ""; wsl = "ok"; virtualization = "enabled"; free_mb = 20480; port_free = $true }

    $threw = $false
    $message = ""
    $result = $null
    try {
        $message = Get-HrmDockerMissingMessage -InstallDir $installDir
        $result = Invoke-HrmDockerPrepare -InstallDir $installDir -StateDir $state -Port 8080
    }
    catch { $threw = $true }

    Assert-HrmFalse $threw "несовпавший sidecar не должен превращать подсказку/prepare в исключение"
    Assert-HrmContains $message "https://www.docker.com/products/docker-desktop/" "при несовпадении нужен официальный URL"
    Assert-HrmNotContains $message "DockerDesktopInstaller.exe" "при несовпадении нельзя показывать локальный путь"
    Assert-HrmEqual "docker_missing" ([string]$result.state) "должен сохраниться обычный отказ по отсутствующему Docker"
    Assert-HrmContains $result.message "https://www.docker.com/products/docker-desktop/" "обычный Docker-отказ должен содержать официальный URL"
    Assert-HrmNotContains $result.message "DockerDesktopInstaller.exe" "ошибка хеша не должна раскрывать локальный путь"
    $log = Get-Content -LiteralPath $env:HRM_LOG_FILE -Raw -Encoding UTF8
    Assert-HrmContains $log "[warn]" "hash mismatch должен журналироваться как предупреждение"
    Assert-HrmContains $log "SHA256 Docker Desktop sidecar не совпал" "журнал не объясняет скрытие sidecar"

    # Отсутствующий ожидаемый SHA — тот же безопасный fallback: оставить URL,
    # скрыть путь и только предупредить, не превращая проверку в ошибку установки.
    $release.docker_desktop_installer.sha256 = $null
    $release | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $installDir "release.json") -Encoding UTF8
    Set-Content -LiteralPath $env:HRM_LOG_FILE -Value "" -Encoding UTF8
    $missingPinMessage = Get-HrmDockerMissingMessage -InstallDir $installDir
    Assert-HrmContains $missingPinMessage "https://www.docker.com/products/docker-desktop/" "без pin должен оставаться официальный URL"
    Assert-HrmNotContains $missingPinMessage "DockerDesktopInstaller.exe" "без pin нельзя показывать локальный путь"
    $missingPinLog = Get-Content -LiteralPath $env:HRM_LOG_FILE -Raw -Encoding UTF8
    Assert-HrmContains $missingPinLog "[warn]" "отсутствующий pin должен журналироваться как предупреждение"
    Assert-HrmContains $missingPinLog "не содержит доступный sidecar с корректным SHA256" "предупреждение не объясняет отсутствие pin"
}

Test-Case "Docker readiness больше не публикует pending_docker" {
    New-HrmDockerTestContext
    Set-HrmDockerOverride @{ desktop = "not_installed"; desktop_path = ""; wsl = "ok"; virtualization = "enabled"; free_mb = 20480; port_free = $true }
    $readiness = Get-HrmDockerReadiness -InstallDir (Get-HrmTestInstallDir) -StateDir (Get-HrmTestStateDir) -Port 8080
    $propertyNames = @($readiness.PSObject.Properties | ForEach-Object { [string]$_.Name })
    Assert-HrmFalse ($propertyNames -contains "pending_docker") "Docker-specific pending state остался в readiness"
}

Test-Case "WSL2 отсутствует и виртуализация выключена: шаги показывают предупреждение и действие" {
    New-HrmDockerTestContext
    $world = New-HrmMockWorld
    $world.WslAvailable = $false
    Set-HrmDockerOverride @{ desktop = "not_installed"; desktop_path = ""; wsl = "missing"; virtualization = "disabled"; free_mb = 20480 }
    $steps = Get-HrmSetupProgressPlan -StateDir (Get-HrmTestStateDir)
    $virtualizationStep = @($steps | Where-Object { $_.key -eq "virtualization" })[0]
    $wslStep = @($steps | Where-Object { $_.key -eq "wsl" })[0]
    Assert-HrmEqual "fail" $virtualizationStep.status "выключенная виртуализация должна быть явным действием"
    Assert-HrmContains $virtualizationStep.detail "BIOS" "нужно объяснить, что делать"
    Assert-HrmEqual "warn" $wslStep.status "WSL2 должен быть предупреждением"
}

Test-Case "порт занят: подбор свободного порта и предупреждение, а не молчаливая ошибка" {
    New-HrmDockerTestContext
    Set-HrmDockerOverride @{ desktop = "engine_ready"; engine = $true; port_free = $false; free_port = 8081; free_mb = 20480; wsl = "ok"; virtualization = "enabled" }
    $free = Find-HrmFreePort -StartPort 8080
    $plan = Get-HrmSetupProgressPlan -StateDir (Get-HrmTestStateDir) -Port 8080
    $portStep = @($plan | Where-Object { $_.key -eq "port" })[0]
    Assert-HrmTrue ($portStep.status -eq "warn" -or $portStep.status -eq "ok") "шаг порта должен быть warn/ok"
    Assert-HrmEqual 8081 $free "результат подбора порта зависит от реальных слушающих портов"
    Assert-HrmTrue ($free -ge 0) "подбор порта должен завершиться"
}

Test-Case "права: обычного пользователя достаточно, UAC может понадобиться при ручной установке Docker и для LAN" {
    New-HrmDockerTestContext
    Set-HrmDockerOverride @{ desktop = "engine_ready"; engine = $true; admin = $false; free_mb = 20480; wsl = "ok"; virtualization = "enabled" }
    $plan = Get-HrmSetupProgressPlan -StateDir (Get-HrmTestStateDir)
    $rightsStep = @($plan | Where-Object { $_.key -eq "rights" })[0]
    Assert-HrmEqual "ok" $rightsStep.status "работа без прав администратора — норма"
    Assert-HrmContains $rightsStep.detail "UAC" "нужно честно объяснить, когда потребуется разрешение Windows"
}

Test-Case "сводная проверка готовности: human-текст без технических команд" {
    New-HrmDockerTestContext
    Set-HrmDockerOverride @{ desktop = "not_installed"; desktop_path = ""; wsl = "missing"; virtualization = "enabled"; free_mb = 20480; port_free = $true }
    $lines = Format-HrmDockerReadiness -StateDir (Get-HrmTestStateDir) -Port 8080
    $text = ($lines -join "`n")
    Assert-HrmContains $text "Подготовка рабочей среды" "нет заголовка"
    foreach ($forbidden in @("docker compose", "PowerShell", "DATABASE_URL", "проверьте переменную")) {
        Assert-HrmNotContains $text $forbidden ("техническая формулировка для пользователя: " + $forbidden)
    }
    Assert-HrmContains $text "Docker Desktop" "должно говориться о Docker Desktop простыми словами"
    Assert-HrmContains $text "лицензи" "нужно упомянуть лицензию Docker"
}

Test-Case "ручная установка Docker описана без команд, автоматизации и выдуманной кнопки" {
    $guide = (Get-HrmDockerInstallGuide -InstallDir (Get-HrmTestInstallDir)) -join "`n"
    Assert-HrmContains $guide "https://www.docker.com/products/docker-desktop/" "нужна официальная ссылка"
    foreach ($forbidden in @("powershell", "winget", "choco", "msiexec", "Нажмите «Установить Docker Desktop»")) {
        Assert-HrmNotContains $guide $forbidden ("лишняя команда/несуществующий шаг: " + $forbidden)
    }
    Assert-HrmContains $guide "лицензи" "принятие лицензии Docker — действие пользователя"
    Assert-HrmContains $guide "повторите запуск HR Manager" "нужно объяснить следующий шаг после Docker Engine readiness"
}

Test-Case "занятый порт: prepare возвращает свободный порт для ПРИМЕНЕНИЯ (регрессия 12.10.2026)" {
    # Дефект: свободный порт подбирался и упоминался в журнале, но переменная
    # порта не менялась — стек публиковался на занятом порту, и в браузере
    # оказывался чужой стек. Теперь prepare возвращает эффективный порт.
    $world = New-HrmDockerTestContext
    $world.ForeignPortEnvironments = @{ "8080" = "development" }
    Set-HrmDockerOverride @{ desktop = "engine_ready"; engine = $true; port_free = $false; free_port = 8081; free_mb = 20480; wsl = "ok"; virtualization = "enabled" }
    $result = Invoke-HrmDockerPrepare -InstallDir (Get-HrmTestInstallDir) -StateDir (Get-HrmTestStateDir) -Port 8080
    Assert-HrmTrue $result.ok "prepare должен пройти с подобранным свободным портом"
    Assert-HrmEqual 8081 ([int]$result.port) "эффективный порт должен возвращаться вызывающему"
    Assert-HrmTrue ([bool]$result.port_changed) "смена порта должна быть зафиксирована"
}

Test-Case "порт занят собственным пилотом: порт сохраняется (чужой стек не путается с нашим)" {
    $world = New-HrmDockerTestContext
    # На порту отвечает НАШ пилот (environment=pilot) — это не повод менять порт.
    $world.HealthEnvironment = "pilot"
    Set-HrmDockerOverride @{ desktop = "engine_ready"; engine = $true; port_free = $false; free_port = 8081; free_mb = 20480; wsl = "ok"; virtualization = "enabled" }
    $result = Invoke-HrmDockerPrepare -InstallDir (Get-HrmTestInstallDir) -StateDir (Get-HrmTestStateDir) -Port 8080
    Assert-HrmTrue $result.ok "prepare должен пройти"
    Assert-HrmEqual 8080 ([int]$result.port) "порт собственного работающего пилота менять нельзя"
    Assert-HrmFalse ([bool]$result.port_changed) "порт меняться не должен"
}

Test-Case "занятый порт без свободной альтернативы: честный отказ вместо публикации на чужом порту" {
    $world = New-HrmDockerTestContext
    $world.ForeignPortEnvironments = @{ "8080" = "development" }
    Set-HrmDockerOverride @{ desktop = "engine_ready"; engine = $true; port_free = $false; free_port = 0; free_mb = 20480; wsl = "ok"; virtualization = "enabled" }
    $result = Invoke-HrmDockerPrepare -InstallDir (Get-HrmTestInstallDir) -StateDir (Get-HrmTestStateDir) -Port 8080
    Assert-HrmFalse $result.ok "без свободного порта prepare обязан отказать"
    Assert-HrmEqual "port_busy" $result.state "состояние должно объяснять занятость порта"
    Assert-HrmContains $result.message "занят" "сообщение должно называть причину"
}

Test-Case "применение порта: запись установки получает тот же порт, что публикует Compose" {
    New-HrmDockerTestContext
    $state = Get-HrmTestStateDir
    Set-HrmInstallRecord $state @{ port = 8080; release_sha = "sha-old"; install_dir = (Get-HrmTestInstallDir) }
    $prepare = [pscustomobject]@{ ok = $true; state = "ready"; port = 8085; port_changed = $true }
    $effective = Sync-HrmPortAfterPrepare -InstallDir (Get-HrmTestInstallDir) -StateDir $state -RequestedPort 8080 -Prepare $prepare
    Assert-HrmEqual 8085 $effective "эффективный порт не применён"
    Assert-HrmEqual 8085 ([int](Get-HrmInstallRecord $state).port) "запись установки не обновилась — ярлык и readiness смотрели бы на чужой порт"
}

Test-Case "Write-HrmPilotEnv без явного порта берёт порт из записи установки" {
    New-HrmDockerTestContext
    $state = Get-HrmTestStateDir
    Set-HrmInstallRecord $state @{ port = 8087; release_sha = "sha"; pilot_created = $true }
    $envFile = Write-HrmPilotEnv $state "sha"
    $text = Get-Content $envFile -Raw -Encoding UTF8
    Assert-HrmContains $text "HRM_PILOT_PORT=8087" "порт публикации должен браться из записи установки"
}

Test-Case "шаг «порт» в плане: занят своим пилотом — ok, чужим приложением — warn с подбором порта" {
    $world = New-HrmDockerTestContext
    Set-HrmDockerOverride @{ desktop = "engine_ready"; engine = $true; port_free = $false; free_port = 8081; free_mb = 20480; wsl = "ok"; virtualization = "enabled" }
    $ownStep = @((Get-HrmSetupProgressPlan -StateDir (Get-HrmTestStateDir) -Port 8080) | Where-Object { $_.key -eq "port" })[0]
    Assert-HrmEqual "ok" $ownStep.status "порт, занятый собственным пилотом, — не ошибка"
    $world.ForeignPortEnvironments = @{ "8080" = "development" }
    $foreignStep = @((Get-HrmSetupProgressPlan -StateDir (Get-HrmTestStateDir) -Port 8080) | Where-Object { $_.key -eq "port" })[0]
    Assert-HrmEqual "warn" $foreignStep.status "чужое приложение на порту — предупреждение"
    Assert-HrmContains $foreignStep.detail "свободный порт" "план должен честно говорить о подборе свободного порта"
}

Test-Case "CLI только в пользовательском каталоге Docker Desktop: вызовы идут разрешённым путём" {
    # Регрессия на рассогласование «детекция ↔ вызов»: CLI найден только в
    # пользовательском каталоге (в PATH его нет), детекция сообщает
    # «установлен». Раньше фактические вызовы шли голым именем и резолвились
    # через PATH процесса — теперь все они обязаны идти разрешённым путём.
    # На машине пользователя этот сценарий не воспроизводился (там PATH
    # резолвит CLI) — проверяется контракт движка, а не живой сбой.
    $world = New-HrmDockerTestContext
    $cliPath = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\bin\docker.exe"
    Set-HrmDockerOverride @{ desktop = "engine_ready"; engine = $true; cli_path = $cliPath; port_free = $true }
    Assert-HrmEqual $cliPath (Get-HrmDockerCliPath) "резолвер должен вернуть путь из пользовательского каталога"
    Assert-HrmTrue (Test-HrmDockerEngineReady) "готовность Engine по разрешённому CLI"
    Assert-HrmEqual "ok" (Get-HrmDockerState) "диагностика Docker по разрешённому CLI"
    $config = Invoke-HrmCompose -InstallDir (Get-HrmTestInstallDir) -StateDir (Get-HrmTestStateDir) -Command @("config", "--quiet") -IgnoreExitCode
    Assert-HrmEqual 0 $config.ExitCode "compose должен работать через разрешённый путь (диспатч по базовому имени)"
    Assert-HrmTrue (Test-HrmBackendReady -BaseUrl "http://127.0.0.1:8080") "readiness по разрешённому CLI"
    $dockerCalls = @($world.Calls | Where-Object { (Get-HrmMockCliName $_.Name) -eq "docker.exe" })
    Assert-HrmTrue ($dockerCalls.Count -ge 3) "ожидались вызовы CLI: готовность, диагностика, compose"
    foreach ($call in $dockerCalls) {
        Assert-HrmEqual $cliPath $call.Name "вызов ушёл не разрешённым путём — откат на голое имя"
    }
}

Test-Case "CLI не найден нигде: структурированный отказ 127 без исключения, диагностика missing" {
    $world = New-HrmDockerTestContext
    Set-HrmDockerOverride @{ cli_path = "" }
    Assert-HrmFalse ([bool](Get-HrmDockerCliPath)) "резолвер не должен ничего найти"
    $result = Invoke-HrmDocker -Arguments @("--version") -IgnoreExitCode
    Assert-HrmEqual 127 $result.ExitCode "ожидался код 127 («команда не найдена»)"
    Assert-HrmEqual "" $result.Stdout "вывода быть не должно"
    Assert-HrmContains $result.Stderr "CLI контейнеров не найден" "сообщение должно объяснять причину"
    Assert-HrmContains $result.Stderr "Проверенные места" "сообщение должно перечислять проверенные места"
    Assert-HrmContains $result.Stderr "resources" "в перечне мест должен быть каталог установки CLI"
    Assert-HrmFalse (Test-HrmDockerEngineReady) "готовность Engine без CLI — false"
    Assert-HrmEqual "missing" (Get-HrmDockerState) "диагностика без CLI — missing"
    $preflight = Test-HrmDockerCli
    Assert-HrmFalse $preflight.Passed "предполётная проверка должна отказать"
    Assert-HrmContains $preflight.Message "Docker" "отказ предполётной проверки должен быть понятным"
    # Без -IgnoreExitCode контракт Invoke-HrmExternal сохранён: ошибка — исключение
    # с понятным текстом, а не молчаливое продолжение работы без движка.
    $threw = $false
    try { Invoke-HrmDocker -Arguments @("--version") | Out-Null }
    catch { $threw = ($_.Exception.Message -match "CLI контейнеров не найден") }
    Assert-HrmTrue $threw "без -IgnoreExitCode отказ обязан быть исключением с понятным текстом"
    Assert-HrmEqual 0 $world.Calls.Count "реальный CLI не должен вызываться"
}

Test-Case "P5.3: таймаут пробы CLI и демона не выдаётся за «не найден» и «не запущен»" {
    # До исправления (e298202) предполётная проверка смотрела только на код
    # возврата, а таймаут даёт код 124: зависший CLI сообщал «Docker CLI не
    # найден», зависший демон — «Демон Docker не запущен». Человек шёл
    # переустанавливать Docker Desktop вместо того, чтобы дождаться готовности.
    $world = New-HrmDockerTestContext
    # Нужен НАСТОЯЩИЙ Invoke-HrmExternal (механизм ожидания и код 124), поэтому
    # подменяется только создание процесса: двойник «висит».
    Clear-HrmExternalMock
    Clear-HrmProcessLaunchMock
    $env:HRM_EXTERNAL_TIMEOUT_SECONDS = "1"
    $global:HRM_TestKillProcess = New-HrmFakeExternalProcess -ProcessId 9 -Hangs $false -ExitCode 0
    # КАЖДОЙ пробе — свой «висящий» двойник: после остановки дерева первый
    # двойник помечен завершившимся (Kill выставляет exited), и вторая проба не
    # дала бы таймаута, то есть тест проверял бы не то поведение.
    $global:HRM_TestHungCli = New-HrmFakeExternalProcess -ProcessId 424300 -Hangs $true
    $global:HRM_TestHungDaemon = New-HrmFakeExternalProcess -ProcessId 424301 -Hangs $true
    Set-HrmProcessStartMock {
        param($StartInfo)
        if ((Get-HrmMockCliName ([string]$StartInfo.FileName)) -eq "taskkill.exe") { return $global:HRM_TestKillProcess }
        if ([string]$StartInfo.Arguments -like "*info*") { return $global:HRM_TestHungDaemon }
        return $global:HRM_TestHungCli
    }
    $cli = Test-HrmDockerCli
    Assert-HrmFalse $cli.Passed "проба CLI с таймаутом прошла"
    Assert-HrmEqual "docker" ([string]$cli.Name) "имя проверки изменено"
    Assert-HrmNotContains $cli.Message "не найден" "таймаут выдан за отсутствие Docker CLI"
    Assert-HrmContains $cli.Message "не ответил за 1 с" "в отказе не названо ожидание"
    Assert-HrmContains $cli.Message "отчёт для поддержки" "у отказа нет следующего действия"
    $daemon = Test-HrmDockerDaemon
    Assert-HrmFalse $daemon.Passed "проба демона с таймаутом прошла"
    Assert-HrmEqual "daemon" ([string]$daemon.Name) "имя проверки изменено"
    Assert-HrmNotContains $daemon.Message "не запущен" "таймаут выдан за незапущенный демон"
    Assert-HrmContains $daemon.Message "не ответил за 1 с" "в отказе не названо ожидание"
    Assert-HrmContains $daemon.Message "отчёт для поддержки" "у отказа нет следующего действия"
    # Отсутствие CLI по-прежнему отличимо от таймаута: код 127 — это «не найден».
    Clear-HrmProcessStartMock
    Set-HrmDockerOverride @{ cli_path = "" }
    $missing = Test-HrmDockerCli
    Assert-HrmFalse $missing.Passed "отсутствие CLI прошло проверку"
    Assert-HrmContains $missing.Message "не найден" "текст об отсутствии CLI потерян"
    Assert-HrmNotContains $missing.Message "не ответил" "отсутствие CLI выдано за таймаут"
    Remove-Item Env:HRM_EXTERNAL_TIMEOUT_SECONDS -ErrorAction SilentlyContinue
    Assert-HrmEqual 0 $world.Calls.Count "реальный CLI не должен вызываться"
}

Test-Case "пин CLI из обвязки переживает набор переопределений конкретного теста" {
    # Set-HrmDockerOverride заменяет набор целиком и сливает его с базой
    # (Set-HrmDockerOverrideDefaults): без этого тест, задающий свои ключи,
    # терял бы пин пути CLI, резолвер уходил бы в реальные каталоги машины
    # (на раннере там оказался C:\Windows\system32\docker.exe) и прогон зависел
    # бы от того, установлен ли Docker на машине теста.
    $world = New-HrmDockerTestContext
    Set-HrmDockerOverride @{ port_free = $false; free_port = 8081 }
    Assert-HrmEqual "docker.exe" (Get-HrmDockerCliPath) "пин CLI должен накладываться поверх набора теста"
    $null = Invoke-HrmDocker -Arguments @("--version") -IgnoreExitCode
    $calls = @($world.Calls | Where-Object { $_.Name -eq "docker.exe" })
    Assert-HrmEqual 1 $calls.Count "вызов должен идти базовым именем из пина"
}


Write-Host "== Ревизия стабильности (12.10.2026): выбор CLI, плагин compose, факты =="

function New-HrmCliCandidateObject {
    # Кандидат CLI для проверки ПРАВИЛА ВЫБОРА на фиксированных данных: селектор
    # читает только path/file_version/file_last_write/desktop_root_match.
    param([string]$Path, [string]$Version = "", [string]$Stamp = "", [bool]$DesktopMatch = $false, [string]$Source = "docker-desktop-dir")
    return [pscustomobject]@{
        path = $Path; source = $Source; file_version = $Version; file_last_write = $Stamp
        desktop_root_match = $DesktopMatch; install_root = ""; probe_ok = $false
        probe_version = ""; probe_exit_code = 0; probe_stderr = ""
    }
}

Test-Case "D6: корень установки Docker Desktop определяется и для Desktop.exe, и для resources\bin" {
    New-HrmDockerTestContext
    $machineRoot = Get-HrmDockerInstallRoot "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    Assert-HrmEqual "c:\program files\docker\docker" $machineRoot "корень машинной установки определён неверно"
    $machineCli = Get-HrmDockerInstallRoot "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    Assert-HrmEqual $machineRoot $machineCli "CLI из той же установки не сопоставлен с Desktop.exe"
    $userRoot = Get-HrmDockerInstallRoot "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe"
    $userCli = Get-HrmDockerInstallRoot "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe"
    Assert-HrmEqual $userRoot $userCli "корень пользовательской установки определён неверно"
    Assert-HrmTrue ($machineRoot -ne $userRoot) "две разные установки дали один корень"
    Assert-HrmEqual "" (Get-HrmDockerInstallRoot "") "пустой путь должен давать пустой корень"
    # Посторонний файл: корень — его собственный каталог, совпадения с корнем
    # установки Docker Desktop не будет (значит и выбран он не по правилу 1).
    Assert-HrmEqual "c:\windows\system32" (Get-HrmDockerInstallRoot "C:\Windows\system32\notepad.exe") "посторонний файл получил неверный корень"
}

Test-Case "D6: правило выбора CLI — тот же корень Docker Desktop, затем версия, затем дата, затем прежний порядок" {
    New-HrmDockerTestContext
    $machineCli = New-HrmCliCandidateObject -Path "C:\Program Files\Docker\Docker\resources\bin\docker.exe" -Version "24.0.7" -Stamp "2024-01-01T00:00:00.0000000" -DesktopMatch $false
    $userCli = New-HrmCliCandidateObject -Path "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe" -Version "27.3.1" -Stamp "2026-09-01T00:00:00.0000000" -DesktopMatch $true
    # 1) Совпадение с корнем работающего Docker Desktop важнее всего: прежний
    #    резолвер брал ПЕРВЫЙ найденный файл (устаревшая машинная установка),
    #    а детекция считала Docker установленным по пользовательской.
    $choice = Select-HrmDockerCliCandidate -Candidates @($machineCli, $userCli) -DesktopPath "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe"
    Assert-HrmEqual $userCli.path $choice.path "выбран CLI не из той установки, что и Docker Desktop"
    Assert-HrmEqual 1 ([int]$choice.index) "индекс выбранного кандидата неверен"
    Assert-HrmContains ([string]$choice.reason) "тот же корень установки" "правило выбора не объяснено"
    # 2) Корни не совпали (или Docker Desktop не найден) — выбирается наибольшая версия.
    $otherCli = New-HrmCliCandidateObject -Path "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe" -Version "27.3.1" -Stamp "2026-09-01T00:00:00.0000000" -DesktopMatch $false
    $choice2 = Select-HrmDockerCliCandidate -Candidates @($machineCli, $otherCli) -DesktopPath ""
    Assert-HrmEqual $otherCli.path $choice2.path "не выбрана наибольшая версия файла"
    Assert-HrmContains ([string]$choice2.reason) "наибольшая версия" "правило выбора не объяснено"
    # 3) Версии равны — выбирается более свежий файл.
    $sameOld = New-HrmCliCandidateObject -Path "C:\a\docker.exe" -Version "27.3.1" -Stamp "2025-01-01T00:00:00.0000000" -Source "path"
    $sameNew = New-HrmCliCandidateObject -Path "C:\b\docker.exe" -Version "27.3.1" -Stamp "2026-01-01T00:00:00.0000000" -Source "path"
    $choice3 = Select-HrmDockerCliCandidate -Candidates @($sameOld, $sameNew) -DesktopPath ""
    Assert-HrmEqual $sameNew.path $choice3.path "не выбрана самая свежая дата файла"
    Assert-HrmContains ([string]$choice3.reason) "дата файла" "правило выбора не объяснено"
    # 4) Всё неразличимо — прежний порядок поиска (первый кандидат).
    $twinA = New-HrmCliCandidateObject -Path "C:\a\docker.exe" -Version "" -Stamp "" -Source "path"
    $twinB = New-HrmCliCandidateObject -Path "C:\b\docker.exe" -Version "" -Stamp "" -Source "path"
    $choice4 = Select-HrmDockerCliCandidate -Candidates @($twinA, $twinB) -DesktopPath ""
    Assert-HrmEqual $twinA.path $choice4.path "нарушен прежний порядок поиска"
    Assert-HrmContains ([string]$choice4.reason) "порядок поиска" "правило выбора не объяснено"
    # 5) Кандидатов нет — пустой путь и понятная причина (а не исключение).
    $choice5 = Select-HrmDockerCliCandidate -Candidates @() -DesktopPath ""
    Assert-HrmEqual "" ([string]$choice5.path) "пустой список кандидатов должен давать пустой путь"
    Assert-HrmContains ([string]$choice5.reason) "не найден" "причина отсутствия CLI не объяснена"
}

Test-Case "D6: сравнение версий — числовые разряды, предпоиск, пустая версия" {
    New-HrmDockerTestContext
    Assert-HrmEqual 1 (Compare-HrmCliVersion "27.3.1" "24.0.7") "числовые разряды сравниваются как числа"
    Assert-HrmEqual -1 (Compare-HrmCliVersion "2.23.9" "2.24") "2.23.9 должна быть меньше 2.24"
    Assert-HrmEqual 0 (Compare-HrmCliVersion "2.24.0" "2.24") "2.24.0 и 2.24 эквивалентны"
    Assert-HrmEqual 1 (Compare-HrmCliVersion "5.1.0" "2.24") "прежнее правило (major>=2 и minor>=24) отвергло бы Compose 5.1.0"
    Assert-HrmEqual 1 (Compare-HrmCliVersion "v2.29.7" "2.24") "префикс v не мешает сравнению"
    Assert-HrmEqual 1 (Compare-HrmCliVersion "24.0.7" "24.0.7-beta") "релиз должен быть больше предпоиска"
    Assert-HrmEqual -1 (Compare-HrmCliVersion "" "2.24") "пустая версия меньше любой"
    Assert-HrmEqual 0 (Compare-HrmCliVersion "" "") "две пустые версии равны"
}

Test-Case "D6: резолвер перечисляет всех кандидатов и не принимает путь со стороны" {
    $world = New-HrmDockerTestContext
    $fakeRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM-cli-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    $fakeBin = Join-Path $fakeRoot "path-bin"
    $fakeProgramFiles = Join-Path $fakeRoot "ProgramFiles"
    $fakeProgramFilesX86 = Join-Path $fakeRoot "ProgramFilesX86"
    $fakeLocalAppData = Join-Path $fakeRoot "LocalAppData"
    $fakeMachineBin = Join-Path $fakeProgramFiles "Docker\Docker\resources\bin"
    $fakeUserBin = Join-Path $fakeLocalAppData "Programs\DockerDesktop\resources\bin"
    $fakeCli = Join-Path $fakeBin "docker.exe"
    $fakeMachineCli = Join-Path $fakeMachineBin "docker.exe"
    $fakeUserCli = Join-Path $fakeUserBin "docker.exe"
    New-Item -ItemType Directory -Path @($fakeBin, $fakeMachineBin, $fakeUserBin, $fakeProgramFilesX86) -Force | Out-Null
    Set-Content -Path $fakeCli -Value "fake PATH CLI" -Encoding ASCII
    Set-Content -Path $fakeMachineCli -Value "fake machine CLI" -Encoding ASCII
    Set-Content -Path $fakeUserCli -Value "fake user CLI" -Encoding ASCII
    $originalPath = [string]$env:PATH
    $originalProgramFiles = $env:ProgramFiles
    $originalProgramFilesX86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
    $originalLocalAppData = $env:LOCALAPPDATA
    try {
        # Снимаем пин CLI из обвязки и подменяем все источники каталогов:
        # кандидаты из Docker Desktop и PATH должны быть только из этого temp-мок-мира.
        Clear-HrmDockerOverride
        Reset-HrmDockerCliChoiceCache
        $env:ProgramFiles = $fakeProgramFiles
        [Environment]::SetEnvironmentVariable("ProgramFiles(x86)", $fakeProgramFilesX86, "Process")
        $env:LOCALAPPDATA = $fakeLocalAppData
        $env:PATH = $fakeBin
        $paths = @(Get-HrmDockerCliCandidatePaths)
        foreach ($expectedCli in @($fakeCli, $fakeMachineCli, $fakeUserCli)) {
            Assert-HrmTrue ($paths -contains $expectedCli) ("кандидат не найден резолвером: " + $expectedCli)
        }
        # -Candidate возвращает путь только если он в списке кандидатов того же резолвера:
        # имя внешней команды по-прежнему не может прийти из произвольного места.
        Assert-HrmEqual $fakeCli (Get-HrmDockerCliPath -Candidate $fakeCli) "свой кандидат отклонён"
        Assert-HrmEqual "" (Get-HrmDockerCliPath -Candidate (Join-Path $fakeBin "evil.exe")) "принят путь, которого нет среди кандидатов"
        Assert-HrmEqual "" (Get-HrmDockerCliPath -Candidate "C:\Windows\system32\cmd.exe") "принят произвольный системный путь"
        $facts = Get-HrmDockerCliFacts
        Assert-HrmEqual 3 ([int]$facts.candidate_count) "факты должны содержать ровно кандидатов тестового мира"
        Assert-HrmTrue ([bool][string]$facts.chosen_reason) "правило выбора не зафиксировано в фактах"
        Assert-HrmTrue (@($facts.searched_dirs).Count -ge 1) "в фактах нет перечня проверенных каталогов"
        $listed = @($facts.candidates | ForEach-Object { [string]$_.path })
        foreach ($expectedCli in @($fakeCli, $fakeMachineCli, $fakeUserCli)) {
            Assert-HrmTrue ($listed -contains $expectedCli) ("кандидат не попал в факты для журнала: " + $expectedCli)
        }
    }
    finally {
        $env:PATH = $originalPath
        if ($null -eq $originalProgramFiles) { Remove-Item Env:ProgramFiles -ErrorAction SilentlyContinue }
        else { $env:ProgramFiles = [string]$originalProgramFiles }
        [Environment]::SetEnvironmentVariable("ProgramFiles(x86)", $originalProgramFilesX86, "Process")
        if ($null -eq $originalLocalAppData) { Remove-Item Env:LOCALAPPDATA -ErrorAction SilentlyContinue }
        else { $env:LOCALAPPDATA = [string]$originalLocalAppData }
        Reset-HrmDockerCliChoiceCache
        Set-HrmDockerOverride @{}
        Remove-Item -Path $fakeRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    Assert-HrmEqual "docker.exe" (Get-HrmDockerCliPath) "пин CLI из обвязки не восстановлен"
}

Test-Case "D6: проба версии CLI идёт через единственную точку внешних команд и даёт факт для журнала" {
    $world = New-HrmDockerTestContext
    $probe = Invoke-HrmDockerCliVersionProbe -CandidatePath "docker.exe" -TimeoutSeconds 5
    Assert-HrmTrue ([bool]$probe.ok) "проба версии не прошла на мок-мире"
    Assert-HrmEqual "27.3.1" ([string]$probe.version) "версия из ответа CLI не разобрана"
    Assert-HrmEqual 0 ([int]$probe.exit_code) "код возврата пробы"
    $calls = @($world.Calls | Where-Object { $_.Args -contains "--version" })
    Assert-HrmEqual 1 $calls.Count "проба обязана быть одним вызовом через единственную точку"
    $facts = Get-HrmDockerCliFacts -Probe
    Assert-HrmTrue ([bool]$facts.cli_probe_ok) "факты не содержат результата пробы"
    Assert-HrmEqual "27.3.1" ([string]$facts.cli_version_probed) "факты не содержат версию из пробы"
}

Test-Case "D5: классификация ответа compose — CLI не запустился / плагина нет / версия не разобрана / версия стара / другая ошибка" {
    New-HrmDockerTestContext
    $cliMissing = Get-HrmComposeVersionVerdict -ExitCode 127 -Stdout "" -Stderr ""
    Assert-HrmEqual "cli_missing" ([string]$cliMissing.verdict) "код 127 не распознан как «CLI не запустился»"
    $pluginMissing = Get-HrmComposeVersionVerdict -ExitCode 1 -Stdout "" -Stderr "docker: 'compose' is not a docker command. See 'docker --help'."
    Assert-HrmEqual "plugin_missing" ([string]$pluginMissing.verdict) "ответ CLI без плагина не распознан"
    $unparsable = Get-HrmComposeVersionVerdict -ExitCode 0 -Stdout "Docker Compose version unknown" -Stderr ""
    Assert-HrmEqual "version_unparsable" ([string]$unparsable.verdict) "неразобранная версия не распознана"
    Assert-HrmContains ([string]$unparsable.detail) "unknown" "в детали не попал ответ CLI"
    $old = Get-HrmComposeVersionVerdict -ExitCode 0 -Stdout "2.20.1" -Stderr ""
    Assert-HrmEqual "version_old" ([string]$old.verdict) "старая версия не распознана"
    Assert-HrmEqual "2.20.1" ([string]$old.version) "старая версия не сохранена"
    $other = Get-HrmComposeVersionVerdict -ExitCode 1 -Stdout "" -Stderr "error during connect: daemon is not running"
    Assert-HrmEqual "probe_failed" ([string]$other.verdict) "ошибка демона смешана с отсутствием плагина"
    Assert-HrmContains ([string]$other.detail) "daemon is not running" "в детали не попал stderr"
    $timedOut = Get-HrmComposeVersionVerdict -ExitCode 124 -Stdout "" -Stderr "" -TimedOut $true
    Assert-HrmEqual "probe_failed" ([string]$timedOut.verdict) "таймаут пробы не распознан"
    Assert-HrmContains ([string]$timedOut.detail) "не завершилась" "таймаут не объяснён"
    $ok = Get-HrmComposeVersionVerdict -ExitCode 0 -Stdout "2.29.7" -Stderr ""
    Assert-HrmEqual "ok" ([string]$ok.verdict) "подходящая версия не принята"
    Assert-HrmEqual "2.29.7" ([string]$ok.version) "версия не сохранена"
    $future = Get-HrmComposeVersionVerdict -ExitCode 0 -Stdout "v5.1.0" -Stderr ""
    Assert-HrmEqual "ok" ([string]$future.verdict) "Compose 5.x отвергнут правилом major>=2 и minor>=24"
}

Test-Case "D5: отказ по compose называет причину и проверенные каталоги плагинов; проверка не ослаблена" {
    $world = New-HrmDockerTestContext
    Assert-HrmFalse (Test-HrmDockerOverrideKey "compose_plugin_files") "список файлов плагина не должен подставляться override-defaults"
    $world.ComposePluginMissing = $true
    # Пустой список задаётся явно: D5 не должен читать Docker Desktop на машине теста.
    Set-HrmDockerOverride @{ compose_plugin_files = @() }
    $result = Test-HrmComposeVersion
    Assert-HrmFalse ([bool]$result.Passed) "проверка compose ослаблена до предупреждения"
    Assert-HrmEqual "plugin_missing" ([string]$result.Reason) "причина отказа не классифицирована"
    Assert-HrmContains ([string]$result.Message) "Плагин Docker Compose" "отказ не называет причину"
    Assert-HrmContains ([string]$result.Message) "cli-plugins" "отказ не перечисляет каталоги плагинов"
    Assert-HrmContains ([string]$result.Message) ".docker" "отказ не называет пользовательский каталог плагинов"
    # Честность про машинный каталог: Docker CLI 29.2.0+ его не ищет
    # (GHSA-p436-gjf2-799p) — совет «положите туда плагин» был бы нерабочим.
    Assert-HrmContains ([string]$result.Message) "29.2.0" "не указано, что новый CLI не ищет машинный каталог"
    Assert-HrmFalse ([bool]$result.PluginFound) "флаг найденного плагина недостоверен"
    Assert-HrmEqual 1 ([int]$result.ExitCode) "код возврата пробы не сохранён"
    Assert-HrmTrue (@($result.PluginDirs).Count -ge 1) "в результате нет перечня каталогов плагинов"

    # Сам CLI всё ещё сообщает plugin_missing даже при наличии файла плагина:
    # PluginFound описывает только файловый факт и не должен делать probe Passed.
    Set-HrmDockerOverride @{ compose_plugin_files = @("docker-compose.exe") }
    $filePresentResult = Test-HrmComposeVersion
    Assert-HrmTrue ([bool]$filePresentResult.PluginFound) "файл плагина не отразился в PluginFound"
    Assert-HrmFalse ([bool]$filePresentResult.Passed) "наличие файла ошибочно превратило неуспешную команду compose в успех"
    Assert-HrmEqual "plugin_missing" ([string]$filePresentResult.Reason) "файл плагина изменил причину отказа CLI"
    Assert-HrmEqual 1 ([int]$filePresentResult.ExitCode) "код возврата CLI потерян при наличии файла плагина"
    $overriddenFiles = @($filePresentResult.PluginDirs | ForEach-Object { $_.files } | ForEach-Object { [string]$_ })
    Assert-HrmTrue ($overriddenFiles -contains "docker-compose.exe") "список файлов override не дошёл до фактов PluginDirs"

    # Версия compose ниже требуемой — тоже отказ (не предупреждение).
    $world.ComposePluginMissing = $false
    Set-HrmDockerOverride @{ compose_plugin_files = @() }
    $world.ComposeVersion = "2.20.1"
    $oldResult = Test-HrmComposeVersion
    Assert-HrmFalse ([bool]$oldResult.Passed) "старая версия compose принята"
    Assert-HrmEqual "version_old" ([string]$oldResult.Reason) "старая версия не классифицирована"
    Assert-HrmContains ([string]$oldResult.Message) "2.24" "в отказе нет требуемой версии"
    Assert-HrmContains ([string]$oldResult.Message) "2.20.1" "в отказе нет найденной версии"
    # Подходящая версия — проверка пройдена, версия видна в фактах.
    $world.ComposeVersion = "v2.29.7"
    $okResult = Test-HrmComposeVersion
    Assert-HrmTrue ([bool]$okResult.Passed) "подходящая версия compose отвергнута"
    Assert-HrmEqual "ok" ([string]$okResult.Reason) "verdict успешной проверки"
    Assert-HrmEqual "2.29.7" ([string]$okResult.Version) "версия не сохранена в результате"
    Assert-HrmContains ([string]$okResult.Message) "2.29.7" "версия не видна в сообщении (её читает отчёт о хосте)"
}

Write-Host ("Docker Desktop: {0} пройдено, {1} провалено" -f $global:HRM_TestPassed, $global:HRM_TestFailed)

Test-Case "PORT: сравнение версии Compose учитывает patch 2.24.4 и prerelease" {
    Assert-HrmEqual -1 (Compare-HrmCliVersion "2.24" "2.24.4")
    Assert-HrmEqual -1 (Compare-HrmCliVersion "2.24.3" "2.24.4")
    Assert-HrmEqual -1 (Compare-HrmCliVersion "2.24.4-beta" "2.24.4")
    Assert-HrmEqual 0 (Compare-HrmCliVersion "v2.24.4" "2.24.4")
    Assert-HrmEqual 1 (Compare-HrmCliVersion "5.5.0" "2.24.4")
}

foreach ($version in @("2.24.0", "2.24.3", "2.24.4-beta", "2.24.4", "5.5.0")) {
    Test-Case "PORT: preflight Compose $version проверяет поддержку !override" {
        $world = New-HrmDockerTestContext
        Set-HrmDockerOverride @{ compose_plugin_files = @() }
        $world.ComposeVersion = $version
        $result = Test-HrmComposeVersion
        if ($version -eq "2.24.4" -or $version -eq "5.5.0") {
            Assert-HrmTrue $result.Passed
            Assert-HrmEqual "ok" $result.Reason
        } else {
            Assert-HrmFalse $result.Passed
            Assert-HrmEqual "version_old" $result.Reason
            Assert-HrmContains $result.Message "2.24.4"
            Assert-HrmContains $result.Message "!override"
        }
    }
}
