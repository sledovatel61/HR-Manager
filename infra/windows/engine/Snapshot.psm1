# Идентичность снимка ПРЕДЫДУЩЕЙ версии.
#
# Зачем модуль: перед обновлением (и до того, как мастер установки перезапишет
# файлы {app}) прежняя версия сохраняется в каталог состояния ВМЕСТЕ с
# манифестом — относительным путём, размером и sha256 каждого файла — и
# описанием previous-snapshot.json. Откат потом обязан доказать, что в работе
# оказалась ИМЕННО прежняя версия, а не «что-то, что ответило HTTP 200»: без
# манифеста (содержимого) и release_sha (идентификатора) такого доказательства
# нет.
#
# ГРАНИЦЫ МОДУЛЯ:
#   * модуль НЕ полагается на уже УСТАНОВЛЕННЫЙ движок: его использует тонкий CLI
#     hrm-snapshot.ps1, который мастер установки распаковывает из СВОЕГО пакета
#     во временный каталог и запускает до первой перезаписи {app}. Старая версия
#     движка может не знать ни действий, ни формата описания снимка;
#   * модуль не трогает секреты, pilot.env, лицензионный ключ и Docker volumes:
#     снимок — это кодовая часть релиза (infra, backend, frontend, release.json);
#   * снимок создаётся ТОЛЬКО из работающих файлов {app}. Если файлы уже заменены
#     новой версией, сохранять их как «прежнюю» запрещено: откат вернул бы
#     сбойный код и объявил успех.
#
# Зависимости (все — обычные модули движка, без новых библиотек):
#   Common.psm1  — журнал, JSON, sha256, пути каталога состояния;
#   Secrets.psm1 — запись установки (installed.json);
#   Install.psm1 — Copy-HrmSnapshot/Assert-HrmSnapshotComplete (безопасное
#                  копирование снимка с проверкой полноты).

Set-StrictMode -Version 2.0

$script:HrmSnapshotSchema = "hrm-previous-snapshot-1"
$script:HrmSnapshotComponents = @("infra", "backend", "frontend")

# --- Идентификатор релиза в каталоге ----------------------------------------

function Get-HrmSnapshotReleaseSha {
    # release_sha из release.json каталога. Запись установки сюда НЕ
    # подставляется: нужно уметь отличать «в каталоге лежит установленная
    # версия» от «файлы уже заменены новой версией».
    param([string]$Directory)
    return (Get-HrmSnapshotReleaseField -Directory $Directory -Field "release_sha")
}

function Get-HrmSnapshotVersionInDir {
    # version из release.json каталога (у старых сборок может отсутствовать).
    param([string]$Directory)
    return (Get-HrmSnapshotReleaseField -Directory $Directory -Field "version")
}

function Get-HrmSnapshotReleaseField {
    param([string]$Directory, [string]$Field)
    if (-not $Directory) { return "" }
    $file = Join-Path $Directory "release.json"
    if (-not (Test-Path $file)) { return "" }
    try {
        $data = Get-HrmJsonFile $file
        if ($null -ne $data -and $data.PSObject.Properties[$Field] -and $data.PSObject.Properties[$Field].Value) {
            return [string]$data.PSObject.Properties[$Field].Value
        }
    }
    catch { return "" }
    return ""
}

function Get-HrmSnapshotIdentity {
    # Идентификатор прежней версии: запись установки, иначе release.json каталога.
    # Пустая строка означает «идентификатора нет» — это отдельный, строго
    # документированный случай (см. docs/UPDATE_GUIDE.md, «Правило прежней
    # идентичности»).
    param([string]$InstallDir = "", [string]$StateDir = "")
    if ($InstallDir -and -not $StateDir) { $StateDir = Get-HrmStateDir }
    if ($StateDir) {
        $record = Get-HrmInstallRecord $StateDir
        $recordSha = [string](Get-HrmSnapshotField -Metadata $record -Field "release_sha")
        if ($recordSha) { return $recordSha }
    }
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    return (Get-HrmSnapshotReleaseSha -Directory $InstallDir)
}

# --- Манифест снимка --------------------------------------------------------

function Get-HrmSnapshotRelativePath {
    # Путь файла относительно корня снимка — всегда с прямыми слэшами, чтобы
    # манифест не зависел от разделителей пути.
    param([string]$Root, [string]$Path)
    $rootFull = [System.IO.Path]::GetFullPath($Root)
    if (-not $rootFull.EndsWith("\")) { $rootFull = $rootFull + "\" }
    $full = [System.IO.Path]::GetFullPath($Path)
    if ($full.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        return ($full.Substring($rootFull.Length)).Replace("\", "/")
    }
    return (Split-Path $full -Leaf)
}

function Get-HrmSnapshotManifest {
    # Манифест: относительный путь, размер и sha256 каждого файла снимка.
    param([string]$Root)
    $entries = New-Object System.Collections.ArrayList
    if (-not (Test-Path $Root)) { return @() }
    foreach ($name in $script:HrmSnapshotComponents) {
        $dir = Join-Path $Root $name
        if (-not (Test-Path $dir)) { continue }
        foreach ($file in @(Get-ChildItem -Path $dir -Recurse -File -Force -ErrorAction Stop)) {
            $relative = Get-HrmSnapshotRelativePath -Root $Root -Path $file.FullName
            $hash = Get-HrmFileSha256 $file.FullName
            if (-not $hash) { throw ("Не удалось прочитать файл снимка: " + $relative) }
            [void]$entries.Add([pscustomobject]@{
                    path = $relative
                    size = [long]$file.Length
                    sha256 = $hash
                })
        }
    }
    $release = Join-Path $Root "release.json"
    if (Test-Path -LiteralPath $release -PathType Leaf) {
        $releaseFile = Get-Item -LiteralPath $release
        [void]$entries.Add([pscustomobject]@{
                path = "release.json"
                size = [long]$releaseFile.Length
                sha256 = (Get-HrmFileSha256 $releaseFile.FullName)
            })
    }
    return @($entries | Sort-Object -Property path)
}

function Get-HrmSnapshotManifestDigest {
    # Короткий отпечаток манифеста: диагностика и сравнение снимков между собой.
    param([object[]]$Manifest)
    $lines = @()
    foreach ($entry in @($Manifest)) {
        if ($null -eq $entry) { continue }
        $lines += ("{0}`t{1}`t{2}" -f [string]$entry.path, [long]$entry.size, [string]$entry.sha256)
    }
    $text = ""
    if ($lines.Count -gt 0) { $text = (($lines -join "`n") + "`n") }
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { $digest = $sha.ComputeHash($bytes) } finally { $sha.Dispose() }
    return (($digest | ForEach-Object { $_.ToString("x2") }) -join "")
}

function Test-HrmSnapshotManifest {
    # Каждый файл манифеста обязан быть на месте с тем же размером и тем же
    # содержимым. Дополнительные файлы в каталоге не считаются ошибкой: снимок
    # описывает ровно infra/backend/frontend/release.json.
    param([string]$Root, [object[]]$Manifest)
    $problems = New-Object System.Collections.ArrayList
    $checked = 0
    foreach ($entry in @($Manifest)) {
        if ($null -eq $entry) { continue }
        $relative = [string]$entry.path
        if (-not $relative) {
            [void]$problems.Add("в манифесте снимка пустой путь")
            continue
        }
        $path = Join-Path $Root ($relative -replace "/", "\")
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            [void]$problems.Add("нет файла " + $relative)
            continue
        }
        try { $file = Get-Item -LiteralPath $path } catch {
            [void]$problems.Add("не удалось прочитать файл " + $relative)
            continue
        }
        if ([long]$entry.size -ne [long]$file.Length) {
            [void]$problems.Add("размер " + $relative + " не совпадает")
            continue
        }
        if ((Get-HrmFileSha256 $path) -ne [string]$entry.sha256) {
            [void]$problems.Add("содержимое " + $relative + " не совпадает")
            continue
        }
        $checked++
    }
    return [pscustomobject]@{
        ok = ($problems.Count -eq 0)
        checked = $checked
        total = @($Manifest).Count
        problems = @($problems)
    }
}

# --- Описание снимка --------------------------------------------------------

function Get-HrmPreviousSnapshotMetadata {
    # Описание проверенного снимка (previous-snapshot.json) или $null.
    param([string]$StateDir)
    $file = Join-Path $StateDir "previous-snapshot.json"
    if (-not (Test-Path $file)) { return $null }
    try { return (Get-HrmJsonFile $file) } catch { return $null }
}

function Get-HrmSnapshotField {
    param($Metadata, [string]$Field)
    if ($null -eq $Metadata) { return "" }
    if ($Metadata.PSObject.Properties[$Field] -and $null -ne $Metadata.PSObject.Properties[$Field].Value) {
        return $Metadata.PSObject.Properties[$Field].Value
    }
    return ""
}

function Test-HrmPreviousSnapshotUsable {
    # Годен ли уже сохранённый снимок как «прежняя версия» с ожидаемым
    # идентификатором. Проверяется описание (verified), release_sha и манифест
    # файлов: снимок без описания или с расхождением содержимого прежней
    # версией не считается.
    #
    # Ожидаемый sha передаётся явно; пустой ожидаемый sha допускается только
    # при описании снимка с пустым sha (legacy-версия без идентификатора).
    param([string]$StateDir, [string]$ReleaseSha = "")
    $metadata = Get-HrmPreviousSnapshotMetadata -StateDir $StateDir
    if ($null -eq $metadata) {
        return [pscustomobject]@{ usable = $false; reason = "no_descriptor"; metadata = $null }
    }
    if (-not [bool](Get-HrmSnapshotField -Metadata $metadata -Field "verified")) {
        return [pscustomobject]@{ usable = $false; reason = "not_verified"; metadata = $metadata }
    }
    $snapshotSha = [string](Get-HrmSnapshotField -Metadata $metadata -Field "release_sha")
    # Источник ожидаемой идентичности: передан вызывающим, либо (для старых
    # установок без release_sha) идентификатор берётся из самого снимка и
    # называется явно — вызывающий обязан проверить его по версии в работе.
    $identitySource = "none"
    if ($ReleaseSha) {
        if (-not $snapshotSha) {
            return [pscustomobject]@{ usable = $false; reason = "snapshot_sha_missing"; metadata = $metadata }
        }
        if ($snapshotSha -ne $ReleaseSha) {
            return [pscustomobject]@{ usable = $false; reason = "release_sha_mismatch"; metadata = $metadata }
        }
        $identitySource = "expected"
    }
    elseif ($snapshotSha) {
        $identitySource = "snapshot"
    }
    $dir = Get-HrmPreviousSnapshotDir $StateDir
    if (-not (Test-Path (Join-Path $dir "infra\compose.pilot.yml"))) {
        return [pscustomobject]@{ usable = $false; reason = "snapshot_dir_missing"; metadata = $metadata }
    }
    $manifest = @(Get-HrmSnapshotField -Metadata $metadata -Field "manifest")
    if ($manifest.Count -eq 0) {
        return [pscustomobject]@{ usable = $false; reason = "manifest_missing"; metadata = $metadata }
    }
    $check = Test-HrmSnapshotManifest -Root $dir -Manifest $manifest
    if (-not $check.ok) {
        return [pscustomobject]@{ usable = $false; reason = "snapshot_incomplete"; metadata = $metadata; problems = $check.problems }
    }
    return [pscustomobject]@{ usable = $true; reason = ""; metadata = $metadata; problems = @(); identity_source = $identitySource; identity = $snapshotSha }
}

function Write-HrmSnapshotDescriptor {
    # Описание снимка. verified = true означает: файлы снимка проверены
    # манифестом, а release_sha — идентификатор той версии, что была сохранена.
    param(
        [string]$StateDir,
        [string]$SnapshotDir,
        [string]$ReleaseSha,
        [string]$Version,
        [string]$Origin,
        [object[]]$Manifest,
        [long]$Bytes
    )
    $descriptor = [ordered]@{
        schema = $script:HrmSnapshotSchema
        verified = $true
        origin = $Origin
        release_sha = $ReleaseSha
        version = $Version
        snapshot_dir = $SnapshotDir
        files = @($Manifest).Count
        bytes = [long]$Bytes
        digest = (Get-HrmSnapshotManifestDigest -Manifest @($Manifest))
        created_at = (Get-Date).ToString("o")
        manifest = @($Manifest)
    }
    Set-HrmJsonFile $StateDir "previous-snapshot.json" $descriptor
    return $descriptor
}

function Write-HrmSnapshotResultFile {
    # Машиночитаемый результат для мастера установки: строки key=value, только
    # коды, хеши и статусы. Секретов и пользовательских данных здесь нет.
    param(
        [string]$Path,
        [string]$Status,
        [string]$Reason,
        [string]$ReleaseSha = "",
        [int]$Files = 0,
        [string]$Digest = ""
    )
    if (-not $Path) { return }
    $lines = @(
        ("status=" + $Status),
        ("reason=" + $Reason),
        ("release_sha=" + $ReleaseSha),
        ("files=" + $Files),
        ("digest=" + $Digest),
        ("written_at=" + (Get-Date).ToString("o"))
    )
    try {
        $dir = Split-Path $Path -Parent
        if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        [System.IO.File]::WriteAllLines($Path, [string[]]$lines, (New-Object System.Text.UTF8Encoding($false)))
    }
    catch { }
}

function Resolve-HrmPreviousSnapshot {
    # Найти пригодный снимок прежней версии. Если описания нет (снимок оставила
    # ПРЕДЫДУЩАЯ версия движка, он писал только файлы), снимок принимается
    # (adoption) при условии, что release.json снимка совпадает с ожидаемым
    # идентификатором: манифест считается по найденным файлам, описание
    # записывается, и дальше откат может доказать целостность содержимого.
    # Без этого обновление с версии без описания снимка теряло бы откат.
    param([string]$StateDir, [string]$ReleaseSha = "")
    $usable = Test-HrmPreviousSnapshotUsable -StateDir $StateDir -ReleaseSha $ReleaseSha
    if ($usable.usable) { return $usable }
    $metadata = Get-HrmPreviousSnapshotMetadata -StateDir $StateDir
    if ($null -ne $metadata) {
        # Описание есть, но снимок не пригоден: подменять вывод нельзя.
        return $usable
    }
    $dir = Get-HrmPreviousSnapshotDir $StateDir
    if (-not (Test-Path (Join-Path $dir "infra\compose.pilot.yml"))) { return $usable }
    $snapshotSha = Get-HrmSnapshotReleaseSha -Directory $dir
    if ($ReleaseSha -and $snapshotSha -ne $ReleaseSha) {
        return [pscustomobject]@{ usable = $false; reason = "release_sha_mismatch"; metadata = $null }
    }
    try {
        $manifest = @(Get-HrmSnapshotManifest -Root $dir)
        if ($manifest.Count -eq 0) {
            return [pscustomobject]@{ usable = $false; reason = "snapshot_incomplete"; metadata = $null }
        }
        $bytes = [long]0
        foreach ($entry in $manifest) { $bytes += [long]$entry.size }
        Write-HrmSnapshotDescriptor -StateDir $StateDir -SnapshotDir $dir -ReleaseSha $snapshotSha `
            -Version (Get-HrmSnapshotVersionInDir -Directory $dir) -Origin "adopted" -Manifest $manifest -Bytes $bytes | Out-Null
        $null = Write-HrmLog "info" ("Снимок прежней версии предыдущего движка проверен и принят: {0} файл(ов)." -f $manifest.Count)
        return (Test-HrmPreviousSnapshotUsable -StateDir $StateDir -ReleaseSha $ReleaseSha)
    }
    catch {
        $null = Write-HrmLog "warn" ("Снимок прежней версии не удалось проверить: " + (Redact-HrmText $_.Exception.Message))
        return [pscustomobject]@{ usable = $false; reason = "snapshot_incomplete"; metadata = $null }
    }
}

function Format-HrmSnapshotProblem {
    # Простое объяснение причины, по которой снимок прежней версии не принят
    # (для сообщения владельцу и журнала; без технических кодов).
    param([string]$Reason = "")
    switch ($Reason) {
        "no_descriptor" { return "сохранённый снимок прежней версии не найден" }
        "snapshot_dir_missing" { return "каталог снимка прежней версии не найден" }
        "not_verified" { return "снимок прежней версии не проверен" }
        "snapshot_sha_missing" { return "у снимка прежней версии нет идентификатора версии" }
        "release_sha_mismatch" { return "снимок относится к другой версии, а не к установленной" }
        "manifest_missing" { return "у снимка прежней версии нет манифеста файлов" }
        "snapshot_incomplete" { return "снимок прежней версии повреждён или неполон" }
        default { return "проверенный снимок прежней версии недоступен" }
    }
}

# --- Создание проверенного снимка -------------------------------------------

function New-HrmVerifiedSnapshotFromDir {
    # Копирование каталога как «прежней версии»: копия делается во временный
    # каталог, проверяется манифестом и только потом подменяет снимок. Так
    # неудачная попытка не уничтожает уже сохранённый рабочий снимок.
    # -SkipLicenseKey: снимок ПРЕДЫДУЩЕЙ (установленной) версии — у старой
    # установки публичного ключа лицензии в каталоге может не быть вовсе, а
    # обновление обязано пройти (ключ восстановится из нового релиза).
    param(
        [string]$SourceDir,
        [string]$StateDir,
        [string]$ReleaseSha = "",
        [string]$Version = "",
        [string]$Origin = "engine",
        [switch]$SkipLicenseKey
    )
    $target = Get-HrmPreviousSnapshotDir $StateDir
    if (-not $SourceDir -or -not (Test-Path $SourceDir)) {
        return [pscustomobject]@{
            saved = $false; verified = $false; skipped = $false; reason = "source_missing"
            digest = ""; files = 0; bytes = 0; release_sha = $ReleaseSha
            message = "Каталог прежней версии не найден — снимок не сохранён."
        }
    }
    $temp = Join-Path $StateDir ("previous-snapshot.tmp-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
    $backup = $target + ".old"
    try {
        if (Test-Path $temp) { Remove-Item -Path $temp -Recurse -Force -ErrorAction Stop }
        New-Item -ItemType Directory -Path $temp -Force -ErrorAction Stop | Out-Null
        # Тот же безопасный копировщик, что и при обновлении: он проверяет
        # границы путей и полноту снимка (infra, backend, frontend,
        # release.json) и бросает исключение вместо «половинчатой» копии.
        Copy-HrmSnapshot -SourceDir $SourceDir -InstallDir $temp -SkipLicenseKey:$SkipLicenseKey
        Assert-HrmSnapshotComplete -Dir $temp -Label "Снимок прежней версии" -SkipLicenseKey:$SkipLicenseKey | Out-Null
        $manifest = @(Get-HrmSnapshotManifest -Root $temp)
        if ($manifest.Count -eq 0) { throw "Манифест снимка прежней версии пуст — снимок недостоверен." }
        $check = Test-HrmSnapshotManifest -Root $temp -Manifest $manifest
        if (-not $check.ok) {
            throw ("Копия снимка не совпала с манифестом: " + (($check.problems | Select-Object -First 3) -join "; "))
        }
        $bytes = [long]0
        foreach ($entry in $manifest) { $bytes += [long]$entry.size }
        if (Test-Path $backup) { Remove-Item -Path $backup -Recurse -Force -ErrorAction SilentlyContinue }
        $replaced = $false
        if (Test-Path $target) {
            Move-Item -Path $target -Destination $backup -Force -ErrorAction Stop
            $replaced = $true
        }
        Move-Item -Path $temp -Destination $target -Force -ErrorAction Stop
        if ($replaced -and (Test-Path $backup)) { Remove-Item -Path $backup -Recurse -Force -ErrorAction SilentlyContinue }
        $descriptor = Write-HrmSnapshotDescriptor -StateDir $StateDir -SnapshotDir $target -ReleaseSha $ReleaseSha -Version $Version -Origin $Origin -Manifest $manifest -Bytes $bytes
        $null = Write-HrmLog "info" ("Снимок прежней версии сохранён и проверен: {0} файл(ов), отпечаток {1}." -f $manifest.Count, ([string]$descriptor.digest).Substring(0, 12))
        return [pscustomobject]@{
            saved = $true; verified = $true; skipped = $false; reason = ""
            digest = [string]$descriptor.digest; files = $manifest.Count; bytes = $bytes
            release_sha = $ReleaseSha
            message = "Снимок предыдущей версии сохранён и проверен."
        }
    }
    catch {
        $message = Redact-HrmText $_.Exception.Message
        if (Test-Path $temp) { Remove-Item -Path $temp -Recurse -Force -ErrorAction SilentlyContinue }
        if (-not (Test-Path $target) -and $backup -and (Test-Path $backup)) {
            # Прежний снимок не должен потеряться из-за неудачной подмены.
            Move-Item -Path $backup -Destination $target -Force -ErrorAction SilentlyContinue
        }
        $null = Write-HrmLog "warn" ("Снимок прежней версии не сохранён: " + $message)
        return [pscustomobject]@{
            saved = $false; verified = $false; skipped = $false; reason = "copy_failed"
            digest = ""; files = 0; bytes = 0; release_sha = $ReleaseSha
            message = $message
        }
    }
}

function Save-HrmVerifiedPreviousSnapshot {
    # Снимок УСТАНОВЛЕННОЙ версии ДО того, как мастер установки заменит файлы
    # {app}. Возвращает @{saved; verified; skipped; reason; message}:
    #   saved/verified = true — снимок готов (или уже был готов) и проверен;
    #   skipped = true        — сохранять нечего: установки ещё не было;
    #   saved = false         — снимок нужен, но не получился. Мастер обязан
    #                           остановиться, пока ничего не перезаписано.
    param(
        [string]$InstallDir = "",
        [string]$StateDir = "",
        [string]$ResultFile = "",
        # Идентификатор НОВОГО релиза этого пакета (release.json рядом с CLI).
        # Нужен, чтобы отличить «в {app} лежит прежняя версия» от «мастер уже
        # разложил файлы новой версии» даже когда запись установки не содержит
        # release_sha (старые установки): снимок из новых файлов вернул бы откат
        # к сбойному коду и объявил успех.
        [string]$NewReleaseSha = ""
    )
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    # Каталог состояния здесь НЕ создаём: на чистой машине его создаёт движок
    # (со строгими правами на каталог), а мастеру установки важно лишь одно —
    # есть ли что сохранять. Создание каталога «на будущее» ослабило бы права.
    $record = $null
    if (Test-Path $StateDir) { $record = Get-HrmInstallRecord $StateDir }
    if ($null -eq $record) {
        # Свежая установка: прежней версии нет, останавливать мастера не за чем.
        Write-HrmSnapshotResultFile -Path $ResultFile -Status "skipped" -Reason "first_install"
        return [pscustomobject]@{
            saved = $false; verified = $false; skipped = $true; reason = "first_install"
            message = "Установка ещё не выполнялась — сохранять нечего."
        }
    }
    $installedSha = [string](Get-HrmSnapshotField -Metadata $record -Field "release_sha")
    $installedVersion = [string](Get-HrmSnapshotField -Metadata $record -Field "version")
    if (-not $installedVersion) { $installedVersion = Get-HrmSnapshotVersionInDir -Directory $InstallDir }
    $fileSha = Get-HrmSnapshotReleaseSha -Directory $InstallDir
    # Идентификатор прежней версии: запись установки, иначе release.json каталога.
    # Пустая строка возможна только у очень старых установок — это отдельный,
    # строго документированный случай (см. docs/UPDATE_GUIDE.md).
    $identity = $installedSha
    if (-not $identity) { $identity = $fileSha }
    if ((Test-Path $InstallDir) -and
        (-not (Test-Path (Join-Path $InstallDir "infra\compose.pilot.yml")))) {
        # Запись установки есть, а файлов программы в каталоге нет: сохранять
        # нечего (прежней версии на диске уже нет). Это не отказ: установка
        # может продолжаться, а политика отката честно скажет, что прежняя
        # версия недоступна.
        Write-HrmSnapshotResultFile -Path $ResultFile -Status "skipped" -Reason "no_previous_files"
        return [pscustomobject]@{
            saved = $false; verified = $false; skipped = $true; reason = "no_previous_files"
            message = "Файлов предыдущей версии в каталоге установки нет — сохранять нечего."
        }
    }
    # Снимок этой же версии уже есть (например, установка уже обновлялась и
    # откатывалась): второй раз не копируем. Этот случай проверяется ДО
    # сравнения с новым пакетом: после прерванной установки {app} может уже
    # содержать новый release.json, но валидный снимок старой версии позволяет
    # безопасно продолжить восстановление.
    $existing = Resolve-HrmPreviousSnapshot -StateDir $StateDir -ReleaseSha $identity
    if ($existing.usable) {
        $metadata = $existing.metadata
        Write-HrmSnapshotResultFile -Path $ResultFile -Status "verified" -Reason "reused" -ReleaseSha $identity `
            -Files ([int](Get-HrmSnapshotField -Metadata $metadata -Field "files")) `
            -Digest ([string](Get-HrmSnapshotField -Metadata $metadata -Field "digest"))
        return [pscustomobject]@{
            saved = $false; verified = $true; skipped = $true; reason = "reused"
            message = "Проверенный снимок этой версии уже сохранён."
        }
    }
    if ($NewReleaseSha -and $fileSha -and ($fileSha -eq $NewReleaseSha)) {
        # В {app} уже лежат файлы НОВОГО релиза, а валидного снимка прежней
        # версии нет: это результат прерванной установки, и продолжать нельзя.
        $message = "Файлы каталога установки уже заменены новой версией — снимок прежней версии невозможен."
        Write-HrmSnapshotResultFile -Path $ResultFile -Status "failed" -Reason "files_replaced" -ReleaseSha $identity
        return [pscustomobject]@{
            saved = $false; verified = $false; skipped = $false; reason = "files_replaced"
            message = $message
        }
    }
    if ($fileSha -and $identity -and $fileSha -ne $identity) {
        # Файлы {app} уже заменены новой версией: снимок из них был бы НОВЫМ
        # кодом. Это отказ, а не «продолжаем молча»: откат должен быть возможен.
        $message = ("Файлы каталога установки уже не совпадают с установленной версией ({0} вместо {1}) — снимок прежней версии невозможен." -f $fileSha, $identity)
        Write-HrmSnapshotResultFile -Path $ResultFile -Status "failed" -Reason "files_replaced" -ReleaseSha $identity
        return [pscustomobject]@{
            saved = $false; verified = $false; skipped = $false; reason = "files_replaced"
            message = $message
        }
    }
    # Снимок УСТАНОВЛЕННОЙ версии: у старой установки ключа лицензии в {app}
    # может не быть — снимок прежней версии не требует его (ключ восстановится
    # из нового релиза при обновлении).
    $created = New-HrmVerifiedSnapshotFromDir -SourceDir $InstallDir -StateDir $StateDir -ReleaseSha $identity -Version $installedVersion -Origin "installer" -SkipLicenseKey
    if ($created.verified) {
        Write-HrmSnapshotResultFile -Path $ResultFile -Status "verified" -Reason "saved" -ReleaseSha $identity -Files $created.files -Digest $created.digest
    }
    else {
        Write-HrmSnapshotResultFile -Path $ResultFile -Status "failed" -Reason $created.reason -ReleaseSha $identity
    }
    return $created
}

function Invoke-HrmSnapshotCli {
    # Вход для мастера установки (тонкий CLI hrm-snapshot.ps1). Код возврата
    # читает мастер, поэтому «продолжаем молча» здесь быть не может:
    #   0 — можно продолжать: снимок прежней версии сохранён и проверен ЛИБО
    #       сохранять нечего (первая установка, файлов прежней версии нет,
    #       проверенный снимок уже есть);
    #   3 — снимок НУЖЕН, но не получился (в том числе файлы {app} уже заменены
    #       новой версией): мастер обязан остановиться ДО перезаписи файлов;
    #   1 — непредвиденная ошибка (тоже остановка).
    param(
        [string]$InstallDir = "",
        [string]$StateDir = "",
        [string]$ResultFile = "",
        [string]$PackageReleaseDir = ""
    )
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $newReleaseSha = ""
    if ($PackageReleaseDir) { $newReleaseSha = Get-HrmSnapshotReleaseSha -Directory $PackageReleaseDir }
    try {
        $result = Save-HrmVerifiedPreviousSnapshot -InstallDir $InstallDir -StateDir $StateDir `
            -ResultFile $ResultFile -NewReleaseSha $newReleaseSha
    }
    catch {
        $message = Redact-HrmText $_.Exception.Message
        Write-HrmSnapshotResultFile -Path $ResultFile -Status "failed" -Reason "helper_error"
        Write-Host ("Снимок предыдущей версии не подготовлен: " + $message)
        return 1
    }
    if ($result.verified -or $result.skipped) {
        # Сводка — только в консоль, не в выходной поток: иначе значение
        # функции стало бы массивом и код возврата потерялся бы.
        Write-Host ("Снимок предыдущей версии: " + (Format-HrmSnapshotReason -Reason $result.reason))
        return 0
    }
    Write-Host ("Не удалось подготовить откат к предыдущей версии: " + (Format-HrmSnapshotReason -Reason $result.reason))
    return 3
}

# --- Тексты причин для человека ---------------------------------------------

function Format-HrmSnapshotReason {
    # Простое объяснение результата снимка для мастера установки (без Docker и
    # PowerShell: «технические» коды остаются в журнале).
    param([string]$Reason = "")
    switch ($Reason) {
        "first_install" { return "Прежней версии нет — это первая установка." }
        "reused" { return "Снимок предыдущей версии уже сохранён." }
        "saved" { return "Снимок предыдущей версии сохранён и проверен." }
        "files_replaced" { return "Файлы программы уже заменены новой версией — вернуть прежнюю автоматически нельзя." }
        "copy_failed" { return "Не удалось сохранить копию предыдущей версии." }
        "source_missing" { return "Каталог предыдущей версии не найден." }
        "no_previous_files" { return "Файлов предыдущей версии в каталоге установки нет — сохранять нечего." }
        "helper_error" { return "Подготовка отката к предыдущей версии завершилась ошибкой." }
        default { return "Не удалось подготовить откат к предыдущей версии." }
    }
}
