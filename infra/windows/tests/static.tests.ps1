# Статические проверки движка и пилотного оверлея: парсер PowerShell,
# отсутствие секретов-литералов, единственная точка запуска процессов,
# отсутствие битых символов, структура compose.pilot.yml (loopback-only).
# НЕ трогают реальную машину — только читают файлы репозитория.

param([string]$HarnessPath = $PSScriptRoot)

. (Join-Path $HarnessPath "test-harness.ps1")

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$script:EngineDir = Join-Path $RepoRoot "infra\windows\engine"
$script:WindowsDir = Join-Path $RepoRoot "infra\windows"

function Get-HrmEngineFiles {
    $files = @(
        Get-ChildItem -Path $WindowsDir -File -Filter *.ps1
        Get-ChildItem -Path $EngineDir -File -Filter *.psm1
    ) | ForEach-Object { $_.FullName }
    return $files
}

Write-Host "== Статические проверки =="

Test-Case "все файлы движка проходят парсер PowerShell" {
    $files = Get-HrmEngineFiles
    Assert-HrmTrue ($files.Count -ge 8) "ожидались файлы движка"
    foreach ($file in $files) {
        $tokens = $null
        $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile($file, [ref]$tokens, [ref]$errors) | Out-Null
        if ($errors -and $errors.Count -gt 0) {
            throw ("Ошибки парсера в {0}: {1}" -f $file, ($errors[0].Message))
        }
    }
}

Test-Case "нет битых символов U+FFFD в файлах движка" {
    foreach ($file in Get-HrmEngineFiles) {
        $text = Get-Content -Path $file -Raw -Encoding UTF8
        Assert-HrmNotContains $text ([char]0xFFFD) ("битый символ в $file")
    }
}

Test-Case "нет секретов-литералов (пароли разработки, 32/64-hex) в движке" {
    $patterns = @("AdminAdmin123", "Str0ng-Pass-2026", "DEVELOPMENT_BOOTSTRAP_ADMIN_PASSWORD\s*=\s*""[^""]+")
    $hex = "[^0-9a-fA-F]([0-9a-fA-F]{32}|[0-9a-fA-F]{64})[^0-9a-fA-F]"
    foreach ($file in Get-HrmEngineFiles) {
        if ($file -match "tests\\") { continue } # тесты содержат примеры секретов
        if ($file -match "Crypto\.psm1$") { continue } # публичные константы RFC 8032, не секреты
        $text = Get-Content -Path $file -Raw -Encoding UTF8
        foreach ($pattern in $patterns) {
            if ($text -match $pattern) {
                throw ("Секрет-литерал ({0}) в {1}" -f $pattern, $file)
            }
        }
        # hex-литералы допустимы только в тестах/комментариях примеров; в
        # engine/*.psm1 их быть не должно.
        if ($file -match "engine\\[^\\]+\.psm1$" -and $text -match $hex) {
            throw ("Шестнадцатеричный литерал, похожий на секрет, в $file")
        }
    }
}

Test-Case "внешние процессы запускаются только через Invoke-HrmExternal" {
    foreach ($file in Get-HrmEngineFiles) {
        $text = Get-Content -Path $file -Raw -Encoding UTF8
        if ($file -match "Common\.psm1$") { continue } # сам слой может
        if ($text -match "Start-Process") { throw ("Start-Process вне Common.psm1: $file") }
        if ($text -match "System\.Diagnostics\.Process") { throw ("Прямой Process вне Common.psm1: $file") }
        if ($text -match "(&\s*docker|docker\.exe\s+(?!\s*$))") { throw ("Прямой вызов docker: $file") }
    }
}

Test-Case "разрешённые имена внешних команд — только белый список" {
    $allowed = @("docker", "docker.exe", "icacls.exe", "git.exe")
    foreach ($file in Get-HrmEngineFiles) {
        $text = Get-Content -Path $file -Raw -Encoding UTF8
        $matches = [regex]::Matches($text, 'Invoke-HrmExternal\s+-Name\s+"([^"]+)"')
        foreach ($m in $matches) {
            $name = $m.Groups[1].Value
            if ($allowed -notcontains $name) {
                throw ("Запрещённая внешняя команда '{0}' в {1}" -f $name, $file)
            }
        }
    }
}

Test-Case "все действия ValidateSet реализованы в switch входной точки" {
    $entry = Get-Content -Path (Join-Path $WindowsDir "hr-manager.ps1") -Raw -Encoding UTF8
    $vsMatch = [regex]::Match($entry, '\[ValidateSet\((.*?)\)\]', [System.Text.RegularExpressions.RegexOptions]::Singleline)
    Assert-HrmTrue $vsMatch.Success "не найден ValidateSet"
    $actions = @([regex]::Matches($vsMatch.Groups[1].Value, '"([^"]+)"') | ForEach-Object { $_.Groups[1].Value })
    foreach ($action in $actions) {
        Assert-HrmContains $entry ('"' + $action + '" {') ("нет ветки switch для действия " + $action)
    }
    Assert-HrmContains $entry '#requires -Version 5.1' "нет требования PowerShell 5.1"
}

Test-Case "secrets.json пишется только через Set-HrmJsonFile (атомарно + ACL)" {
    foreach ($file in Get-HrmEngineFiles) {
        if ($file -match "Common\.psm1$" -or $file -match "Secrets\.psm1$") { continue }
        $text = Get-Content -Path $file -Raw -Encoding UTF8
        Assert-HrmNotContains $text "secrets.json" ("прямое упоминание secrets.json в $file")
    }
    $secrets = Get-Content -Path (Join-Path $EngineDir "Secrets.psm1") -Raw -Encoding UTF8
    Assert-HrmContains $secrets 'Set-HrmJsonFile $StateDir "secrets.json"' "secrets.json должен писаться атомарно"
}

Test-Case "пилотный оверлей: стабильное имя проекта и только loopback-публикация" {
    $overlay = Get-Content -Path (Join-Path $RepoRoot "infra\compose.pilot.yml") -Raw -Encoding UTF8
    Assert-HrmContains $overlay "name: hr-manager-pilot" "нет стабильного имени проекта"
    # Все published-порты — только 127.0.0.1.
    $portLines = @($overlay -split "`n" | Where-Object { $_ -match '"(\d+\.\d+\.\d+\.\d+):\d+:\d+"' -or $_ -match '"127\.0\.0\.1:\$\{HRM_PILOT_PORT' })
    Assert-HrmTrue ($portLines.Count -ge 1) "не найдены published-порты"
    foreach ($line in $portLines) {
        $trimmed = $line.Trim()
        if ($trimmed -match '^"\d+:\d+"') { continue } # внутренние порты контейнеров
        Assert-HrmContains $trimmed "127.0.0.1" ("порт опубликован не на loopback: " + $trimmed)
    }
    Assert-HrmContains $overlay '127.0.0.1:${HRM_PILOT_PORT:-8080}:8080' "фронтенд должен публиковаться только на 127.0.0.1"
    Assert-HrmNotContains $overlay 'APP_DEBUG: "true"' "APP_DEBUG=true в пилотном оверлее"
    Assert-HrmContains $overlay "pilot_pgdata" "нет именованного тома pilot_pgdata"
    Assert-HrmContains $overlay "pilot_backups" "нет именованного тома pilot_backups"
    # mailpit — только неактивный профиль.
    Assert-HrmContains $overlay 'profiles: ["pilot-disabled"]' "mailpit не вынесен в неактивный профиль"
}

Test-Case 'пилотный оверлей требует все обязательные секреты (${VAR:?})' {
    $overlay = Get-Content -Path (Join-Path $RepoRoot "infra\compose.pilot.yml") -Raw -Encoding UTF8
    foreach ($required in @("HRM_POSTGRES_PASSWORD", "HRM_SIGNING_KEY", "HRM_BOOTSTRAP_ADMIN_PASSWORD", "HRM_EXCHANGE_TOKEN", "HRM_BACKUP_KEY", "HRM_BACKUP_KEY_ID")) {
        Assert-HrmContains $overlay ('${' + $required + ':?') ("обязательная переменная " + $required + " не затребована")
    }
    $signingKeyUses = ([regex]::Matches($overlay, 'SECRET_KEY: \$\{HRM_SIGNING_KEY:\?')).Count
    Assert-HrmEqual 3 $signingKeyUses "SECRET_KEY обязателен для backend, worker и backup"
}

Test-Case 'пилотный оверлей: открытый ключ лицензии обязателен (${HRM_LICENSE_PUBLIC_KEY:?}) для backend, worker и backup' {
    $overlay = Get-Content -Path (Join-Path $RepoRoot "infra\compose.pilot.yml") -Raw -Encoding UTF8
    $mapping = 'LICENSE_PUBLIC_KEY: ${HRM_LICENSE_PUBLIC_KEY:?HRM_LICENSE_PUBLIC_KEY is required for the pilot}'
    Assert-HrmContains $overlay $mapping "открытый ключ лицензии не передаётся в backend как обязательная переменная"
    $uses = ([regex]::Matches($overlay, [regex]::Escape($mapping))).Count
    Assert-HrmEqual 3 $uses "LICENSE_PUBLIC_KEY обязателен для backend, worker и backup (все загружают Settings)"
    # Значение по умолчанию отключило бы fail-closed проверку.
    Assert-HrmNotContains $overlay '${HRM_LICENSE_PUBLIC_KEY:-' "у открытого ключа лицензии не должно быть значения по умолчанию"
    # pilot.env подключается ТОЛЬКО через --env-file (интерполяция); env_file: в сервисе
    # скопировал бы в контейнер backend весь файл, включая ключ шифрования бэкапов.
    Assert-HrmFalse ([regex]::IsMatch($overlay, '(?m)^\s+env_file:')) "env_file: в пилотном оверлее (утечка секретов в контейнер)"
    # В приложении только ОТКРЫТЫЙ ключ; закрытый не упоминается даже по имени.
    Assert-HrmNotContains $overlay "PRIVATE_KEY" "закрытый ключ упомянут в оверлее"
    Assert-HrmNotContains $overlay "private_key" "закрытый ключ упомянут в оверлее"
}

Test-Case "backend видит состояние бэкапов только для чтения" {
    $overlay = Get-Content -Path (Join-Path $RepoRoot "infra\compose.pilot.yml") -Raw -Encoding UTF8
    Assert-HrmContains $overlay "BACKUP_STATE_FILE: /var/backups/hr-manager/state.json" "backend не настроен на состояние бэкапов"
    Assert-HrmContains $overlay "pilot_backups:/var/backups/hr-manager:ro" "backend не подключает backup volume только для чтения"
}

Test-Case "пилотный оверлей: канал обновлений требует токен движка и staging bind mount" {
    $overlay = Get-Content -Path (Join-Path $RepoRoot "infra\compose.pilot.yml") -Raw -Encoding UTF8
    Assert-HrmContains $overlay '${HRM_UPDATE_ENGINE_TOKEN:?' "нет обязательного токена движка канала"
    Assert-HrmContains $overlay '${HRM_STAGING_DIR:?' "нет обязательного staging-каталога"
    Assert-HrmContains $overlay ":/updates" "нет bind mount /updates"
    Assert-HrmContains $overlay "UPDATE_STAGING_DIR: /updates" "нет UPDATE_STAGING_DIR"
    # Никаких docker.sock/командных сокетов в контейнер.
    Assert-HrmNotContains $overlay "docker.sock" "Docker socket в оверлее"
}

Test-Case "комментарий-заголовок оверлея описывает локальную модель доверия" {
    $overlay = Get-Content -Path (Join-Path $RepoRoot "infra\compose.pilot.yml") -Raw -Encoding UTF8
    Assert-HrmContains $overlay "127.0.0.1" "заголовок не описывает loopback"
    Assert-HrmContains $overlay "SESSION_COOKIE_SECURE" "нет строки о локальной модели Secure-кук"
}

Test-Case "движок всегда объединяет базовый compose и пилотный overlay" {
    $compose = Get-Content -Path (Join-Path $EngineDir "Compose.psm1") -Raw -Encoding UTF8
    $preflight = Get-Content -Path (Join-Path $EngineDir "Preflight.psm1") -Raw -Encoding UTF8
    $update = Get-Content -Path (Join-Path $EngineDir "Update.psm1") -Raw -Encoding UTF8
    foreach ($text in @($compose, $preflight, $update)) {
        Assert-HrmContains $text 'infra\docker-compose.yml' "не подключён базовый compose-файл"
        Assert-HrmContains $text 'infra\compose.pilot.yml' "не подключён пилотный overlay"
    }
}

Test-Case "frontend повторно разрешает адрес backend после пересоздания контейнера" {
    $nginx = Get-Content -Path (Join-Path $RepoRoot "frontend\nginx.conf") -Raw -Encoding UTF8
    Assert-HrmContains $nginx "resolver 127.0.0.11" "не настроен встроенный DNS Docker"
    Assert-HrmContains $nginx 'set $backend_upstream http://backend:8000;' "backend задан статически"
    Assert-HrmContains $nginx 'proxy_pass $backend_upstream;' "proxy_pass не использует динамическое разрешение"
    Assert-HrmContains $nginx 'rewrite ^/api/(.*)$ /$1 break;' "при динамическом proxy_pass потеряна очистка /api"
}

Test-Case "деинсталлятор удаляет обновлённые файлы приложения, но не StateDir" {
    $installer = Get-Content -Path (Join-Path $RepoRoot "installer\installer.iss") -Raw -Encoding UTF8
    Assert-HrmContains $installer "[UninstallDelete]" "нет очистки файлов, заменённых update-пайплайном"
    Assert-HrmContains $installer 'Type: filesandordirs; Name: "{app}"' "каталог приложения не очищается целиком"
    Assert-HrmNotContains $installer 'Type: filesandordirs; Name: "{localappdata}\HRManager"' "деинсталлятор не должен удалять StateDir"
}

# --- Phase 14: installer-скрипты (сборка и Authenticode-подпись) --------------

$script:InstallerDir = Join-Path $RepoRoot "installer"

Test-Case "installer-скрипты проходят парсер PowerShell" {
    $files = Get-ChildItem -Path $InstallerDir -File -Filter *.ps1 | ForEach-Object { $_.FullName }
    Assert-HrmTrue ($files.Count -ge 2) "ожидались build.ps1 и sign.ps1"
    foreach ($file in $files) {
        $tokens = $null
        $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile($file, [ref]$tokens, [ref]$errors) | Out-Null
        if ($errors -and $errors.Count -gt 0) {
            throw ("Ошибки парсера в {0}: {1}" -f $file, ($errors[0].Message))
        }
    }
}

Test-Case "подпись релиза не трогает системные хранилища сертификатов" {
    # certutil -addstore Root/TrustedPublisher на CI-раннере показывает диалог
    # подтверждения и висит до таймаута job'а: test-режим обязан обходиться
    # только Cert:\CurrentUser\My.
    $sign = Get-Content -Path (Join-Path $InstallerDir "sign.ps1") -Raw -Encoding UTF8
    Assert-HrmNotContains $sign "certutil -addstore" "certutil -addstore зависает в CI"
    Assert-HrmNotContains $sign 'X509Store("Root"' "запись в системный Root запрещена"
    Assert-HrmNotContains $sign 'X509Store("TrustedPublisher"' "запись в TrustedPublisher запрещена"
    Assert-HrmContains $sign 'CertStoreLocation "Cert:\CurrentUser\My"' "test-сертификат обязан жить в CurrentUser\My"
}

Test-Case "sign.ps1 завершается явным кодом возврата" {
    # Без явного exit $LASTEXITCODE вызывающей стороны остаётся от signtool
    # verify, и CI ошибочно считает успешную подпись провалом.
    $sign = Get-Content -Path (Join-Path $InstallerDir "sign.ps1") -Raw -Encoding UTF8
    Assert-HrmContains $sign "exit `$scriptExitCode" "нет явного exit с кодом возврата"
    Assert-HrmContains $sign 'Write-Host ("::error title=HRM-AUTHENTICODE::' "отказ не виден в check-runs"
}

Test-Case "attestation отражает фактический результат проверки подписи" {
    $sign = Get-Content -Path (Join-Path $InstallerDir "sign.ps1") -Raw -Encoding UTF8
    Assert-HrmNotContains $sign "signtool_verify_ok = `$true" "signtool_verify_ok захардкожен"
    Assert-HrmContains $sign "signtool_verify_ok = `$signtoolVerifyOk" "attestation не берёт фактический результат"
    Assert-HrmContains $sign "authenticode_present = (`$windowsStatus -ne ""NotSigned"")" "authenticode_present захардкожен"
    Assert-HrmContains $sign "windows_chain_trusted = `$windowsChainTrusted" "нет фактического статуса доверия Windows"
    Assert-HrmContains $sign "Test-HrmUntrustedRootOnly" "недоверенный корень не отличён от других ошибок"
    Assert-HrmContains $sign '"authenticode.py"' "нет независимой проверки подписи"
}

Test-Case "production-режим подписи остаётся fail-closed" {
    $sign = Get-Content -Path (Join-Path $InstallerDir "sign.ps1") -Raw -Encoding UTF8
    Assert-HrmContains $sign "production: нужен -PfxPath" "production без PFX"
    Assert-HrmContains $sign "production: нужен -TimestampUrl" "production без обязательной метки времени"
    Assert-HrmContains $sign "production: нужен -ExpectedPublisher" "production без ожидаемого издателя"
    Assert-HrmContains $sign "HRM_AUTHENTICODE_PFX_PASSWORD" "пароль PFX не берётся из переменной окружения"
    Assert-HrmContains $sign "production: подпись не подтверждена Windows" "production терпит недоверенную цепочку"
    Assert-HrmContains $sign "--require-timestamp" "production не требует метку времени у верификатора"
}

Test-Case "JSON-артефакты релиза пишутся без BOM" {
    # Python-часть release-пайплайна читает их через json.loads, который BOM
    # не принимает; Set-Content -Encoding UTF8 в PowerShell 5.1 BOM добавляет.
    foreach ($name in @("build.ps1", "sign.ps1")) {
        $text = Get-Content -Path (Join-Path $InstallerDir $name) -Raw -Encoding UTF8
        foreach ($line in ($text -split "`r?`n")) {
            if ($line.TrimStart().StartsWith("#")) { continue }
            Assert-HrmFalse ($line -match "Set-Content.*-Encoding\s+UTF8") ("BOM-запись JSON в {0}: {1}" -f $name, $line.Trim())
        }
        Assert-HrmContains $text "New-Object System.Text.UTF8Encoding(`$false)" "нет записи UTF-8 без BOM"
    }
}

Test-Case "production pre-flight PEM выполняется до signtool sign" {
    # Битый/пустой/отсутствующий PEM обязан отказаться до подписи. Здесь только
    # структурный контракт: исполняемый pre-flight на Windows — sign.ps1 в CI,
    # поведенческие случаи PEM закрыты pytest без production PFX.
    $sign = Get-Content -Path (Join-Path $InstallerDir "sign.ps1") -Raw -Encoding UTF8
    Assert-HrmContains $sign "function Assert-HrmPinnedRootsPem" "нет функции pre-flight"
    Assert-HrmContains $sign "Assert-HrmPinnedRootsPem -Mode `$Mode" "pre-flight не вызывается"
    Assert-HrmContains $sign "production pre-flight: PEM не найден" "нет отказа на отсутствующий PEM"
    Assert-HrmContains $sign "production pre-flight: PEM пуст" "нет отказа на пустой PEM"
    Assert-HrmContains $sign "production pre-flight: невалидный PEM" "нет отказа на невалидный PEM"
    Assert-HrmContains $sign "X509Certificate2" "PEM не разбирается как сертификат"
    Assert-HrmContains $sign 'if ($Mode -ne "production") { return }' "test-режим требует operator PEM"
    $call = $sign.IndexOf("Assert-HrmPinnedRootsPem -Mode")
    $signed = $sign.IndexOf('Label "signtool sign"')
    Assert-HrmTrue ($call -ge 0 -and $signed -gt $call) "pre-flight не раньше signtool sign"
}

Write-Host ("Статические проверки: {0} пройдено, {1} провалено" -f $global:HRM_TestPassed, $global:HRM_TestFailed)
