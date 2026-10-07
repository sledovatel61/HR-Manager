# Запуск всех тестов движка (статических и поведенческих с моками).
# Реальная машина не затрагивается. Выход: 0 — всё прошло, 1 — есть провалы.
#
#   powershell -ExecutionPolicy Bypass -File infra\windows\tests\run-tests.ps1
#   pwsh infra/windows/tests/run-tests.ps1

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$testsDir = $PSScriptRoot
$harness = Join-Path $testsDir "test-harness.ps1"

$exitCode = 0
$totalPassed = 0
$failures = @()
try {
    foreach ($file in @("static.tests.ps1", "engine.tests.ps1", "channel.tests.ps1", "installer-roots.tests.ps1", "docker.tests.ps1", "stack.tests.ps1", "supervisor.tests.ps1", "pilot-final.tests.ps1")) {
        $path = Join-Path $testsDir $file
        & (Resolve-Path $path).Path -HarnessPath $testsDir
        if (-not $?) { $exitCode = 1 }
        # Each suite reloads the harness and resets counters. Preserve failures
        # before the next suite can reset them (purge failures must fail CI).
        $totalPassed += $global:HRM_TestPassed
        $failures += $global:HRM_TestFailures
        if ($global:HRM_TestFailed -gt 0) { $exitCode = 1 }
    }
}
catch {
    # Фатальная ошибка вне Test-Case: печатаем как GitHub-аннотацию
    # (::error::), чтобы детали были видны в check-runs даже без логов.
    $stack = $_.ScriptStackTrace
    if (-not $stack) { $stack = "(без стектрейса)" }
    $detail = ($_.Exception.ToString() + "`n" + $stack)
    Write-Host ("::error title=HRM-FATAL::{0}" -f ($detail -replace "[`r`n]+", " | "))
    Write-Host ("ФАТАЛЬНАЯ ОШИБКА: {0}" -f $_.Exception.Message)
    exit 2
}

# Итоговый вердикт по всем suites, а не только последнему.
$global:HRM_TestPassed = $totalPassed
$global:HRM_TestFailures = $failures
$global:HRM_TestFailed = $failures.Count
if ($global:HRM_TestFailed -gt 0) { $exitCode = 1 }
if ($exitCode -eq 0) {
    Write-Host ("ВСЕ ТЕСТЫ ПРОЙДЕНЫ ({0})" -f $global:HRM_TestPassed) -ForegroundColor Green
}
else {
    Write-Host ("ЕСТЬ ПРОВАЛЫ: {0}" -f $global:HRM_TestFailed) -ForegroundColor Red
    foreach ($failure in $global:HRM_TestFailures) { Write-Host ("  " + $failure) -ForegroundColor Red }
}
# GitHub показывает в check-run не более 10 аннотаций `::error::`, поэтому
# полный список провалов дополнительно пишется в файл (CI загружает его как
# артефакт) и в шаг-summary, если он доступен.
$failuresFile = ""
if ($env:RUNNER_TEMP) { $failuresFile = Join-Path $env:RUNNER_TEMP "hr-engine-test-failures.txt" }
elseif ($env:TEMP) { $failuresFile = Join-Path $env:TEMP "hr-engine-test-failures.txt" }
if ($failuresFile) {
    $report = @(
        ("HR Manager engine tests: {0} пройдено, {1} провалено" -f $global:HRM_TestPassed, $global:HRM_TestFailed)
    ) + @($global:HRM_TestFailures)
    try {
        Set-Content -Path $failuresFile -Value $report -Encoding UTF8
        Write-Host ("Отчёт о провалах: " + $failuresFile)
    } catch { }
    if ($global:HRM_TestFailed -gt 0 -and $env:GITHUB_ACTIONS) {
        # Аннотаций ::error:: на шаг не больше 10 — остальные провалы видны
        # только в файле артефакта. Дополнительно публикуем весь список
        # notice-аннотациями (свой лимит 10), чтобы читать его через API.
        $rx = "(?<![A-Za-z0-9+/_-])(?:[0-9a-fA-F]{64,}|[A-Za-z0-9+/_-]{40,}={0,2})(?![A-Za-z0-9+/_=-])"
        $chunks = New-Object System.Collections.ArrayList
        $cur = "failure list (" + $global:HRM_TestFailed + "):"
        foreach ($failure in @($global:HRM_TestFailures)) {
            $safe = [regex]::Replace([string]$failure, $rx, "<redacted>")
            # Запись уже содержит компактный стектрейс; короткий лимит нужен,
            # чтобы ВЕСЬ список попал в notice-аннотации (логи и артефакты
            # GitHub из песочницы не скачиваются, аннотации читаются через API).
            if ($safe.Length -gt 700) { $safe = $safe.Substring(0, 700) }
            if (($cur.Length + $safe.Length + 3) -gt 3800) { [void]$chunks.Add($cur); $cur = "" }
            if ($cur.Length -gt 0) { $cur += "`n" }
            $cur += $safe
        }
        if ($cur.Length -gt 0) { [void]$chunks.Add($cur) }
        $emitCount = [Math]::Min($chunks.Count, 10)
        for ($i = 0; $i -lt $emitCount; $i++) {
            $m = ([string]$chunks[$i]).Replace("%", "%25").Replace("`r", "%0D").Replace("`n", "%0A")
            Write-Host ("::notice title=HRM engine failures " + ($i + 1) + "/" + $chunks.Count + "::" + $m)
        }
    }
    if ($global:HRM_TestFailed -gt 0 -and $env:GITHUB_STEP_SUMMARY) {
        try {
            Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value ("### Провалы тестов движка" + "`n" + '```text' + "`n" + ($report -join "`n") + "`n" + '```' + "`n") -Encoding utf8
        } catch { }
    }
}
exit $exitCode
