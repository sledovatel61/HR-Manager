<#
.SYNOPSIS
    Движок установки и локального запуска HR Manager (pilot, Windows).
.DESCRIPTION
    Единая точка для GUI-установщика (Inno Setup), ярлыков и CI: идемпотентные
    действия install / ensure-running / start / stop / status / update /
    diagnostics / uninstall / resume / repair. Никаких интерактивных запросов
    (кроме явного -ConfirmPhrase при удалении данных), никакого Docker Desktop
    не принимается за пользователя, секреты не выводятся.
.PARAMETER Action
   install | ensure-running | start | stop | status | update | diagnostics |
    uninstall | resume | repair | preflight
.PARAMETER AppDir
    Каталог релизного комплекта (по умолчанию %LOCALAPPDATA%\HR Manager\app).
.PARAMETER StateRoot
    Каталог локального состояния (по умолчанию %LOCALAPPDATA%\HR Manager\state;
    для тестов — HRMGR_TEST_STATE_DIR).
.PARAMETER InputFile
    JSON от GUI-установщика: { "surname": "...", "workRole": "hr|manager|admin" }.
    Только через файл — фамилия и код никогда не попадают в командную строку.
.PARAMETER UpdateBundle
    Каталог нового релиза для -Action update.
.PARAMETER JsonOut
    Файл, куда записать результат (JSON) — для GUI-опроса и CI-артефактов.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet(
        "install", "ensure-running", "start", "stop", "status", "update",
        "diagnostics", "uninstall", "resume", "repair", "preflight")]
    [string]$Action,

    [string]$AppDir = "",
    [string]$StateRoot = "",
    [string]$InputFile = "",
    [string]$UpdateBundle = "",
    [string]$JsonOut = "",
    [int]$Port = 8081,
    [switch]$RemoveData,
    [string]$ConfirmPhrase = "",
    [string]$ExportBackupTo = "",
    [switch]$SkipDocker,
    [switch]$AllowNonWindows,
    [switch]$FromShortcut,
    [switch]$TestMode
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
# Тестовый режим (CI/Linux): временные каталоги, mock-хуки инструментов.
if ($TestMode) {
    if (-not $env:HRMGR_TEST_STATE_DIR) {
        $env:HRMGR_TEST_STATE_DIR = Join-Path ([System.IO.Path]::GetTempPath()) ("hrmgr-test-{0}" -f (Get-Random))
    }
    $AllowNonWindows = $true
    $SkipDocker = $true
}

. (Join-Path $scriptRoot "lib\HrManager.Common.ps1")
. (Join-Path $scriptRoot "lib\HrManager.Preflight.ps1")
. (Join-Path $scriptRoot "lib\HrManager.Secrets.ps1")
. (Join-Path $scriptRoot "lib\HrManager.Actions.ps1")
. (Join-Path $scriptRoot "lib\HrManager.Update.ps1")
. (Join-Path $scriptRoot "lib\HrManager.Uninstall.ps1")

function ConvertTo-HrmSerializable {
    <# PSCustomObject/OrderedDictionary → plain-совместимый JSON. #>
    param($Value, [int]$Depth = 0)
    if ($null -eq $Value) { return $null }
    if ($Value -is [switch]) { return [bool]$Value }
    if ($Value -is [System.Collections.IDictionary]) {
        $out = [ordered]@{}
        foreach ($k in $Value.Keys) { $out[[string]$k] = ConvertTo-HrmSerializable $Value[$k] ($Depth + 1) }
        if ($Depth -eq 0) { return [pscustomobject]$out }
        return $out
    }
    if ($Value -is [System.Collections.IEnumerable] -and $Value -isnot [string]) {
        return @($Value | ForEach-Object { ConvertTo-HrmSerializable $_ ($Depth + 1) })
    }
    if ($Value -is [psobject]) {
        $out = [ordered]@{}
        foreach ($prop in $Value.PSObject.Properties) {
            if ($prop.Name -like "Implicit*" -or $prop.Name -eq "PSComputerName") { continue }
            $out[$prop.Name] = ConvertTo-HrmSerializable $prop.Value ($Depth + 1)
        }
        if ($Depth -eq 0) { return [pscustomobject]$out }
        return $out
    }
    return $Value
}

function Write-HrmResult {
    param([bool]$Ok, $Payload, [int]$ExitCode = 0, $ErrorInfo = $null)
    $obj = [ordered]@{ ok = $Ok; action = $Action }
    if ($null -ne $Payload) { $obj.result = ConvertTo-HrmSerializable $Payload }
    if ($null -ne $ErrorInfo) {
        $obj.error = [pscustomobject][ordered]@{
            message  = [string]$ErrorInfo.Message
            hint     = [string]$ErrorInfo.Hint
            exitCode = [int]$ErrorInfo.ExitCode
        }
    }
    $json = ConvertTo-Json (ConvertTo-HrmSerializable ([pscustomobject]$obj)) -Depth 8
    if ($JsonOut) {
        [IO.File]::WriteAllText($JsonOut, $json + [Environment]::NewLine, (New-Object Text.UTF8Encoding($false)))
    }
    Write-Output $json
}

# --- Тестовые хуки: mock docker/compose (см. tests/Invoke-HrmTests.ps1) ---
if ($env:HRMGR_MOCK_COMMANDS -eq "1") {
    $mockScript = $env:HRMGR_MOCK_SCRIPT
    if (-not $mockScript -or -not (Test-Path $mockScript)) { throw "HRMGR_MOCK_COMMANDS=1, но HRMGR_MOCK_SCRIPT не задан/не найден" }
    . $mockScript
}

$resolvedStateRoot = Get-HrmStateRoot
if ($StateRoot) { $resolvedStateRoot = [IO.Path]::GetFullPath($StateRoot) }
$resolvedAppDir = Get-HrmAppRoot
if ($AppDir) { $resolvedAppDir = [IO.Path]::GetFullPath($AppDir) }
if (-not (Test-Path $resolvedStateRoot)) { New-Item -ItemType Directory -Path $resolvedStateRoot -Force | Out-Null }

try {
    switch ($Action) {
        "install" {
            $r = Invoke-HrmInstall -AppDir $resolvedAppDir -StateRoot $resolvedStateRoot -InputFile $InputFile -Port $Port -SkipDocker:$SkipDocker -AllowNonWindows:$AllowNonWindows -DockerManifestDir $scriptRoot
            Write-HrmResult $r.Ok $r 0
        }
        "repair" {
            $r = Invoke-HrmInstall -AppDir $resolvedAppDir -StateRoot $resolvedStateRoot -SkipDocker:$SkipDocker -AllowNonWindows:$AllowNonWindows -DockerManifestDir $scriptRoot
            Write-HrmResult $true $r 0
        }
        "resume" {
            $r = Invoke-HrmResume -AppDir $resolvedAppDir -StateRoot $resolvedStateRoot
            Write-HrmResult $true $r 0
        }
        "ensure-running" {
            $r = Invoke-HrmEnsureRunning -AppDir $resolvedAppDir -StateRoot $resolvedStateRoot
            Write-HrmResult $true $r 0
        }
        "start" {
            $r = Invoke-HrmStart -AppDir $resolvedAppDir -StateRoot $resolvedStateRoot
            Write-HrmResult $r.Ok $r 0
        }
        "stop" {
            $r = Invoke-HrmStop -AppDir $resolvedAppDir -StateRoot $resolvedStateRoot
            Write-HrmResult $r.Ok $r $(if ($r.Ok) { 0 } else { 1 })
        }
        "status" {
            $st = Invoke-HrmStatus -StateRoot $resolvedStateRoot
            Write-HrmResult $true $st 0
        }
        "diagnostics" {
            $d = Invoke-HrmDiagnostics -AppDir $resolvedAppDir -StateRoot $resolvedStateRoot
            Write-HrmResult $d.Ok @{ path = $d.Path; status = $d.Status } 0
        }
        "update" {
            if (-not $UpdateBundle) { throw (New-HrmError "Для -Action update нужен -UpdateBundle (каталог нового релиза)." "" 2) }
            $u = Invoke-HrmUpdate -StateRoot $resolvedStateRoot -NewReleaseDir ([IO.Path]::GetFullPath($UpdateBundle)) -AllowNonWindows:$AllowNonWindows
            Write-HrmResult $u.Ok $u 0
        }
        "uninstall" {
            $u = Invoke-HrmUninstall -AppDir $resolvedAppDir -StateRoot $resolvedStateRoot -RemoveData:$RemoveData -ConfirmPhrase $ConfirmPhrase -ExportBackupTo $ExportBackupTo
            Write-HrmResult $u.Ok $u 0
        }
        "preflight" {
            $p = Invoke-HrmPreflight -StateRoot $resolvedStateRoot -Port $Port -SkipDocker:$SkipDocker -AllowNonWindows:$AllowNonWindows
            $checks = @($p.Checks | ForEach-Object { @{ ok = [bool]$_.Ok; code = [string]$_.Code; message = [string]$_.Message } })
            Write-HrmResult $p.Ok @{ checks = $checks } $(if ($p.Ok) { 0 } else { 2 })
        }
    }
    exit 0
} catch {
    $e = $_.Exception
    $message = [string]$e.Message
    $hint = ""
    $code = 1
    if ($e -is [HrmEngineException]) {
        $message = [string]$e.Message
        $hint = [string]$e.Hint
        $code = [int]$e.ExitCode
    }
    Write-HrmLog -StateRoot $resolvedStateRoot -Message "action=$Action ошибка: $message" -Level error
    Write-HrmResult $false $null $code @{ Message = $message; Hint = $hint; ExitCode = $code }
    exit $code
}
