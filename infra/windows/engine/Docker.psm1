# Жизненный цикл Docker Desktop для пилота: обнаружение, официальная установка
# по явному согласию пользователя, запуск, ожидание готовности Docker Engine,
# проверка WSL2 и аппаратной виртуализации, свободного места, порта и прав.
#
# ЧЕСТНОСТЬ ВМЕСТО АВТОМАТИЗАЦИИ ЛЮБОЙ ЦЕНОЙ:
#   - движок НИКОГДА не делает вид, что Docker установлен;
#   - НИКОГДА не запускает бесконечный цикл перезапуска: у каждой операции есть
#     таймаут и понятное русское сообщение;
#   - установка Docker Desktop выполняется ТОЛЬКО официальным установщиком с
#     сайта docker.com и требует согласия пользователя (UAC + лицензия Docker);
#   - лицензионное соглашение Docker Desktop принимает ЧЕЛОВЕК: движок не
#     передаёт --accept-license за пользователя;
#   - никаких сторонних бинарников и зеркал;
#   - перезагрузка Windows и включение виртуализации в BIOS — только человек.
#
# Все внешние команды идут через Invoke-HrmExternal (см. Common.psm1), запуск
# программ — через Start-HrmDetached/Start-HrmElevatedAndWait (тоже Common).
# Тесты подменяют результаты через Set-HrmDockerOverride/Set-HrmExternalMock/
# Set-HrmProcessLaunchMock/Set-HrmDownloadMock и не трогают реальную машину.

Set-StrictMode -Version 2.0

$script:DockerOverride = $null

$script:DockerInstallerUrl = "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"
$script:DockerDownloadPage = "https://www.docker.com/products/docker-desktop/"
# Издатель официального установщика Docker Desktop. Раньше здесь была подстрока
# "Docker", и проверка пропускала любой корректно подписанный файл, у которого
# слово Docker встречается в subject (например "CN=Fake Docker Signer, O=Evil").
# Теперь допускается только точное совпадение с одной из зафиксированных строк
# или отпечаток сертификата из списка ниже.
#
# Значения сняты с настоящего файла Docker Desktop Installer.exe
# (635 493 296 байт с https://desktop.docker.com/win/main/amd64/) на Windows-раннере
# CI скриптом infra/windows/tools/Audit-HrmDockerPublisher.ps1, прогон 37653666837:
#   signed_status = Valid
#   subject = CN=Docker Inc, O=Docker Inc, L=Palo Alto, S=California, C=US,
#             SERIALNUMBER=4817464, OID.2.5.4.15=Private Organization,
#             OID.1.3.6.1.4.1.311.60.2.1.2=Delaware, OID.1.3.6.1.4.1.311.60.2.1.3=US
#   thumbprint (SHA1) = b6bd29272b07ad4d0f1322a739499d67ca3bac3f
#   срок действия сертификата: до 2027-06-25
#   SHA256-хеш сертификата (64 hex) записан в docs/PILOT_FINAL_REPORT.md: в самом
#   модуле его держать нельзя — статический контракт запрещает 64 hex-символа
#   подряд в engine/*.psm1 (так выглядит утечка приватного ключа).
# Обратите внимание: у настоящего издателя CN=Docker Inc — БЕЗ точки. Именно
# поэтому проверка подстрокой «Docker» была опасна: под неё подходил любой
# корректно подписанный сертификат со словом Docker в имени.
#
# Правило доверия (все три условия обязательны, см. Test-HrmDockerInstallerTrusted):
#   1) Windows подтверждает подпись (status = Valid, доверенная цепочка);
#   2) отпечаток сертификата совпадает с одним из зафиксированных, ИЛИ
#   3) subject совпадает с зафиксированным ИЛИ его CN и O равны ровно «Docker Inc»
#      (сравнение по разобранным атрибутам, посимвольно, без подстрок).
#
# Когда Docker сменит сертификат (после 2027-06-25 или раньше): прогнать аудит
# (ручной запуск CI: Actions → CI → Run workflow → docker_audit = true), он
# напечатает subject и отпечатки новой подписи, добавить их сюда рядом со старыми
# и только потом собирать релиз. Пока значения не обновлены, движок честно
# откажется ставить Docker автоматически (fail-closed) и предложит официальный
# сайт — это правильнее, чем молча доверять неизвестному сертификату.
$script:DockerPublisherSubjects = @(
    "CN=Docker Inc, O=Docker Inc, L=Palo Alto, S=California, C=US, SERIALNUMBER=4817464, OID.2.5.4.15=Private Organization, OID.1.3.6.1.4.1.311.60.2.1.2=Delaware, OID.1.3.6.1.4.1.311.60.2.1.3=US"
)
# Отпечатки: SHA1 Thumbprint (40 hex) и проверка через него. SHA256-хеш
# сертификата из аудита хранится в отчёте (в модуле его держать нельзя: 64 hex
# подряд запрещены статическим контрактом «нет hex-литералов в движке»).
$script:DockerPublisherThumbprints = @("b6bd29272b07ad4d0f1322a739499d67ca3bac3f")
# Имя издателя, которое обязано стоять в CN и O сертификата.
$script:DockerPublisherRequiredName = "Docker Inc"
$script:DockerRequiredComposeMinor = 24

# --- Тестовые переопределения -------------------------------------------------

function Set-HrmDockerOverride {
    # Ключи: desktop, cli, engine, wsl, virtualization, admin, reboot, port_free,
    # free_mb, desktop_path, install_result, install_signature.
    param([hashtable]$State)
    $script:DockerOverride = $State
}

function Clear-HrmDockerOverride { $script:DockerOverride = $null }

function Get-HrmDockerOverrideValue {
    param([string]$Key)
    if ($null -eq $script:DockerOverride) { return $null }
    if (-not $script:DockerOverride.ContainsKey($Key)) { return $null }
    return $script:DockerOverride[$Key]
}

# --- Обнаружение записей реестра --------------------------------------------

function Get-HrmRegistryString {
    # Чтение строкового значения реестра; отсутствие ключа — не ошибка.
    param([string]$Path, [string]$Name)
    try {
        $item = Get-ItemProperty -Path $Path -Name $Name -ErrorAction Stop
        $value = $item.$Name
        if ($null -eq $value) { return "" }
        return [string]$value
    }
    catch { return "" }
}

function Get-HrmDockerDesktopPath {
    # Путь к Docker Desktop.exe: реестр установки, затем стандартные каталоги.
    $override = Get-HrmDockerOverrideValue "desktop_path"
    if ($null -ne $override) { return [string]$override }
    $candidates = @()
    $candidates += (Get-HrmRegistryString "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Docker Desktop.exe" "(default)")
    foreach ($uninstallKey in @(
            "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Docker Desktop",
            "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Docker Desktop",
            "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Docker Desktop"
        )) {
        $display = Get-HrmRegistryString $uninstallKey "DisplayName"
        if ($display -match "Docker") {
            $icon = Get-HrmRegistryString $uninstallKey "DisplayIcon"
            if ($icon) { $candidates += ($icon -replace ',\d+$', '').Trim('"') }
            $candidates += (Get-HrmRegistryString $uninstallKey "InstallLocation")
        }
    }
    if ($env:ProgramFiles) { $candidates += (Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe") }
    if ($env:LOCALAPPDATA) { $candidates += (Join-Path $env:LOCALAPPDATA "Docker\Docker Desktop.exe") }
    foreach ($candidate in $candidates) {
        if (-not $candidate) { continue }
        $path = [string]$candidate
        if ($path -match "\.exe$" -and (Test-Path $path)) { return $path }
        if ($path -notmatch "\.exe$") {
            $exe = Join-Path $path "Docker Desktop.exe"
            if (Test-Path $exe) { return $exe }
        }
    }
    return ""
}

function Get-HrmDockerCliPath {
    # Путь к CLI контейнеров без запуска процесса и без Get-Command: известные
    # каталоги Docker Desktop, затем каталоги из PATH (проверка Test-Path).
    $override = Get-HrmDockerOverrideValue "cli_path"
    if ($null -ne $override) { return [string]$override }
    $dirs = @()
    if ($env:ProgramFiles) { $dirs += (Join-Path $env:ProgramFiles "Docker\Docker\resources\bin") }
    $programFilesX86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
    if ($programFilesX86) { $dirs += (Join-Path $programFilesX86 "Docker\Docker\resources\bin") }
    if ($env:LOCALAPPDATA) { $dirs += (Join-Path $env:LOCALAPPDATA "Docker\Docker\resources\bin") }
    foreach ($dir in $dirs) {
        $candidate = Join-Path $dir "docker.exe"
        if (Test-Path $candidate) { return $candidate }
    }
    foreach ($dir in @($env:PATH -split ";")) {
        if (-not $dir) { continue }
        $candidate = Join-Path $dir "docker.exe"
        if (Test-Path $candidate) { return $candidate }
    }
    return ""
}

function Get-HrmDockerInstallerUrl {
    # Единственный источник адреса официального установщика: его используют
    # движок (Install-HrmDockerDesktop) и аудит издателя в CI
    # (infra/windows/tools/Audit-HrmDockerPublisher.ps1).
    return $script:DockerInstallerUrl
}

function Get-HrmDockerPublisherSubjects {
    # Зафиксированные издатели официального установщика (заполняются из аудита CI).
    return @($script:DockerPublisherSubjects)
}

function Get-HrmDockerDesktopState {
    # Состояние Docker Desktop: not_installed | installed_stopped | starting | engine_ready.
    # Возвращает @{ state; installed; running; engine_ready; path; version }.
    $overrideState = Get-HrmDockerOverrideValue "desktop"
    if ($null -ne $overrideState) {
        return [pscustomobject]@{
            state = [string]$overrideState
            installed = ($overrideState -ne "not_installed")
            running = ($overrideState -eq "starting" -or $overrideState -eq "engine_ready")
            engine_ready = ($overrideState -eq "engine_ready")
            path = [string](Get-HrmDockerOverrideValue "desktop_path")
            version = ""
        }
    }
    $path = Get-HrmDockerDesktopPath
    $running = Get-HrmProcessRunning "Docker Desktop"
    # Готовность Engine проверяется у Docker CLI независимо от того, найден ли
    # процесс Docker Desktop: Engine может быть поднят и иначе (например, через
    # WSL), а имя процесса может отличаться в разных сборках.
    $engineReady = Test-HrmDockerEngineReady
    $cliPresent = [bool](Get-HrmDockerCliPath)
    $installed = ($path -ne "" -or $running -or $cliPresent -or $engineReady)
    $state = "not_installed"
    if ($engineReady) { $state = "engine_ready" }
    elseif ($running) { $state = "starting" }
    elseif ($installed) { $state = "installed_stopped" }
    return [pscustomobject]@{
        state = $state
        installed = $installed
        running = $running
        engine_ready = $engineReady
        path = $path
        version = ""
    }
}

function Get-HrmWslState {
    # WSL2: ok | missing | not_wsl2 | unknown.
    # Проверка без прав администратора: реестр Lxss (default version) + wsl.exe.
    $override = Get-HrmDockerOverrideValue "wsl"
    if ($null -ne $override) {
        # Сообщение обязано быть содержательным и в тестовом/заданном состоянии:
        # шаги мастера берут текст именно отсюда (иначе пользователь видит пустое
        # «действие»), поэтому дублируем формулировки реальных веток.
        $overrideState = [string]$override
        $overrideMessage = ""
        if ($overrideState -eq "missing") { $overrideMessage = "WSL не отвечает: команда wsl недоступна." }
        elseif ($overrideState -eq "not_wsl2") { $overrideMessage = "Ни один дистрибутив WSL не использует версию 2." }
        elseif ($overrideState -eq "ok") { $overrideMessage = "WSL2 готов к работе." }
        else { $overrideMessage = "Состояние WSL2 определить не удалось." }
        return [pscustomobject]@{ state = $overrideState; default_version = 0; distributions = @(); message = $overrideMessage }
    }
    $defaultVersion = 0
    $distributions = @()
    $registryDistro = Get-HrmRegistryString "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Lxss" "DefaultDistribution"
    if ($registryDistro) {
        $versionText = Get-HrmRegistryString ("HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Lxss\{0}" -f $registryDistro) "Version"
        if ($versionText -match "^\d+$") { $defaultVersion = [int]$versionText }
    }
    $list = Invoke-HrmExternal -Name "wsl.exe" -Arguments @("--list", "--verbose") -IgnoreExitCode
    if ($list.ExitCode -ne 0) {
        if ($defaultVersion -ge 2) {
            return [pscustomobject]@{ state = "ok"; default_version = $defaultVersion; distributions = @(); message = "WSL2 настроен (реестр), команда wsl недоступна из движка." }
        }
        return [pscustomobject]@{ state = "missing"; default_version = $defaultVersion; distributions = @(); message = "WSL не отвечает: команда wsl недоступна." }
    }
    # Вывод wsl --list --verbose в Windows PowerShell 5.1 приходит в UTF-16LE и
    # часто содержит NUL-байты; имена дистрибутивов нужны только для сообщения.
    $text = ($list.Stdout -replace "\x00", "")
    foreach ($line in ($text -split "`r?`n")) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed -match "^(NAME|ЛОКАЛЬНОЕ)") { continue }
        $trimmed = $trimmed -replace "^\*\s*", ""
        $parts = @($trimmed -split "\s+" | Where-Object { $_ })
        if ($parts.Count -ge 3) {
            $name = $parts[0]
            $version = $parts[-1]
            $distributions += $name
            if ($version -eq "2") { $defaultVersion = [Math]::Max($defaultVersion, 2) }
        }
    }
    if ($defaultVersion -ge 2 -or $distributions.Count -gt 0) {
        return [pscustomobject]@{ state = "ok"; default_version = $defaultVersion; distributions = $distributions; message = "" }
    }
    return [pscustomobject]@{ state = "not_wsl2"; default_version = $defaultVersion; distributions = $distributions; message = "Ни один дистрибутив WSL не использует версию 2." }
}

function Get-HrmVirtualizationState {
    # Аппаратная виртуализация: enabled | disabled | unknown.
    # HypervisorPresent=$true означает, что виртуализация уже используется
    # (Hyper-V/WSL2/Credential Guard) — это тоже «включено».
    $override = Get-HrmDockerOverrideValue "virtualization"
    if ($null -ne $override) {
        # То же правило, что и для WSL2: шаг мастера показывает это сообщение
        # как «что делать», пустая строка здесь — потерянное действие.
        $overrideState = [string]$override
        $overrideMessage = ""
        if ($overrideState -eq "disabled") {
            $overrideMessage = "Виртуализация не обнаружена. Включите её в BIOS/UEFI (Intel VT-x или AMD-V) и перезагрузите компьютер."
        }
        elseif ($overrideState -eq "enabled") {
            $overrideMessage = "Аппаратная виртуализация включена."
        }
        else {
            $overrideMessage = "Не удалось определить состояние виртуализации."
        }
        return [pscustomobject]@{ state = $overrideState; hypervisor_present = $false; firmware_enabled = $false; message = $overrideMessage }
    }
    try {
        $computer = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop
        $hypervisorPresent = [bool]$computer.HypervisorPresent
        $firmwareEnabled = $false
        try {
            $processors = @(Get-CimInstance -ClassName Win32_Processor -ErrorAction Stop)
            foreach ($processor in $processors) {
                if ($processor.VirtualizationFirmwareEnabled -eq $true) { $firmwareEnabled = $true }
            }
        }
        catch { }
        if ($hypervisorPresent -or $firmwareEnabled) {
            return [pscustomobject]@{ state = "enabled"; hypervisor_present = $hypervisorPresent; firmware_enabled = $firmwareEnabled; message = "" }
        }
        return [pscustomobject]@{
            state = "disabled"
            hypervisor_present = $false
            firmware_enabled = $false
            message = "Виртуализация не обнаружена. Включите её в BIOS/UEFI (Intel VT-x или AMD-V) и перезагрузите компьютер."
        }
    }
    catch {
        return [pscustomobject]@{ state = "unknown"; hypervisor_present = $false; firmware_enabled = $false; message = "Не удалось определить состояние виртуализации." }
    }
}

function Test-HrmAdministrator {
    # Нужны ли права администратора для текущих операций (установка Docker,
    # правило Firewall). Обычная работа движка — без администратора.
    $override = Get-HrmDockerOverrideValue "admin"
    if ($null -ne $override) { return [bool]$override }
    try {
        $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
        return $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
    }
    catch { return $false }
}

function Test-HrmRebootPending {
    # Windows ждёт перезагрузку (например, после включения WSL2 или остатков
    # установки Docker Desktop).
    $override = Get-HrmDockerOverrideValue "reboot"
    if ($null -ne $override) { return [bool]$override }
    try {
        if (Get-HrmRegistryString "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending" "RebootPending") { return $true }
        if (Get-HrmRegistryString "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired" "RebootRequired") { return $true }
        $rename = Get-HrmRegistryString "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager" "PendingFileRenameOperations"
        if ($rename) { return $true }
    }
    catch { return $false }
    return $false
}

function Find-HrmFreePort {
    # Подбор свободного порта (по умолчанию от 8080 вверх, максимум 20 попыток).
    param([int]$StartPort = 8080, [int]$Attempts = 20)
    for ($port = $StartPort; $port -lt ($StartPort + $Attempts); $port++) {
        try {
            $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
            if (-not $listeners) { return $port }
        }
        catch { return $StartPort }
    }
    return 0
}

function Test-HrmPortAvailable {
    param([int]$Port = 0)
    $override = Get-HrmDockerOverrideValue "port_free"
    if ($null -ne $override) { return [bool]$override }
    $port = Get-HrmPort $Port
    try {
        $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        return (-not $listeners)
    }
    catch { return $true }
}

function Get-HrmFreeSpaceMb {
    param([string]$Path)
    $override = Get-HrmDockerOverrideValue "free_mb"
    if ($null -ne $override) { return [long]$override }
    try {
        $drive = (Split-Path -Qualifier $Path)
        $psDrive = Get-PSDrive -Name ($drive.TrimEnd(":")) -ErrorAction SilentlyContinue
        if ($null -eq $psDrive) { return -1 }
        return [long]($psDrive.Free / 1MB)
    }
    catch { return -1 }
}

# --- Готовность Docker Engine ------------------------------------------------

function Test-HrmDockerEngineReady {
    $override = Get-HrmDockerOverrideValue "engine"
    if ($null -ne $override) { return [bool]$override }
    $info = Invoke-HrmExternal -Name "docker.exe" -Arguments @("info", "--format", "{{.ServerVersion}}") -IgnoreExitCode
    return ($info.ExitCode -eq 0 -and [bool]$info.Stdout)
}

function Wait-HrmDockerEngine {
    # Ожидание готовности Docker Engine с таймаутом и понятным прогрессом.
    # Никаких бесконечных циклов: по истечении таймаута возвращаем $false.
    param([int]$TimeoutSeconds = 300, [int]$IntervalSeconds = 5)
    $started = Get-Date
    $deadline = $started.AddSeconds($TimeoutSeconds)
    $lastReport = -30
    Write-HrmLog "info" "Ожидаем запуск службы контейнеров (Docker Engine)…"
    while ((Get-Date) -lt $deadline) {
        if (Test-HrmDockerEngineReady) {
            $elapsed = [int]((Get-Date) - $started).TotalSeconds
            Write-HrmLog "info" ("Служба контейнеров готова (за {0} с)." -f $elapsed)
            return $true
        }
        $elapsed = [int]((Get-Date) - $started).TotalSeconds
        if (($elapsed - $lastReport) -ge 30) {
            $lastReport = $elapsed
            Write-HrmLog "info" ("Подготавливаем рабочую среду… прошло {0} с из {1} с." -f $elapsed, $TimeoutSeconds)
        }
        Start-Sleep -Seconds $IntervalSeconds
    }
    return $false
}

function Start-HrmDockerDesktop {
    # Запуск установленного Docker Desktop. Возвращает @{ started; message }.
    param([int]$TimeoutSeconds = 300)
    $desktop = Get-HrmDockerDesktopState
    if ($desktop.state -eq "engine_ready") {
        return [pscustomobject]@{ started = $true; message = "Docker Engine уже работает." }
    }
    if ($desktop.state -eq "not_installed") {
        throw "Docker Desktop не установлен. Установите его официальным установщиком (мастер установки HR Manager предложит это одной кнопкой)."
    }
    if ($desktop.state -eq "installed_stopped") {
        if (-not $desktop.path) {
            # Не исключение: prepare/supervisor должны вернуть понятный код
            # состояния и текст («без crash-loop»), а не падать у пользователя.
            return [pscustomobject]@{
                started = $false
                message = "Не удалось найти Docker Desktop. Откройте Docker Desktop из меню «Пуск», дождитесь надписи «Engine running» и нажмите «Повторить»."
            }
        }
        Write-HrmLog "info" "Запускаем Docker Desktop…"
        Start-HrmDetached -FilePath $desktop.path -WorkingDirectory (Split-Path $desktop.path -Parent) | Out-Null
    }
    else {
        Write-HrmLog "info" "Docker Desktop уже запускается — продолжаем ожидание."
    }
    $ready = Wait-HrmDockerEngine -TimeoutSeconds $TimeoutSeconds
    if (-not $ready) {
        return [pscustomobject]@{
            started = $false
            message = ("Docker Desktop не успел запуститься за {0} секунд. Откройте Docker Desktop, дождитесь надписи «Engine running» и нажмите «Повторить»." -f $TimeoutSeconds)
        }
    }
    return [pscustomobject]@{ started = $true; message = "Docker Engine готов." }
}

# --- Установка Docker Desktop (только официальный установщик) ----------------

function Get-HrmDockerInstallGuide {
    # Простая инструкция для пользователя (русский язык, без команд).
    return @(
        "1. Нажмите «Установить Docker Desktop» — будет скачан официальный установщик с сайта docker.com.",
        "2. Windows попросит разрешение (окно UAC) — нажмите «Да».",
        "3. В окне установщика примите лицензионное соглашение Docker и нажмите «Ok».",
        "4. Если Windows попросит перезагрузиться — перезагрузите компьютер; HR Manager продолжит сам.",
        "5. После установки Docker Desktop откроется и потребует принять лицензию — выберите «Accept».",
        "6. HR Manager сам дождётся надписи «Engine running» и запустит приложение."
    )
}

function Test-HrmDockerPublisherSubject {
    # Решение «издатель — это Docker Inc.» вынесено в чистую функцию: её можно
    # проверить тестами без сертификатов, и именно её использует аудит CI.
    # Сравнение нормализованное: пробелы сжимаются, регистр не важен, чтобы
    # форматирование X.500 (пробел после запятой, порядок атрибутов в строке)
    # не влияло на решение.
    param([string]$Subject, [string[]]$Thumbprints = @())
    foreach ($thumbprint in @($Thumbprints)) {
        if (-not [string]::IsNullOrWhiteSpace($thumbprint)) {
            $clean = ($thumbprint -replace '[^0-9a-fA-F]', '').ToLowerInvariant()
            if ($script:DockerPublisherThumbprints -contains $clean) { return $true }
        }
    }
    if ([string]::IsNullOrWhiteSpace($Subject)) { return $false }
    $normalized = (($Subject -replace '\s+', ' ').Trim()).ToLowerInvariant()
    foreach ($allowed in @($script:DockerPublisherSubjects)) {
        $expected = (($allowed -replace '\s+', ' ').Trim()).ToLowerInvariant()
        if ($expected -and $normalized -ceq $expected) { return $true }
    }
    # Разбор X.500: атрибуты разделены запятыми, сравнение посимвольное (никаких
    # подстрок). Так проверка переживёт смену сертификата Docker (не изменится, а
    # вот подпись другого владельца с «Docker» внутри имени — нет).
    $attributes = @{}
    foreach ($part in ($Subject -split ',')) {
        $piece = $part.Trim()
        if (-not $piece) { continue }
        $separator = $piece.IndexOf('=')
        if ($separator -le 0) { continue }
        $key = $piece.Substring(0, $separator).Trim().ToUpperInvariant()
        $value = $piece.Substring($separator + 1).Trim()
        if (-not $attributes.ContainsKey($key)) { $attributes[$key] = @() }
        $attributes[$key] += $value
    }
    $expectedName = $script:DockerPublisherRequiredName
    foreach ($key in @("CN", "O")) {
        $values = @()
        if ($attributes.ContainsKey($key)) { $values = @($attributes[$key]) }
        # Ровно один атрибут и ровно ожидаемое значение — иначе отказ.
        if ($values.Count -ne 1) { return $false }
        if (-not ($values[0] -ieq $expectedName)) { return $false }
    }
    return $true
}

function Test-HrmDockerInstallerTrusted {
    # Проверка подписи скачанного установщика: подпись действительна И издатель
    # ровно Docker Inc. (точное совпадение subject/отпечатка из списка выше).
    # Возвращает @{ trusted; status; subject; reason }.
    param([string]$Path)
    $override = Get-HrmDockerOverrideValue "install_signature"
    if ($null -ne $override) {
        # Тесты могут переопределить проверку. $true/$false — «подпись верна» без
        # реального файла; строка — subject, который надо пропустить через НАСТОЯЩЕЕ
        # решение о издателе (так проверяется отказ фальшивому «Docker» в subject).
        if ($override -is [string]) {
            $trusted = Test-HrmDockerPublisherSubject $override
            $reason = ""
            if (-not $trusted) { $reason = ("Издатель установщика не Docker Inc. (сертификат: {0})." -f $override) }
            return [pscustomobject]@{ trusted = $trusted; status = "override"; subject = $override; reason = $reason }
        }
        return [pscustomobject]@{ trusted = [bool]$override; status = "override"; subject = "CN=Docker Inc."; reason = "" }
    }
    try {
        $signature = Get-AuthenticodeSignature -FilePath $Path
        $status = [string]$signature.Status
        $subject = ""
        $thumbprints = @()
        if ($null -ne $signature.SignerCertificate) {
            $subject = [string]$signature.SignerCertificate.Subject
            $thumbprints = @([string]$signature.SignerCertificate.Thumbprint, [string]$signature.SignerCertificate.GetCertHashString("SHA256"))
        }
        if ($status -ne "Valid") {
            return [pscustomobject]@{ trusted = $false; status = $status; subject = $subject; reason = ("Подпись установщика не подтверждена Windows (статус: {0})." -f $status) }
        }
        if (-not (Test-HrmDockerPublisherSubject -Subject $subject -Thumbprints $thumbprints)) {
            return [pscustomobject]@{ trusted = $false; status = $status; subject = $subject; reason = ("Издатель установщика не Docker Inc. (сертификат: {0})." -f $subject) }
        }
        return [pscustomobject]@{ trusted = $true; status = $status; subject = $subject; reason = "" }
    }
    catch {
        return [pscustomobject]@{ trusted = $false; status = "error"; subject = ""; reason = "Не удалось проверить подпись установщика." }
    }
}

function Get-HrmPendingDockerOperationFile {
    param([string]$StateDir)
    return (Join-Path $StateDir "docker-pending.json")
}

function Set-HrmPendingDockerOperation {
    # Фиксирует, что после установки/перезагрузки нужно автоматически продолжить
    # подготовку среды (это и есть «после установки автоматически продолжить»).
    param([string]$StateDir, [string]$Kind, [string]$Message = "")
    Set-HrmJsonFile $StateDir "docker-pending.json" ([ordered]@{
            kind = $Kind
            message = $Message
            created_at = (Get-Date).ToString("o")
        })
}

function Get-HrmPendingDockerOperation {
    param([string]$StateDir)
    $file = Get-HrmPendingDockerOperationFile $StateDir
    if (-not (Test-Path $file)) { return $null }
    return (Get-HrmJsonFile $file)
}

function Clear-HrmPendingDockerOperation {
    param([string]$StateDir)
    $file = Get-HrmPendingDockerOperationFile $StateDir
    if (Test-Path $file) { Remove-Item $file -Force }
}

function Install-HrmDockerDesktop {
    # Установка Docker Desktop ТОЛЬКО официальным установщиком Docker.
    #
    # - скачивание: https://desktop.docker.com/win/main/amd64/Docker Desktop Installer.exe
    # - проверка подписи: издатель Docker (иначе отказ);
    # - запуск: с повышением прав (UAC) — отмена UAC не считается ошибкой;
    # - лицензию Docker принимает пользователь в окне установщика; движок НЕ
    #   передаёт --accept-license;
    # - после установки фиксируется «продолжить автоматически».
    param(
        [string]$StateDir = "",
        [switch]$Interactive,
        [switch]$AllowSilent
    )
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $desktop = Get-HrmDockerDesktopState
    if ($desktop.state -eq "engine_ready") {
        Clear-HrmPendingDockerOperation $StateDir
        return [pscustomobject]@{ status = "already_installed"; message = "Docker Desktop уже установлен и работает."; needs_reboot = $false }
    }
    if ($desktop.installed) {
        Set-HrmPendingDockerOperation $StateDir "resume" "Docker Desktop установлен — продолжаем подготовку."
        return [pscustomobject]@{ status = "already_installed"; message = "Docker Desktop уже установлен — продолжаем запуск."; needs_reboot = $false }
    }
    $overrideResult = Get-HrmDockerOverrideValue "install_result"
    if ($null -ne $overrideResult) {
        # Тестовый сценарий: результат установки задан заранее.
        $result = $overrideResult
        if ($result.status -eq "installed") {
            Set-HrmPendingDockerOperation $StateDir "resume" "Docker Desktop установлен — продолжаем подготовку."
        }
        if ($result.status -eq "reboot_required") {
            Set-HrmPendingDockerOperation $StateDir "reboot" "После перезагрузки HR Manager продолжит сам."
        }
        return $result
    }

    $downloadDir = Join-Path ([System.IO.Path]::GetTempPath()) "HRM-docker"
    $installerPath = Join-Path $downloadDir "DockerDesktopInstaller.exe"
    try {
        Write-HrmLog "info" "Скачиваем официальный установщик Docker Desktop с desktop.docker.com…"
        $null = Save-HrmDownload -Uri $script:DockerInstallerUrl -Destination $installerPath
    }
    catch {
        $message = ("Не удалось скачать установщик Docker Desktop: {0}. Скачайте его вручную: {1}" -f (Redact-HrmText $_.Exception.Message), $script:DockerDownloadPage)
        return [pscustomobject]@{ status = "failed"; message = $message; needs_reboot = $false }
    }

    $trust = Test-HrmDockerInstallerTrusted -Path $installerPath
    if (-not $trust.trusted) {
        Write-HrmLog "warn" ("Установщик Docker Desktop отклонён: {0}" -f $trust.reason)
        return [pscustomobject]@{
            status = "failed"
            message = ("Скачанный установщик Docker Desktop не прошёл проверку подписи ({0}). Установите Docker Desktop вручную с {1}." -f $trust.reason, $script:DockerDownloadPage)
            needs_reboot = $false
        }
    }
    Write-HrmLog "info" ("Установщик Docker Desktop проверен: {0}" -f $trust.subject)

    if ($AllowSilent) {
        Write-HrmLog "info" "Запускаем установку Docker Desktop (может потребоваться разрешение Windows)…"
        $silent = Start-HrmElevatedAndWait -FilePath $installerPath -Arguments @("install", "--quiet", "--backend=wsl-2")
        if ($silent.CanceledByUser) {
            return [pscustomobject]@{
                status = "uac_declined"
                message = "Установка Docker Desktop отменена: вы не дали разрешение Windows (UAC). Нажмите «Установить Docker Desktop» и подтвердите запрос."
                needs_reboot = $false
            }
        }
        if ($silent.ExitCode -eq 0) {
            if (Test-HrmRebootPending) {
                Set-HrmPendingDockerOperation $StateDir "reboot" "После перезагрузки HR Manager продолжит сам."
                return [pscustomobject]@{ status = "reboot_required"; message = "Docker Desktop установлен. Windows просит перезагрузку — перезагрузите компьютер, HR Manager продолжит сам."; needs_reboot = $true }
            }
            Set-HrmPendingDockerOperation $StateDir "resume" "Docker Desktop установлен — продолжаем подготовку."
            return [pscustomobject]@{ status = "installed"; message = "Docker Desktop установлен."; needs_reboot = $false }
        }
        Write-HrmLog "warn" ("Тихая установка Docker Desktop не удалась (код {0}) — открываем обычный мастер установки." -f $silent.ExitCode)
    }

    if (-not $Interactive) {
        Clear-HrmPendingDockerOperation $StateDir
        return [pscustomobject]@{
            status = "manual_required"
            message = ("Установщик Docker Desktop был скачан, но требует действий пользователя (принятие лицензии и разрешение Windows). Файл: {0}" -f $installerPath)
            needs_reboot = $false
        }
    }

    Write-HrmLog "info" "Открываем мастер установки Docker Desktop (Windows запросит разрешение, лицензию принимаете вы)…"
    $wizard = Start-HrmElevatedAndWait -FilePath $installerPath -Arguments @()
    if ($wizard.CanceledByUser) {
        return [pscustomobject]@{
            status = "uac_declined"
            message = "Установка Docker Desktop отменена: вы не дали разрешение Windows (UAC). Повторите, когда будете готовы."
            needs_reboot = $false
        }
    }
    if ($wizard.ExitCode -eq 0) {
        Set-HrmPendingDockerOperation $StateDir "resume" "Docker Desktop установлен — продолжаем подготовку."
        $needsReboot = Test-HrmRebootPending
        return [pscustomobject]@{
            status = if ($needsReboot) { "reboot_required" } else { "installed" }
            message = if ($needsReboot) { "Docker Desktop установлен. Windows просит перезагрузку — перезагрузите компьютер, HR Manager продолжит сам." } else { "Docker Desktop установлен." }
            needs_reboot = $needsReboot
        }
    }
    if (Test-HrmRebootPending) {
        Set-HrmPendingDockerOperation $StateDir "reboot" "После перезагрузки HR Manager продолжит сам."
        return [pscustomobject]@{ status = "reboot_required"; message = "Установка Docker Desktop почти завершена — нужна перезагрузка Windows. HR Manager продолжит сам."; needs_reboot = $true }
    }
    return [pscustomobject]@{
        status = "failed"
        message = ("Установка Docker Desktop завершилась с кодом {0}. Если окна установки не было — откройте файл {1} двойным кликом." -f $wizard.ExitCode, $installerPath)
        needs_reboot = $false
    }
}

# --- Сводная готовность среды ------------------------------------------------

function Get-HrmSetupProgressPlan {
    # Список шагов «Подготавливаем рабочую среду» для мастера и трея.
    # Каждый шаг: ключ, русская подпись, статус ok|warn|fail|pending, деталь.
    param(
        [string]$InstallDir = "",
        [string]$StateDir = "",
        [int]$Port = 0
    )
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    if (-not $InstallDir) { $InstallDir = Get-HrmInstallDefaultDirSafe }
    $steps = @()
    $virtualization = Get-HrmVirtualizationState
    $steps += [pscustomobject]@{
        key = "virtualization"; label = "Проверяем виртуализацию"
        status = if ($virtualization.state -eq "enabled") { "ok" } elseif ($virtualization.state -eq "disabled") { "fail" } else { "warn" }
        detail = if ($virtualization.state -eq "enabled") { "Аппаратная виртуализация включена." } else { $virtualization.message }
    }
    $wsl = Get-HrmWslState
    $steps += [pscustomobject]@{
        key = "wsl"; label = "Проверяем WSL2"
        status = if ($wsl.state -eq "ok") { "ok" } elseif ($wsl.state -eq "missing") { "warn" } else { "warn" }
        detail = if ($wsl.state -eq "ok") { "WSL2 готов к работе." } elseif ($wsl.message) { $wsl.message } else { "WSL2 будет настроен установщиком Docker Desktop." }
    }
    $desktop = Get-HrmDockerDesktopState
    $steps += [pscustomobject]@{
        key = "docker_app"; label = "Ищем Docker Desktop"
        status = if ($desktop.installed) { "ok" } else { "fail" }
        detail = if ($desktop.installed) { "Docker Desktop найден." } else { "Docker Desktop не установлен — мастер предложит установить." }
    }
    $steps += [pscustomobject]@{
        key = "docker_engine"; label = "Ожидаем запуск службы контейнеров"
        status = if ($desktop.engine_ready) { "ok" } elseif ($desktop.running) { "warn" } else { "pending" }
        detail = if ($desktop.engine_ready) { "Docker Engine работает." } elseif ($desktop.running) { "Docker Desktop запускается…" } else { "Docker Engine ещё не запущен." }
    }
    $freeMb = Get-HrmFreeSpaceMb $StateDir
    $steps += [pscustomobject]@{
        key = "disk"; label = "Проверяем свободное место"
        status = if ($freeMb -lt 0) { "warn" } elseif ($freeMb -ge 5120) { "ok" } else { "fail" }
        detail = if ($freeMb -lt 0) { "Не удалось определить свободное место." } else { ("Свободно {0} МБ (нужно не меньше 5120 МБ)." -f $freeMb) }
    }
    $port = Get-HrmPort $Port
    $portFree = Test-HrmPortAvailable -Port $port
    $steps += [pscustomobject]@{
        key = "port"; label = "Проверяем порт приложения"
        status = if ($portFree) { "ok" } else { "warn" }
        detail = if ($portFree) { ("Порт {0} свободен." -f $port) } else { ("Порт {0} занят другим приложением." -f $port) }
    }
    $steps += [pscustomobject]@{
        key = "rights"; label = "Проверяем права"
        status = "ok"
        detail = if (Test-HrmAdministrator) { "Запущено с правами администратора." } else { "Обычные права пользователя — этого достаточно; разрешение Windows потребуется только для установки Docker Desktop и доступа по сети." }
    }
    return $steps
}

function Get-HrmInstallDefaultDirSafe {
    # Обёртка без обязательных модулей (Install.psm1 может быть не загружен в
    # диагностическом сценарии) — используется только для проверки места.
    if ($env:HRM_INSTALL_DIR) { return $env:HRM_INSTALL_DIR }
    return (Join-Path $env:LOCALAPPDATA "Programs\HRManager")
}

function Get-HrmDockerReadiness {
    # Полная проверка перед запуском: docker, engine, WSL2, виртуализация,
    # место, порт, права. Возвращает @{ ready; state; steps; message }.
    param([string]$InstallDir = "", [string]$StateDir = "", [int]$Port = 0)
    $steps = Get-HrmSetupProgressPlan -InstallDir $InstallDir -StateDir $StateDir -Port $Port
    $failed = @($steps | Where-Object { $_.status -eq "fail" })
    $desktop = Get-HrmDockerDesktopState
    $state = "unknown"
    if (-not $desktop.installed) { $state = "docker_missing" }
    elseif ($desktop.engine_ready) { $state = "ready" }
    elseif ($desktop.running) { $state = "engine_starting" }
    else { $state = "docker_stopped" }
    $message = "Рабочая среда готова."
    if ($state -eq "docker_missing") { $message = "Docker Desktop не установлен." }
    elseif ($state -eq "engine_starting") { $message = "Docker Desktop запускается…" }
    elseif ($state -eq "docker_stopped") { $message = "Docker Desktop установлен, но не запущен." }
    elseif ($failed.Count -gt 0) { $message = $failed[0].detail }
    return [pscustomobject]@{
        ready = ($state -eq "ready" -and $failed.Count -eq 0)
        state = $state
        steps = $steps
        message = $message
        pending_reboot = Test-HrmRebootPending
        pending_docker = (Get-HrmPendingDockerOperation $StateDir)
    }
}

function Invoke-HrmDockerPrepare {
    # Приведение среды к готовности перед запуском приложения.
    # Возвращает @{ ok; state; message; needs_reboot; needs_install }.
    # НИКОГДА не запускает бесконечный цикл: один проход с таймаутами.
    param(
        [string]$InstallDir = "",
        [string]$StateDir = "",
        [int]$Port = 0,
        [switch]$AllowInstall,
        [switch]$Interactive,
        [int]$TimeoutSeconds = 300
    )
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    if (-not $InstallDir) { $InstallDir = Get-HrmInstallDefaultDirSafe }

    $pendingOperation = Get-HrmPendingDockerOperation $StateDir
    if ($null -ne $pendingOperation) {
        $pendingKind = ""
        if ($pendingOperation.PSObject.Properties["kind"]) { $pendingKind = [string]$pendingOperation.kind }
        if ($pendingKind -eq "reboot") {
            return [pscustomobject]@{
                ok = $false; state = "reboot_required"; needs_install = $false; needs_reboot = $true
                message = "Установка Docker Desktop завершится после перезагрузки Windows. Перезагрузите компьютер — HR Manager продолжит сам."
            }
        }
    }

    if (-not (Test-HrmPortAvailable -Port $Port)) {
        $freePort = Find-HrmFreePort -StartPort (Get-HrmPort $Port)
        $port = Get-HrmPort $Port
        if ($freePort -gt 0 -and $freePort -ne $port) {
            Write-HrmLog "warn" ("Порт {0} занят. Свободен порт {1} — приложение будет опубликовано на нём." -f $port, $freePort)
        }
    }

    $desktop = Get-HrmDockerDesktopState
    if ($desktop.state -eq "engine_ready") {
        Clear-HrmPendingDockerOperation $StateDir
        return [pscustomobject]@{ ok = $true; state = "ready"; message = "Рабочая среда готова."; needs_reboot = $false; needs_install = $false }
    }
    if ($desktop.state -eq "not_installed") {
        if (-not $AllowInstall) {
            return [pscustomobject]@{
                ok = $false; state = "docker_missing"; needs_install = $true; needs_reboot = $false
                message = "Для работы HR Manager нужен Docker Desktop. Нажмите «Установить Docker Desktop» — это официальная программа Docker, установка займёт несколько минут."
            }
        }
        $install = Install-HrmDockerDesktop -StateDir $StateDir -Interactive:$Interactive -AllowSilent
        if ($install.status -eq "installed") {
            Write-HrmLog "info" "Docker Desktop установлен — продолжаем запуск."
        }
        elseif ($install.status -eq "reboot_required" -or $install.status -eq "already_installed") {
            if ($install.needs_reboot) {
                return [pscustomobject]@{ ok = $false; state = "reboot_required"; needs_install = $false; needs_reboot = $true; message = $install.message }
            }
            if ($install.status -eq "already_installed") {
                Clear-HrmPendingDockerOperation $StateDir
            }
        }
        else {
            return [pscustomobject]@{ ok = $false; state = $install.status; needs_install = ($install.status -eq "uac_declined"); needs_reboot = [bool]$install.needs_reboot; message = $install.message }
        }
        $desktop = Get-HrmDockerDesktopState
    }
    if ($desktop.state -eq "installed_stopped" -or $desktop.state -eq "starting") {
        # Прогресс для значка в трее: установка идёт долго, человек должен
        # видеть, что происходит, а не пустой значок (дефект P2 ревью).
        try { Set-HrmSupervisorState -StateDir $StateDir -State "starting" -Message "Ожидаем запуск службы контейнеров…" -Busy $true } catch { }
        $start = Start-HrmDockerDesktop -TimeoutSeconds $TimeoutSeconds
        if (-not $start.started) {
            return [pscustomobject]@{ ok = $false; state = "engine_timeout"; needs_install = $false; needs_reboot = (Test-HrmRebootPending); message = $start.message }
        }
    }
    if (-not (Test-HrmDockerEngineReady)) {
        return [pscustomobject]@{
            ok = $false; state = "engine_not_ready"; needs_install = $false; needs_reboot = (Test-HrmRebootPending)
            message = "Служба контейнеров ещё не готова. Подождите минуту и нажмите «Повторить»."
        }
    }
    Clear-HrmPendingDockerOperation $StateDir
    return [pscustomobject]@{ ok = $true; state = "ready"; message = "Рабочая среда готова."; needs_reboot = $false; needs_install = $false }
}

function Format-HrmDockerReadiness {
    # Человеческий текст для консоли/диагностики (без технических команд).
    param([string]$InstallDir = "", [string]$StateDir = "", [int]$Port = 0)
    $readiness = Get-HrmDockerReadiness -InstallDir $InstallDir -StateDir $StateDir -Port $Port
    $lines = @()
    $lines += "Подготовка рабочей среды HR Manager"
    foreach ($step in $readiness.steps) {
        $mark = switch ($step.status) {
            "ok" { "[ГОТОВО]" }
            "warn" { "[ВНИМАНИЕ]" }
            "fail" { "[НУЖНО ДЕЙСТВИЕ]" }
            default { "[ОЖИДАНИЕ]" }
        }
        $lines += ("{0} {1}: {2}" -f $mark, $step.label, $step.detail)
    }
    if ($readiness.state -ne "ready") {
        $lines += ""
        $lines += "Что делать:"
        $lines += (Get-HrmDockerInstallGuide)
    }
    return $lines
}
