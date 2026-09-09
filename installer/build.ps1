# Сборка HR Manager Setup.exe из исходников репозитория.
#
# Инструментальная цепочка ЗАКРЕПЛЕНА (см. installer/README.md):
#   Inno Setup 6.7.3, официальный установщик с GitHub Releases.
#   URL:    https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe
#   SHA256: 9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732
# Никакие другие версии/источники не используются; установщик запускается
# молча (/VERYSILENT) ТОЛЬКО на сборочной машине/CI, никогда на машине
# пользователя — пользователю доставляется готовый HR-Manager-Setup.exe.
#
# Результат: installer/output/HR-Manager-Setup-<Version>.exe и
# installer/release-manifest.json (release_sha, версия, хеши пакета и exe).
#
# ИСПОЛЬЗОВАНИЕ (Windows 10/11, PowerShell 5.1+ или pwsh):
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1 [-Version 0.13.0]

[CmdletBinding()]
param(
    [string]$Version = "0.13.0"
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

function Invoke-RobocopyMirror {
    # robocopy с исключениями; коды выхода 0-7 — успех. Пути с пробелами
    # заключаются в кавычки явно (ArgumentList массива их не цитирует).
    param([string]$Source, [string]$Destination, [string[]]$ExcludeDirs = @())
    $robocopyArgs = @(('"{0}"' -f $Source), ('"{0}"' -f $Destination), "/MIR", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")
    foreach ($dir in $ExcludeDirs) { $robocopyArgs += "/XD"; $robocopyArgs += ('"{0}"' -f $dir) }
    $process = Start-Process -FilePath "robocopy.exe" -ArgumentList $robocopyArgs -Wait -PassThru -NoNewWindow
    if ($process.ExitCode -ge 8) {
        throw "robocopy $Source -> $Destination завершился с кодом $($process.ExitCode)"
    }
}

Write-Host "== HR Manager installer build =="

# 0. Чистое состояние сборки.
if (Test-Path $stagingDir) { Remove-Item $stagingDir -Recurse -Force }
if (Test-Path $outputDir) { Remove-Item $outputDir -Recurse -Force }
New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null

# 1. Inno Setup 6.7.3: скачивание + проверка SHA256 + тихая установка.
$innoExe = Join-Path $cacheDir "innosetup-6.7.3.exe"
if (-not (Test-Path $innoExe)) {
    Write-Host "Downloading Inno Setup 6.7.3 (pinned)…"
    Invoke-WebRequest -Uri $InnoUrl -OutFile $innoExe -UseBasicParsing
}
$hash = (Get-FileHash -Path $innoExe -Algorithm SHA256).Hash.ToLowerInvariant()
if ($hash -ne $InnoSha256) {
    throw "SHA256 установщика Inno Setup не совпал: $hash (ожидался $InnoSha256)"
}
Write-Host "Inno Setup 6.7.3 SHA256 verified."
$iscc = Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) {
    Write-Host "Installing Inno Setup silently (build machine only)…"
    $p = Start-Process -FilePath $innoExe -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER" -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "Inno Setup install failed: $($p.ExitCode)" }
}

# 2. Снимок приложения для пакета: backend/, frontend/, infra/, release.json.
# Контейнеры собираются из ИСХОДНИКОВ (docker build), поэтому node_modules/
# и артефакты сборки в пакет не входят.
$appStaging = Join-Path $stagingDir "app"
New-Item -ItemType Directory -Path $appStaging -Force | Out-Null
Write-Host "Staging backend/…"
Invoke-RobocopyMirror (Join-Path $repoRoot "backend") (Join-Path $appStaging "backend") @("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "tests", ".venv", "venv")
Write-Host "Staging frontend/ (без node_modules и dist)…"
Invoke-RobocopyMirror (Join-Path $repoRoot "frontend") (Join-Path $appStaging "frontend") @("node_modules", "dist", ".vite")
Write-Host "Staging infra/…"
Invoke-RobocopyMirror (Join-Path $repoRoot "infra") (Join-Path $appStaging "infra") @()

# release.json: версия и SHA релиза (движок сверяет с /ops/status).
# Из git; при сборке из архива без git — через HRM_RELEASE_SHA.
$releaseSha = if ($env:HRM_RELEASE_SHA) {
    $env:HRM_RELEASE_SHA
}
else {
    (git -C $repoRoot rev-parse HEAD).Trim()
}
$releaseJson = [ordered]@{
    release_sha = $releaseSha
    version = $Version
    built_at = (Get-Date).ToString("o")
    installer_commit = $releaseSha
}
$releaseJson | ConvertTo-Json | Set-Content -Path (Join-Path $appStaging "release.json") -Encoding UTF8

# 3. Компиляция установщика.
Write-Host "Compiling installer with ISCC…"
& $iscc (Join-Path $installerDir "installer.iss") ("/DAppVersion=" + $Version)
if ($LASTEXITCODE -ne 0) { throw "ISCC failed: $LASTEXITCODE" }

$setupExe = Join-Path $outputDir ("HR-Manager-Setup-" + $Version + ".exe")
if (-not (Test-Path $setupExe)) { throw "Установщик не создан: $setupExe" }

# 4. Манифест релиза: хеши пакета (детерминированные) + хеш exe.
Write-Host "Writing release manifest…"
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
    signing = [ordered]@{
        # Честно: подпись НЕ выполняется в этой сборке. Хук для кодовой
        # подписи — параметр SignTool установщика (см. installer/README.md).
        status = "unsigned"
        instruction = "installer/README.md (раздел «Кодовая подпись»)"
    }
    package_files_sha256 = $fileHashes
}
$manifestPath = Join-Path $installerDir "release-manifest.json"
$manifest | ConvertTo-Json -Depth 8 | Set-Content -Path $manifestPath -Encoding UTF8

Write-Host ""
Write-Host "Готово: $setupExe"
Write-Host ("SHA256 установщика: {0}" -f $manifest.installer_exe.sha256)
Write-Host "Манифест: $manifestPath"
