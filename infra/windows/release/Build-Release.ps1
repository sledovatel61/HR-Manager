<#
.SYNOPSIS
    Сборка релизного комплекта HR Manager для пилота (фаза 12).
.DESCRIPTION
    Репозиторий НЕ содержит бинарников: из исходников собирается каталог
    bundle с манифестом и SHA-256-ведомостью. Тот же bundle вкладывается в
    Setup.exe (Inno Setup) и используется движком для проверок целостности и
    обновлений. Состав строгий: только контексты сборки docker и движок.
.PARAMETER OutDir
    Каталог результата.
.PARAMETER Version
    Версия релиза (по умолчанию 12.0.0-dev).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$OutDir,
    [string]$Version = "12.0.0-dev",
    [string]$RepoRoot = ""
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

if (-not $RepoRoot) { $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path }
if (Test-Path $OutDir) { Remove-Item $OutDir -Recurse -Force }
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

$items = @(
    @{ Src = "backend";                 Dst = "backend";                 Exclude = @("tests", ".venv*", "__pycache__", "*.egg-info", ".mypy_cache", ".pytest_cache", ".ruff_cache") },
    @{ Src = "frontend";                Dst = "frontend";                Exclude = @("node_modules", "dist", ".vite") },
    @{ Src = "infra\docker-compose.yml";   Dst = "infra\docker-compose.yml";   Exclude = @() },
    @{ Src = "infra\compose.pilot.yml";    Dst = "infra\compose.pilot.yml";    Exclude = @() },
    @{ Src = "infra\windows";              Dst = "infra\windows";              Exclude = @() },
    @{ Src = "prompts\PHASE_12_PROMPT.md"; Dst = "docs\PHASE_12_PROMPT.md";    Exclude = @() }
)

foreach ($item in $items) {
    $src = Join-Path $RepoRoot $item.Src
    if (-not (Test-Path $src)) { throw "Build-Release: нет источника $src (сборка из неполного дерева)" }
    $dst = Join-Path $OutDir $item.Dst
    if ((Get-Item $src).PSIsContainer) {
        Copy-Item -Path $src -Destination $dst -Recurse -Force
        foreach ($pat in $item.Exclude) {
            Get-ChildItem -Path $dst -Recurse -Force -Filter $pat -ErrorAction SilentlyContinue |
                Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }
    } else {
        New-Item -ItemType Directory -Path (Split-Path -Parent $dst) -Force | Out-Null
        Copy-Item -Path $src -Destination $dst -Force
    }
}

# Состав bundle документируется списком файлов (сортировка стабильная).
$files = Get-ChildItem -Path $OutDir -Recurse -File | ForEach-Object {
    $_.FullName.Substring($OutDir.Length + 1).Replace('\', '/').ToLowerInvariant()
} | Sort-Object

$sums = foreach ($rel in $files) {
    $full = Join-Path $OutDir ($rel.Replace('/', [IO.Path]::DirectorySeparatorChar))
    "{0}  {1}" -f (Get-FileHash -Algorithm SHA256 -Path $full).Hash.ToLowerInvariant(), $rel
}
$sumsPath = Join-Path $OutDir "SHA256SUMS.txt"
Set-Content -Path $sumsPath -Value $sums -Encoding UTF8

# releaseSha = SHA-256 ведомости целиком: любой побайтовый сдвиг меняет id.
$sumsHash = (Get-FileHash -Algorithm SHA256 -Path $sumsPath).Hash.ToLowerInvariant()
# SHA256SUMS.txt сам в себя не включает (курица-яйцо), но манифест включается
# в ведомость после расчёта releaseSha — пересчёт один, циклов нет.
$manifestPath = Join-Path $OutDir "release-manifest.json"
$manifest = [ordered]@{
    product    = "hr-manager-pilot"
    version    = $Version
    releaseSha = $sumsHash
    builtAt    = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    fileCount  = @($files).Count
}
$manifest | ConvertTo-Json | Set-Content -Path $manifestPath -Encoding UTF8
# дописываем манифест в ведомость (движок сверит его хэш при проверке)
Add-Content -Path $sumsPath -Value ("{0}  {1}" -f (Get-FileHash -Algorithm SHA256 -Path $manifestPath).Hash.ToLowerInvariant(), "release-manifest.json")

Write-Host "release bundle: $OutDir ($(@($files).Count + 1) files), version=$Version sha=$sumsHash"
