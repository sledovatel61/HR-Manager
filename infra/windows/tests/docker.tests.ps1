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

Write-Host ("Docker Desktop: {0} пройдено, {1} провалено" -f $global:HRM_TestPassed, $global:HRM_TestFailed)
