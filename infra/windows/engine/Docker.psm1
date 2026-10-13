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
# Кэш ВЫБРАННОГО пути CLI контейнеров (регрессия D6): выбор делается перебором
# кандидатов и чтением версий файлов, а CLI вызывается десятки раз за операцию.
# Кэшируется только УСПЕШНЫЙ выбор: пустой результат не кэшируется, иначе
# установка Docker Desktop внутри того же процесса не стала бы видна движку.
# Сбрасывается через Reset-HrmDockerCliChoiceCache (после установки Docker и в
# тестах).
$script:DockerCliChoiceCache = ""
# База переопределений (тестовый шов): Set-HrmDockerOverride заменяет набор
# целиком, поэтому договорённости, на которые опирается весь прогон (свободный
# порт, путь CLI контейнеров), хранятся отдельно и не стираются, когда тест
# задаёт свои ключи. Значения теста важнее базы.
$script:DockerOverrideDefaults = $null

$script:DockerInstallerUrl = "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"
$script:DockerDownloadPage = "https://www.docker.com/products/docker-desktop/"
# Сколько секунд ждём завершение установщика Docker Desktop (дефект P3
# дораздела PR #55). Значение совпадает с потолком Start-HrmElevatedAndWait и
# НЕ увеличено: 3600 с — это ограничение бесконечного ожидания, а не «лечение
# медленной установки». Константа объявлена здесь, а не читается из значения по
# умолчанию Common.psm1, потому что текст сообщения обязан честно называть
# ожидание, которое запросил вызывающий.
$script:DockerInstallerWaitSeconds = 3600

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

function Set-HrmDockerOverrideDefaults {
    # База переопределений, которая действует и в тестах, задающих свои ключи.
    # Нужна из-за того, что Set-HrmDockerOverride заменяет набор целиком: без неё
    # прогон зависел бы от машины (например, путь CLI контейнеров резолвился бы
    # в реальной установке Docker, а не в мок-мире).
    param([hashtable]$State)
    $script:DockerOverrideDefaults = $State
}

function Set-HrmDockerOverride {
    # Ключи: desktop, cli, cli_path (путь CLI контейнеров; "" — «не найден»),
    # engine, wsl, virtualization, admin, reboot, port_free, free_mb,
    # desktop_path, install_result, install_signature.
    # Набор теста накладывается поверх базы (Set-HrmDockerOverrideDefaults).
    param([hashtable]$State)
    $merged = @{}
    if ($null -ne $script:DockerOverrideDefaults) {
        foreach ($key in $script:DockerOverrideDefaults.Keys) { $merged[$key] = $script:DockerOverrideDefaults[$key] }
    }
    foreach ($key in $State.Keys) { $merged[$key] = $State[$key] }
    $script:DockerOverride = $merged
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
    # Пользовательская установка Docker Desktop (без администратора, вариант по
    # умолчанию в 2026): %LOCALAPPDATA%\Programs\DockerDesktop. На живой машине
    # (диагностика 12.10.2026) EXE лежал именно здесь, а среди кандидатов этого
    # каталога не было — движок зависал на ожидании Engine, хотя Docker Desktop
    # был установлен. Реестр может не содержать путь (перенос/ручная установка),
    # поэтому каталог проверяется явно.
    if ($env:LOCALAPPDATA) { $candidates += (Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\Docker Desktop.exe") }
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

function Get-HrmDockerCliDirs {
    # Известные каталоги CLI контейнеров (без обращения к PATH). Один список для
    # резолвера и для отчёта «где искали» в отказе — иначе они разойдутся.
    $dirs = @()
    if ($env:ProgramFiles) { $dirs += (Join-Path $env:ProgramFiles "Docker\Docker\resources\bin") }
    $programFilesX86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
    if ($programFilesX86) { $dirs += (Join-Path $programFilesX86 "Docker\Docker\resources\bin") }
    # Пользовательская установка Docker Desktop (без администратора): CLI лежит в
    # %LOCALAPPDATA%\Programs\DockerDesktop\resources\bin (подтверждено на живой
    # машине 12.10.2026). PATH скрытого процесса движка может не содержать этот
    # каталог, поэтому он проверяется явно, до перебора PATH.
    if ($env:LOCALAPPDATA) { $dirs += (Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\bin") }
    if ($env:LOCALAPPDATA) { $dirs += (Join-Path $env:LOCALAPPDATA "Docker\Docker\resources\bin") }
    return $dirs
}

function Get-HrmDockerCliCandidatePaths {
    # ВСЕ существующие пути CLI контейнеров в порядке поиска: известные каталоги
    # Docker Desktop, затем каталоги из PATH. Дубликаты (один и тот же файл,
    # найденный дважды) схлопываются. Без запуска процессов.
    $paths = @()
    $seen = @{}
    foreach ($dir in (Get-HrmDockerCliDirs)) {
        if (-not $dir) { continue }
        $candidate = Join-Path $dir "docker.exe"
        if (-not (Test-Path $candidate)) { continue }
        $key = $candidate.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        $paths += $candidate
    }
    foreach ($dir in @($env:PATH -split ";")) {
        if (-not $dir) { continue }
        $candidate = Join-Path $dir.Trim() "docker.exe"
        if (-not (Test-Path $candidate)) { continue }
        $key = $candidate.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        $paths += $candidate
    }
    return $paths
}

function Get-HrmDockerInstallRoot {
    # Корень установки Docker Desktop для файла (строка в нижнем регистре):
    #   «…\Docker Desktop\Docker Desktop.exe»            → «…\Docker Desktop»
    #   «…\Docker Desktop\resources\bin\docker.exe»      → «…\Docker Desktop»
    # Совпадение корней означает «этот CLI из той же установки, что и Docker
    # Desktop, который мы считаем установленным». Пустая строка — не определён.
    param([string]$Path = "")
    if (-not $Path) { return "" }
    try {
        $normalized = ([string]$Path).Replace("/", "\").TrimEnd("\")
        $leaf = Split-Path $normalized -Leaf
        $dir = Split-Path $normalized -Parent
        if (-not $dir) { return "" }
        # СНАЧАЛА шаблон CLI из установки Docker Desktop
        # («…\Docker Desktop\resources\bin\docker.exe»): если проверять имя файла
        # первым, «docker.exe» совпал бы с правилом для Docker Desktop.exe и корнем
        # оказался бы каталог «resources\bin», то есть CLI из той же установки не
        # сопоставлялся бы с работающим Docker Desktop (правило выбора D6 не работало).
        $parent = Split-Path $dir -Parent
        if ($parent) {
            $grandParent = Split-Path $parent -Parent
            if ($grandParent -and ((Split-Path $parent -Leaf) -ieq "resources") -and ((Split-Path $dir -Leaf) -ieq "bin")) {
                return $grandParent.ToLowerInvariant()
            }
        }
        if ($leaf -match "(?i)^docker(\s*desktop)?\.exe$") { return $dir.ToLowerInvariant() }
        # Посторонний файл: корень — его собственный каталог (совпадения с корнем
        # установки Docker Desktop не будет, значит и выбран он не по правилу 1).
        return $dir.ToLowerInvariant()
    }
    catch { return "" }
}

function Get-HrmVersionParts {
    # Разбор версии («27.3.1», «v2.29.7», «24.0.7-beta») на числовые разряды и
    # текстовые хвосты: [{ number = 27; text = "" }, …]. Не бросает исключений на
    # мусоре — неразобранный разряд даёт 0.
    param([string]$Value = "")
    $clean = ([string]$Value).Trim()
    if ($clean.Length -gt 0 -and ($clean[0] -eq "v" -or $clean[0] -eq "V")) { $clean = $clean.Substring(1) }
    $parts = @()
    foreach ($chunk in ($clean -split "\.")) {
        $digits = ""
        foreach ($char in $chunk.ToCharArray()) {
            if ($char -ge [char]"0" -and $char -le [char]"9") { $digits += [string]$char }
            else { break }
        }
        $number = 0
        if ($digits -and $digits.Length -le 9) { $number = [int]$digits }
        $text = ""
        if ($chunk.Length -gt $digits.Length) { $text = $chunk.Substring($digits.Length) }
        $parts += [pscustomobject]@{ number = $number; text = $text }
    }
    return $parts
}

function Compare-HrmCliVersion {
    # Сравнение версий CLI/плагинов: -1 (Left меньше), 0, 1 (Left больше).
    # Правила:
    #   * числовые разряды сравниваются как числа: 27.3.1 > 24.0.7;
    #   * пустая строка меньше любой непустой (версию не удалось прочитать);
    #   * при равных числах разряд БЕЗ текстового хвоста больше разряда с
    #     хвостом: 24.0.7 > 24.0.7-beta (релиз старше предпоиска);
    #   * два хвоста сравниваются ординально (побайтово), без учёта регистра
    #     не делаем — порядок предпоисков не критичен для выбора CLI.
    param([string]$Left = "", [string]$Right = "")
    if (-not $Left -and -not $Right) { return 0 }
    if (-not $Left) { return -1 }
    if (-not $Right) { return 1 }
    $leftParts = @(Get-HrmVersionParts $Left)
    $rightParts = @(Get-HrmVersionParts $Right)
    $count = [Math]::Max($leftParts.Count, $rightParts.Count)
    for ($i = 0; $i -lt $count; $i++) {
        $leftNumber = 0
        $leftText = ""
        if ($i -lt $leftParts.Count) { $leftNumber = [int]$leftParts[$i].number; $leftText = [string]$leftParts[$i].text }
        $rightNumber = 0
        $rightText = ""
        if ($i -lt $rightParts.Count) { $rightNumber = [int]$rightParts[$i].number; $rightText = [string]$rightParts[$i].text }
        if ($leftNumber -ne $rightNumber) {
            if ($leftNumber -gt $rightNumber) { return 1 }
            return -1
        }
        if ($leftText -ne $rightText) {
            if (-not $leftText) { return 1 }
            if (-not $rightText) { return -1 }
            if ([string]::CompareOrdinal($leftText, $rightText) -gt 0) { return 1 }
            return -1
        }
    }
    return 0
}

function New-HrmDockerCliCandidate {
    # Описание одного кандидата CLI (без запуска процесса): путь, источник,
    # версия файла, дата файла и признак «тот же корень установки, что и Docker
    # Desktop». Чтение версии файла может не сработать (нет прав/файл занят) —
    # тогда поля остаются пустыми, кандидат не отбрасывается.
    param([string]$Path, [string]$Source, [string]$DesktopPath = "")
    $fileVersion = ""
    $fileLastWrite = ""
    try {
        $item = Get-Item -LiteralPath $Path -ErrorAction Stop
        $fileLastWrite = $item.LastWriteTime.ToString("o")
        $info = $item.VersionInfo
        if ($null -ne $info) {
            if ($info.ProductVersion) { $fileVersion = [string]$info.ProductVersion }
            elseif ($info.FileVersion) { $fileVersion = [string]$info.FileVersion }
        }
    }
    catch { }
    $desktopMatch = $false
    $desktopRoot = Get-HrmDockerInstallRoot $DesktopPath
    if ($desktopRoot) {
        $cliRoot = Get-HrmDockerInstallRoot $Path
        if ($cliRoot -and $cliRoot -eq $desktopRoot) { $desktopMatch = $true }
    }
    return [pscustomobject]@{
        path = [string]$Path
        source = [string]$Source
        file_version = $fileVersion
        file_last_write = $fileLastWrite
        desktop_root_match = $desktopMatch
        install_root = (Get-HrmDockerInstallRoot $Path)
        probe_ok = $false
        probe_version = ""
        probe_exit_code = 0
        probe_stderr = ""
    }
}

function Get-HrmDockerCliCandidates {
    # Все кандидаты CLI контейнеров с их фактами. Порядок — порядок поиска
    # (каталоги Docker Desktop, затем PATH): он же последний критерий выбора.
    param([string]$DesktopPath = "")
    $candidates = @()
    $knownDirs = @(Get-HrmDockerCliDirs)
    foreach ($path in (Get-HrmDockerCliCandidatePaths)) {
        $source = "path"
        $parent = Split-Path $path -Parent
        foreach ($dir in $knownDirs) {
            if ($dir -and $parent -and ($parent.ToLowerInvariant() -eq $dir.ToLowerInvariant())) { $source = "docker-desktop-dir"; break }
        }
        $candidates += (New-HrmDockerCliCandidate -Path $path -Source $source -DesktopPath $DesktopPath)
    }
    return $candidates
}

function Select-HrmDockerCliCandidate {
    # ПРАВИЛО ВЫБОРА CLI контейнеров (регрессия D6). Раньше возвращался ПЕРВЫЙ
    # найденный файл: при двух установках Docker Desktop (устаревшая машинная в
    # Program Files и актуальная пользовательская в %LOCALAPPDATA%\Programs)
    # детекция считала Docker установленным по одной установке, а вызовы шли в
    # CLI другой — версии расходились, compose-плагин мог отсутствовать, и
    # предполётная проверка падала с текстом, который не объяснял причину.
    #
    # Приоритет (по убыванию):
    #   1) кандидат из ТОГО ЖЕ корня установки, что и Docker Desktop.exe;
    #   2) наибольшая версия файла (ProductVersion, иначе FileVersion);
    #   3) самая свежая дата изменения файла;
    #   4) исходный порядок поиска (прежнее поведение) — последний критерий.
    # Функция ЧИСТАЯ (без Test-Path и без запуска процессов), поэтому правило
    # проверяется тестом на фиксированных данных. Возвращает @{ path; index;
    # reason } — reason пишется в журнал и в отчёт поддержки.
    param([object[]]$Candidates = @(), [string]$DesktopPath = "")
    $pool = @($Candidates)
    if ($pool.Count -eq 0) {
        return [pscustomobject]@{ path = ""; index = -1; reason = "CLI контейнеров не найден ни в одном из известных каталогов" }
    }
    if ($pool.Count -eq 1) {
        return [pscustomobject]@{ path = [string]$pool[0].path; index = 0; reason = "единственный найденный CLI контейнеров" }
    }
    $reasons = @()
    $matched = @($pool | Where-Object { [bool]$_.desktop_root_match })
    if ($matched.Count -ge 1) {
        $reasons += ("тот же корень установки, что и Docker Desktop ({0})" -f (Get-HrmDockerInstallRoot $DesktopPath))
        $pool = $matched
    }
    if ($pool.Count -gt 1) {
        $bestVersion = ""
        foreach ($item in $pool) {
            if ((Compare-HrmCliVersion ([string]$item.file_version) $bestVersion) -gt 0) { $bestVersion = [string]$item.file_version }
        }
        if ($bestVersion) {
            $versioned = @($pool | Where-Object { [string]$_.file_version -eq $bestVersion })
            if ($versioned.Count -ge 1) {
                $reasons += ("наибольшая версия файла {0}" -f $bestVersion)
                $pool = $versioned
            }
        }
    }
    if ($pool.Count -gt 1) {
        $newest = ""
        foreach ($item in $pool) {
            $stamp = [string]$item.file_last_write
            if ($stamp -and (-not $newest -or ($stamp -gt $newest))) { $newest = $stamp }
        }
        if ($newest) {
            $fresh = @($pool | Where-Object { [string]$_.file_last_write -eq $newest })
            if ($fresh.Count -ge 1) {
                $reasons += "самая свежая дата файла"
                $pool = $fresh
            }
        }
    }
    if ($reasons.Count -eq 0) { $reasons += "исходный порядок поиска (каталоги Docker Desktop, затем PATH)" }
    $chosen = [string]$pool[0].path
    $index = -1
    for ($i = 0; $i -lt $Candidates.Count; $i++) {
        if ([string]$Candidates[$i].path -eq $chosen) { $index = $i; break }
    }
    return [pscustomobject]@{ path = $chosen; index = $index; reason = ($reasons -join "; ") }
}

function Get-HrmDockerCliPath {
    # ЕДИНСТВЕННАЯ точка разрешения CLI контейнеров — её используют и детекция
    # («Docker установлен»), и фактические вызовы (Invoke-HrmDocker в
    # Common.psm1). Иначе детекция находит CLI в пользовательском каталоге
    # Docker Desktop, а вызов идёт голым именем через PATH процесса и падает.
    # Без запуска процесса и без Get-Command: выбор из всех найденных кандидатов
    # по правилу Select-HrmDockerCliCandidate. "" — CLI не найден.
    #
    # -Candidate: вернуть именно этот путь, ЕСЛИ он входит в список кандидатов
    # того же резолвера (проба версий в диагностике). Имя внешней команды
    # по-прежнему не может прийти из произвольного места — это же проверяет
    # static.tests.ps1 («имя команды получено из Get-HrmDockerCliPath»).
    param([string]$Candidate = "")
    $override = Get-HrmDockerOverrideValue "cli_path"
    if ($null -ne $override) { return [string]$override }
    if ($Candidate) {
        $key = ([string]$Candidate).ToLowerInvariant()
        foreach ($path in (Get-HrmDockerCliCandidatePaths)) {
            if (([string]$path).ToLowerInvariant() -eq $key) { return [string]$path }
        }
        return ""
    }
    if ($script:DockerCliChoiceCache) { return [string]$script:DockerCliChoiceCache }
    $choice = Select-HrmDockerCliCandidate -Candidates @(Get-HrmDockerCliCandidates -DesktopPath (Get-HrmDockerDesktopPath)) -DesktopPath (Get-HrmDockerDesktopPath)
    if ($choice.path) { $script:DockerCliChoiceCache = [string]$choice.path }
    return [string]$choice.path
}

function Reset-HrmDockerCliChoiceCache {
    # Сброс кэша выбора CLI: после установки/обновления Docker Desktop (в том же
    # процессе появляется новый CLI) и в тестах между кейсами.
    $script:DockerCliChoiceCache = ""
    return $true
}

function Invoke-HrmDockerCliVersionProbe {
    # Проба «--version» конкретного кандидата CLI. Отдельная функция: это
    # ЕДИНСТВЕННОЕ место, где движок запускает CLI ради проверки, и оно вызывается
    # только из диагностики/отчёта поддержки и из объяснения отказа предполётной
    # проверки — НЕ на горячем пути (каждый вызов docker). Проба ограничена
    # таймаутом (класс «probe»): зависший CLI не утаскивает за собой движок.
    # Возвращает @{ ok; version; exit_code; stderr; timed_out }.
    param([string]$CandidatePath = "", [int]$TimeoutSeconds = 0)
    $cli = Get-HrmDockerCliPath -Candidate $CandidatePath
    if (-not $cli) {
        return [pscustomobject]@{ ok = $false; version = ""; exit_code = -1; stderr = "кандидат не входит в список резолвера CLI контейнеров"; timed_out = $false }
    }
    try {
        $result = Invoke-HrmExternal -Name $cli -Arguments @("--version") -IgnoreExitCode -TimeoutSeconds $TimeoutSeconds
    }
    catch {
        return [pscustomobject]@{ ok = $false; version = ""; exit_code = -1; stderr = (Redact-HrmText $_.Exception.Message); timed_out = $false }
    }
    $stdout = [string]$result.Stdout
    $version = ""
    if ($stdout -match "(\d+\.\d+(?:\.\d+)?(?:-[0-9A-Za-z\.\-]+)?)") { $version = $matches[1] }
    $timedOut = $false
    if ($result.PSObject.Properties["TimedOut"]) { $timedOut = [bool]$result.TimedOut }
    return [pscustomobject]@{
        ok = ([int]$result.ExitCode -eq 0 -and [bool]$version)
        version = $version
        exit_code = [int]$result.ExitCode
        stderr = (Redact-HrmText [string]$result.Stderr)
        timed_out = $timedOut
    }
}

function Get-HrmDockerCliFacts {
    # Факты о CLI контейнеров для журнала, диагностики и отчёта поддержки:
    # ВСЕ найденные кандидаты (путь, источник, версия файла, дата, корень
    # установки), выбранный путь и правило выбора, путь Docker Desktop и
    # перечень проверенных каталогов.
    # -Probe добавляет запуск «--version» для выбранного CLI (и только для него):
    # проба всех кандидатов — это N процессов CLI, то есть ровно тот симптом
    # (накопление процессов), который чинится этой ревизией.
    param([switch]$Probe, [int]$ProbeTimeoutSeconds = 0)
    $desktopPath = Get-HrmDockerDesktopPath
    $candidates = @(Get-HrmDockerCliCandidates -DesktopPath $desktopPath)
    $choice = Select-HrmDockerCliCandidate -Candidates $candidates -DesktopPath $desktopPath
    $override = Get-HrmDockerOverrideValue "cli_path"
    $cli = [string]$choice.path
    $reason = [string]$choice.reason
    if ($null -ne $override) {
        $cli = [string]$override
        $reason = "переопределение пути CLI (тестовый шов Set-HrmDockerOverride)"
    }
    $cliVersion = ""
    $cliSource = ""
    $cliRoot = ""
    foreach ($item in $candidates) {
        if ([string]$item.path -eq $cli) {
            $cliVersion = [string]$item.file_version
            $cliSource = [string]$item.source
            $cliRoot = [string]$item.install_root
            break
        }
    }
    $probeOk = $false
    $probeVersion = ""
    $probeStderr = ""
    $probeExitCode = 0
    if ($Probe -and $cli) {
        # Имя локальной переменной НЕ должно совпадать с именем параметра даже с
        # точностью до регистра: $Probe — типизированный [switch]-параметр, и
        # присваивание $probe = <объект> приводило бы к преобразованию результата
        # пробы в SwitchParameter (ошибка приведения типа).
        $probeResult = Invoke-HrmDockerCliVersionProbe -CandidatePath $cli -TimeoutSeconds $ProbeTimeoutSeconds
        $probeOk = [bool]$probeResult.ok
        $probeVersion = [string]$probeResult.version
        $probeStderr = [string]$probeResult.stderr
        $probeExitCode = [int]$probeResult.exit_code
        if (-not $cliVersion -and $probeVersion) { $cliVersion = $probeVersion }
    }
    return [pscustomobject]@{
        cli = $cli
        cli_source = $cliSource
        cli_install_root = $cliRoot
        cli_version = $cliVersion
        cli_version_probed = $probeVersion
        cli_probe_ok = $probeOk
        cli_probe_exit_code = $probeExitCode
        cli_probe_stderr = $probeStderr
        probed = [bool]$Probe
        candidates = $candidates
        candidate_count = $candidates.Count
        chosen_reason = $reason
        desktop_path = [string]$desktopPath
        desktop_install_root = (Get-HrmDockerInstallRoot $desktopPath)
        searched_dirs = @(Get-HrmDockerCliDirs)
        search_places = @(Get-HrmDockerCliSearchPlaces)
    }
}

function Get-HrmDockerCliSearchPlaces {
    # Человекочитаемый перечень проверенных мест для отказа «CLI не найден»:
    # только ожидаемые каталоги CLI и факт перебора PATH — без дампа окружения
    # и без списка каталогов PATH (в нём могут быть имена пользователя).
    $places = @()
    foreach ($dir in (Get-HrmDockerCliDirs)) { $places += (Join-Path $dir "docker.exe") }
    $pathDirs = @($env:PATH -split ";" | Where-Object { $_ -and $_.Trim() })
    if ($pathDirs.Count -gt 0) { $places += ("каталоги из PATH ({0} шт.)" -f $pathDirs.Count) }
    return $places
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
    # Возвращает 0, если свободный порт не найден, — вызывающий обязан честно
    # отказать, а не публиковать стек на занятом порту.
    # Тестовый шов: free_port (детерминированный результат подбора).
    param([int]$StartPort = 8080, [int]$Attempts = 20)
    $override = Get-HrmDockerOverrideValue "free_port"
    if ($null -ne $override) { return [int]$override }
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

function Test-HrmPortServedByPilot {
    # Порт занят НАШИМ собственным пилотным стеком: /api/ops/status на этом порту
    # отвечает приложением с окружением pilot. Чужой dev-стек HR Manager
    # (APP_ENV=development) и любое стороннее приложение пилотом НЕ считаются:
    # их HTTP 200 не принадлежит целевой установке (диагностика 12.10.2026 —
    # на 8080 отвечал старый development-стек). Только чтение, без секретов.
    param([int]$Port = 0)
    $port = Get-HrmPort $Port
    try {
        $result = Invoke-HrmHttp -Uri ("http://127.0.0.1:{0}/api/ops/status" -f $port)
    }
    catch { return $false }
    if ($result.StatusCode -ne 200 -and $result.StatusCode -ne 503) { return $false }
    $body = $result.Body
    if ($null -eq $body) { return $false }
    if ($body -is [string]) {
        # 503 приходит сырой строкой (Invoke-HrmHttp): пробуем разобрать JSON.
        try { $body = ($body | ConvertFrom-Json) } catch { return $false }
    }
    if ($null -eq $body -or $body -is [string]) { return $false }
    if (-not $body.PSObject.Properties["environment"]) { return $false }
    return ([string]$body.environment -eq "pilot")
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
    # Вызов идёт через Invoke-HrmDocker: CLI резолвится разрешённым путём, а не
    # голым именем через PATH скрытого процесса (пользовательская установка
    # Docker Desktop). Если CLI не найден, проверка получает структурированный
    # отказ (ExitCode 127) и честно сообщает «не готов» вместо падения с fatal.
    try {
        $info = Invoke-HrmDocker -Arguments @("info", "--format", "{{.ServerVersion}}") -IgnoreExitCode
    }
    catch { return $false }
    return ($info.ExitCode -eq 0 -and [bool]$info.Stdout)
}

function Test-HrmDockerLinuxEngineReady {
    # HR Manager работает в Linux-контейнерах: нужен именно Linux-движок
    # (Docker Desktop по умолчанию). Windows-движок (Windows-контейнеры)
    # для этого стека бесполезен: Linux-образы не собираются и не запускаются.
    # Тестовый шов: linux_engine; при его отсутствии — engine (в мок-мире
    # «готов» подразумевает Linux-движок).
    $override = Get-HrmDockerOverrideValue "linux_engine"
    if ($null -ne $override) { return [bool]$override }
    $engineOverride = Get-HrmDockerOverrideValue "engine"
    if ($null -ne $engineOverride) { return [bool]$engineOverride }
    try {
        $info = Invoke-HrmDocker -Arguments @("info", "--format", "{{.OSType}}") -IgnoreExitCode
    }
    catch { return $false }
    return ($info.ExitCode -eq 0 -and ($info.Stdout.Trim() -eq "linux"))
}

function Wait-HrmDockerEngine {
    # Ожидание готовности Docker Engine с таймаутом и понятным прогрессом.
    # Никаких бесконечных циклов: по истечении таймаута возвращаем $false.
    param([int]$TimeoutSeconds = 300, [int]$IntervalSeconds = 5)
    # К этому моменту Docker Desktop мог быть только что установлен или обновлён
    # (в том же процессе движка): состав и версии CLI контейнеров меняются,
    # поэтому кэш выбранного пути сбрасывается. Без сброса движок продолжал бы
    # звать CLI из прежней установки (регрессия D6). Таймауты ожидания при этом
    # не меняются: 300 с на готовность Engine, 5 с между проверками.
    Reset-HrmDockerCliChoiceCache | Out-Null
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
    # В постоянный журнал пишутся выбранный путь, факт запуска, PID (если
    # доступен) и результат: диагностика 12.10.2026 показала, что без этих
    # данных ожидание Engine выглядит как «просто таймаут 300 секунд».
    param([int]$TimeoutSeconds = 300)
    $desktop = Get-HrmDockerDesktopState
    if ($desktop.state -eq "engine_ready") {
        return [pscustomobject]@{ started = $true; message = "Docker Engine уже работает." }
    }
    if ($desktop.state -eq "not_installed") {
        throw "Docker Desktop не установлен. Установите его официальным установщиком (мастер установки HR Manager предложит это одной кнопкой)."
    }
    $launchedProcess = $null
    if ($desktop.state -eq "installed_stopped") {
        if (-not $desktop.path) {
            # Не исключение: prepare/supervisor должны вернуть понятный код
            # состояния и текст («без crash-loop»), а не падать у пользователя.
            Write-HrmLog "warn" "Docker Desktop установлен (есть CLI/процесс), но путь к EXE не найден: ни реестр, ни стандартные каталоги не дали результата."
            return [pscustomobject]@{
                started = $false
                message = "Не удалось найти Docker Desktop. Откройте Docker Desktop из меню «Пуск», дождитесь надписи «Engine running» и нажмите «Повторить»."
            }
        }
        Write-HrmLog "info" ("Запускаем Docker Desktop… (путь: {0})" -f $desktop.path)
        try {
            $launchedProcess = Start-HrmDetached -FilePath $desktop.path -WorkingDirectory (Split-Path $desktop.path -Parent)
        }
        catch {
            # Ошибка запуска EXE (файл заблокирован, повреждён, нет прав) — это
            # конкретная причина, а не повод молча ждать Engine 300 секунд.
            $reason = Redact-HrmText $_.Exception.Message
            Write-HrmLog "error" ("Не удалось запустить Docker Desktop ({0}): {1}" -f $desktop.path, $reason)
            return [pscustomobject]@{
                started = $false
                message = ("Не удалось запустить Docker Desktop ({0}): {1}. Откройте Docker Desktop вручную из меню «Пуск», дождитесь надписи «Engine running» и нажмите «Повторить»." -f $desktop.path, $reason)
            }
        }
        $launchedPid = 0
        try {
            if ($null -ne $launchedProcess -and $launchedProcess.PSObject.Properties["Id"]) { $launchedPid = [int]$launchedProcess.Id }
        }
        catch { $launchedPid = 0 }
        if ($launchedPid -gt 0) { Write-HrmLog "info" ("Docker Desktop запущен (PID {0})." -f $launchedPid) }
        else { Write-HrmLog "info" "Docker Desktop запущен (PID недоступен)." }
    }
    else {
        Write-HrmLog "info" "Docker Desktop уже запускается — продолжаем ожидание."
    }
    # Быстрый контроль старта: если процесс завершился сразу (диалог лицензии,
    # ошибка WSL2, другая учетная запись), ждать Engine весь таймаут бессмысленно.
    # Одна короткая пауза вместо цикла: живой процесс ничего не ждёт лишнего.
    $earlyExitMessage = ""
    if ($null -ne $launchedProcess -and $launchedProcess.PSObject.Properties["HasExited"]) {
        Start-HrmWaitSleep -Seconds (Get-HrmWaitIntervalSeconds 5)
        if (Test-HrmDockerEngineReady) {
            return [pscustomobject]@{ started = $true; message = "Docker Engine готов." }
        }
        $exited = $false
        try { $exited = [bool]$launchedProcess.HasExited } catch { $exited = $false }
        if ($exited) {
            $exitCode = -1
            try { $exitCode = [int]$launchedProcess.ExitCode } catch { $exitCode = -1 }
            $earlyExitMessage = ("Docker Desktop завершился сразу после запуска (код {0}). Возможные причины: не принято лицензионное соглашение Docker, требуется перезагрузка Windows или обновление WSL2. Откройте Docker Desktop вручную и дождитесь надписи «Engine running», затем нажмите «Повторить»." -f $exitCode)
            Write-HrmLog "error" $earlyExitMessage
        }
    }
    if ($earlyExitMessage) {
        return [pscustomobject]@{ started = $false; message = $earlyExitMessage }
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

function Get-HrmDockerInstallerWaitSeconds {
    # Потолок ожидания установщика Docker Desktop в секундах (открыт для теста,
    # чтобы значение нельзя было незаметно «вылечить» увеличением).
    return $script:DockerInstallerWaitSeconds
}

function Get-HrmDockerInstallerTimeoutMessage {
    # Сообщение для случая «ожидание установщика истекло».
    # Это НЕ ошибка установщика и НЕ отмена UAC: процесс намеренно не завершается
    # принудительно (прерванная установка Docker Desktop хуже явного отказа —
    # после неё Windows может требовать ручного восстановления), поэтому текст
    # обязан говорить три вещи: сколько ждали, что процесс мог остаться живым и
    # что делать человеку. Автоматически второй установщик не запускается.
    param(
        [string]$FilePath = "",
        [int]$TimeoutSeconds = 0,
        [string]$Mode = "установка Docker Desktop"
    )
    $text = "Ожидание установщика Docker Desktop истекло: {0} не завершилась за {1} с. Процесс установщика НЕ завершался принудительно — установка могла ещё идти. Файл установщика: {2}. Что делать: дождитесь окна установщика и завершите установку вручную (лицензию принимаете вы); если окна нет — закройте установщик через Диспетчер задач и нажмите «Установить Docker Desktop» ещё раз. Второй установщик автоматически не запускается."
    return ($text -f $Mode, $TimeoutSeconds, $FilePath)
}

function Install-HrmDockerDesktop {
    # Установка Docker Desktop ТОЛЬКО официальным установщиком Docker.
    #
    # - скачивание: https://desktop.docker.com/win/main/amd64/Docker Desktop Installer.exe
    # - проверка подписи: издатель Docker (иначе отказ);
    # - запуск: с повышением прав (UAC) — отмена UAC не считается ошибкой;
    # - лицензию Docker принимает пользователь в окне установщика; движок НЕ
    #   передаёт --accept-license;
    # - после УСПЕШНОЙ установки фиксируется «продолжить автоматически»; истёкшее
    #   ожидание установщика — отдельный статус installer_timeout (дефект P3):
    #   без автоматического продолжения и без второго установщика.
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
        $waitSeconds = Get-HrmDockerInstallerWaitSeconds
        $silent = Start-HrmElevatedAndWait -FilePath $installerPath -Arguments @("install", "--quiet", "--backend=wsl-2") -TimeoutSeconds $waitSeconds
        if ($silent.CanceledByUser) {
            return [pscustomobject]@{
                status = "uac_declined"
                message = "Установка Docker Desktop отменена: вы не дали разрешение Windows (UAC). Нажмите «Установить Docker Desktop» и подтвердите запрос."
                needs_reboot = $false
            }
        }
        if (Test-HrmResultTimedOut $silent) {
            # Потолок ожидания истёк — это НЕ «код -1» и НЕ отказ: раньше такой
            # случай попадал в ветку ниже, журнал писал «не удалась (код -1)», и
            # движок автоматически открывал обычный мастер, добавляя к часу
            # молчания ещё один час ожидания (дефект P3 дораздела PR #55).
            # Теперь состояние отдельное, текст называет ожидание, файл и
            # следующее действие, а второй установщик не запускается сам.
            $timeoutMessage = Get-HrmDockerInstallerTimeoutMessage -FilePath $installerPath -TimeoutSeconds $waitSeconds -Mode "тихая установка Docker Desktop"
            Write-HrmLog "warn" $timeoutMessage
            return [pscustomobject]@{
                status = "installer_timeout"
                message = $timeoutMessage
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
    $wizardWaitSeconds = Get-HrmDockerInstallerWaitSeconds
    $wizard = Start-HrmElevatedAndWait -FilePath $installerPath -Arguments @() -TimeoutSeconds $wizardWaitSeconds
    if ($wizard.CanceledByUser) {
        return [pscustomobject]@{
            status = "uac_declined"
            message = "Установка Docker Desktop отменена: вы не дали разрешение Windows (UAC). Повторите, когда будете готовы."
            needs_reboot = $false
        }
    }
    if (Test-HrmResultTimedOut $wizard) {
        # То же правило, что и для тихой установки: истёкший потолок ожидания не
        # выдаётся за «код -1» и не превращается в требование перезагрузки
        # (дефект P3 дораздела PR #55). Мастер мог остаться открытым — человек
        # должен это увидеть в тексте, а не в догадках.
        $wizardTimeoutMessage = Get-HrmDockerInstallerTimeoutMessage -FilePath $installerPath -TimeoutSeconds $wizardWaitSeconds -Mode "установка Docker Desktop через мастер"
        Write-HrmLog "warn" $wizardTimeoutMessage
        return [pscustomobject]@{
            status = "installer_timeout"
            message = $wizardTimeoutMessage
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
    $portServedByPilot = $false
    if (-not $portFree) { $portServedByPilot = Test-HrmPortServedByPilot -Port $port }
    $portDetail = ("Порт {0} свободен." -f $port)
    $portStatus = "ok"
    if (-not $portFree -and $portServedByPilot) {
        # Порт занят нашим же пилотом — это не чужая программа, а работающее
        # приложение этой установки. Порт сохраняется.
        $portDetail = ("Порт {0} занят собственным приложением HR Manager (пилот работает на нём)." -f $port)
    }
    elseif (-not $portFree) {
        # Чужое приложение (например, старый development-стек): при запуске
        # будет подобран свободный порт, и он применяется согласованно — запись
        # установки, pilot.env, supervisor, readiness, ссылка первого запуска.
        $portStatus = "warn"
        $portDetail = ("Порт {0} занят другим приложением. При запуске будет подобран свободный порт и применён в окружении и ярлыке." -f $port)
    }
    $steps += [pscustomobject]@{
        key = "port"; label = "Проверяем порт приложения"
        status = $portStatus
        detail = $portDetail
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
    elseif ($desktop.engine_ready -and -not (Test-HrmDockerLinuxEngineReady)) { $state = "engine_not_linux" }
    elseif ($desktop.engine_ready) { $state = "ready" }
    elseif ($desktop.running) { $state = "engine_starting" }
    else { $state = "docker_stopped" }
    $message = "Рабочая среда готова."
    if ($state -eq "docker_missing") { $message = "Docker Desktop не установлен." }
    elseif ($state -eq "engine_not_linux") { $message = "Docker Engine запущен, но это не Linux-движок: HR Manager работает в Linux-контейнерах." }
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
                port = (Get-HrmPort $Port); port_changed = $false
                message = "Установка Docker Desktop завершится после перезагрузки Windows. Перезагрузите компьютер — HR Manager продолжит сам."
            }
        }
    }

    # --- Порт публикации: занятой порт НЕЛЬЗЯ оставлять «как есть» -------------
    # Дефект 12.10.2026: свободный порт подбирался и упоминался в журнале, но
    # $port не менялся — стек публиковался на занятом порту, а в браузере
    # открывался чужой dev-стек. Теперь подобранный порт возвращается
    # вызывающему (поле port), и вызывающий ОБЯЗАН применить его согласованно:
    # pilot.env (HRM_PILOT_PORT), запись установки, supervisor, readiness,
    # first-run URL и ярлык (см. Sync-HrmPortAfterPrepare, Install.psm1).
    # Порт, занятый НАШИМ собственным пилотом, сохраняется: это не чужое
    # приложение, а уже опубликованный стек этой установки.
    $port = Get-HrmPort $Port
    $portChanged = $false
    if (-not (Test-HrmPortAvailable -Port $port)) {
        if (Test-HrmPortServedByPilot -Port $port) {
            Write-HrmLog "info" ("Порт {0} занят собственным приложением HR Manager (пилот уже опубликован) — порт сохраняется." -f $port)
        }
        else {
            $freePort = Find-HrmFreePort -StartPort $port
            if ($freePort -gt 0 -and $freePort -ne $port) {
                Write-HrmLog "warn" ("Порт {0} занят другим приложением. Приложение будет опубликовано на свободном порту {1}: запись установки и окружение пилота обновляются." -f $port, $freePort)
                $port = $freePort
                $portChanged = $true
            }
            elseif ($freePort -eq $port) {
                # Проверка доступности и подбор разошлись (гонка/ошибка учета
                # слушателей): остаёмся на запрошенном порту, честная заметка.
                Write-HrmLog "warn" ("Порт {0} считается занятым, но подбор вернул его же — продолжаю на порту {0}." -f $port)
            }
            else {
                $message = ("Порт {0} занят другим приложением, а свободный порт подобрать не удалось (проверено {1} портов подряд, начиная с {0}). Освободите порт (закройте занявшее его приложение) или запустите установку с параметром -Port <свободный порт>." -f $port, 20)
                Write-HrmLog "error" $message
                return [pscustomobject]@{
                    ok = $false; state = "port_busy"; needs_install = $false; needs_reboot = $false
                    port = $port; port_changed = $false; message = $message
                }
            }
        }
    }

    $desktop = Get-HrmDockerDesktopState
    if ($desktop.state -eq "engine_ready") {
        # Демон отвечает, но стек — Linux-only: Windows-движок (Windows-контейнеры)
        # не соберёт и не запустит эти образы. Честный отказ с понятным текстом
        # (состояние engine_not_linux) вместо падения на сборке образов.
        if (-not (Test-HrmDockerLinuxEngineReady)) {
            return [pscustomobject]@{
                ok = $false; state = "engine_not_linux"; needs_install = $false; needs_reboot = (Test-HrmRebootPending)
                port = $port; port_changed = $portChanged
                message = "Docker Engine запущен, но это не Linux-движок: HR Manager работает в Linux-контейнерах. Запустите Docker Desktop с Linux-движком (WSL2) и повторите попытку."
            }
        }
        Clear-HrmPendingDockerOperation $StateDir
        return [pscustomobject]@{ ok = $true; state = "ready"; message = "Рабочая среда готова."; needs_reboot = $false; needs_install = $false; port = $port; port_changed = $portChanged }
    }
    if ($desktop.state -eq "not_installed") {
        if (-not $AllowInstall) {
            return [pscustomobject]@{
                ok = $false; state = "docker_missing"; needs_install = $true; needs_reboot = $false
                port = $port; port_changed = $portChanged
                message = "Для работы HR Manager нужен Docker Desktop. Нажмите «Установить Docker Desktop» — это официальная программа Docker, установка займёт несколько минут."
            }
        }
        $install = Install-HrmDockerDesktop -StateDir $StateDir -Interactive:$Interactive -AllowSilent
        if ($install.status -eq "installed") {
            Write-HrmLog "info" "Docker Desktop установлен — продолжаем запуск."
        }
        elseif ($install.status -eq "reboot_required" -or $install.status -eq "already_installed") {
            if ($install.needs_reboot) {
                return [pscustomobject]@{ ok = $false; state = "reboot_required"; needs_install = $false; needs_reboot = $true; port = $port; port_changed = $portChanged; message = $install.message }
            }
            if ($install.status -eq "already_installed") {
                Clear-HrmPendingDockerOperation $StateDir
            }
        }
        else {
            return [pscustomobject]@{ ok = $false; state = $install.status; needs_install = ($install.status -eq "uac_declined"); needs_reboot = [bool]$install.needs_reboot; port = $port; port_changed = $portChanged; message = $install.message }
        }
        $desktop = Get-HrmDockerDesktopState
    }
    if ($desktop.state -eq "installed_stopped" -or $desktop.state -eq "starting") {
        # Прогресс для значка в трее: установка идёт долго, человек должен
        # видеть, что происходит, а не пустой значок (дефект P2 ревью).
        try { Set-HrmSupervisorState -StateDir $StateDir -State "starting" -Message "Ожидаем запуск службы контейнеров…" -Busy $true } catch { }
        $start = Start-HrmDockerDesktop -TimeoutSeconds $TimeoutSeconds
        if (-not $start.started) {
            return [pscustomobject]@{ ok = $false; state = "engine_timeout"; needs_install = $false; needs_reboot = (Test-HrmRebootPending); port = $port; port_changed = $portChanged; message = $start.message }
        }
    }
    if (-not (Test-HrmDockerEngineReady)) {
        return [pscustomobject]@{
            ok = $false; state = "engine_not_ready"; needs_install = $false; needs_reboot = (Test-HrmRebootPending)
            port = $port; port_changed = $portChanged
            message = "Служба контейнеров ещё не готова. Подождите минуту и нажмите «Повторить»."
        }
    }
    if (-not (Test-HrmDockerLinuxEngineReady)) {
        return [pscustomobject]@{
            ok = $false; state = "engine_not_linux"; needs_install = $false; needs_reboot = (Test-HrmRebootPending)
            port = $port; port_changed = $portChanged
            message = "Docker Engine запущен, но это не Linux-движок: HR Manager работает в Linux-контейнерах. Запустите Docker Desktop с Linux-движком (WSL2) и повторите попытку."
        }
    }
    Clear-HrmPendingDockerOperation $StateDir
    return [pscustomobject]@{ ok = $true; state = "ready"; message = "Рабочая среда готова."; needs_reboot = $false; needs_install = $false; port = $port; port_changed = $portChanged }
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
