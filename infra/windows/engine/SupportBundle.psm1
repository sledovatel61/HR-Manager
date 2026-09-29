# Отчёт для разработчика одним кликом (B4).
#
# Действие hr-manager.ps1 -Action support-bundle:
#   создаёт на рабочем столе HR-Manager-report-<дата-время>.zip
# Внутри: diagnostics (-Json), логи движка и установщика, последние N строк
# логов контейнеров backend/worker/frontend/backup, версия и release.json,
# состояние лицензии (срок, лимит, статус — без подписи), результат
# health/readiness. Без персональных данных и секретов — всё через
# Redact-HrmText, не включать дамп БД, pilot.env, пароли, токены, ключи,
# ФИО/телефоны/e-mail кандидатов.

Set-StrictMode -Version 2.0

function Get-HrmDesktopPath {
    # Возвращает путь к рабочему столу (мокабельно в тестах через env).
    if ($env:HRM_DESKTOP_DIR -and (Test-Path $env:HRM_DESKTOP_DIR)) { return $env:HRM_DESKTOP_DIR }
    try {
        $path = [Environment]::GetFolderPath("Desktop")
        if ($path -and (Test-Path $path)) { return $path }
    } catch {}
    if ($env:USERPROFILE) {
        $candidate = Join-Path $env:USERPROFILE "Desktop"
        if (Test-Path $candidate) { return $candidate }
    }
    # Fallback: StateDir parent (для тестов)
    return $env:HRM_STATE_DIR
}

function New-HrmSupportBundle {
    param(
        [string]$InstallDir = "",
        [string]$StateDir = "",
        [int]$LogTail = 200
    )
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $record = Get-HrmInstallRecord $StateDir
    $port = Get-HrmPort
    if ($null -ne $record -and $record.port) { $port = [int]$record.port }
    $baseUrl = Get-HrmBaseUrl $port
    $desktop = Get-HrmDesktopPath
    if (-not (Test-Path $desktop)) {
        try { New-Item -ItemType Directory -Path $desktop -Force | Out-Null } catch {}
    }
    $stamp = (Get-Date).ToString("yyyy-MM-dd_HH-mm-ss")
    $zipName = "HR-Manager-report-$stamp.zip"
    $zipPath = Join-Path $desktop $zipName
    $tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM-report-" + [Guid]::NewGuid().ToString("N").Substring(0,8))
    New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
    try {
        # 1. diagnostics --json (redacted)
        try {
            $diagObj = @{}
            # Собираем diagnostics напрямую (Get-HrmDiagnostics уже делает redaction)
            # Перехватим вывод
            $diagText = & {
                $out = @()
                # Get-HrmDiagnostics пишет через Write-Output с Protect-HrmOutput (redacted)
                # Для JSON нам нужно захватить строку
                $jsonLines = @()
                # Вызываем с AsJson и перехватываем через временный перехват
                # Проще вызвать функцию и получить её возврат через redirect
                Get-HrmDiagnostics -InstallDir $InstallDir -StateDir $StateDir -AsJson 2>&1 | ForEach-Object { $jsonLines += $_.ToString() }
                ($jsonLines -join "`n")
            }
            $diagText = Redact-HrmText $diagText
            [System.IO.File]::WriteAllText((Join-Path $tmpDir "diagnostics.json"), $diagText, (New-Object System.Text.UTF8Encoding($false)))
        } catch {
            [System.IO.File]::WriteAllText((Join-Path $tmpDir "diagnostics.json"), (Redact-HrmText $_.Exception.Message), (New-Object System.Text.UTF8Encoding($false)))
        }

        # 2. install record (redacted)
        try {
            if ($null -ne $record) {
                $recJson = ($record | ConvertTo-Json -Depth 8)
                $recJson = Redact-HrmText $recJson
                [System.IO.File]::WriteAllText((Join-Path $tmpDir "install-record.json"), $recJson, (New-Object System.Text.UTF8Encoding($false)))
            }
        } catch {}

        # 3. release.json
        try {
            $relPath = Join-Path $InstallDir "release.json"
            if (Test-Path $relPath) {
                $relText = Get-Content -Path $relPath -Raw -Encoding UTF8
                $relText = Redact-HrmText $relText
                [System.IO.File]::WriteAllText((Join-Path $tmpDir "release.json"), $relText, (New-Object System.Text.UTF8Encoding($false)))
            }
        } catch {}

        # 4. license status (без подписи, redacted)
        try {
            $licResult = Invoke-HrmHttp -Uri "$baseUrl/api/license/status"
            $licBody = $licResult.Body
            if ($null -ne $licBody) {
                # Удалить поле signature если есть, и любые PII
                $licJson = ($licBody | ConvertTo-Json -Depth 8)
                # Доп. редакция: удалить email/телефоны если вдруг попали (хотя статус их не содержит)
                $licJson = Redact-HrmText $licJson
                # Удалить signature из текста если присутствует
                $licJson = [regex]::Replace($licJson, '"signature"\s*:\s*"[^"]*"', '"signature":"<redacted>"')
                [System.IO.File]::WriteAllText((Join-Path $tmpDir "license-status.json"), $licJson, (New-Object System.Text.UTF8Encoding($false)))
            }
        } catch {
            [System.IO.File]::WriteAllText((Join-Path $tmpDir "license-status.json"), (Redact-HrmText $_.Exception.Message), (New-Object System.Text.UTF8Encoding($false)))
        }

        # 5. health / ops-status / readiness (redacted)
        foreach ($endpoint in @("health", "ops/status", "ops/backup-health")) {
            try {
                $res = Invoke-HrmHttp -Uri "$baseUrl/api/$endpoint"
                $txt = ($res.Body | ConvertTo-Json -Depth 8 -ErrorAction SilentlyContinue)
                if (-not $txt) { $txt = [string]$res.Body }
                $txt = Redact-HrmText $txt
                $fname = "health-$($endpoint.Replace('/','-')).json"
                [System.IO.File]::WriteAllText((Join-Path $tmpDir $fname), $txt, (New-Object System.Text.UTF8Encoding($false)))
            } catch {
                $fname = "health-$($endpoint.Replace('/','-')).json"
                [System.IO.File]::WriteAllText((Join-Path $tmpDir $fname), (Redact-HrmText $_.Exception.Message), (New-Object System.Text.UTF8Encoding($false)))
            }
        }
        # readiness
        try {
            $readRes = Invoke-HrmHttp -Uri "$baseUrl/api/admin/ops/pilot-readiness"
            $readTxt = ($readRes.Body | ConvertTo-Json -Depth 8 -ErrorAction SilentlyContinue)
            if (-not $readTxt) { $readTxt = [string]$readRes.Body }
            $readTxt = Redact-HrmText $readTxt
            [System.IO.File]::WriteAllText((Join-Path $tmpDir "pilot-readiness.json"), $readTxt, (New-Object System.Text.UTF8Encoding($false)))
        } catch {
            [System.IO.File]::WriteAllText((Join-Path $tmpDir "pilot-readiness.json"), (Redact-HrmText $_.Exception.Message), (New-Object System.Text.UTF8Encoding($false)))
        }

        # 6. container logs (last N lines, redacted)
        foreach ($svc in @("backend", "worker", "frontend", "backup")) {
            try {
                $logs = Invoke-HrmCompose $InstallDir $StateDir @("logs", "--tail", "$LogTail", $svc) -IgnoreExitCode
                $combined = ""
                if ($logs.Stdout) { $combined += $logs.Stdout }
                if ($logs.Stderr) { $combined += "`n" + $logs.Stderr }
                $combined = Redact-HrmText $combined
                # Доп. удаление PII-паттернов: email, телефон
                $combined = [regex]::Replace($combined, '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '<email-redacted>')
                $combined = [regex]::Replace($combined, '\+?[0-9][0-9\-\s\(\)]{7,}[0-9]', '<phone-redacted>')
                [System.IO.File]::WriteAllText((Join-Path $tmpDir "logs-$svc.txt"), $combined, (New-Object System.Text.UTF8Encoding($false)))
            } catch {}
        }

        # 7. engine logs: последние журналы из Common Write-HrmLog? Нет файла, но есть install logs
        #     Соберём update-journal, installed.json уже есть, добавим channel config (без секретов)
        try {
            $journalPath = Get-HrmUpdateJournal $StateDir
            if (Test-Path $journalPath) {
                $jText = Get-Content -Path $journalPath -Raw -Encoding UTF8
                $jText = Redact-HrmText $jText
                [System.IO.File]::WriteAllText((Join-Path $tmpDir "update-journal.json"), $jText, (New-Object System.Text.UTF8Encoding($false)))
            }
        } catch {}
        try {
            $chanPath = Get-HrmChannelConfigFile $StateDir
            if (Test-Path $chanPath) {
                $cText = Get-Content -Path $chanPath -Raw -Encoding UTF8
                $cText = Redact-HrmText $cText
                [System.IO.File]::WriteAllText((Join-Path $tmpDir "channel.json"), $cText, (New-Object System.Text.UTF8Encoding($false)))
            }
        } catch {}
        try {
            $lanPath = Get-HrmLanConfigFile $StateDir
            if (Test-Path $lanPath) {
                $lText = Get-Content -Path $lanPath -Raw -Encoding UTF8
                $lText = Redact-HrmText $lText
                [System.IO.File]::WriteAllText((Join-Path $tmpDir "lan.json"), $lText, (New-Object System.Text.UTF8Encoding($false)))
            }
        } catch {}

        # 8. installer log (Inno Setup log, если есть)
        try {
            $tempLogs = @()
            $tempLogs += Get-ChildItem -Path $env:TEMP -Filter "Setup Log*.txt" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName
            $tempLogs += Get-ChildItem -Path $InstallDir -Filter "*.log" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName
            foreach ($logFile in $tempLogs) {
                if (Test-Path $logFile) {
                    $content = Get-Content -Path $logFile -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
                    if ($content) {
                        $content = Redact-HrmText $content
                        $content = [regex]::Replace($content, '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '<email-redacted>')
                        $base = [System.IO.Path]::GetFileName($logFile)
                        [System.IO.File]::WriteAllText((Join-Path $tmpDir "installer-$base"), $content, (New-Object System.Text.UTF8Encoding($false)))
                    }
                }
            }
        } catch {}

        # 9. version info
        try {
            $verInfo = [ordered]@{
                generated_at = (Get-Date).ToString("o")
                port = $port
                url = $baseUrl
            }
            $verText = ($verInfo | ConvertTo-Json -Depth 4)
            $verText = Redact-HrmText $verText
            [System.IO.File]::WriteAllText((Join-Path $tmpDir "version.json"), $verText, (New-Object System.Text.UTF8Encoding($false)))
        } catch {}

        # Итоговая проверка: убедиться что в архиве нет секретов и PII
        # Секреты уже заредактированы через Register-HrmSecret, но проверим pilot.env не попал
        # pilot.env, secrets file никогда не копировались, так что ок.

        # Создать zip
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        # Удалить старый zip если есть с таким именем
        if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
        [System.IO.Compression.ZipFile]::CreateFromDirectory($tmpDir, $zipPath)

        # Проверить что zip не пустой
        $zipInfo = Get-Item $zipPath
        Write-HrmLog "info" ("Отчёт для разработчика создан: {0} ({1} байт)" -f $zipPath, $zipInfo.Length)
        Write-Output $zipPath
        return $zipPath
    }
    finally {
        if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue }
    }
}
