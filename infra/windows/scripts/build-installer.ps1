#requires -Version 5.1
<#
.SYNOPSIS
  Сборка HR Manager Setup.exe (Inno Setup) и генерация release-manifest.json.

.DESCRIPTION
  Закреплённая, воспроизводимая сборка Windows-установщика. Запускается в CI
  (windows-latest) и локально разработчиком; бинарный .exe НЕ коммитится.

    -ManifestOnly   — только хэши payload и пишет release-manifest.json
                      (тестируемо без установленного Inno Setup);
    -Verify <dir>   — пересчитать SHA-256 файлов и сверить с манифестом;
    без флагов      — собрать Setup.exe через iscc и сгенерировать манифест.

  Честность подписи: манифест всегда записывает signature.signed. Если
  SIGNING_CERT_THUMBPRINT не задан, записывается signed=false (детерминированный
  signing hook документирован в docs/phase-12-report-arena.md).
#>
[CmdletBinding()]
param(
    [string]$OutputDir = '',
    [string]$PayloadDir = '',
    [string]$Version = '0.1.0',
    [string]$ReleaseSha = '',
    [switch]$ManifestOnly,
    [string]$Verify = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not $PayloadDir) {
    $PayloadDir = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
}
if (-not $OutputDir) {
    $OutputDir = Join-Path $PayloadDir 'dist\windows'
}
if (-not $ReleaseSha) {
    # Разрешаем git SHA из текущего checkout (только для локальной сборки).
    try {
        $ReleaseSha = (& git -C $PayloadDir rev-parse HEAD).Trim()
    } catch {
        $ReleaseSha = ''
    }
}

# Файлы payload, чья целостность фиксируется в манифесте. Относительные пути
# от корня payload; пути с кириллицей/пробелами поддерживаются.
$script:PayloadFiles = @(
    'infra/windows/hr-manager.ps1',
    'infra/windows/hr-manager.iss',
    'infra/windows/redaction-patterns.json',
    'infra/windows/scripts/build-installer.ps1',
    'infra/compose.pilot.yml',
    'infra/docker-compose.yml',
    'infra/nginx/default.conf.template',
    'backend/Dockerfile',
    'backend/requirements.txt',
    'frontend/Dockerfile',
    'frontend/package.json',
    'frontend/nginx.conf'
)

function Get-Sha256 {
    param([string]$Path)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        try {
            $hash = $sha.ComputeHash($stream)
            return ([System.BitConverter]::ToString($hash) -replace '-', '').ToLowerInvariant()
        } finally {
            $stream.Dispose()
        }
    } finally {
        $sha.Dispose()
    }
}

function Build-Manifest {
    if ($ReleaseSha -notmatch '^[0-9a-fA-F]{40}$') {
        throw 'ReleaseSha должен быть полным 40-символьным git SHA.'
    }
    $files = @{}
    foreach ($rel in $script:PayloadFiles) {
        $abs = Join-Path $PayloadDir ($rel -replace '/', '\')
        if (-not (Test-Path -LiteralPath $abs)) {
            throw "Отсутствует файл payload: $rel"
        }
        $item = Get-Item -LiteralPath $abs
        $files[$rel] = @{ sha256 = (Get-Sha256 $abs); bytes = $item.Length }
    }
    $manifest = [pscustomobject]@{
        name        = 'hr-manager-pilot'
        version     = $Version
        channel     = 'pilot'
        release_sha = $ReleaseSha
        built_at    = [DateTime]::UtcNow.ToString('o')
        signature   = [pscustomobject]@{
            signed                 = $false
            certificate_thumbprint = $null
            note                   = 'не подписан: доверенный сертификат не предоставлен (см. docs/phase-12-report-arena.md)'
        }
        files       = $files
    }
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
    $manifestPath = Join-Path $OutputDir 'release-manifest.json'
    $json = ConvertTo-Json -InputObject $manifest -Depth 10
    Set-Content -LiteralPath $manifestPath -Value $json -Encoding UTF8
    return $manifestPath
}

function Test-Manifest {
    param([string]$ManifestPath)
    $manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $fail = $false
    foreach ($rel in $script:PayloadFiles) {
        $abs = Join-Path $PayloadDir ($rel -replace '/', '\')
        if (-not (Test-Path -LiteralPath $abs)) {
            Write-Error "Файл отсутствует: $rel"
            $fail = $true
            continue
        }
        $expected = [string]$manifest.files.$rel.sha256
        $actual = Get-Sha256 $abs
        if ($expected -ne $actual) {
            Write-Error "Хэш не совпадает: $rel"
            $fail = $true
        }
    }
    if ($fail) { exit 1 }
    Write-Output 'Манифест целостности подтверждён.'
}

if ($Verify) {
    Test-Manifest $Verify
    exit 0
}

if ($ManifestOnly) {
    $path = Build-Manifest
    Write-Output "Манифест записан: $path"
    exit 0
}

# --- Полная сборка Setup.exe -------------------------------------------------
$iscc = $env:ISCC_PATH
if (-not $iscc) {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c) { $iscc = $c; break }
    }
}
if (-not $iscc -or -not (Test-Path -LiteralPath $iscc)) {
    throw 'Компилятор Inno Setup 6.5.1 не найден (задайте ISCC_PATH).'
}

$issFile = Join-Path $PayloadDir 'infra\windows\hr-manager.iss'
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

& $iscc "/DVersion=$Version" "/DPayloadDir=$PayloadDir" "/DOutputDir=$OutputDir" "/DReleaseSha=$ReleaseSha" $issFile
if ($LASTEXITCODE -ne 0) {
    throw "iscc завершился с кодом $LASTEXITCODE."
}

$exe = Join-Path $OutputDir 'HR Manager Setup.exe'
if (-not (Test-Path -LiteralPath $exe)) {
    throw 'Setup.exe не создан.'
}

$manifestPath = Build-Manifest
$exeHash = Get-Sha256 $exe
$exeSize = (Get-Item -LiteralPath $exe).Length

Write-Output "Setup.exe: $exe"
Write-Output "SHA-256:   $exeHash"
Write-Output "Размер:    $exeSize байт"
Write-Output "Манифест:  $manifestPath"
