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
try {
    foreach ($file in @("static.tests.ps1", "engine.tests.ps1", "channel.tests.ps1")) {
        $path = Join-Path $testsDir $file
        & (Resolve-Path $path).Path -HarnessPath $testsDir
        if (-not $?) { $exitCode = 1 }
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

# Итоговый вердикт по глобальным счётчикам.
if ($global:HRM_TestFailed -gt 0) { $exitCode = 1 }
if ($exitCode -eq 0) {
    Write-Host ("ВСЕ ТЕСТЫ ПРОЙДЕНЫ ({0})" -f $global:HRM_TestPassed) -ForegroundColor Green
}
else {
    Write-Host ("ЕСТЬ ПРОВАЛЫ: {0}" -f $global:HRM_TestFailed) -ForegroundColor Red
    foreach ($failure in $global:HRM_TestFailures) { Write-Host ("  " + $failure) -ForegroundColor Red }
}
exit $exitCode
