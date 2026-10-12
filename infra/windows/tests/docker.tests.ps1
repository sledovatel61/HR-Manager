# Поведенческие тесты жизненного цикла Docker Desktop (0.15.0).
# Реальная машина не затрагивается: Docker/процессы/скачивание подменяются
# моками (Set-HrmDockerOverride, Set-HrmProcessLaunchMock, Set-HrmDownloadMock,
# Set-HrmExternalMock, Set-HrmHttpMock).

param([string]$HarnessPath = $PSScriptRoot)

. (Join-Path $HarnessPath "test-harness.ps1")

Write-Host "== Docker Desktop: обнаружение, установка, ожидание Engine =="

function New-HrmDockerTestContext {
    # Изолированные каталоги + мир моков + базовые предусловия.
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    $env:HRM_DESKTOP_DIR = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM-desktop-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Path $env:HRM_DESKTOP_DIR -Force | Out-Null
    return $world
}

Test-Case "Docker Desktop не установлен: prepare не делает вид, что Docker есть, и предлагает установку" {
    $world = New-HrmDockerTestContext
    Set-HrmDockerOverride @{ desktop = "not_installed"; desktop_path = ""; wsl = "ok"; virtualization = "enabled"; free_mb = 20480; port_free = $true }
    $state = Get-HrmTestStateDir
    $result = Invoke-HrmDockerPrepare -InstallDir (Get-HrmTestInstallDir) -StateDir $state -Port 8080
    Assert-HrmFalse $result.ok "prepare не должен считаться успешным без Docker"
    Assert-HrmEqual "docker_missing" $result.state "неверное состояние"
    Assert-HrmTrue $result.needs_install "должно предлагаться установка Docker"
    Assert-HrmContains $result.message "Docker Desktop" "сообщение должно быть понятным"
    # Бесконечного цикла нет: один проход, состояние зафиксировано.
    $steps = Get-HrmSetupProgressPlan -StateDir $state
    $dockerStep = @($steps | Where-Object { $_.key -eq "docker_app" })[0]
    Assert-HrmEqual "fail" $dockerStep.status "шаг «Ищем Docker Desktop» должен быть fail"
}

Test-Case "Docker Desktop установлен, но не запущен: движок запускает его и ждёт Engine" {
    $world = New-HrmDockerTestContext
    # Счётчик запусков принадлежит тесту: движок фиксирует действия в журнале и
    # через docker-pending, а не в глобальных переменных, поэтому инициализируем
    # его сами (иначе StrictMode падает на несуществующей переменной).
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

Test-Case "установка Docker Desktop: официальный адрес, проверка подписи, UAC и лицензия за пользователя не принимаются" {
    $world = New-HrmDockerTestContext
    Set-HrmDownloadMock {
        param($Uri, $Destination)
        $global:HRM_DockerDownloads += $Uri
        Set-Content -Path $Destination -Value "stub-installer" -Encoding ASCII
        return $Destination
    }
    $global:HRM_DockerDownloads = @()
    Set-HrmProcessLaunchMock {
        param($FilePath, $Arguments, $Mode, $LogFile)
        $global:HRM_DockerLaunches += [pscustomobject]@{ Args = @($Arguments); Mode = $Mode }
        return [pscustomobject]@{ Started = $true; CanceledByUser = $false; ExitCode = 0 }
    }
    $global:HRM_DockerLaunches = @()
    Set-HrmDockerOverride @{ desktop = "not_installed"; desktop_path = ""; install_signature = $true; reboot = $false }
    $state = Get-HrmTestStateDir
    $result = Install-HrmDockerDesktop -StateDir $state -Interactive -AllowSilent
    Assert-HrmEqual "installed" $result.status "установка должна считаться успешной при нулевом коде"
    Assert-HrmEqual 1 $global:HRM_DockerDownloads.Count "должен быть ровно один скачанный установщик"
    Assert-HrmContains $global:HRM_DockerDownloads[0] "desktop.docker.com" "установщик берётся не с официального адреса Docker"
    foreach ($launch in $global:HRM_DockerLaunches) {
        Assert-HrmNotContains ($launch.Args -join " ") "--accept-license" "лицензия Docker не принимается автоматически"
    }
    Assert-HrmTrue (Test-Path (Get-HrmPendingDockerOperationFile $state)) "должна быть зафиксирована автоматическая продолжение после установки"
    $pending = Get-HrmPendingDockerOperation $state
    Assert-HrmEqual "resume" ([string]$pending.kind) "вид отложенной операции"
}

Test-Case "установщик Docker с непроверенной подписью отклоняется (fail-closed)" {
    New-HrmDockerTestContext
    Set-HrmDownloadMock {
        param($Uri, $Destination)
        Set-Content -Path $Destination -Value "stub-installer" -Encoding ASCII
        return $Destination
    }
    Set-HrmDockerOverride @{ desktop = "not_installed"; desktop_path = ""; install_signature = $false }
    $state = Get-HrmTestStateDir
    $result = Install-HrmDockerDesktop -StateDir $state -Interactive
    Assert-HrmEqual "failed" $result.status "неподписанный установщик должен быть отклонён"
    Assert-HrmContains $result.message "подписи" "сообщение должно объяснять причину"
}

Test-Case "издатель установщика проверяется точно: «Docker» в имени не делает сертификат доверенным" {
    # P2 ревью раунда 12. Раньше проверка была подстрокой «Docker», и любой
    # корректно подписанный файл со словом Docker в subject считался доверенным.
    Assert-HrmFalse (Test-HrmDockerPublisherSubject "CN=Fake Docker Signer, O=Evil Corp, C=US") `
        "поддельный издатель со словом Docker в имени прошёл проверку"
    Assert-HrmFalse (Test-HrmDockerPublisherSubject "CN=Dockerizer LLC, O=Dockerizer, C=US") `
        "посторонний издатель со словом Docker в имени прошёл проверку"
    Assert-HrmFalse (Test-HrmDockerPublisherSubject "CN=Docker Inc, O=Evil Corp, C=US") `
        "совпал только CN: издатель не должен приниматься"
    Assert-HrmFalse (Test-HrmDockerPublisherSubject "CN=Evil Corp, O=Docker Inc") `
        "совпал только O: издатель не должен приниматься"
    Assert-HrmFalse (Test-HrmDockerPublisherSubject "") "пустой subject должен считаться недоверенным"
    Assert-HrmFalse (Test-HrmDockerPublisherSubject "CN=Docker Inc, CN=Docker Inc, O=Docker Inc") `
        "два атрибута CN должны считаться недоверенными (неоднозначная строка)"
}

Test-Case "зафиксированный издатель Docker принимается по точному subject и по отпечатку" {
    $subjects = @(Get-HrmDockerPublisherSubjects)
    Assert-HrmTrue ($subjects.Count -ge 1) `
        "список издателей пуст: официальный установщик Docker не будет принят (значения снимаются аудитом CI)"
    foreach ($subject in $subjects) {
        Assert-HrmTrue (Test-HrmDockerPublisherSubject $subject) ("зафиксированный издатель не проходит проверку: " + $subject)
    }
    # Значения из аудита CI (прогон 37653666837): subject настоящего установщика
    # и его отпечаток. True-случай не выдуман — это реальная подпись.
    $realSubject = "CN=Docker Inc, O=Docker Inc, L=Palo Alto, S=California, C=US, SERIALNUMBER=4817464, " +
        "OID.2.5.4.15=Private Organization, OID.1.3.6.1.4.1.311.60.2.1.2=Delaware, OID.1.3.6.1.4.1.311.60.2.1.3=US"
    Assert-HrmTrue (Test-HrmDockerPublisherSubject $realSubject) "настоящий subject установщика Docker не принят"
    # Порядок атрибутов и лишние пробелы не должны ломать проверку.
    $reordered = "O=Docker Inc, CN=Docker Inc, C=US, L=Palo Alto,  S=California"
    Assert-HrmTrue (Test-HrmDockerPublisherSubject $reordered) "перестановка атрибутов ломает проверку издателя"
    # Отпечаток сертификата — второй, независимый способ (переживает смену subject).
    Assert-HrmTrue (Test-HrmDockerPublisherSubject -Subject "CN=Кто-то другой" `
            -Thumbprints @("b6bd29272b07ad4d0f1322a739499d67ca3bac3f")) "отпечаток сертификата не принят"
    Assert-HrmFalse (Test-HrmDockerPublisherSubject -Subject "CN=Кто-то другой" `
            -Thumbprints @("0000000000000000000000000000000000000000")) "чужой отпечаток принят"
}

Test-Case "проверка издателя end-to-end: строка-subject проходит через настоящее решение" {
    # Переопределение install_signature строкой заменяет только чтение подписи,
    # а решение об издателе остаётся настоящим (см. Test-HrmDockerInstallerTrusted).
    Set-HrmDockerOverride @{ install_signature = "CN=Fake Docker Signer, O=Evil Corp" }
    $fake = Test-HrmDockerInstallerTrusted -Path "C:\нет-такого-файла.exe"
    Assert-HrmFalse $fake.trusted "поддельный издатель прошёл end-to-end проверку"
    Assert-HrmContains $fake.reason "не Docker Inc" "сообщение об отказе должно называть причину"
    Set-HrmDockerOverride @{ install_signature = "CN=Docker Inc, O=Docker Inc, C=US" }
    $real = Test-HrmDockerInstallerTrusted -Path "C:\нет-такого-файла.exe"
    Assert-HrmTrue $real.trusted "настоящий издатель отвергнут"
    Set-HrmDockerOverride @{ install_signature = $false }
    $unsigned = Test-HrmDockerInstallerTrusted -Path "C:\нет-такого-файла.exe"
    Assert-HrmFalse $unsigned.trusted "прежний смысл override = $false не сохранён"
}

Test-Case "пользователь отменил UAC: это штатный результат с понятным текстом, а не crash-loop" {
    New-HrmDockerTestContext
    Set-HrmDownloadMock {
        param($Uri, $Destination)
        Set-Content -Path $Destination -Value "stub-installer" -Encoding ASCII
        return $Destination
    }
    Set-HrmProcessLaunchMock {
        param($FilePath, $Arguments, $Mode, $LogFile)
        return [pscustomobject]@{ Started = $false; CanceledByUser = $true; ExitCode = -1 }
    }
    Set-HrmDockerOverride @{ desktop = "not_installed"; desktop_path = ""; install_signature = $true; reboot = $false }
    $state = Get-HrmTestStateDir
    $result = Install-HrmDockerDesktop -StateDir $state -Interactive -AllowSilent
    Assert-HrmEqual "uac_declined" $result.status "отмена UAC — не ошибка установки"
    Assert-HrmContains $result.message "разрешение Windows" "сообщение должно объяснять UAC"
}

Test-Case "после установки Docker с требованием перезагрузки фиксируется автоматическое продолжение" {
    New-HrmDockerTestContext
    Set-HrmDownloadMock {
        param($Uri, $Destination)
        Set-Content -Path $Destination -Value "stub-installer" -Encoding ASCII
        return $Destination
    }
    Set-HrmProcessLaunchMock {
        param($FilePath, $Arguments, $Mode, $LogFile)
        return [pscustomobject]@{ Started = $true; CanceledByUser = $false; ExitCode = 0 }
    }
    Set-HrmDockerOverride @{ desktop = "not_installed"; desktop_path = ""; install_signature = $true; reboot = $true }
    $state = Get-HrmTestStateDir
    $result = Install-HrmDockerDesktop -StateDir $state -Interactive -AllowSilent
    Assert-HrmTrue $result.needs_reboot "перезагрузка должна быть зафиксирована"
    $pending = Get-HrmPendingDockerOperation $state
    Assert-HrmEqual "reboot" ([string]$pending.kind) "вид отложенной операции после перезагрузки"
    $prepare = Invoke-HrmDockerPrepare -InstallDir (Get-HrmTestInstallDir) -StateDir $state -Port 8080
    Assert-HrmEqual "reboot_required" $prepare.state "prepare должен честно сообщить о перезагрузке"
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
    Set-HrmDockerOverride @{ desktop = "engine_ready"; engine = $true; port_free = $false; free_mb = 20480; wsl = "ok"; virtualization = "enabled" }
    $free = Find-HrmFreePort -StartPort 8080
    $plan = Get-HrmSetupProgressPlan -StateDir (Get-HrmTestStateDir) -Port 8080
    $portStep = @($plan | Where-Object { $_.key -eq "port" })[0]
    Assert-HrmTrue ($portStep.status -eq "warn" -or $portStep.status -eq "ok") "шаг порта должен быть warn/ok"
    Assert-HrmTrue ($free -ge 0) "подбор порта должен завершиться"
}

Test-Case "права: обычного пользователя достаточно, UAC нужен только для Docker и LAN" {
    New-HrmDockerTestContext
    Set-HrmDockerOverride @{ desktop = "engine_ready"; engine = $true; admin = $false; free_mb = 20480; wsl = "ok"; virtualization = "enabled" }
    $plan = Get-HrmSetupProgressPlan -StateDir (Get-HrmTestStateDir)
    $rightsStep = @($plan | Where-Object { $_.key -eq "rights" })[0]
    Assert-HrmEqual "ok" $rightsStep.status "работа без прав администратора — норма"
    Assert-HrmContains $rightsStep.detail "разрешение Windows" "нужно честно объяснить, когда появится UAC"
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

Test-Case "установка Docker: инструкция для пользователя без команд и без сторонних источников" {
    $guide = (Get-HrmDockerInstallGuide) -join "`n"
    Assert-HrmContains $guide "docker.com" "нужна официальная ссылка"
    foreach ($forbidden in @("powershell", "winget", "choco", "msiexec")) {
        Assert-HrmNotContains $guide $forbidden ("лишняя техническая команда: " + $forbidden)
    }
    Assert-HrmContains $guide "лицензи" "принятие лицензии Docker — действие пользователя"
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

Write-Host ("Docker Desktop: {0} пройдено, {1} провалено" -f $global:HRM_TestPassed, $global:HRM_TestFailed)
