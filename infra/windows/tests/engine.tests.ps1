# Поведенческие тесты движка с моком Invoke-HrmExternal/Invoke-HrmHttp:
# реальная машина (Docker, реестр, сеть, браузер) НЕ затрагивается.
# Секреты, установка/обновление/удаление/диагностика/первый запуск.

param([string]$HarnessPath = $PSScriptRoot)

. (Join-Path $HarnessPath "test-harness.ps1")

function New-HrmTestWorld {
    param([string]$TestRoot = "", [string]$ReleaseSha = "snapshot-sha-0013", [switch]$SkipSnapshot)
    if (-not $TestRoot) {
        $TestRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM world " + [System.Guid]::NewGuid().ToString("N").Substring(0, 8))
    }
    $sourceDir = Join-Path $TestRoot "источник с пробелами"
    if (-not $SkipSnapshot) {
        New-HrmFakeSnapshot -Root $sourceDir -ReleaseSha $ReleaseSha
    }
    else {
        New-Item -ItemType Directory -Path $sourceDir -Force | Out-Null
    }
    $world = New-HrmMockWorld -ReleaseSha $ReleaseSha
    Set-HrmPreflightOverride @{
        windows = $true; powershell = $true; docker = $true; daemon = $true;
        compose = "v2.29.7 (mock)"; port = $true; state_dir = $true; space = $true; config = $true
    }
    return [pscustomobject]@{ Root = $TestRoot; Source = $sourceDir; World = $world }
}

function Get-HrmSecretValues {
    # Все непустые значения secrets.json (для проверки отсутствия в командной строке).
    param([string]$StateDir)
    $file = Get-HrmSecretsFile $StateDir
    $values = @()
    if (Test-Path $file) {
        $data = Get-HrmJsonFile $file
        foreach ($prop in $data.PSObject.Properties) {
            if ($prop.Value) { $values += [string]$prop.Value }
        }
    }
    return $values
}

function Assert-HrmNoSecretInArgs {
    param($World, [string]$StateDir)
    foreach ($secret in (Get-HrmSecretValues $StateDir)) {
        foreach ($call in $World.Calls) {
            foreach ($arg in $call.Args) {
                Assert-HrmNotContains ([string]$arg) $secret ("СЕКРЕТ В КОМАНДНОЙ СТРОКЕ: '$secret' в аргументе '$arg'")
            }
        }
    }
}

Write-Host "== Секреты =="

Test-Case "ACL использует наследуемые права только для каталога, а прямые — для файла" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    $state = Get-HrmTestStateDir
    New-Item -ItemType Directory -Path $state -Force | Out-Null
    $file = Join-Path $state "protected.json"
    Set-Content -Path $file -Value "{}" -Encoding UTF8

    Protect-HrmFile $state $state
    Protect-HrmFile $state $file

    $directoryCall = @($world.IcaclsArgs | Where-Object { $_[0] -eq $state } | Select-Object -Last 1)
    $fileCall = @($world.IcaclsArgs | Where-Object { $_[0] -eq $file } | Select-Object -Last 1)
    Assert-HrmEqual 1 $directoryCall.Count "нет вызова ACL для каталога"
    Assert-HrmEqual 1 $fileCall.Count "нет вызова ACL для файла"
    Assert-HrmTrue ($directoryCall[0][3] -match ':\(OI\)\(CI\)F$') "каталог не выдаёт наследуемые права"
    Assert-HrmTrue ($fileCall[0][3] -match ':F$') "файл не выдаёт прямые права"
    Assert-HrmNotContains $fileCall[0][3] "(OI)" "файлу ошибочно выданы только наследуемые права"
}

Test-Case "секреты уникальны между установками и неизменны при повторах" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    $suffix = [System.Guid]::NewGuid().ToString("N")
    $a = Join-Path ([System.IO.Path]::GetTempPath()) ("hrm-sec-a-" + $suffix)
    $b = Join-Path ([System.IO.Path]::GetTempPath()) ("hrm-sec-b-" + $suffix)
    Initialize-HrmStateDir $a | Out-Null
    Initialize-HrmStateDir $b | Out-Null
    $pa = Get-HrmSecret $a "HRM_POSTGRES_PASSWORD"
    $pb = Get-HrmSecret $b "HRM_POSTGRES_PASSWORD"
    Assert-HrmFalse ($pa -eq $pb) "пароли БД разных установок совпали"
    $pa2 = Get-HrmSecret $a "HRM_POSTGRES_PASSWORD"
    Assert-HrmEqual $pa $pa2 "секрет перегенерирован при повторном чтении"
    # Все семь секретов существуют.
    foreach ($name in @("HRM_POSTGRES_PASSWORD", "HRM_SIGNING_KEY", "HRM_BOOTSTRAP_ADMIN_PASSWORD", "HRM_BACKUP_KEY", "HRM_BACKUP_KEY_ID", "HRM_EXCHANGE_TOKEN", "HRM_UPDATE_ENGINE_TOKEN")) {
        $value = Get-HrmSecret $a $name
        Assert-HrmTrue (-not [string]::IsNullOrEmpty($value)) "секрет $name пуст"
    }
    $keyId = Get-HrmSecret $a "HRM_BACKUP_KEY_ID"
    Assert-HrmTrue ($keyId -match "^pilot-[0-9a-f]{8}$") "неверный формат HRM_BACKUP_KEY_ID: $keyId"
    Assert-HrmEqual 64 (Get-HrmSecret $a "HRM_SIGNING_KEY").Length "длина ключа подписи"
    $backupKey = Get-HrmSecret $a "HRM_BACKUP_KEY"
    Assert-HrmEqual 44 $backupKey.Length "длина base64 ключа бэкапов"
    Assert-HrmEqual 32 ([Convert]::FromBase64String($backupKey)).Length "ключ бэкапов должен декодироваться в 32 байта"
    Assert-HrmEqual 32 (Get-HrmSecret $a "HRM_POSTGRES_PASSWORD").Length "длина пароля БД"
    Assert-HrmEqual 32 (Get-HrmSecret $a "HRM_BOOTSTRAP_ADMIN_PASSWORD").Length "длина bootstrap-пароля"
    Assert-HrmEqual 32 (Get-HrmSecret $a "HRM_EXCHANGE_TOKEN").Length "длина токена обмена"
}

Test-Case "pilot.env: токен обмена есть до создания владельца и retired-заглушка после" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    $state = Get-HrmTestStateDir
    Initialize-HrmStateDir $state | Out-Null
    $null = Write-HrmPilotEnv $state "snapshot-sha-0013" 8080
    $env = Get-Content (Get-HrmEnvFile $state) -Raw
    Assert-HrmContains $env "HRM_BOOTSTRAP_ADMIN_PASSWORD=" "нет bootstrap-пароля в pilot.env"
    Assert-HrmContains $env "HRM_EXCHANGE_TOKEN=" "нет токена обмена в pilot.env"
    Assert-HrmContains $env "HRM_RELEASE_SHA=snapshot-sha-0013" "нет release sha"
    Assert-HrmContains $env "HRM_PILOT_PORT=8080" "нет порта"
    Assert-HrmContains $env "HRM_UPDATE_ENGINE_TOKEN=" "нет токена движка канала в pilot.env"
    Assert-HrmContains $env "HRM_STAGING_DIR=" "нет staging-каталога в pilot.env"
    Assert-HrmContains $env "HRM_UPDATE_CHANNEL_URL=" "нет URL канала в pilot.env"
    Assert-HrmFalse ([regex]::IsMatch($env, '(?m)^BOOTSTRAP_ADMIN_PASSWORD=')) "имя переменной в pilot.env не совпадает с оверлеем"
    Set-HrmInstallRecord $state @{ pilot_created = $true; release_sha = "snapshot-sha-0013"; port = 8080 }
    $null = Write-HrmPilotEnv $state "snapshot-sha-0013" 8080
    $env2 = Get-Content (Get-HrmEnvFile $state) -Raw
    Assert-HrmContains $env2 "HRM_EXCHANGE_TOKEN=retired-" "после создания владельца нет retired-заглушки"
    $realToken = (($env -split "`n" | Where-Object { $_ -like "HRM_EXCHANGE_TOKEN=*" }) -replace "HRM_EXCHANGE_TOKEN=", "").Trim()
    Assert-HrmNotContains $env2 $realToken "старый токен остался в pilot.env после создания владельца"
}

Test-Case "редакция секретов: логи и вывод никогда не содержат зарегистрированных значений" {
    Initialize-HrmTestEngine
    Register-HrmSecret "topsecret-12345678"
    $redacted = Redact-HrmText "текст topsecret-12345678 конец"
    Assert-HrmNotContains $redacted "topsecret-12345678" "секрет не отредактирован"
    Assert-HrmContains $redacted "<redacted>" "нет маркера редакции"
    $log = (Write-HrmLog "info" "пароль topsecret-12345678 тут") | Out-String
    Assert-HrmNotContains $log "topsecret-12345678" "секрет попал в журнал"
    Assert-HrmContains $log "<redacted>" "журнал без маркера редакции"
    Reset-HrmRedaction
}

Write-Host "== Предполётная проверка =="

Test-Case "префлайт честно перечисляет провалы (переопределения)" {
    Initialize-HrmTestEngine
    New-HrmMockWorld | Out-Null
    Set-HrmPreflightOverride @{ windows = $true; powershell = $true; docker = $false; daemon = $false; compose = "v2.20.0"; port = $false; state_dir = $true; space = $false; config = $true }
    $threw = $false
    try {
        Assert-HrmPreflight -InstallDir (Get-HrmTestInstallDir) -StateDir (Get-HrmTestStateDir) -Port 8080 | Out-Null
    }
    catch {
        $threw = $true
        Assert-HrmContains $_.Exception.Message "docker" "нет docker в списке провалов"
        Assert-HrmContains $_.Exception.Message "daemon" "нет daemon в списке провалов"
        Assert-HrmContains $_.Exception.Message "port" "нет port в списке провалов"
        Assert-HrmContains $_.Exception.Message "space" "нет space в списке провалов"
    }
    Assert-HrmTrue $threw "префлайт не упал при провалах"
    Clear-HrmPreflightOverride
}

Test-Case "префлайт реально падает при отсутствии Docker CLI (через мок)" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    $world.DockerMissing = $true
    Clear-HrmPreflightOverride
    $threw = $false
    try {
        Assert-HrmPreflight -InstallDir (Get-HrmTestInstallDir) -StateDir (Get-HrmTestStateDir) -Port 8080 -SkipCompose | Out-Null
    }
    catch {
        $threw = $true
        Assert-HrmContains $_.Exception.Message "docker" "нет docker в списке провалов"
    }
    Assert-HrmTrue $threw "префлайт не заметил отсутствие Docker"
}

Write-Host "== Установка и первый запуск =="

Test-Case "установка: секреты, env-файл, claim по loopback, ссылка первого запуска" {
    Initialize-HrmTestEngine
    $t = New-HrmTestWorld
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    # Защищённый файл ввода первого запуска (как от мастера установки).
    $input = [ordered]@{ surname = "Иванова"; working_mode = "hr"; timezone = "Europe/Moscow" }
    Set-HrmJsonFile $state "first-run-input.json" $input

    Install-HrmApp -SourceDir $t.Source -InstallDir $install -StateDir $state -Port 8080 | Out-Null

    # Файлы состояния.
    Assert-HrmTrue (Test-Path (Get-HrmSecretsFile $state)) "нет secrets.json"
    Assert-HrmTrue (Test-Path (Get-HrmEnvFile $state)) "нет pilot.env"
    Assert-HrmTrue (Test-Path (Get-HrmInstalledFile $state)) "нет installed.json"
    Assert-HrmTrue (Test-Path (Get-HrmSetupUrlFile $state)) "нет first-run-url.txt"
    $record = Get-HrmInstallRecord $state
    Assert-HrmEqual "snapshot-sha-0013" $record.release_sha "release sha в записи установки"
    Assert-HrmTrue (Test-Path (Join-Path $install "backend\marker.txt")) "снимок не скопирован"

    # Ссылка первого запуска (неинтерактивно — в файл, браузер не открывался).
    $url = (Get-Content (Get-HrmSetupUrlFile $state) -Raw).Trim()
    Assert-HrmContains $url "http://127.0.0.1:8080/#setup=ticket-test-123" "неверная ссылка первого запуска"

    # Claim был по loopback с данными из файла ввода и токеном из env-файла.
    $claim = $t.World.HttpCalls | Where-Object { $_.Uri -like "*/api/setup/owner/claim" } | Select-Object -First 1
    Assert-HrmTrue ($null -ne $claim) "claim не выполнялся"
    Assert-HrmEqual "127.0.0.1" $claim.Headers["X-Real-IP"] "claim не помечен loopback"
    Assert-HrmEqual "Иванова" $claim.Body.surname "фамилия не из файла ввода"
    Assert-HrmEqual "hr" $claim.Body.working_mode "режим не из файла ввода"
    $envToken = (((Get-Content (Get-HrmEnvFile $state) -Raw) -split "`n" | Where-Object { $_ -like "HRM_EXCHANGE_TOKEN=*" }) -replace "HRM_EXCHANGE_TOKEN=", "").Trim()
    Assert-HrmEqual $envToken $claim.Body.exchange_token "токен обмена в claim не совпадает с env-файлом"

    # Compose: стабильное имя проекта + env-файл состояния.
    $up = $t.World.Calls | Where-Object { $_.Args -contains "up" } | Select-Object -First 1
    Assert-HrmTrue ($null -ne $up) "compose up не вызывался"
    Assert-HrmTrue (($up.Args -contains "hr-manager-pilot")) "нет --project-name hr-manager-pilot"
    Assert-HrmTrue (($up.Args -contains (Get-HrmEnvFile $state))) "нет --env-file <state>\pilot.env"

    # ACL на каталоге состояния и секретах.
    $protected = @($t.World.IcaclsArgs | ForEach-Object { $_[0] })
    Assert-HrmTrue (($protected -contains $state)) "нет icacls на каталог состояния"
    Assert-HrmTrue (($protected -contains (Get-HrmSecretsFile $state))) "нет icacls на secrets.json"

    # СЕКРЕТЫ НЕ В КОМАНДНОЙ СТРОКЕ НИ ОДНОГО ПРОЦЕССА.
    Assert-HrmNoSecretInArgs $t.World $state
}

Test-Case "повторный запуск: существующая установка не пересоздаётся, владелец фиксируется" {
    Initialize-HrmTestEngine
    $t = New-HrmTestWorld
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Install-HrmApp -SourceDir $t.Source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    # Меняем снимок-источник; повторный запуск НЕ должен его копировать.
    Set-Content -Path (Join-Path $t.Source "backend\marker.txt") -Value "v2"
    $t.World.ClaimResponses = @(@{ StatusCode = 409; Body = "" })
    Install-HrmApp -SourceDir $t.Source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    $marker = (Get-Content (Join-Path $install "backend\marker.txt") -Raw).Trim()
    Assert-HrmEqual "v1" $marker "повторный запуск перезаписал установленный снимок"
    $record = Get-HrmInstallRecord $state
    Assert-HrmTrue $record.pilot_created "владелец не зафиксирован после 409"
    $secrets = Get-HrmJsonFile (Get-HrmSecretsFile $state)
    Assert-HrmTrue ([string]::IsNullOrEmpty([string]$secrets.HRM_EXCHANGE_TOKEN)) "сырой токен обмена не удалён после 409"
    Assert-HrmFalse (Test-Path (Get-HrmInputFile $state)) "файл ввода первого запуска не удалён"
    Assert-HrmFalse (Test-Path (Get-HrmSetupUrlFile $state)) "URL первого запуска не удалён"
    $env = Get-Content (Get-HrmEnvFile $state) -Raw
    Assert-HrmContains $env "HRM_EXCHANGE_TOKEN=retired-" "токен обмена не заменён заглушкой после 409"
}

Test-Case "claim повторяется при недоступности обмена (403), затем тикет выдаётся" {
    Initialize-HrmTestEngine
    $t = New-HrmTestWorld
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    $t.World.ClaimResponses = @(
        @{ StatusCode = 403; Body = "" },
        @{ StatusCode = 403; Body = "" },
        @{ StatusCode = 201; Body = [pscustomobject]@{ ticket = "ticket-after-retry" } }
    )
    Install-HrmApp -SourceDir $t.Source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    Assert-HrmEqual 3 $t.World.ClaimCount "количество попыток claim"
    $url = (Get-Content (Get-HrmSetupUrlFile $state) -Raw).Trim()
    Assert-HrmContains $url "#setup=ticket-after-retry" "тикет после повторов не попал в ссылку"
}

Write-Host "== Обновление =="

Test-Case "обновление: полный путь prepare→done, журнал очищается, версия фиксируется" {
    Initialize-HrmTestEngine
    $t = New-HrmTestWorld
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Install-HrmApp -SourceDir $t.Source -InstallDir $install -StateDir $state -Port 8080 | Out-Null

    $releaseDir = Join-Path $t.Root "релиз 1.1.0"
    New-HrmFakeSnapshot -Root $releaseDir -ReleaseSha "snapshot-sha-0014"

    Update-HrmApp -ReleaseDir $releaseDir -InstallDir $install -StateDir $state | Out-Null

    Assert-HrmEqual 1 $t.World.BackupNowCount "бэкап перед миграцией не создан"
    Assert-HrmEqual 1 $t.World.AlembicUpgradeCount "alembic upgrade не выполнялся ровно один раз"
    Assert-HrmTrue ($t.World.BuildCount -ge 1) "новые образы не собирались"
    Assert-HrmEqual 3 $t.World.TagCount "предыдущие образы не закреплены до сборки"
    $record = Get-HrmInstallRecord $state
    Assert-HrmEqual "snapshot-sha-0014" $record.release_sha "новая версия не зафиксирована"
    Assert-HrmFalse (Test-Path (Get-HrmUpdateJournal $state)) "журнал обновления не очищен"
    Assert-HrmFalse (Test-Path (Get-HrmUpdateLock $state)) "блокировка обновления не снята"
    Assert-HrmNoSecretInArgs $t.World $state
}

Test-Case "обновление: свежая блокировка не пускает параллельное обновление" {
    Initialize-HrmTestEngine
    $t = New-HrmTestWorld
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Install-HrmApp -SourceDir $t.Source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    $releaseDir = Join-Path $t.Root "релиз 2"
    New-HrmFakeSnapshot -Root $releaseDir -ReleaseSha "snapshot-sha-0015"
    Set-Content -Path (Get-HrmUpdateLock $state) -Value "locked" -Encoding UTF8
    Assert-HrmThrows "свежая блокировка не остановила обновление" {
        Update-HrmApp -ReleaseDir $releaseDir -InstallDir $install -StateDir $state
    }
    Assert-HrmEqual 0 $t.World.BackupNowCount "бэкап не должен был создаваться при блокировке"
}

Test-Case "обновление: старая (прерванная) блокировка разрешает перехват" {
    Initialize-HrmTestEngine
    $t = New-HrmTestWorld
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Install-HrmApp -SourceDir $t.Source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    $releaseDir = Join-Path $t.Root "релиз 3"
    New-HrmFakeSnapshot -Root $releaseDir -ReleaseSha "snapshot-sha-0016"
    $lock = Get-HrmUpdateLock $state
    Set-Content -Path $lock -Value "locked" -Encoding UTF8
    (Get-Item $lock).LastWriteTime = (Get-Date).AddHours(-5)
    Update-HrmApp -ReleaseDir $releaseDir -InstallDir $install -StateDir $state | Out-Null
    Assert-HrmEqual "snapshot-sha-0016" (Get-HrmInstallRecord $state).release_sha "обновление после прерванной блокировки не прошло"
}

Test-Case "обновление: невалидный бэкап останавливает обновление и возвращает прежние образы" {
    Initialize-HrmTestEngine
    $t = New-HrmTestWorld
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Install-HrmApp -SourceDir $t.Source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    $releaseDir = Join-Path $t.Root "релиз 4"
    New-HrmFakeSnapshot -Root $releaseDir -ReleaseSha "snapshot-sha-0017"
    $t.World.BackupCheckOk = $false
    Assert-HrmThrows "невалидный бэкап не остановил обновление" {
        Update-HrmApp -ReleaseDir $releaseDir -InstallDir $install -StateDir $state
    }
    Assert-HrmEqual 6 $t.World.TagCount "предыдущие образы не закреплены и не восстановлены"
    Assert-HrmTrue (Test-Path (Get-HrmUpdateJournal $state)) "журнал прерванного обновления не сохранён"
    Assert-HrmEqual "snapshot-sha-0013" (Get-HrmInstallRecord $state).release_sha "версия изменилась при провале"
    Assert-HrmEqual 0 $t.World.AlembicUpgradeCount "миграция не должна была выполняться"
}

Test-Case "обновление: возобновление с фазы migrate не повторяет бэкап" {
    Initialize-HrmTestEngine
    $t = New-HrmTestWorld
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Install-HrmApp -SourceDir $t.Source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    $releaseDir = Join-Path $t.Root "релиз 5"
    New-HrmFakeSnapshot -Root $releaseDir -ReleaseSha "snapshot-sha-0018"
    # Журнал прерванного обновления (фаза migrate, предыдущие образы сохранены).
    $previous = [ordered]@{ "hr-manager-pilot-backend:pilot" = "sha256:old-backend"; "hr-manager-pilot-frontend:pilot" = "sha256:old-frontend"; "hr-manager-pilot-backup:pilot" = "sha256:old-backup" }
    Set-HrmJsonFile $state "update-journal.json" ([ordered]@{ phase = "migrate"; release_dir = $releaseDir; release_sha = "snapshot-sha-0018"; previous_ids = $previous })
    Update-HrmApp -ReleaseDir $releaseDir -InstallDir $install -StateDir $state | Out-Null
    Assert-HrmEqual 0 $t.World.BackupNowCount "бэкап не должен повторяться при возобновлении"
    Assert-HrmEqual 1 $t.World.AlembicUpgradeCount "миграция при возобновлении не выполнена"
    Assert-HrmEqual "snapshot-sha-0018" (Get-HrmInstallRecord $state).release_sha "версия не зафиксирована при возобновлении"
    Assert-HrmFalse (Test-Path (Get-HrmUpdateJournal $state)) "журнал не очищен после возобновления"
}

Test-Case "обновление: каталог релиза без release.json отвергается" {
    Initialize-HrmTestEngine
    $t = New-HrmTestWorld
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Install-HrmApp -SourceDir $t.Source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    $bad = Join-Path $t.Root "плохой релиз"
    New-HrmFakeSnapshot -Root $bad -ReleaseSha "x"
    Remove-Item (Join-Path $bad "release.json") -Force
    Assert-HrmThrows "релиз без release.json принят" {
        Update-HrmApp -ReleaseDir $bad -InstallDir $install -StateDir $state
    }
}

Test-Case "обновление: smoke несовпадения версии → откат к прежним образам" {
    Initialize-HrmTestEngine
    $t = New-HrmTestWorld
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Install-HrmApp -SourceDir $t.Source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    $releaseDir = Join-Path $t.Root "релиз 6"
    New-HrmFakeSnapshot -Root $releaseDir -ReleaseSha "snapshot-sha-0019"
    # Работающий бэкенд отдаёт старую версию — дрейф.
    $t.World.SimulateStaleRelease = $true
    $t.World.OpsBody.release_sha = "snapshot-sha-0013"
    Assert-HrmThrows "дрейф версии не остановил обновление" {
        Update-HrmApp -ReleaseDir $releaseDir -InstallDir $install -StateDir $state
    }
    Assert-HrmEqual 6 $t.World.TagCount "закрепление и откат образов не выполнены"
    Assert-HrmEqual "snapshot-sha-0013" (Get-HrmInstallRecord $state).release_sha "версия изменилась при провале smoke"
}

Test-Case "обновление: дрейф миграций в smoke → откат" {
    Initialize-HrmTestEngine
    $t = New-HrmTestWorld
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Install-HrmApp -SourceDir $t.Source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    $releaseDir = Join-Path $t.Root "релиз 7"
    New-HrmFakeSnapshot -Root $releaseDir -ReleaseSha "snapshot-sha-0020"
    $t.World.AlembicCurrent = "0012 (head)"
    Assert-HrmThrows "дрейф миграций не остановил обновление" {
        Update-HrmApp -ReleaseDir $releaseDir -InstallDir $install -StateDir $state
    }
    Assert-HrmEqual 6 $t.World.TagCount "закрепление и откат образов не выполнены при дрейфе миграций"
}

Test-Case "обновление: worker-check не проходит → откат" {
    Initialize-HrmTestEngine
    $t = New-HrmTestWorld
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Install-HrmApp -SourceDir $t.Source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    $releaseDir = Join-Path $t.Root "релиз 8"
    New-HrmFakeSnapshot -Root $releaseDir -ReleaseSha "snapshot-sha-0021"
    $t.World.WorkerCheckOk = $false
    Assert-HrmThrows "worker-check не остановил обновление" {
        Update-HrmApp -ReleaseDir $releaseDir -InstallDir $install -StateDir $state
    }
    Assert-HrmEqual 6 $t.World.TagCount "закрепление и откат образов не выполнены при провале worker-check"
}

Write-Host "== Удаление =="

Test-Case "удаление по умолчанию сохраняет данные и тома (нет down -v)" {
    Initialize-HrmTestEngine
    $t = New-HrmTestWorld
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Install-HrmApp -SourceDir $t.Source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    Remove-HrmApp -InstallDir $install -StateDir $state -PurgeData:$false | Out-Null
    $withV = @($t.World.Calls | Where-Object { $_.Args -contains "down" -and $_.Args -contains "-v" })
    Assert-HrmEqual 0 $withV.Count "тома данных удалены без подтверждения"
    Assert-HrmEqual 0 $t.World.BackupNowCount "бэкап при обычном удалении не нужен"
}

Test-Case "удаление данных: неверная фраза не удаляет, верная — удаляет с бэкапом" {
    Initialize-HrmTestEngine
    $t = New-HrmTestWorld
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Install-HrmApp -SourceDir $t.Source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    # Неверная фраза.
    $env:HRM_PURGE_CONFIRMATION = "нет"
    Assert-HrmThrows "неверная фраза подтверждения не остановила удаление" {
        Remove-HrmApp -InstallDir $install -StateDir $state -PurgeData:$true
    }
    $withV = @($t.World.Calls | Where-Object { $_.Args -contains "down" -and $_.Args -contains "-v" })
    Assert-HrmEqual 0 $withV.Count "данные удалены при неверной фразе"
    # Верная фраза: бэкап + проверка + удаление томов.
    $env:HRM_PURGE_CONFIRMATION = "УДАЛИТЬ ДАННЫЕ HR MANAGER"
    Remove-HrmApp -InstallDir $install -StateDir $state -PurgeData:$true | Out-Null
    Assert-HrmEqual 1 $t.World.BackupNowCount "бэкап перед удалением не создан"
    $withV = @($t.World.Calls | Where-Object { $_.Args -contains "down" -and $_.Args -contains "-v" })
    Assert-HrmEqual 1 $withV.Count "тома данных не удалены после подтверждения"
    Remove-Item Env:HRM_PURGE_CONFIRMATION -ErrorAction SilentlyContinue
}

Write-Host "== Статус и диагностика =="

Test-Case "статус работающей установки" {
    Initialize-HrmTestEngine
    $t = New-HrmTestWorld
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Install-HrmApp -SourceDir $t.Source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    $out = (Get-HrmAppStatus -InstallDir $install -StateDir $state) | Out-String
    Assert-HrmContains $out "запущены" "статус не сообщает о запущенных контейнерах"
    Assert-HrmContains $out "готов" "статус не сообщает о готовности"
    Assert-HrmContains $out "snapshot-sha-0013" "статус не сообщает версию"
}

Test-Case "диагностика: состояния docker/app/db/migration/worker/backup/version" {
    Initialize-HrmTestEngine
    $t = New-HrmTestWorld
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Install-HrmApp -SourceDir $t.Source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    $json = (Get-HrmDiagnostics -InstallDir $install -StateDir $state -AsJson) | Out-String
    $d = $json | ConvertFrom-Json
    Assert-HrmEqual "ok" $d.docker "docker"
    Assert-HrmEqual "ready" $d.app "app"
    Assert-HrmEqual "ok" $d.database "database"
    Assert-HrmEqual "ok" $d.migration "migration"
    Assert-HrmEqual "ok" $d.worker "worker"
    Assert-HrmEqual "ok" $d.backup "backup"
    Assert-HrmEqual "match" $d.version "version"
    Assert-HrmEqual "not_configured" $d.smtp "smtp"
    Assert-HrmEqual "not_configured" $d.telegram "telegram"
    Assert-HrmEqual "0013" $d.migration_current "migration current"
    Assert-HrmEqual "0013" $d.migration_expected "migration expected"
}

Test-Case "диагностика: Docker отсутствует → docker=missing, app=stopped" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    $world.DockerMissing = $true
    Set-HrmPreflightOverride @{ windows = $true; powershell = $true; docker = $true; daemon = $true; compose = "v2.29.7"; port = $true; state_dir = $true; space = $true }
    $state = Get-HrmTestStateDir
    Set-HrmInstallRecord $state @{ release_sha = "snapshot-sha-0013"; port = 8080 }
    $json = (Get-HrmDiagnostics -InstallDir (Get-HrmTestInstallDir) -StateDir $state -AsJson) | Out-String
    $d = $json | ConvertFrom-Json
    Assert-HrmEqual "missing" $d.docker "docker должен быть missing"
    Assert-HrmEqual "stopped" $d.app "app должен быть stopped"
    Assert-HrmEqual "unknown" $d.database "database должен быть unknown"
}

Test-Case "диагностика: БД недоступна → db=down, app=degraded" {
    Initialize-HrmTestEngine
    $t = New-HrmTestWorld
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Install-HrmApp -SourceDir $t.Source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    $t.World.OpsStatusCode = 503
    $t.World.OpsBody.database.status = "error"
    $t.World.BackendStatus = 503
    $json = (Get-HrmDiagnostics -InstallDir $install -StateDir $state -AsJson) | Out-String
    $d = $json | ConvertFrom-Json
    Assert-HrmEqual "down" $d.database "db должен быть down"
    Assert-HrmEqual "degraded" $d.app "app должен быть degraded"
}

Test-Case "диагностика: worker отстал → stale; бэкап провален → failed" {
    Initialize-HrmTestEngine
    $t = New-HrmTestWorld
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Install-HrmApp -SourceDir $t.Source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    $t.World.OpsBody.notifications.worker.alive = $false
    $t.World.BackupHealthStatusCode = 503
    $t.World.BackupHealthBody.fresh = $false
    $t.World.OpsBody.backup.ok = $false
    $t.World.OpsBody.backup.available = $true
    $json = (Get-HrmDiagnostics -InstallDir $install -StateDir $state -AsJson) | Out-String
    $d = $json | ConvertFrom-Json
    Assert-HrmEqual "stale" $d.worker "worker должен быть stale"
    Assert-HrmEqual "failed" $d.backup "backup должен быть failed"
}

Test-Case "диагностика: бэкап отсутствует → missing; версии разошлись → mismatch" {
    Initialize-HrmTestEngine
    $t = New-HrmTestWorld
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Install-HrmApp -SourceDir $t.Source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    $t.World.BackupHealthStatusCode = 503
    $t.World.BackupHealthBody.fresh = $false
    $t.World.OpsBody.backup.ok = $null
    $t.World.OpsBody.backup.available = $false
    $t.World.OpsBody.release_sha = "other-sha-9999"
    $json = (Get-HrmDiagnostics -InstallDir $install -StateDir $state -AsJson) | Out-String
    $d = $json | ConvertFrom-Json
    Assert-HrmEqual "missing" $d.backup "backup должен быть missing"
    Assert-HrmEqual "mismatch" $d.version "version должен быть mismatch"
}

Test-Case "диагностика: редакция секретов в выводе (регрессия)" {
    Initialize-HrmTestEngine
    $t = New-HrmTestWorld
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Install-HrmApp -SourceDir $t.Source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    $secret = "supersecret-abc123"
    $t.World.OpsBody.release_sha = $secret
    Register-HrmSecret $secret
    $json = (Get-HrmDiagnostics -InstallDir $install -StateDir $state -AsJson) | Out-String
    Assert-HrmNotContains $json $secret "секрет попал в вывод диагностики"
    Assert-HrmContains $json "<redacted>" "нет маркера редакции в диагностике"
}

Write-Host "== Возобновление =="

Test-Case "resume: продолжает прерванное обновление из журнала" {
    Initialize-HrmTestEngine
    $t = New-HrmTestWorld
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Install-HrmApp -SourceDir $t.Source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    $releaseDir = Join-Path $t.Root "релиз 9"
    New-HrmFakeSnapshot -Root $releaseDir -ReleaseSha "snapshot-sha-0022"
    $previous = [ordered]@{ "hr-manager-pilot-backend:pilot" = "sha256:old-backend" }
    Set-HrmJsonFile $state "update-journal.json" ([ordered]@{ phase = "backup"; release_dir = $releaseDir; release_sha = "snapshot-sha-0022"; previous_ids = $previous })
    Resume-HrmOperation -InstallDir $install -StateDir $state | Out-Null
    Assert-HrmEqual 1 $t.World.BackupNowCount "резюме не продолжило с фазы backup"
    Assert-HrmEqual "snapshot-sha-0022" (Get-HrmInstallRecord $state).release_sha "резюме не завершило обновление"
    Assert-HrmFalse (Test-Path (Get-HrmUpdateJournal $state)) "журнал не очищен после резюме"
}

Test-Case "resume: без журнала просто поднимает приложение" {
    Initialize-HrmTestEngine
    $t = New-HrmTestWorld
    $state = Get-HrmTestStateDir
    $install = Get-HrmTestInstallDir
    Install-HrmApp -SourceDir $t.Source -InstallDir $install -StateDir $state -Port 8080 | Out-Null
    Stop-HrmApp -InstallDir $install -StateDir $state | Out-Null
    Assert-HrmFalse $t.World.Running "стек не остановлен"
    Resume-HrmOperation -InstallDir $install -StateDir $state | Out-Null
    Assert-HrmTrue $t.World.Running "резюме не подняло приложение"
}

Write-Host ("Тесты движка: {0} пройдено, {1} провалено" -f $global:HRM_TestPassed, $global:HRM_TestFailed)
