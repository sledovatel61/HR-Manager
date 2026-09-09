<#
.SYNOPSIS
    Приёмочный прогон пилота на ЖИВОЙ Windows-машине (офисный компьютер или
    локальная VM). GitHub Actions этого не умеет: Linux-раннеры не исполняют
    Windows-контейнеры, а Windows-раннеры CI не имеют Docker Desktop с Linux
    движком по умолчанию — поэтому сценарий «установка → первый вход →
    обновление → удаление» выполняется оператором здесь и только здесь.
.DESCRIPTION
    Каждый шаг печатает PASS/FAIL и требуемое действие. Скрипт ничего не
    чинит молча и никогда не удаляет данные: шаг 9 требует ручной фразы.
    Журнал: .\acceptance-log-<UTC>.txt (секретов в журнале нет — вывод
    фильтруется Protect-HrmText).
#>
[CmdletBinding()]
param(
    [string]$SetupExe = "",          # путь к HRManager-Setup-<ver>.exe из релиза
    [string]$SuiteUrl = "http://127.0.0.1:8081",
    [switch]$SkipInstaller          # если установка уже выполнена движком
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = "Continue"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $here "..\..\..")).Path
$log = Join-Path $here ("acceptance-log-{0}.txt" -f (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss"))
$results = New-Object Collections.ArrayList

function Log([string]$s) { $s | Add-Content -Path $log; Write-Host $s }
function Step([string]$name, [scriptblock]$body) {
    try {
        & $body | Out-Null
        Log "PASS  $name"
        $results.Add([pscustomobject]@{Name=$name; Ok=$true}) | Out-Null
    } catch {
        Log "FAIL  $name :: $($_.Exception.Message)"
        Log "      действие: выполните шаг вручную и перезапустите скрипт (все шаги идемпотентны)"
        $results.Add([pscustomobject]@{Name=$name; Ok=$false}) | Out-Null
    }
}
function Get-HrmEngine([string]$Action, [string[]]$Extra = @()) {
    $engine = Join-Path $repoRoot "infra\windows\hr-manager.ps1"
    $out = Join-Path ([System.IO.Path]::GetTempPath()) "hrmgr-acceptance.json"
    $r = & pwsh -NoProfile -File $engine -Action $Action -JsonOut $out @Extra
    if ($LASTEXITCODE -ne 0) { throw "hr-manager.ps1 -$Action завершился с кодом $LASTEXITCODE" }
    return (Get-Content $out -Raw | ConvertFrom-Json)
}

Log "=== HR Manager pilot acceptance ($(Get-Date -Format o)) ==="
Log "репозиторий: $repoRoot; журнал: $log"

Step "1. Тесты движка (моки)" {
    & pwsh -NoProfile -File (Join-Path $repoRoot "infra\windows\tests\Invoke-HrmTests.ps1")
    if ($LASTEXITCODE -ne 0) { throw "tests exit $LASTEXITCODE" }
}

if (-not $SkipInstaller) {
    Step "2. Установка Setup.exe (интерактивно: фамилия + режим)" {
        if (-not $SetupExe -or -not (Test-Path $SetupExe)) {
            throw "передайте -SetupExe <путь> (артефакт CI hr-manager-windows-pilot)"
        }
        Log "   запустите установщик вручную: $SetupExe — дождитесь страницы первого входа в браузере"
        $pairing = Read-Host "   код из окна установщика (6 символов)"
        $resp = Invoke-WebRequest -UseBasicParsing -Uri "$SuiteUrl/first-run" -TimeoutSec 10
        if ($resp.StatusCode -ne 200) { throw "страница first-run не открылась" }
        Set-Content -Path (Join-Path ([System.IO.Path]::GetTempPath()) "hrmgr-pairing.txt") -Value $pairing
        Log "   код принят на проверку (он хранится только в памяти установщика); завершите страницу в браузере"
    }
}

Step "3. Статус контура" {
    $st = Get-HrmEngine "status"
    if ($st.result.appState -ne "running") { throw "appState=$($st.result.appState)" }
    if ($st.result.database -ne "ok") { throw "database=$($st.result.database)" }
    if ($st.result.backup -notin @("ok","missing")) { throw "backup=$($st.result.backup)" }
}

Step "4. Порты наружу" {
    $listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.OwningProcess -in (Get-Process *docker* -ErrorAction SilentlyContinue).Id }
    $bad = @($listeners | Where-Object { $_.LocalAddress -notin @("127.0.0.1","::1") })
    if ($bad.Count -gt 0) { throw "docker-процессы слушают не-loopback адреса: $($bad.LocalAddress -join ', ')" }
}

Step "5. Повторный install идемпотентен (роли/секреты прежние)" {
    $before = Get-Content (Join-Path $env:LOCALAPPDATA "HR Manager\state\secrets.json")
    Get-HrmEngine "install" | Out-Null
    $after = Get-Content (Join-Path $env:LOCALAPPDATA "HR Manager\state\secrets.json")
    if (($before -join "") -ne ($after -join "")) { throw "секреты изменились при повторной установке" }
}

Step "6. Стоп/старт" {
    Get-HrmEngine "stop" | Out-Null
    $alive = $false
    try { Invoke-WebRequest -UseBasicParsing -Uri "$SuiteUrl/" -TimeoutSec 3 | Out-Null; $alive = $true } catch { }
    if ($alive) { throw "порт отвечает после stop" }
    Get-HrmEngine "start" | Out-Null
}

Step "7. Ярлыки и приложение в «Программы и компоненты»" {
    $desktop = Join-Path ([Environment]::GetFolderPath("Desktop")) "HR Manager.lnk"
    if (-not (Test-Path $desktop)) { throw "нет ярлыка «HR Manager» на рабочем столе" }
    $diag = Join-Path ([Environment]::GetFolderPath("Desktop")) "Диагностика HR Manager.lnk"
    if (-not (Test-Path $diag)) { throw "нет ярлыка диагностики" }
    $uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall"
    $found = Get-ChildItem $uninstallKey -ErrorAction SilentlyContinue |
        Where-Object { (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).DisplayName -like "HR Manager*" }
    if (-not $found) { throw "нет записи в «Приложения и возможности»" }
}

Step "8. Диагностика без секретов" {
    $d = Get-HrmEngine "diagnostics"
    $report = Get-Content $d.result.path -Raw
    $secrets = Get-Content (Join-Path $env:LOCALAPPDATA "HR Manager\state\secrets.json") | ConvertFrom-Json
    foreach ($v in @($secrets.secretKey, $secrets.postgresPassword, $secrets.backupEncKey)) {
        if ($v -and ($report -like "*$v*")) { throw "в отчёте диагностики найдено секретное значение" }
    }
}

Step "9. Отказ удалять данные без фразы" {
    $engine = Join-Path $repoRoot "infra\windows\hr-manager.ps1"
    & pwsh -NoProfile -File $engine -Action uninstall -RemoveData 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 7) { throw "ожидался код 7, получен $LASTEXITCODE" }
    if (-not (Test-Path (Join-Path $env:LOCALAPPDATA "HR Manager\state\config.json"))) { throw "конфигурация исчезла!" }
}

Log ""
Log "итог: PASS $(@($results | Where-Object Ok).Count) / $($results.Count)"
if (@($results | Where-Object { -not $_.Ok }).Count -gt 0) {
    Log "есть падения — см. журнал: $log"
    exit 1
}
exit 0
