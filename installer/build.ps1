# Сборка HR Manager Setup.exe из исходников репозитория.
#
# Инструментальная цепочка ЗАКРЕПЛЕНА (см. installer/README.md):
#   Inno Setup 6.7.3, официальный установщик с GitHub Releases.
#   URL:    https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe
#   SHA256: 9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732
# Никакие другие версии/источники не используются; установщик запускается
# молча (/VERYSILENT) ТОЛЬКО на сборочной машине/CI, никогда на машине
# пользователя - пользователю доставляется готовый HR-Manager-Setup.exe.
#
# Результат: installer/output/HR-Manager-Setup-<Version>.exe и
# installer/release-manifest.json (release_sha, версия, хеши пакета и exe).
#
# ИСПОЛЬЗОВАНИЕ (Windows 10/11, PowerShell 5.1+ или pwsh):
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1 [-Version 0.13.0]

[CmdletBinding()]
param(
    [string]$Version = "0.13.0",
    [string]$TrustStoreFile = "",
    [string]$TrustStoreSha256 = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$installerDir = $PSScriptRoot
$repoRoot = Split-Path $installerDir -Parent
$stagingDir = Join-Path $installerDir "staging"
$outputDir = Join-Path $installerDir "output"
$cacheDir = Join-Path $installerDir ".cache"

$InnoUrl = "https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe"
$InnoSha256 = "9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732"

function Write-HrmUtf8NoBom {
    # Windows PowerShell 5.1 пишет Set-Content -Encoding UTF8 с BOM, а Python
    # (publish_channel.py, sign.ps1-верификатор) читает JSON через json.loads,
    # который BOM не принимает. Все JSON-артефакты пишем без BOM.
    param([string]$Path, [string]$Text)
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

function Invoke-RobocopyMirror {
    param([string]$Source, [string]$Destination, [string[]]$ExcludeDirs = @())
    $robocopyArgs = @(('"{0}"' -f $Source), ('"{0}"' -f $Destination), "/MIR", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")
    foreach ($dir in $ExcludeDirs) { $robocopyArgs += "/XD"; $robocopyArgs += ('"{0}"' -f $dir) }
    $process = Start-Process -FilePath "robocopy.exe" -ArgumentList $robocopyArgs -Wait -PassThru -NoNewWindow
    if ($process.ExitCode -ge 8) {
        throw "robocopy $Source -> $Destination завершился с кодом $($process.ExitCode)"
    }
}

Write-Host "== HR Manager installer build =="

if (Test-Path $stagingDir) { Remove-Item $stagingDir -Recurse -Force }
if (Test-Path $outputDir) { Remove-Item $outputDir -Recurse -Force }
New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null

$innoExe = Join-Path $cacheDir "innosetup-6.7.3.exe"
if (-not (Test-Path $innoExe)) {
    Write-Host "Downloading Inno Setup 6.7.3 (pinned) from $InnoUrl ..."
    try {
        Invoke-WebRequest -Uri $InnoUrl -OutFile $innoExe -UseBasicParsing -ErrorAction Stop
        Write-Host "Invoke-WebRequest succeeded, file size $((Get-Item $innoExe).Length) bytes"
    } catch {
        Write-Host "Invoke-WebRequest failed: $_"
        Write-Host "Trying curl.exe fallback..."
        & curl.exe -L -o $innoExe $InnoUrl
        if ($LASTEXITCODE -ne 0) { throw "curl.exe failed with exit $LASTEXITCODE : $_" }
        Write-Host "curl.exe succeeded, file size $((Get-Item $innoExe).Length) bytes"
    }
}
Write-Host "Verifying Inno Setup hash..."
$hash = (Get-FileHash -Path $innoExe -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "Inno hash: $hash expected $InnoSha256"
if ($hash -ne $InnoSha256.ToLowerInvariant()) {
    throw "SHA256 установщика Inno Setup не совпал: $hash (ожидался $InnoSha256)"
}
Write-Host "Inno Setup 6.7.3 SHA256 verified."
$iscc = Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"
Write-Host "Checking ISCC at $iscc : $(Test-Path $iscc)"
if (-not (Test-Path $iscc)) {
    Write-Host "ISCC not at default, searching for ISCC.exe..."
    try {
      $found = Get-ChildItem -Path "C:\Program Files*" -Recurse -Filter "ISCC.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($found) { $iscc = $found.FullName; Write-Host "Found ISCC at $iscc" }
      else { Write-Host "ISCC not found via search, will try install" }
    } catch { Write-Host "Search failed: $_" }
}
if (-not (Test-Path $iscc)) {
    Write-Host "Installing Inno Setup silently (build machine only)..."
    $p = Start-Process -FilePath $innoExe -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER" -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "Inno Setup install failed: $($p.ExitCode)" }
    Write-Host "Install completed, checking again $iscc : $(Test-Path $iscc)"
    if (-not (Test-Path $iscc)) {
      # Try alternative location after install (Program Files)
      $found2 = Get-ChildItem -Path "C:\Program Files*" -Recurse -Filter "ISCC.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($found2) { $iscc = $found2.FullName; Write-Host "Found after install at $iscc" }
    }
}
Write-Host "Final ISCC path: $iscc exists $(Test-Path $iscc)"

$appStaging = Join-Path $stagingDir "app"
New-Item -ItemType Directory -Path $appStaging -Force | Out-Null
Write-Host "Staging backend/..."
Invoke-RobocopyMirror (Join-Path $repoRoot "backend") (Join-Path $appStaging "backend") @("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "tests", ".venv", "venv")
Write-Host "Staging frontend/ (без node_modules и dist)..."
Invoke-RobocopyMirror (Join-Path $repoRoot "frontend") (Join-Path $appStaging "frontend") @("node_modules", "dist", ".vite")
Write-Host "Staging infra/..."
$pythonCruft = @("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "venv")
Invoke-RobocopyMirror (Join-Path $repoRoot "infra") (Join-Path $appStaging "infra") $pythonCruft

Write-Host "Resolving release_sha..."
$releaseSha = if ($env:HRM_RELEASE_SHA) {
    Write-Host "Using HRM_RELEASE_SHA from env: $env:HRM_RELEASE_SHA"
    $env:HRM_RELEASE_SHA
}
else {
    Write-Host "Running git rev-parse HEAD in $repoRoot"
    $gitOut = & git -C $repoRoot rev-parse HEAD 2>&1
    if ($LASTEXITCODE -ne 0) { throw "git rev-parse failed ${LASTEXITCODE}: $gitOut" }
    $gitOut.Trim()
}
Write-Host "release_sha: $releaseSha"
$releaseJson = [ordered]@{
    release_sha = $releaseSha
    version = $Version
    built_at = (Get-Date).ToString("o")
    installer_commit = $releaseSha
}
Write-HrmUtf8NoBom -Path (Join-Path $appStaging "release.json") -Text ($releaseJson | ConvertTo-Json)

$trustStoreInfo = $null
if ($TrustStoreFile) {
    Write-Host "Resolving trust store: $TrustStoreFile (cwd $(Get-Location))"
    if (-not (Test-Path $TrustStoreFile)) { throw "TrustStoreFile not found: $TrustStoreFile (cwd $(Get-Location))" }
    $trustStorePath = (Resolve-Path $TrustStoreFile).Path
    Write-Host "Resolved trust store to $trustStorePath"
    $actualSha = (Get-FileHash -Path $trustStorePath -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Host "Trust store actual SHA256: $actualSha"
    Write-Host "Trust store expected SHA256: '$TrustStoreSha256'"
    if (-not $TrustStoreSha256) { throw "TrustStoreSha256 is empty - GITHUB_ENV not propagated? actual $actualSha" }
    if ($actualSha -ne $TrustStoreSha256.ToLowerInvariant()) {
        throw ("SHA256 trust store не совпал: {0} (ожидался {1})" -f $actualSha, $TrustStoreSha256)
    }
    $trustStoreText = [System.IO.File]::ReadAllText($trustStorePath, [System.Text.Encoding]::UTF8)
    if ($trustStoreText -match "PRIVATE KEY|BEGIN .*PRIVATE") {
        throw "trust store содержит приватный материал - сборка installer'а остановлена"
    }
    $trustStoreJson = $trustStoreText | ConvertFrom-Json
    $keyIds = @()
    foreach ($property in $trustStoreJson.PSObject.Properties) { $keyIds += $property.Name }
    if ($keyIds.Count -eq 0) { throw "trust store пуст: канал без доверенных ключей собирать нельзя" }
    $trustStoreTarget = Join-Path $appStaging "infra\release"
    New-Item -ItemType Directory -Path $trustStoreTarget -Force | Out-Null
    [System.IO.File]::WriteAllText(
        (Join-Path $trustStoreTarget "trust-store.json"),
        $trustStoreText,
        (New-Object System.Text.UTF8Encoding($false))
    )
    $trustStoreInfo = [ordered]@{
        embedded = $true
        file = "infra/release/trust-store.json"
        sha256 = $actualSha
        keys = $keyIds
    }
    Write-Host ("Trust store встроен: {0} ключ(а), sha256 {1}" -f $keyIds.Count, $actualSha.Substring(0, 16))
}
else {
    $trustStoreInfo = [ordered]@{
        embedded = $false
        reason = "trust store не передан (локальная сборка); production-релиз требует его обязательно"
    }
}

Write-Host "Compiling installer with ISCC..."
Write-Host "ISCC path: $iscc exists $(Test-Path $iscc)"
Write-Host "ISS path: $(Join-Path $installerDir 'installer.iss') exists $(Test-Path (Join-Path $installerDir 'installer.iss'))"
$stagedCount = @(Get-ChildItem $stagingDir -Recurse -File).Count
Write-Host "Staged files: $stagedCount"
if (@(Get-ChildItem $stagingDir -Recurse -Directory -Filter "__pycache__").Count -gt 0) {
    throw "в staging попал __pycache__: исключение кэша Python не сработало"
}
& $iscc (Join-Path $installerDir "installer.iss") ("/DAppVersion=" + $Version)
if ($LASTEXITCODE -ne 0) { throw "ISCC failed: $LASTEXITCODE" }
Write-Host "ISCC succeeded"

$setupExe = Join-Path $outputDir ("HR-Manager-Setup-" + $Version + ".exe")
if (-not (Test-Path $setupExe)) { throw "Установщик не создан: $setupExe" }

Write-Host "Writing release manifest..."
$fileHashes = [ordered]@{}
$packageFiles = Get-ChildItem -Path $appStaging -Recurse -File | Sort-Object FullName
foreach ($file in $packageFiles) {
    $relative = $file.FullName.Substring($stagingDir.Length + 1).Replace("\", "/")
    $fileHashes[$relative] = (Get-FileHash -Path $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
}
$manifest = [ordered]@{
    product = "hr-manager-pilot-windows"
    version = $Version
    release_sha = $releaseSha
    toolchain = [ordered]@{
        name = "Inno Setup"
        version = "6.7.3"
        installer_url = $InnoUrl
        installer_sha256 = $InnoSha256
        source = "https://github.com/jrsoftware/issrc (official release)"
    }
    installer_exe = [ordered]@{
        file = "HR-Manager-Setup-" + $Version + ".exe"
        sha256 = (Get-FileHash -Path $setupExe -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    trust_store = $trustStoreInfo
    signing = [ordered]@{
        status = "unsigned"
        instruction = "installer/README.md (раздел 'Кодовая подпись') и installer/sign.ps1"
    }
    package_files_sha256 = $fileHashes
}
$manifestPath = Join-Path $installerDir "release-manifest.json"
Write-HrmUtf8NoBom -Path $manifestPath -Text ($manifest | ConvertTo-Json -Depth 8)

Write-Host ""
Write-Host "Готово: $setupExe"
Write-Host ("SHA256 установщика: {0}" -f $manifest.installer_exe.sha256)
Write-Host "Манифест: $manifestPath"
