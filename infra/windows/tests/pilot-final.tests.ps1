# Тесты финальной доводки пилота B1–B6 (Windows PowerShell 5.1, моки)
# Проверяют ярлыки, отчёт, LAN, обновление поверх, сборку лицензионного ключа.

param([string]$HarnessPath = $PSScriptRoot)

. (Join-Path $HarnessPath "test-harness.ps1")

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path

Write-Host "== Пилот финал: B1–B6 =="

# --- B3: ярлыки ---
Test-Case "установщик: секция [Icons] с ярлыками рабочего стола и меню Пуск" {
    Initialize-HrmTestEngine
    $iss = Get-Content -Path (Join-Path $script:RepoRoot "installer\installer.iss") -Raw -Encoding UTF8
    Assert-HrmContains $iss "[Icons]" "нет секции Icons"
    Assert-HrmContains $iss '{userdesktop}\HR Manager' "нет ярлыка на рабочем столе"
    Assert-HrmContains $iss '{group}\HR Manager' "нет ярлыка в меню Пуск"
    Assert-HrmContains $iss 'HR Manager — отчёт для разработчика' "нет ярлыка отчёта"
    Assert-HrmContains $iss '-Action support-bundle' "ярлык отчёта не вызывает support-bundle"
    Assert-HrmContains $iss 'HR Manager — перезапуск' "нет ярлыка перезапуска"
    Assert-HrmContains $iss '-Action restart' "ярлык перезапуска не вызывает restart"
    Assert-HrmContains $iss 'HR Manager — доступ по сети' "нет ярлыка доступа по сети"
    Assert-HrmContains $iss '-Action lan-access' "ярлык сети не вызывает lan-access"
    Assert-HrmContains $iss '{userstartup}\HR Manager' "нет автозапуска"
    Assert-HrmContains $iss '-Action open' "ярлык HR Manager не вызывает open"
    Assert-HrmContains $iss '-WindowStyle Hidden' "ярлык open не скрывает окно"
}

Test-Case "установщик: автозапуск после перезагрузки (если Docker Desktop запущен)" {
    $iss = Get-Content -Path (Join-Path $script:RepoRoot "installer\installer.iss") -Raw -Encoding UTF8
    Assert-HrmContains $iss '{userstartup}' "нет автозапуска через userstartup"
    Assert-HrmContains $iss '-Action start' "автозапуск не вызывает start"
}

# --- B1: ключ лицензии в сборке ---
Test-Case "build.ps1 требует открытый ключ лицензии (fail-closed)" {
    $build = Get-Content -Path (Join-Path $script:RepoRoot "installer\build.ps1") -Raw -Encoding UTF8
    Assert-HrmContains $build "HRM_LICENSE_PUBLIC_KEY" "build.ps1 не упоминает HRM_LICENSE_PUBLIC_KEY"
    Assert-HrmContains $build "public_key.b64" "build.ps1 не упоминает public_key.b64"
    Assert-HrmContains $build "LICENSE PUBLIC KEY missing" "build.ps1 не падает с понятной ошибкой при отсутствии ключа"
    Assert-HrmContains $build "PRIVATE KEY" "build.ps1 не проверяет приватный материал"
    Assert-HrmContains $build "32 байта" "build.ps1 не валидирует base64 32 байта"
}

Test-Case "infra/license/public_key.b64 существует и валиден (44 символа, 32 байта)" {
    $keyFile = Join-Path $script:RepoRoot "infra\license\public_key.b64"
    Assert-HrmTrue (Test-Path $keyFile) "файл public_key.b64 отсутствует"
    $content = (Get-Content $keyFile -Raw -Encoding UTF8).Trim()
    Assert-HrmTrue ($content.Length -eq 44 -or $content.Length -eq 43) "длина публичного ключа не 44"
    $decoded = [Convert]::FromBase64String($content)
    Assert-HrmEqual 32 $decoded.Length "публичный ключ не 32 байта"
    Assert-HrmFalse ($content -match "PRIVATE") "публичный ключ содержит приватный материал"
}

Test-Case ".gitignore разрешает infra/license/public_key.b64, но игнорит приватные ключи" {
    $gi = Get-Content -Path (Join-Path $script:RepoRoot ".gitignore") -Raw -Encoding UTF8
    Assert-HrmContains $gi "public_key.b64" "нет игнора public_key.b64"
    Assert-HrmContains $gi "!infra/license/public_key.b64" "нет исключения для infra/license/public_key.b64"
    Assert-HrmContains $gi "private_key.hex" "нет игнора private_key.hex"
    Assert-HrmContains $gi "*.hrmlicense" "нет игнора *.hrmlicense"
}

# --- B4: support-bundle ---
Test-Case "support-bundle создаёт zip на рабочем столе без секретов и PII" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows=$true; powershell=$true; docker=$true; daemon=$true; compose="v2.29.7 (mock)"; port=$true; state_dir=$true; space=$true; config=$true }
    # Подготовить установку
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Initialize-HrmStateDir $state | Out-Null
    Set-HrmInstallRecord $state @{ release_sha = "a" * 40; install_dir = $install; state_dir = $state; port = 8080; installed_at = "2026-09-29T00:00:00Z"; pilot_created = $true }
    # Сгенерировать секреты и зарегистрировать для редакции
    $secret1 = Get-HrmSecret $state "HRM_POSTGRES_PASSWORD"
    $secret2 = Get-HrmSecret $state "HRM_SIGNING_KEY"
    # Подложить PII в логи контейнеров через мок
    $fakePii = "user@example.com +79161234567 Иван Петров"
    # Переопределить мок для logs чтобы вернуть PII + секреты
    Set-HrmExternalMock {
        param($Name, $Arguments, $Stdin, $IgnoreExitCode)
        $global:HRM_MockWorld.Calls += [pscustomobject]@{ Name=$Name; Args=@($Arguments) }
        if ($Name -eq "docker.exe" -or $Name -eq "docker") {
            if ($Arguments -contains "logs") {
                $svc = $Arguments[-1]
                $txt = "log for $svc contains secret $fakePii and also $($global:HRM_TestSecret1) and $($global:HRM_TestSecret2)"
                # Но $fakePii etc нужно через глобальные
                return [pscustomobject]@{ Name=$Name; ExitCode=0; Stdout=$txt; Stderr="" }
            }
            if ($Arguments -contains "ps") { return [pscustomobject]@{ Name=$Name; ExitCode=0; Stdout='[{"Name":"backend","State":"running"}]'; Stderr="" } }
            if ($Arguments -contains "up") { return [pscustomobject]@{ Name=$Name; ExitCode=0; Stdout="up"; Stderr="" } }
            if ($Arguments -contains "config") { return [pscustomobject]@{ Name=$Name; ExitCode=0; Stdout=""; Stderr="" } }
            if ($Arguments -contains "images") { return [pscustomobject]@{ Name=$Name; ExitCode=0; Stdout="hr-manager-pilot-backend:pilot"; Stderr="" } }
            return [pscustomobject]@{ Name=$Name; ExitCode=0; Stdout=""; Stderr="" }
        }
        if ($Name -eq "icacls.exe") { return [pscustomobject]@{ Name=$Name; ExitCode=0; Stdout=""; Stderr="" } }
        if ($Name -eq "netsh.exe") { return [pscustomobject]@{ Name=$Name; ExitCode=0; Stdout="Ok"; Stderr="" } }
        if ($Name -eq "git.exe") { return [pscustomobject]@{ Name=$Name; ExitCode=0; Stdout="a"*40; Stderr="" } }
        return [pscustomobject]@{ Name=$Name; ExitCode=0; Stdout=""; Stderr="" }
    }
    $global:HRM_TestSecret1 = $secret1
    $global:HRM_TestSecret2 = $secret2
    # Установить desktop в temp
    $desktop = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM-desktop-" + [Guid]::NewGuid().ToString("N").Substring(0,8))
    New-Item -ItemType Directory -Path $desktop -Force | Out-Null
    $env:HRM_DESKTOP_DIR = $desktop
    # Создать release.json
    (@{ release_sha = "a"*40; version="0.14.0" } | ConvertTo-Json) | Set-Content -Path (Join-Path $install "release.json") -Encoding UTF8
    # Мок HTTP: license/status возвращает без подписи
    Set-HrmHttpMock {
        param($Uri, $Method, $Body, $Headers)
        if ($Uri -like "*/api/license/status") {
            return @{ StatusCode=200; Body=[pscustomobject]@{ has_license=$true; is_valid=$true; license=[pscustomobject]@{ expires_at="2026-12-31"; max_active_users=5; client_name="Пилот Марии"; days_left=90 } } }
        }
        if ($Uri -like "*/api/health") { return @{ StatusCode=200; Body=[pscustomobject]@{ status="ok" } } }
        if ($Uri -like "*/api/ops/status") { return @{ StatusCode=200; Body=$global:HRM_MockWorld.OpsBody } }
        if ($Uri -like "*/api/ops/backup-health") { return @{ StatusCode=200; Body=[pscustomobject]@{ fresh=$true } } }
        if ($Uri -like "*/api/admin/ops/pilot-readiness") { return @{ StatusCode=200; Body=[pscustomobject]@{ verdict="готово" } } }
        return @{ StatusCode=200; Body=[pscustomobject]@{} }
    }
    $zipPath = New-HrmSupportBundle -InstallDir $install -StateDir $state -LogTail 50
    Assert-HrmTrue (Test-Path $zipPath) "zip не создан"
    Assert-HrmTrue ($zipPath -like "*HR-Manager-report-*.zip") "имя zip не соответствует шаблону"
    Assert-HrmTrue ($zipPath.StartsWith($desktop)) "zip не на рабочем столе"
    # Проверить содержимое zip на отсутствие секретов/PII
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        $allText = ""
        foreach ($entry in $zip.Entries) {
            $stream = $entry.Open()
            $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
            $content = $reader.ReadToEnd()
            $reader.Dispose()
            $stream.Dispose()
            $allText += $content + "`n"
            # Не должно быть pilot.env, secrets
            Assert-HrmNotContains $entry.FullName "pilot.env" "в архиве не должно быть pilot.env"
            Assert-HrmNotContains $entry.FullName "secrets.json" "в архиве не должно быть secrets.json"
        }
        Assert-HrmNotContains $allText $secret1 "секрет утёк в архив"
        Assert-HrmNotContains $allText $secret2 "секрет утёк в архив"
        Assert-HrmNotContains $allText "user@example.com" "PII email утёк"
        Assert-HrmNotContains $allText "+79161234567" "PII телефон утёк"
        # Должен содержать diagnostics
        Assert-HrmContains $allText "docker" "диагностика не попала"
        # Лицензия без подписи
        Assert-HrmNotContains $allText '"signature"' "подпись лицензии не должна быть в архиве"
    } finally { $zip.Dispose() }
    Remove-Item $desktop -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item Env:HRM_DESKTOP_DIR -ErrorAction SilentlyContinue
}

Test-Case "support-bundle не включает дамп БД и pilot.env" {
    Initialize-HrmTestEngine
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Initialize-HrmStateDir $state | Out-Null
    Set-HrmInstallRecord $state @{ release_sha="a"*40; install_dir=$install; state_dir=$state; port=8080; installed_at="2026-09-29T00:00:00Z"; pilot_created=$true }
    (@{ release_sha="a"*40; version="0.14.0" } | ConvertTo-Json) | Set-Content -Path (Join-Path $install "release.json") -Encoding UTF8
    $world = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows=$true; powershell=$true; docker=$true; daemon=$true; compose="v2.29.7 (mock)"; port=$true; state_dir=$true; space=$true; config=$true }
    $desktop = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM-desktop2-" + [Guid]::NewGuid().ToString("N").Substring(0,8))
    New-Item -ItemType Directory -Path $desktop -Force | Out-Null
    $env:HRM_DESKTOP_DIR = $desktop
    $zipPath = New-HrmSupportBundle -InstallDir $install -StateDir $state -LogTail 10
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        $names = @($zip.Entries | ForEach-Object { $_.FullName })
        foreach ($name in $names) {
            Assert-HrmNotContains $name ".pgdump" "дамп БД не должен быть в архиве"
            Assert-HrmNotContains $name "pilot.env" "pilot.env не должен быть"
        }
    } finally { $zip.Dispose() }
    Remove-Item $desktop -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item Env:HRM_DESKTOP_DIR -ErrorAction SilentlyContinue
}

# --- B5: LAN ---
Test-Case "lan-access по умолчанию выключен, включение меняет бинд и правило Firewall" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows=$true; powershell=$true; docker=$true; daemon=$true; compose="v2.29.7 (mock)"; port=$true; state_dir=$true; space=$true; config=$true }
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Initialize-HrmStateDir $state | Out-Null
    Set-HrmInstallRecord $state @{ release_sha="a"*40; install_dir=$install; state_dir=$state; port=8080; installed_at="2026-09-29T00:00:00Z"; pilot_created=$true }
    (@{ release_sha="a"*40; version="0.14.0" } | ConvertTo-Json) | Set-Content -Path (Join-Path $install "release.json") -Encoding UTF8
    # По умолчанию
    $cfg = Get-HrmLanConfig $state
    Assert-HrmFalse $cfg.enabled "по умолчанию LAN должен быть выключен"
    Assert-HrmEqual "127.0.0.1" $cfg.bind "по умолчанию bind 127.0.0.1"
    # Включить
    $result = Invoke-HrmLanAccess -InstallDir $install -StateDir $state -Enable
    Assert-HrmTrue $result.enabled "после Enable должен быть включён"
    Assert-HrmEqual "0.0.0.0" $result.bind "после Enable bind 0.0.0.0"
    # Проверить pilot.env
    $envFile = Get-HrmEnvFile $state
    # Сгенерировать pilot.env через Write-HrmPilotEnv (он уже вызван внутри, но проверим)
    $content = Get-Content $envFile -Raw -Encoding UTF8
    Assert-HrmContains $content "HRM_PILOT_BIND=0.0.0.0" "pilot.env не содержит HRM_PILOT_BIND=0.0.0.0"
    # Проверить что netsh вызван с private,domain
    $netshCalls = @($world.Calls | Where-Object { $_.Name -eq "netsh.exe" -and ($_.Args -join " ") -match "add rule" })
    Assert-HrmTrue ($netshCalls.Count -ge 1) "netsh add rule не вызван"
    $args = ($netshCalls[0].Args -join " ")
    Assert-HrmContains $args "profile=private,domain" "Firewall должен быть только Private/Domain"
    # Выключить
    $result2 = Invoke-HrmLanAccess -InstallDir $install -StateDir $state -Disable
    Assert-HrmFalse $result2.enabled "после Disable должен быть выключен"
    Assert-HrmEqual "127.0.0.1" $result2.bind "после Disable bind 127.0.0.1"
    $content2 = Get-Content $envFile -Raw -Encoding UTF8
    Assert-HrmContains $content2 "HRM_PILOT_BIND=127.0.0.1" "после Disable bind не 127.0.0.1"
    $delCalls = @($world.Calls | Where-Object { $_.Name -eq "netsh.exe" -and ($_.Args -join " ") -match "delete rule" })
    Assert-HrmTrue ($delCalls.Count -ge 1) "netsh delete rule не вызван"
}

Test-Case "lan-access переживает обновление (настройка в StateDir)" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows=$true; powershell=$true; docker=$true; daemon=$true; compose="v2.29.7 (mock)"; port=$true; state_dir=$true; space=$true; config=$true }
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Initialize-HrmStateDir $state | Out-Null
    Set-HrmInstallRecord $state @{ release_sha="a"*40; install_dir=$install; state_dir=$state; port=8080; installed_at="2026-09-29T00:00:00Z"; pilot_created=$true }
    (@{ release_sha="a"*40; version="0.14.0" } | ConvertTo-Json) | Set-Content -Path (Join-Path $install "release.json") -Encoding UTF8
    Invoke-HrmLanAccess -InstallDir $install -StateDir $state -Enable | Out-Null
    # Симулировать обновление: Write-HrmPilotEnv заново должен сохранить bind
    $null = Write-HrmPilotEnv $state ("a"*40) 8080
    $cfg = Get-HrmLanConfig $state
    Assert-HrmTrue $cfg.enabled "после обновления LAN должен остаться включён"
    $content = Get-Content (Get-HrmEnvFile $state) -Raw -Encoding UTF8
    Assert-HrmContains $content "HRM_PILOT_BIND=0.0.0.0" "после обновления bind сбросился"
}

Test-Case "lan-access показывает адрес hostname и IP" {
    Initialize-HrmTestEngine
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Initialize-HrmStateDir $state | Out-Null
    Set-HrmInstallRecord $state @{ release_sha="a"*40; install_dir=$install; state_dir=$state; port=8080; installed_at="2026-09-29T00:00:00Z"; pilot_created=$true }
    (@{ release_sha="a"*40; version="0.14.0" } | ConvertTo-Json) | Set-Content -Path (Join-Path $install "release.json") -Encoding UTF8
    $world = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows=$true; powershell=$true; docker=$true; daemon=$true; compose="v2.29.7 (mock)"; port=$true; state_dir=$true; space=$true; config=$true }
    Invoke-HrmLanAccess -InstallDir $install -StateDir $state -Enable | Out-Null
    $out = @(Invoke-HrmLanAccess -InstallDir $install -StateDir $state | Out-String)
    $text = $out -join "`n"
    Assert-HrmContains $text "http://" "не показан URL для коллеги"
    Assert-HrmContains $text "8080" "не показан порт"
}

Test-Case "compose.pilot.yml использует HRM_PILOT_BIND с default 127.0.0.1" {
    $overlay = Get-Content -Path (Join-Path $script:RepoRoot "infra\compose.pilot.yml") -Raw -Encoding UTF8
    Assert-HrmContains $overlay '${HRM_PILOT_BIND:-127.0.0.1}' "нет HRM_PILOT_BIND с default 127.0.0.1"
    Assert-HrmContains $overlay '${HRM_PILOT_PORT:-8080}' "нет HRM_PILOT_PORT"
}

Test-Case "nginx перезаписывает X-Real-IP на remote_addr (защита от подделки)" {
    $nginx = Get-Content -Path (Join-Path $script:RepoRoot "frontend\nginx.conf") -Raw -Encoding UTF8
    Assert-HrmContains $nginx 'proxy_set_header X-Real-IP $remote_addr;' "nginx не перезаписывает X-Real-IP"
    Assert-HrmNotContains $nginx 'proxy_set_header X-Real-IP 127.0.0.1;' "nginx всё ещё хардкодит 127.0.0.1"
}

Test-Case "HRM_PILOT_BIND: отсутствие -> 127.0.0.1, LAN flow -> 0.0.0.0, произвольное -> fallback 127.0.0.1" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows=$true; powershell=$true; docker=$true; daemon=$true; compose="v2.29.7 (mock)"; port=$true; state_dir=$true; space=$true; config=$true }
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Initialize-HrmStateDir $state | Out-Null
    Set-HrmInstallRecord $state @{ release_sha="a"*40; install_dir=$install; state_dir=$state; port=8080; installed_at="2026-09-29T00:00:00Z"; pilot_created=$true }
    (@{ release_sha="a"*40; version="0.14.0" } | ConvertTo-Json) | Set-Content -Path (Join-Path $install "release.json") -Encoding UTF8
    # Подготовить валидный лицензионный ключ для pilot.env (чтобы не мешать проверке bind)
    $pubBytes = New-Object byte[] 32; [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($pubBytes); $pubB64 = [Convert]::ToBase64String($pubBytes)
    Set-Content -Path (Join-Path $state "license_public_key.b64") -Value $pubB64 -Encoding UTF8 -NoNewline
    # 1. Без lan.json -> 127.0.0.1
    $lanFile = Join-Path $state "lan.json"
    if (Test-Path $lanFile) { Remove-Item $lanFile -Force }
    $null = Write-HrmPilotEnv $state ("a"*40) 8080
    $content = Get-Content (Get-HrmEnvFile $state) -Raw -Encoding UTF8
    Assert-HrmContains $content "HRM_PILOT_BIND=127.0.0.1" "без lan.json должен быть 127.0.0.1"
    Assert-HrmNotContains $content "HRM_PILOT_BIND=0.0.0.0" "без lan.json не должен быть 0.0.0.0"
    # 2. Валидный LAN flow через lan-access Enable -> 0.0.0.0
    Invoke-HrmLanAccess -InstallDir $install -StateDir $state -Enable | Out-Null
    $content2 = Get-Content (Get-HrmEnvFile $state) -Raw -Encoding UTF8
    Assert-HrmContains $content2 "HRM_PILOT_BIND=0.0.0.0" "после Enable должен быть 0.0.0.0"
    $cfg = Get-HrmLanConfig $state
    Assert-HrmTrue $cfg.enabled "после Enable enabled true"
    Assert-HrmEqual "0.0.0.0" $cfg.bind "после Enable bind 0.0.0.0"
    # 3. Произвольные/недопустимые значения -> отказ или безопасный fallback 127.0.0.1
    $badCases = @("10.0.0.5", "192.168.1.100", "10.255.255.254", "172.16.0.1", "evil", "127.0.0.2", "0.0.0.0; rm -rf")
    foreach ($bad in $badCases) {
        Set-HrmJsonFile $state "lan.json" ([ordered]@{ enabled=$true; bind=$bad; updated_at=(Get-Date).ToString("o") })
        $null = Write-HrmPilotEnv $state ("a"*40) 8080
        $c = Get-Content (Get-HrmEnvFile $state) -Raw -Encoding UTF8
        Assert-HrmNotContains $c $bad "произвольный HRM_PILOT_BIND '$bad' не должен попасть в pilot.env"
        Assert-HrmContains $c "HRM_PILOT_BIND=127.0.0.1" "произвольный bind '$bad' должен fallback в 127.0.0.1"
        $cfgBad = Get-HrmLanConfig $state
        Assert-HrmFalse $cfgBad.enabled "арбитражный bind '$bad' должен сбросить enabled"
        Assert-HrmEqual "127.0.0.1" $cfgBad.bind "арбитражный bind '$bad' должен быть 127.0.0.1"
    }
    # 4. После произвольных, валидный LAN flow снова работает
    Invoke-HrmLanAccess -InstallDir $install -StateDir $state -Enable | Out-Null
    $content3 = Get-Content (Get-HrmEnvFile $state) -Raw -Encoding UTF8
    Assert-HrmContains $content3 "HRM_PILOT_BIND=0.0.0.0" "после повторного Enable должен быть 0.0.0.0"
    # 5. Отключение -> 127.0.0.1
    Invoke-HrmLanAccess -InstallDir $install -StateDir $state -Disable | Out-Null
    $content4 = Get-Content (Get-HrmEnvFile $state) -Raw -Encoding UTF8
    Assert-HrmContains $content4 "HRM_PILOT_BIND=127.0.0.1" "после Disable должен быть 127.0.0.1"
}

# --- B6: обновление поверх ---
Test-Case "Install-HrmApp с новой версией ведёт через Update-HrmApp (бэкап → откат)" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows=$true; powershell=$true; docker=$true; daemon=$true; compose="v2.29.7 (mock)"; port=$true; state_dir=$true; space=$true; config=$true }
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $sourceN1 = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM-sourceN1-" + [Guid]::NewGuid().ToString("N").Substring(0,8))
    $sourceN2 = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM-sourceN2-" + [Guid]::NewGuid().ToString("N").Substring(0,8))
    New-Item -ItemType Directory -Path $sourceN1 -Force | Out-Null
    New-Item -ItemType Directory -Path $sourceN2 -Force | Out-Null
    # Создать снимки N1 и N2 с разными release_sha
    New-HrmFakeSnapshot -Root $sourceN1 -ReleaseSha ("1"*40)
    New-HrmFakeSnapshot -Root $sourceN2 -ReleaseSha ("2"*40)
    # Установить N1
    Install-HrmApp -SourceDir $sourceN1 -InstallDir $install -StateDir $state -Port 8080
    Assert-HrmEqual ("1"*40) (Get-HrmInstallRecord $state).release_sha "первая установка не зафиксировала sha"
    $world.BackupNowCount = 0
    $world.AlembicUpgradeCount = 0
    $world.TagCount = 0
    # Повторный запуск с N2 (Setup.exe новой версии поверх старой) — должен пойти через update с бэкапом
    Install-HrmApp -SourceDir $sourceN2 -InstallDir $install -StateDir $state -Port 8080
    Assert-HrmEqual ("2"*40) (Get-HrmInstallRecord $state).release_sha "обновление не обновило sha"
    Assert-HrmTrue ($world.BackupNowCount -ge 1) "бэкап-ворота не выполнялись при обновлении поверх"
    Assert-HrmTrue ($world.AlembicUpgradeCount -ge 1) "миграция не выполнялась"
    Remove-Item $sourceN1 -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $sourceN2 -Recurse -Force -ErrorAction SilentlyContinue
}

Test-Case "обновление с падающей миграцией откатывается к прежним образам" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows=$true; powershell=$true; docker=$true; daemon=$true; compose="v2.29.7 (mock)"; port=$true; state_dir=$true; space=$true; config=$true }
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $sourceN1 = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM-srcA-" + [Guid]::NewGuid().ToString("N").Substring(0,8))
    $sourceN2 = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM-srcB-" + [Guid]::NewGuid().ToString("N").Substring(0,8))
    New-Item -ItemType Directory -Path $sourceN1 -Force | Out-Null
    New-Item -ItemType Directory -Path $sourceN2 -Force | Out-Null
    New-HrmFakeSnapshot -Root $sourceN1 -ReleaseSha ("a"*40)
    New-HrmFakeSnapshot -Root $sourceN2 -ReleaseSha ("b"*40)
    Install-HrmApp -SourceDir $sourceN1 -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    # Сломать миграцию: мок будет возвращать ошибку на alembic upgrade
    $world.SimulateStaleRelease = $true
    # Сделать чтобы alembic upgrade падал? Вместо Stale сделаем DownOk false для отката?
    # Используем Update-HrmApp напрямую с падающей миграцией: мок для exec alembic upgrade вернёт код 1 если SimulateStaleRelease
    # Настроим мир чтобы upgrade падал через переопределение мока
    Set-HrmExternalMock {
        param($Name, $Arguments, $Stdin, $IgnoreExitCode)
        $global:HRM_MockWorld.Calls += [pscustomobject]@{ Name=$Name; Args=@($Arguments) }
        if ($Name -eq "docker.exe" -or $Name -eq "docker") {
            if ($Arguments -contains "logs") { return [pscustomobject]@{ Name=$Name; ExitCode=0; Stdout="logs"; Stderr="" } }
            if ($Arguments -contains "ps") { return [pscustomobject]@{ Name=$Name; ExitCode=0; Stdout='[{"Name":"backend","State":"running"}]'; Stderr="" } }
            if ($Arguments -contains "up") { $global:HRM_MockWorld.Running=$true; return [pscustomobject]@{ Name=$Name; ExitCode=0; Stdout="up"; Stderr="" } }
            if ($Arguments -contains "down") { return [pscustomobject]@{ Name=$Name; ExitCode=0; Stdout="down"; Stderr="" } }
            if ($Arguments -contains "build") { $global:HRM_MockWorld.BuildCount++; return [pscustomobject]@{ Name=$Name; ExitCode=0; Stdout="built"; Stderr="" } }
            if ($Arguments -contains "exec" -and ($Arguments -join " ") -match "alembic upgrade") {
                return [pscustomobject]@{ Name=$Name; ExitCode=1; Stdout=""; Stderr="migration failed" }
            }
            if ($Arguments -contains "exec" -and ($Arguments -join " ") -match "alembic current") {
                return [pscustomobject]@{ Name=$Name; ExitCode=0; Stdout="0013 (head)"; Stderr="" }
            }
            if ($Arguments -contains "run" -and ($Arguments -join " ") -match "backup") { return [pscustomobject]@{ Name=$Name; ExitCode=0; Stdout="ok"; Stderr="" } }
            if (($Name -eq "docker.exe" -or $Name -eq "docker") -and $Arguments[0] -eq "image") { return [pscustomobject]@{ Name=$Name; ExitCode=0; Stdout="sha256:old"; Stderr="" } }
            if (($Name -eq "docker.exe" -or $Name -eq "docker") -and $Arguments[0] -eq "tag") { $global:HRM_MockWorld.TagCount++; return [pscustomobject]@{ Name=$Name; ExitCode=0; Stdout=""; Stderr="" } }
        }
        if ($Name -eq "icacls.exe") { return [pscustomobject]@{ Name=$Name; ExitCode=0; Stdout=""; Stderr="" } }
        if ($Name -eq "netsh.exe") { return [pscustomobject]@{ Name=$Name; ExitCode=0; Stdout="Ok"; Stderr="" } }
        if ($Name -eq "git.exe") { return [pscustomobject]@{ Name=$Name; ExitCode=0; Stdout="a"*40; Stderr="" } }
        return [pscustomobject]@{ Name=$Name; ExitCode=0; Stdout=""; Stderr="" }
    }
    Set-HrmHttpMock {
        param($Uri, $Method, $Body, $Headers)
        if ($Uri -like "*/api/health") { return @{ StatusCode=200; Body=[pscustomobject]@{ status="ok" } } }
        if ($Uri -like "*/api/ops/status") { return @{ StatusCode=200; Body=$global:HRM_MockWorld.OpsBody } }
        if ($Uri -like "*/api/ops/backup-health") { return @{ StatusCode=200; Body=[pscustomobject]@{ fresh=$true } } }
        return @{ StatusCode=200; Body=[pscustomobject]@{} }
    }
    $caught = $false
    try { Install-HrmApp -SourceDir $sourceN2 -InstallDir $install -StateDir $state -Port 8080 | Out-Null } catch { $caught = $true }
    Assert-HrmTrue $caught "падающая миграция не бросила исключение"
    Assert-HrmEqual ("a"*40) (Get-HrmInstallRecord $state).release_sha "после отката sha должен остаться прежним"
    Assert-HrmTrue ($global:HRM_MockWorld.TagCount -ge 1) "откат к прежним образам не выполнен"
    # Б8: результат обновления фиксируется для трея/мастера/диагностики — «восстановлена прежняя версия», без секретов
    $result = Get-HrmUpdateResult $state
    Assert-HrmTrue ($null -ne $result) "update-result.json не создан при откате"
    Assert-HrmEqual "rolled_back" ([string]$result.status) "статус результата должен быть rolled_back"
    Assert-HrmTrue ([bool]$result.rolled_back) "флаг rolled_back должен быть выставлен"
    Assert-HrmContains ([string]$result.message) "прежняя версия" "сообщение не объясняет откат простыми словами"
    Assert-HrmNotContains ([string]$result.message) "Выполните" "сообщение об откате не должно требовать ручных действий от Марии"
    Assert-HrmEqual "1.0.0" ([string]$result.from_version) "не зафиксирована прежняя версия при откате"
    Remove-Item $sourceN1 -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $sourceN2 -Recurse -Force -ErrorAction SilentlyContinue
    Clear-HrmExternalMock
    Clear-HrmHttpMock
}


# --- B6 (0.15.0): предпросмотр обновления и сохранность данных ---
Test-Case "предпросмотр обновления: текущая/новая версия, changelog, проверки и предупреждение о данных" {
    Initialize-HrmTestEngine
    $null = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows=$true; powershell=$true; docker=$true; daemon=$true; compose="v2.29.7 (mock)"; port=$true; state_dir=$true; space=$true; config=$true }
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $sourceN1 = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM-prevN1-" + [Guid]::NewGuid().ToString("N").Substring(0,8))
    $sourceN2 = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM-prevN2-" + [Guid]::NewGuid().ToString("N").Substring(0,8))
    New-Item -ItemType Directory -Path $sourceN1 -Force | Out-Null
    New-Item -ItemType Directory -Path $sourceN2 -Force | Out-Null
    New-HrmFakeSnapshot -Root $sourceN1 -ReleaseSha ("1"*40)
    New-HrmFakeSnapshot -Root $sourceN2 -ReleaseSha ("2"*40)
    (@{ release_sha = ("2"*40); version = "0.15.0"; changelog = @("Проверка состояния перед запуском", "Обновление сохраняет данные и лицензию") } | ConvertTo-Json) |
        Set-Content -Path (Join-Path $sourceN2 "release.json") -Encoding UTF8
    Install-HrmApp -SourceDir $sourceN1 -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    $preview = Get-HrmUpdatePreview -ReleaseDir $sourceN2 -InstallDir $install -StateDir $state
    Assert-HrmEqual "1.0.0" ([string]$preview.current_version) "предпросмотр не показал текущую версию"
    Assert-HrmEqual "0.15.0" ([string]$preview.new_version) "предпросмотр не показал новую версию"
    Assert-HrmFalse ([bool]$preview.same_version) "разные версии не должны считаться совпадением"
    Assert-HrmTrue ([bool]$preview.can_update) "предпросмотр заблокировал обновление без причины"
    Assert-HrmTrue (@($preview.changelog).Count -ge 2) "changelog не прочитан из release.json"
    Assert-HrmContains ([string]$preview.data_notice) "резервная копия" "нет предупреждения про резервную копию"
    Assert-HrmContains ([string]$preview.data_notice) "сохраняются" "нет предупреждения о сохранении данных"
    Assert-HrmEqual 8080 ([int]$preview.port) "предпросмотр не сохраняет порт установки"
    Assert-HrmFalse ([bool]$preview.lan_enabled) "по умолчанию LAN должен быть выключен"
    $keys = @($preview.checks | ForEach-Object { $_.key })
    foreach ($expected in @("space", "license", "settings", "port", "docker", "backup_volume")) {
        Assert-HrmTrue ($keys -contains $expected) ("в предпросмотре нет проверки '" + $expected + "'")
    }
    $portCheck = @($preview.checks | Where-Object { $_.key -eq "port" })[0]
    Assert-HrmContains ([string]$portCheck.detail) "8080" "проверка порта не упоминает текущий порт"
    $settingsCheck = @($preview.checks | Where-Object { $_.key -eq "settings" })[0]
    Assert-HrmEqual "ok" ([string]$settingsCheck.status) "сохранение пользователей/настроек не подтверждено"
    # Человеческие строки для мастера: без docker/powershell-жаргона.
    $lines = Format-HrmUpdatePreview -Preview $preview
    $text = ($lines -join "`n")
    Assert-HrmContains $text "Обновление HR Manager: 1.0.0" "заголовок предпросмотра без версий"
    Assert-HrmNotContains $text "docker compose" "текст предпросмотра не должен требовать ручных команд Docker"
    Remove-Item $sourceN1 -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $sourceN2 -Recurse -Force -ErrorAction SilentlyContinue
}

Test-Case "предпросмотр обновления: та же версия -> same_version, повторный запуск Setup не ломает установку" {
    Initialize-HrmTestEngine
    $null = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows=$true; powershell=$true; docker=$true; daemon=$true; compose="v2.29.7 (mock)"; port=$true; state_dir=$true; space=$true; config=$true }
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $source = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM-same-" + [Guid]::NewGuid().ToString("N").Substring(0,8))
    New-Item -ItemType Directory -Path $source -Force | Out-Null
    New-HrmFakeSnapshot -Root $source -ReleaseSha ("c"*40)
    Install-HrmApp -SourceDir $source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    $preview = Get-HrmUpdatePreview -ReleaseDir $source -InstallDir $install -StateDir $state
    Assert-HrmTrue ([bool]$preview.same_version) "одинаковый release_sha должен давать same_version=true"
    Remove-Item $source -Recurse -Force -ErrorAction SilentlyContinue
}

Test-Case "обновление поверх сохраняет лицензию, порт, LAN и не удаляет тома данных" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows=$true; powershell=$true; docker=$true; daemon=$true; compose="v2.29.7 (mock)"; port=$true; state_dir=$true; space=$true; config=$true }
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $sourceN1 = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM-keepN1-" + [Guid]::NewGuid().ToString("N").Substring(0,8))
    $sourceN2 = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM-keepN2-" + [Guid]::NewGuid().ToString("N").Substring(0,8))
    New-Item -ItemType Directory -Path $sourceN1 -Force | Out-Null
    New-Item -ItemType Directory -Path $sourceN2 -Force | Out-Null
    New-HrmFakeSnapshot -Root $sourceN1 -ReleaseSha ("3"*40)
    New-HrmFakeSnapshot -Root $sourceN2 -ReleaseSha ("4"*40)
    (@{ release_sha = ("4"*40); version = "0.15.0"; changelog = @("Обновление сохраняет данные") } | ConvertTo-Json) |
        Set-Content -Path (Join-Path $sourceN2 "release.json") -Encoding UTF8
    Install-HrmApp -SourceDir $sourceN1 -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    # Пользовательское состояние ДО обновления: ключ лицензии и явно включённый LAN.
    $licenseKeyBefore = Get-HrmLicensePublicKey $state
    Invoke-HrmLanAccess -InstallDir $install -StateDir $state -Enable | Out-Null
    Assert-HrmTrue ((Get-HrmLanConfig $state).enabled) "LAN не включился до обновления"
    $world.RemovedVolumes = @()
    $world.BackupNowCount = 0
    # Обновление поверх (как второй Setup.exe новой версии).
    Install-HrmApp -SourceDir $sourceN2 -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    # 1) Лицензия/ключ проверки, секреты, порт и LAN не потеряны.
    Assert-HrmEqual $licenseKeyBefore (Get-HrmLicensePublicKey $state) "ключ проверки лицензии изменился при обновлении"
    Assert-HrmTrue (Test-Path (Get-HrmSecretsFile $state)) "секреты установки потеряны"
    $record = Get-HrmInstallRecord $state
    Assert-HrmEqual 8080 ([int]$record.port) "порт изменился при обновлении"
    Assert-HrmTrue ((Get-HrmLanConfig $state).enabled) "настройка LAN потеряна при обновлении"
    Assert-HrmEqual ("4"*40) ([string]$record.release_sha) "запись установки не обновилась на новый релиз"
    Assert-HrmEqual "0.15.0" ([string]$record.version) "версия установки не обновилась"
    # 2) Тома данных и базы не удаляются: ни volume rm/prune, ни down -v.
    Assert-HrmEqual 0 (@($world.RemovedVolumes).Count) "при обновлении удалялись тома"
    $destroying = @($world.Calls | Where-Object {
            $joined = ($_.Args -join " ")
            ($joined -match "volume\s+(rm|prune)") -or ($joined -match "down\b.*-v") -or ($joined -match "down\s+-v")
        })
    Assert-HrmEqual 0 $destroying.Count "при обновлении вызывались разрушительные команды Docker"
    # 3) Бэкап перед миграцией делался, и результат обновления зафиксирован.
    Assert-HrmTrue ($world.BackupNowCount -ge 1) "перед обновлением не создавался свежий бэкап"
    $result = Get-HrmUpdateResult $state
    Assert-HrmTrue ($null -ne $result) "update-result.json не создан после успешного обновления"
    Assert-HrmEqual "done" ([string]$result.status) "статус успешного обновления должен быть done"
    Assert-HrmContains ([string]$result.message) "Обновление завершено" "нет сообщения «Обновление завершено»"
    Assert-HrmFalse ([bool]$result.rolled_back) "успешное обновление не должно помечаться откатом"
    Assert-HrmEqual "1.0.0" ([string]$result.from_version) "не зафиксирована прежняя версия"
    Assert-HrmEqual "0.15.0" ([string]$result.to_version) "не зафиксирована новая версия"
    # 4) Приложение после обновления отвечает (readiness) и открывается.
    Assert-HrmTrue (Test-HrmComposeRunning -InstallDir $install -StateDir $state) "после обновления контейнеры не запущены"
    Remove-Item $sourceN1 -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $sourceN2 -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ("Пилот финал: {0} пройдено, {1} провалено" -f $global:HRM_TestPassed, $global:HRM_TestFailed)
