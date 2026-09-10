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

Test-Case "backend видит состояние бэкапов только для чтения" {
    $overlay = Get-Content -Path (Join-Path $RepoRoot "infra\compose.pilot.yml") -Raw -Encoding UTF8
    Assert-HrmContains $overlay "BACKUP_STATE_FILE: /var/backups/hr-manager/state.json" "backend не настроен на состояние бэкапов"
    Assert-HrmContains $overlay "pilot_backups:/var/backups/hr-manager:ro" "backend не подключает backup volume только для чтения"
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

Write-Host ("Статические проверки: {0} пройдено, {1} провалено" -f $global:HRM_TestPassed, $global:HRM_TestFailed)
