# ============================================================================
# Лёгкий раннер тестов движка (без Pester — только штатные средства, чтобы
# CI на windows-latest не зависел от версии модуля). Запуск:
#   pwsh -File infra/windows/tests/Invoke-HrmTests.ps1
# Каждый тест — функция HrmTest-<Имя> в *.tests.ps1 рядом с этим файлом.
# Выход 0 — все прошли; 1 — есть падения (каждое печатается с контекстом).
# ============================================================================
Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$testsDir = $PSScriptRoot
$windowsDir = Split-Path -Parent $testsDir

. (Join-Path $windowsDir "lib\HrManager.Common.ps1")
. (Join-Path $windowsDir "lib\HrManager.Preflight.ps1")
. (Join-Path $windowsDir "lib\HrManager.Secrets.ps1")
. (Join-Path $windowsDir "lib\HrManager.Actions.ps1")
. (Join-Path $windowsDir "lib\HrManager.Update.ps1")
. (Join-Path $windowsDir "lib\HrManager.Uninstall.ps1")
. (Join-Path $testsDir "HrmTestSupport.ps1")

# 1) Проверка разбора ВСЕХ скриптов движка (аналог PSScriptAnalyzer parse).
$parseFailures = @()
Get-ChildItem -Recurse -Path $windowsDir -Filter "*.ps1" | ForEach-Object {
    $tokens = $null; $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$errors) | Out-Null
    foreach ($e in $errors) { $parseFailures += "$($_.Name): $($e.Message)" }
}
if ($parseFailures.Count -gt 0) {
    Write-Host "PARSE FAILURES:"
    $parseFailures | ForEach-Object { Write-Host "  $_" }
    exit 1
}
Write-Host "parse: OK ($((Get-ChildItem -Recurse -Path $windowsDir -Filter '*.ps1').Count) files)"

# 2) Тесты из *.tests.ps1
Get-ChildItem -Path $testsDir -Filter "*.tests.ps1" | ForEach-Object { . $_.FullName }
$tests = Get-Command -Name "HrmTest-*" | Sort-Object Name
if (-not $tests) { Write-Error "тесты не найдены"; exit 1 }

$failures = New-Object Collections.ArrayList
$passed = 0
foreach ($t in $tests) {
    $name = $t.Name
    try {
        & $name
        $passed++
        Write-Host "ok   $name"
    } catch {
        [void]$failures.Add("$name :: $($_.Exception.Message)")
        Write-Host "FAIL $name :: $($_.Exception.Message)"
    }
}
Write-Host ""
Write-Host "passed=$passed failed=$($failures.Count) total=$($tests.Count)"
if ($failures.Count -gt 0) { exit 1 }
exit 0
