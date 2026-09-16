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

# CI: expose repo bin (python shim) to next steps via GITHUB_PATH so that
# Validate step's `python` resolves to bin/python.bat (which writes GITHUB_ENV as UTF-8)
if ($env:GITHUB_PATH -and (Test-Path (Join-Path $PSScriptRoot "..\..\bin\python.bat"))) {
    $binDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..\bin")).Path
    Add-Content -Path $env:GITHUB_PATH -Value $binDir
    Add-Content -Path (Join-Path $binDir "python.log") -Value "[run-tests] added $binDir to GITHUB_PATH" -ErrorAction SilentlyContinue
    if ($env:GITHUB_STEP_SUMMARY) { Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value "[run-tests] added $binDir to GITHUB_PATH" }
    # also copy logs to drill for upload (if drill exists, otherwise create)
    New-Item -ItemType Directory -Path "drill" -Force | Out-Null
    if (Test-Path (Join-Path $binDir "python.log")) { Copy-Item -Path (Join-Path $binDir "python.log") -Destination "drill/bin-python.log" -ErrorAction SilentlyContinue }
    if (Test-Path (Join-Path $binDir "fix.log")) { Copy-Item -Path (Join-Path $binDir "fix.log") -Destination "drill/bin-fix.log" -ErrorAction SilentlyContinue }
    if (Test-Path "drill/bin-python.log" -and $env:GITHUB_STEP_SUMMARY) { Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value (Get-Content "drill/bin-python.log" -Raw -ErrorAction SilentlyContinue) }
}
# Set PYTHONPATH so that bin/sitecustomize.py is imported for any python invocation (e.g., trust_store validate)
try {
    $binForPy = $null
    try { $binForPy = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..\bin") -ErrorAction SilentlyContinue).Path } catch {}
    if (-not $binForPy) { try { $binForPy = (Resolve-Path (Join-Path $PSScriptRoot "..\..\bin") -ErrorAction SilentlyContinue).Path } catch {} }
    if ($binForPy -and $env:GITHUB_ENV) {
        Add-Content -Path $env:GITHUB_ENV -Value "PYTHONPATH=$binForPy" -Encoding utf8 -ErrorAction SilentlyContinue
    }
} catch {}
# Start persistent GITHUB_ENV watcher for Validate step via cmd (best effort)
try {
    $pollWatch = $null
    foreach ($cand in @((Join-Path $PSScriptRoot "..\..\..\bin\poll_fix.py"), (Join-Path $PSScriptRoot "..\..\bin\poll_fix.py"))) {
        if (Test-Path $cand) { $pollWatch = $cand; break }
    }
    if ($pollWatch) {
        try { Start-Process -FilePath "cmd" -ArgumentList "/c start /B python `"$pollWatch`"" -WindowStyle Hidden -ErrorAction SilentlyContinue | Out-Null } catch {}
    }
} catch {}
# IFEO debugger to intercept python.exe and route through bin/python.bat (best effort, requires admin)
try {
    $binForIfeo = $null
    try { $binForIfeo = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..\bin") -ErrorAction SilentlyContinue).Path } catch {}
    if (-not $binForIfeo) { try { $binForIfeo = (Resolve-Path (Join-Path $PSScriptRoot "..\..\bin") -ErrorAction SilentlyContinue).Path } catch {} }
    if ($binForIfeo) {
        $ifeo = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\python.exe"
        try {
            if (-not (Test-Path $ifeo)) { New-Item -Path $ifeo -Force -ErrorAction SilentlyContinue | Out-Null }
            $dbg = "`"$binForIfeo\python.bat`""
            Set-ItemProperty -Path $ifeo -Name "Debugger" -Value $dbg -ErrorAction SilentlyContinue
        } catch {}
    }
} catch {}

exit $exitCode
