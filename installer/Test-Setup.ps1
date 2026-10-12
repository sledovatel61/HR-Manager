# Diagnostic launcher: streams Setup and engine logs without changing release behaviour.
param([string]$SetupPath = (Join-Path $PSScriptRoot 'output\HR-Manager-Setup-0.15.0.exe'))
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $SetupPath)) { throw "Installer not found: $SetupPath" }
$stateDir = Join-Path $env:LOCALAPPDATA 'HRManager'
$setupLog = Join-Path $env:TEMP ('HRM-Setup-Diagnostic-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
$offsets = @{}
$states = @{}
Write-Host "Setup: $SetupPath"
Write-Host "Setup log: $setupLog"
Write-Host "Engine logs: $stateDir\logs"
Write-Host 'This window stays open after Setup finishes. Copy output or press Ctrl+C to stop monitoring.'
$engineLogDir = Join-Path $stateDir 'logs'
New-Item -ItemType Directory -Path $engineLogDir -Force | Out-Null
$previousLogFile = $env:HRM_LOG_FILE
$env:HRM_LOG_FILE = Join-Path $engineLogDir ('setup-engine-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
try {
    $process = Start-Process -FilePath $SetupPath -ArgumentList ('/LOG="{0}"' -f $setupLog) -PassThru
} finally {
    $env:HRM_LOG_FILE = $previousLogFile
}
$reportedExit = $false
while ($true) {
    $paths = @($setupLog)
    $logDir = Join-Path $stateDir 'logs'
    if (Test-Path -LiteralPath $logDir) {
        $paths += @(Get-ChildItem -LiteralPath $logDir -File | Where-Object { $_.Extension -in @('.log', '.err', '.txt') } | Select-Object -ExpandProperty FullName)
    }
    foreach ($path in $paths) {
        if (-not (Test-Path -LiteralPath $path)) { continue }
        try {
            $lines = @(Get-Content -LiteralPath $path -ErrorAction Stop)
            $offset = 0
            if ($offsets.ContainsKey($path)) { $offset = $offsets[$path] }
            if ($lines.Count -lt $offset) { $offset = 0 }
            if ($lines.Count -gt $offset) {
                Write-Host ("`n--- {0} ---" -f $path) -ForegroundColor Cyan
                for ($index = $offset; $index -lt $lines.Count; $index++) { Write-Host $lines[$index] }
            }
            $offsets[$path] = $lines.Count
        } catch { Write-Host "Log temporarily unavailable: $path" -ForegroundColor Yellow }
    }
    foreach ($name in @('setup-run.json', 'supervisor.json', 'snapshot-diagnostics.json', 'update-result.json')) {
        $path = Join-Path $stateDir $name
        if (-not (Test-Path -LiteralPath $path)) { continue }
        try {
            $text = Get-Content -LiteralPath $path -Raw -Encoding UTF8
            if (-not $states.ContainsKey($path) -or $states[$path] -ne $text) {
                Write-Host ("`n--- {0} ---`n{1}" -f $path, $text) -ForegroundColor Cyan
                $states[$path] = $text
            }
        } catch { }
    }
    if ($process.HasExited -and -not $reportedExit) {
        Write-Host ("`nSetup exited with code {0}. Continuing to monitor engine and tray." -f $process.ExitCode) -ForegroundColor Yellow
        $reportedExit = $true
    }
    Start-Sleep -Seconds 1
}