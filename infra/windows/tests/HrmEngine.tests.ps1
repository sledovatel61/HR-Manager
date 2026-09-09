# ============================================================================
# Тесты движка (без реального docker/Windows): mockable command runner,
# идемпотентность install, порядок операций update, откат кода, безопасный
# uninstall, ACL-файлы, санитизация диагностики, preflight-ошибки.
# ============================================================================

function script:HrmTest-01-PairingCode-Alphabet {
    foreach ($i in 1..40) {
        $code = New-HrmPairingCode
        Assert-HrmEqual 6 $code.Length "длина кода"
        if ($code -notmatch "^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{6}$") {
            throw "недопустимые символы в '$code' (не должно быть 0/O/1/I/L)"
        }
    }
}

function script:HrmTest-02-RandomHex-Strength {
    $a = New-HrmRandomHex 32
    $b = New-HrmRandomHex 32
    Assert-HrmEqual 64 $a.Length "64 hex = 32 байта"
    Assert-HrmTrue ($a -ne $b) "значения уникальны"
    $key = New-HrmBackupKeyBase64
    $bytes = [Convert]::FromBase64String($key)
    Assert-HrmEqual 32 $bytes.Length "backup-ключ = 32 байта base64"
}

function script:HrmTest-03-Redaction {
    $line = "login password=Sup3rPass! token: abc.def secret=xyz postgres://user:pwd@db:5432/app email ivan.petrov@corp.ru тел +7 912 345-67-89"
    $safe = Protect-HrmText -Text $line -Secrets @()
    Assert-HrmNotContains $safe "Sup3rPass!" "пароль redacted"
    Assert-HrmNotContains $safe "ivan.petrov@corp.ru" "email redacted"
    Assert-HrmNotContains $safe "345-67-89" "телефон redacted"
    Assert-HrmNotContains $safe "user:pwd@db" "connection string redacted"
    $exact = Protect-HrmText -Text "upstream key k-777 in header" -Secrets @("k-777")
    Assert-HrmNotContains $exact "k-777" "точное значение секрета заменено"
    Assert-HrmTrue (Test-HrmSecretValueAbsent -Text "ничего секретного" -Secrets @("s3cr3t"))
    Assert-HrmTrue (-not (Test-HrmSecretValueAbsent -Text "утёк s3cr3t сюда" -Secrets @("s3cr3t")))
}

function script:HrmTest-04-Surname-Validation {
    Assert-HrmEqual "" (Test-HrmSurname -Surname "Иванов") "кириллица ок"
    Assert-HrmEqual "" (Test-HrmSurname -Surname "O'Brien-Smith") "апостроф/дефис ок"
    Assert-HrmTrue ((Test-HrmSurname -Surname "И").Length -gt 0) "одно слово мало"
    Assert-HrmTrue ((Test-HrmSurname -Surname "Ivan123").Length -gt 0) "цифры запрещены"
    Assert-HrmTrue ((Test-HrmSurname -Surname ("А" * 61)).Length -gt 0) "длина >60 запрещена"
    Assert-HrmEqual "" (Test-HrmSurname -Surname "  Петров-Водкин  ") "обрамление срезается"
}

function script:HrmTest-05-CommandLine-Escaping {
    $cmd = ConvertTo-HrmCommandLine -Arguments @("compose", "up", "-d", "простой путь", 'a"b')
    Assert-HrmContains $cmd "compose" "простые аргументы без кавычек"
    Assert-HrmContains $cmd '"простой путь"' "пробел -> кавычки"
    Assert-HrmContains $cmd 'a\"b' "кавычка экранирована"
}

function script:HrmTest-06-Secrets-Idempotent {
    $envT = New-HrmTestEnv
    try {
        $s1 = Ensure-HrmSecrets -StateRoot $envT.StateRoot
        $s2 = Ensure-HrmSecrets -StateRoot $envT.StateRoot
        Assert-HrmEqual $s1.secretKey $s2.secretKey "SECRET_KEY не ротируется"
        Assert-HrmEqual $s1.postgresPassword $s2.postgresPassword "пароль БД не ротируется"
        Assert-HrmEqual $s1.backupEncKey $s2.backupEncKey "ключ backup не ротируется"
        $content = [IO.File]::ReadAllText((Join-Path $envT.StateRoot "secrets.json"))
        Assert-HrmContains $content $s1.secretKey "хранение в secrets.json"
        # битое хранилище — fail closed
        Set-Content -Path (Join-Path $envT.StateRoot "secrets.json") -Value '{"secretKey":"x"}'
        $err = Assert-HrmThrows { Ensure-HrmSecrets -StateRoot $envT.StateRoot } -ExpectedExitCode 5
        Assert-HrmContains $err.Message "повреждено"
    } finally { Remove-HrmTestEnv -Root $envT.Root }
}

function script:HrmTest-07-ReleaseIntegrity {
    $root = Join-Path ([IO.Path]::GetTempPath()) ("hrmgr-int-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
    $dir = New-HrmFakeRelease -Dir $root
    try {
        Assert-HrmEqual 0 (@(Test-HrmReleaseIntegrity -ReleaseDir $dir).Count) "целый комплект проходит"
        Add-Content -Path (Join-Path $dir "README.txt") -Value "внедрённый байт"
        Assert-HrmTrue (@(Test-HrmReleaseIntegrity -ReleaseDir $dir) | Where-Object { $_ -like "*хэш не совпал*" }) "подмена файла поймана"
        Remove-Item (Join-Path $dir "infra\compose.pilot.yml") -Force
        $problems = @(Test-HrmReleaseIntegrity -ReleaseDir $dir)
        Assert-HrmTrue (@($problems | Where-Object { $_ -like "*отсутствует*" }).Count -gt 0) "пропавший файл пойман"
        # чужой манифест
        $root2 = Join-Path ([IO.Path]::GetTempPath()) ("hrmgr-int2-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
        New-HrmFakeRelease -Dir $root2 | Out-Null
        Set-Content -Path (Join-Path $root2 "release-manifest.json") -Value '{"product":"evil","version":"1","releaseSha":"s"}'
        $p2 = @(Test-HrmReleaseIntegrity -ReleaseDir $root2)
        Assert-HrmTrue (@($p2 | Where-Object { $_ -like "*чужой продукт*" }).Count -gt 0) "чужой продукт отвергнут"
    } finally {
        Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item $root2 -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function script:HrmTest-08-Preflight-Failures {
    $envT = New-HrmTestEnv
    try {
        # занятый порт
        $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 18081)
        $listener.Start()
        try {
            $p = Invoke-HrmPreflight -StateRoot $envT.StateRoot -Port 18081 -AllowNonWindows
            Assert-HrmTrue (-not $p.Ok) "порт занят -> провал"
            Assert-HrmTrue (@($p.Failed | Where-Object { $_.Code -eq "port" }).Count -gt 0) "код ошибки port"
            Assert-HrmContains $p.Failed[0].Message "занят"
        } finally { $listener.Stop() }
        # docker отсутствует
        $envT.Hooks.DockerMissing = $true
        $p = Invoke-HrmPreflight -StateRoot $envT.StateRoot -Port 18082 -AllowNonWindows
        Assert-HrmTrue (@($p.Failed | Where-Object { $_.Code -eq "docker_missing" }).Count -gt 0) "docker_missing"
        $envT.Hooks.DockerMissing = $false
        # compose слишком старый
        $envT.Hooks.ComposeVersion = "v2.10.2"
        $p = Invoke-HrmPreflight -StateRoot $envT.StateRoot -Port 18083 -AllowNonWindows
        Assert-HrmTrue (@($p.Failed | Where-Object { $_.Code -eq "compose" }).Count -gt 0) "старый compose отвергнут"
        $envT.Hooks.ComposeVersion = "v2.24.0"
        $p = Invoke-HrmPreflight -StateRoot $envT.StateRoot -Port 18084 -AllowNonWindows
        Assert-HrmTrue $p.Ok "ровно 2.24 принимается"
        # повреждённая конфигурация -> fail closed, не перезаписывать
        Set-Content -Path (Join-Path $envT.StateRoot "config.json") -Value "{битый json"
        $p = Invoke-HrmPreflight -StateRoot $envT.StateRoot -Port 18085 -AllowNonWindows
        Assert-HrmTrue (@($p.Failed | Where-Object { $_.Code -eq "config" }).Count -gt 0) "битый config.json -> провал"
    } finally { Remove-HrmTestEnv -Root $envT.Root }
}

function script:HrmTest-09-Install-Happy {
    $envT = New-HrmTestEnv
    try {
        $inputFile = Join-Path $envT.Root "installer-input.json"
        Write-HrmJson $inputFile @{ surname = "Иванов"; workRole = "hr" }
        $r = Invoke-HrmInstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot -InputFile $inputFile -AllowNonWindows
        Assert-HrmTrue $r.Ok "install ok"
        Assert-HrmEqual 6 $r.PairingCode.Length "код выдан"
        Assert-HrmTrue (-not $r.Reused) "первый запуск — не переиспользование"
        # конфигурация и файлы состояния
        $config = Get-HrmConfig $envT.StateRoot
        Assert-HrmEqual 8081 ([int]$config.port) "порт зафиксирован"
        Assert-HrmEqual "hr-manager-pilot" $config.composeProject "проект compose зафиксирован"
        Assert-HrmEqual "12.0.0" $config.releaseVersion "версия из манифеста"
        Test-Path (Join-Path $envT.StateRoot "secrets.json") | Out-Null
        Assert-HrmTrue (Test-Path (Join-Path $envT.StateRoot "secrets.json")) "секреты созданы"
        Assert-HrmTrue (Test-Path (Join-Path $envT.StateRoot "pilot.env")) "pilot.env создан"
        $envText = [IO.File]::ReadAllText((Join-Path $envT.StateRoot "pilot.env"))
        Assert-HrmContains $envText "SECRET_KEY=" "env содержит ключ (файл под ACL)"
        # фамилия и код НЕ попадают в аргументы команд
        foreach ($call in $envT.Hooks.Calls) {
            $joined = $call.Args -join " "
            Assert-HrmNotContains $joined "Иванов" "фамилия не в argv"
            Assert-HrmNotContains $joined $r.PairingCode "код не в argv"
        }
        $issueIdx = Find-HrmCall $envT.Hooks @("pilot-pairing", "issue")
        Assert-HrmTrue ($issueIdx -ge 0) "issue вызван"
        $stdinJson = $envT.Hooks.Calls[$issueIdx].StdIn | ConvertFrom-Json
        Assert-HrmEqual $r.PairingCode $stdinJson.code "код передан по STDIN"
        Assert-HrmEqual "Иванов" $stdinJson.surname "фамилия передана по STDIN"
        Assert-HrmEqual "hr" $stdinJson.work_role "реальная роль передана"
        # логи без секретов и PII
        $logRaw = ""
        if (Test-Path (Join-Path $envT.StateRoot "logs/hr-manager.log")) {
            $logRaw = Get-Content (Join-Path $envT.StateRoot "logs/hr-manager.log") -Raw
        }
        Assert-HrmNotContains $logRaw "Иванов" "нет фамилии в логах"
        $secrets = Read-HrmJson (Join-Path $envT.StateRoot "secrets.json")
        Assert-HrmNotContains $logRaw $secrets.secretKey "нет SECRET_KEY в логах"
        Assert-HrmNotContains $logRaw $secrets.postgresPassword "нет пароля БД в логах"
        # прогресс дошёл до done
        $progress = Read-HrmJson (Join-Path $envT.StateRoot "progress.json")
        Assert-HrmEqual "done" $progress.phase "финальная фаза прогресса"
        # миграции вызываются через backend-консоль, идемпотентно
        Assert-HrmTrue ((Find-HrmCall $envT.Hooks @("run", "--rm", "backend", "alembic", "upgrade", "head")) -ge 0) "alembic upgrade head"
    } finally { Remove-HrmTestEnv -Root $envT.Root }
}

function script:HrmTest-10-Install-Idempotent-Reuse {
    $envT = New-HrmTestEnv
    try {
        $inputFile = Join-Path $envT.Root "installer-input.json"
        Write-HrmJson $inputFile @{ surname = "Петров"; workRole = "manager" }
        $r1 = Invoke-HrmInstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot -InputFile $inputFile -AllowNonWindows
        $s1 = Read-HrmJson (Join-Path $envT.StateRoot "secrets.json")
        # второй прогон (резюме/repair) — нет новых данных, пилот-ожидание в API
        $envT.Hooks.Http = "pending"
        $r2 = Invoke-HrmInstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot -AllowNonWindows
        Assert-HrmTrue $r2.Ok "повторный install ok"
        Assert-HrmTrue $r2.Reused "pairing переиспользован"
        Assert-HrmEqual $r1.PairingCode $r2.PairingCode "тот же код"
        $s2 = Read-HrmJson (Join-Path $envT.StateRoot "secrets.json")
        Assert-HrmEqual $s1.secretKey $s2.secretKey "секреты не ротированы"
        $issues = @($envT.Hooks.Calls | Where-Object { ($_.Args -join " ") -like "*pilot-pairing issue*" })
        Assert-HrmEqual 1 $issues.Count "второй issue НЕ вызывается"
        # владелец уже заявлен — снова тихо, ownerClaimed=true
        $envT.Hooks.Http = "claimed"
        $r3 = Invoke-HrmInstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot -AllowNonWindows
        Assert-HrmTrue $r3.Reused "владелец есть — переиспользование"
        $config = Get-HrmConfig $envT.StateRoot
        Assert-HrmTrue ([bool]$config.ownerClaimed) "ownerClaimed зафиксирован"
    } finally { Remove-HrmTestEnv -Root $envT.Root }
}

function script:HrmTest-11-Compose-Invocation-Shape {
    $envT = New-HrmTestEnv
    try {
        Invoke-HrmStop -AppDir $envT.AppDir -StateRoot $envT.StateRoot | Out-Null
        $stopIdx = Find-HrmCall $envT.Hooks @("compose", "-p", "hr-manager-pilot", "stop")
        Assert-HrmTrue ($stopIdx -ge 0) "stop собрал compose-команду"
        $argsJoined = $envT.Hooks.Calls[$stopIdx].Args -join " "
        Assert-HrmContains $argsJoined "-f" "оба файла overrides"
        Assert-HrmContains $argsJoined "compose.pilot.yml" "pilot-оверлей подключён"
        Assert-HrmContains $argsJoined "--env-file" "секреты через env-file, не через argv значений"
        Assert-HrmNotContains $argsJoined "SECRET_KEY=" "значения секретов не в аргументах"
    } finally { Remove-HrmTestEnv -Root $envT.Root }
}

function script:HrmTest-12-Start-And-Status {
    $envT = New-HrmTestEnv
    try {
        $inputFile = Join-Path $envT.Root "installer-input.json"
        Write-HrmJson $inputFile @{ surname = "Сидоров"; workRole = "hr" }
        Invoke-HrmInstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot -InputFile $inputFile -AllowNonWindows | Out-Null
        $st = Invoke-HrmStatus -StateRoot $envT.StateRoot
        Assert-HrmEqual "running" $st.appState "running из /ops/status"
        Assert-HrmEqual "ok" $st.database "база ok"
        Assert-HrmEqual "match" $st.migrations "миграции match"
        Assert-HrmEqual "ok" $st.worker "worker жив"
        Assert-HrmEqual "ok" $st.backup "backup ok"
        Assert-HrmEqual "not_configured" $st.integrations.telegram "Telegram честно не настроен"
        Assert-HrmEqual $true $st.versionMatch "версии совпадают"
        # ops недоступен -> starting/stopped различимы
        $envT.Hooks.Http = "ops-down"
        $st2 = Invoke-HrmStatus -StateRoot $envT.StateRoot
        Assert-HrmEqual "starting" $st2.appState "контейнеры есть, API молчит -> starting"
    } finally { Remove-HrmTestEnv -Root $envT.Root }
}

function script:HrmTest-13-Update-Order {
    $envT = New-HrmTestEnv
    try {
        $inputFile = Join-Path $envT.Root "installer-input.json"
        Write-HrmJson $inputFile @{ surname = "Кузнецов"; workRole = "hr" }
        Invoke-HrmInstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot -InputFile $inputFile -AllowNonWindows | Out-Null
        $envT.Hooks.Http = "claimed"
        $newDir = New-HrmFakeRelease -Dir (Join-Path $envT.Root "new-release") -Version "12.0.1" -Sha "sha-bbbb"
        $envT.Hooks.Ops["release_sha"] = "sha-bbbb"
        $start = $envT.Hooks.Calls.Count
        $u = Invoke-HrmUpdate -StateRoot $envT.StateRoot -NewReleaseDir $newDir -AllowNonWindows
        Assert-HrmTrue $u.Ok "update ok"
        Assert-HrmEqual "12.0.1" $u.Version
        # порядок ВНУТРИ update: build новых образов -> backup -> stop ->
        # migrate -> up -> (никакого downgrade)
        $build = Find-HrmCallFrom $envT.Hooks $start @("compose", "build")
        $backup = Find-HrmCallFrom $envT.Hooks $start @("run", "--rm", "backup")
        $stop = Find-HrmCallFrom $envT.Hooks $start @("compose", " stop")
        $migrate = Find-HrmCallFrom $envT.Hooks $start @("alembic", "upgrade", "head")
        $upNew = Find-HrmCallFrom $envT.Hooks $migrate @("up", "-d")
        Assert-HrmTrue ($build -ge $start) "build вызван"
        Assert-HrmTrue ($backup -gt $build) "backup строго после сборки образов"
        Assert-HrmTrue ($stop -gt $backup) "остановка после backup"
        Assert-HrmTrue ($migrate -gt $stop) "миграции строго ПОСЛЕ backup и остановки"
        Assert-HrmTrue ($upNew -gt $migrate) "запуск новой версии после миграций"
        foreach ($c in $envT.Hooks.Calls) {
            Assert-HrmNotContains ($c.Args -join " ") "downgrade" "ни одной откатной миграции"
        }
        # конфиг обновлён, блокировка снята, stage-файла нет
        $config = Get-HrmConfig $envT.StateRoot
        Assert-HrmEqual "12.0.1" $config.releaseVersion
        Assert-HrmTrue (-not (Test-Path (Join-Path $envT.StateRoot "update.lock"))) "update.lock снят"
        Assert-HrmTrue (-not (Test-Path (Join-Path $envT.StateRoot "update-stage.json"))) "stage-точка снята"
        $hist = Read-HrmJson (Get-HrmHistoryPath -StateRoot $envT.StateRoot)
        Assert-HrmEqual "ok" $hist.entries[-1].result "история обновления записана"
        # повторный update на той же версии — тихий no-op
        $u2 = Invoke-HrmUpdate -StateRoot $envT.StateRoot -NewReleaseDir $newDir -AllowNonWindows
        Assert-HrmTrue $u2.Skipped "идемпотентность: уже обновлён"
    } finally { Remove-HrmTestEnv -Root $envT.Root }
}

function script:HrmTest-14-Update-Backup-Failure-Aborts-Before-Changes {
    $envT = New-HrmTestEnv
    try {
        $inputFile = Join-Path $envT.Root "installer-input.json"
        Write-HrmJson $inputFile @{ surname = "Смирнов"; workRole = "hr" }
        Invoke-HrmInstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot -InputFile $inputFile -AllowNonWindows | Out-Null
        $newDir = New-HrmFakeRelease -Dir (Join-Path $envT.Root "new-release") -Version "12.0.1" -Sha "sha-bbbb"
        $envT.Hooks.BackupExit = 1   # backup-сервис падает
        $err = Assert-HrmThrows { Invoke-HrmUpdate -StateRoot $envT.StateRoot -NewReleaseDir $newDir -AllowNonWindows } -ExpectedExitCode 4
        Assert-HrmContains $err.Message "не подтверждено"
        $config = Get-HrmConfig $envT.StateRoot
        Assert-HrmEqual "12.0.0" $config.releaseVersion "версия НЕ изменена"
        $readme = Get-Content (Join-Path $envT.AppDir "README.txt") -Raw
        Assert-HrmContains $readme "12.0.0" "файлы приложения НЕ заменены"
        Assert-HrmTrue (-not (Test-Path (Join-Path $envT.AppDir "previous"))) "previous не создан"
        Assert-HrmTrue (-not (Test-Path (Join-Path $envT.StateRoot "update.lock"))) "блокировка снята"
    } finally { Remove-HrmTestEnv -Root $envT.Root }
}

function script:HrmTest-15-Update-Rollback-Code-Only {
    $envT = New-HrmTestEnv
    try {
        $inputFile = Join-Path $envT.Root "installer-input.json"
        Write-HrmJson $inputFile @{ surname = "Попов"; workRole = "hr" }
        Invoke-HrmInstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot -InputFile $inputFile -AllowNonWindows | Out-Null
        $newDir = New-HrmFakeRelease -Dir (Join-Path $envT.Root "new-release") -Version "12.0.1" -Sha "sha-bbbb"
        # post-проверка проваливается: ops показывает чужую версию
        $envT.Hooks.Ops["release_sha"] = "sha-zzzz-чужая"
        Assert-HrmThrows { Invoke-HrmUpdate -StateRoot $envT.StateRoot -NewReleaseDir $newDir -AllowNonWindows } -ExpectedExitCode 4 | Out-Null
        # файлы откачены на предыдущий релиз, config тоже; миграции не откатывались
        $readme = Get-Content (Join-Path $envT.AppDir "README.txt") -Raw
        Assert-HrmContains $readme "12.0.0" "код откачен на предыдущий релиз"
        $config = Get-HrmConfig $envT.StateRoot
        Assert-HrmEqual "12.0.0" $config.releaseVersion "конфиг откачен"
        Assert-HrmTrue (-not (Test-Path (Join-Path $envT.AppDir "previous"))) "previous поднят"
        Assert-HrmTrue (Test-Path (Join-Path $envT.StateRoot "resume.json")) "resume-точка сохранена"
        Assert-HrmEqual "update-failed" (Read-HrmJson (Join-Path $envT.StateRoot "resume.json")).stage
        foreach ($c in $envT.Hooks.Calls) {
            Assert-HrmNotContains ($c.Args -join " ") "downgrade" "миграции не откатываются (forward-only)"
        }
        $hist = Read-HrmJson (Get-HrmHistoryPath -StateRoot $envT.StateRoot)
        Assert-HrmEqual "failed" $hist.entries[-1].result "сбой зафиксирован в истории"
        Assert-HrmTrue (-not (Test-Path (Join-Path $envT.StateRoot "update.lock"))) "блокировка снята"
    } finally { Remove-HrmTestEnv -Root $envT.Root }
}

function script:HrmTest-16-Uninstall-Keeps-Data-By-Default {
    $envT = New-HrmTestEnv
    try {
        $inputFile = Join-Path $envT.Root "installer-input.json"
        Write-HrmJson $inputFile @{ surname = "Орлова"; workRole = "admin" }
        Invoke-HrmInstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot -InputFile $inputFile -AllowNonWindows | Out-Null
        $r = Invoke-HrmUninstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot
        Assert-HrmTrue $r.Ok
        Assert-HrmTrue (-not $r.DataRemoved) "данные не удалены"
        $downIdx = Find-HrmCall $envT.Hooks @("down")
        Assert-HrmTrue ($downIdx -ge 0) "compose down вызван"
        Assert-HrmNotContains ($envT.Hooks.Calls[$downIdx].Args -join " ") " -v" "без -v: тома PostgreSQL сохранены"
        Assert-HrmTrue (Test-Path (Join-Path $envT.StateRoot "config.json")) "состояние сохранено"
        Assert-HrmTrue (Test-Path (Join-Path $envT.StateRoot "secrets.json")) "пароли сохранены"
        # переустановка идемпотентна: те же секреты, тот же installId
        $envT.Hooks.Http = "pending"
        $before = Get-HrmConfig $envT.StateRoot
        $r2 = Invoke-HrmInstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot -AllowNonWindows
        $after = Get-HrmConfig $envT.StateRoot
        Assert-HrmEqual $before.installId $after.installId "installId устойчив"
    } finally { Remove-HrmTestEnv -Root $envT.Root }
}

function script:HrmTest-17-Uninstall-Purge-Requires-Phrase {
    $envT = New-HrmTestEnv
    try {
        $inputFile = Join-Path $envT.Root "installer-input.json"
        Write-HrmJson $inputFile @{ surname = "Громов"; workRole = "hr" }
        Invoke-HrmInstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot -InputFile $inputFile -AllowNonWindows | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $envT.StateRoot "backup") -Force | Out-Null
        Set-Content -Path (Join-Path $envT.StateRoot "backup/backup-final.sql.gz") -Value "gz"
        # неправильная/пустая фраза — отказ, данные целы
        Assert-HrmThrows { Invoke-HrmUninstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot -RemoveData } -ExpectedExitCode 7 | Out-Null
        Assert-HrmThrows { Invoke-HrmUninstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot -RemoveData -ConfirmPhrase "удалить данные hr manager" } -ExpectedExitCode 7 | Out-Null
        Assert-HrmTrue (Test-Path (Join-Path $envT.StateRoot "config.json")) "после отказа данные целы"
        # точная фраза — экспорт копии, затем down -v и удаление состояния
        $export = Join-Path $envT.Root "export"
        $r = Invoke-HrmUninstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot -RemoveData -ConfirmPhrase $script:HrmConfirmPhrase -ExportBackupTo $export
        Assert-HrmTrue $r.DataRemoved
        Assert-HrmTrue (Get-ChildItem $export -Filter "*.sql.gz" | Measure-Object).Count -ge 1 "копия экспортирована"
        $purgeIdx = Find-HrmCall $envT.Hooks @("down", "-v")
        Assert-HrmTrue ($purgeIdx -ge 0) "down -v только после фразы"
        Assert-HrmTrue (-not (Test-Path $envT.StateRoot)) "каталог состояния удалён"
    } finally { Remove-HrmTestEnv -Root $envT.Root }
}

function script:HrmTest-18-Diagnostics-Redacted {
    $envT = New-HrmTestEnv
    try {
        $inputFile = Join-Path $envT.Root "installer-input.json"
        Write-HrmJson $inputFile @{ surname = "Тихонова"; workRole = "hr" }
        Invoke-HrmInstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot -InputFile $inputFile -AllowNonWindows | Out-Null
        $secrets = Read-HrmJson (Join-Path $envT.StateRoot "secrets.json")
        $d = Invoke-HrmDiagnostics -AppDir $envT.AppDir -StateRoot $envT.StateRoot
        Assert-HrmTrue $d.Ok
        $report = [IO.File]::ReadAllText($d.Path)
        Assert-HrmContains $report "HR Manager — диагностика"
        Assert-HrmNotContains $report $secrets.secretKey "SECRET_KEY не в отчёте"
        Assert-HrmNotContains $report $secrets.postgresPassword "пароль БД не в отчёте"
        Assert-HrmNotContains $report $secrets.backupEncKey "ключ backup не в отчёте"
        Assert-HrmNotContains $report "Тихонова" "PII нет"
        Assert-HrmTrue (Test-HrmSecretValueAbsent -Text $report -Secrets @([string]$secrets.secretKey, [string]$secrets.postgresPassword, [string]$secrets.backupEncKey))
        Assert-HrmTrue (Test-Path (Join-Path $envT.StateRoot "status.json")) "агрегированный статус сохранён"
    } finally { Remove-HrmTestEnv -Root $envT.Root }
}

function script:HrmTest-19-Resume-After-Docker {
    $envT = New-HrmTestEnv
    try {
        $inputFile = Join-Path $envT.Root "installer-input.json"
        Write-HrmJson $inputFile @{ surname = "Медведев"; workRole = "hr" }
        Invoke-HrmInstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot -InputFile $inputFile -AllowNonWindows | Out-Null
        $envT.Hooks.Http = "pending"
        Write-HrmJson (Join-Path $envT.StateRoot "resume.json") @{ stage = "await-docker"; note = "t" }
        $r = Invoke-HrmResume -AppDir $envT.AppDir -StateRoot $envT.StateRoot
        Assert-HrmTrue $r.Resumed "resume продолжил установку"
        Assert-HrmTrue (-not (Test-Path (Join-Path $envT.StateRoot "resume.json"))) "resume-файл убран после продолжения"
        # нет resume-точки — тихий ok
        $r2 = Invoke-HrmResume -AppDir $envT.AppDir -StateRoot $envT.StateRoot
        Assert-HrmTrue (-not $r2.Resumed) "без точки — ничего не делаем"
    } finally { Remove-HrmTestEnv -Root $envT.Root }
}

function script:HrmTest-20-Docker-Missing-Preflight-Gate {
    $envT = New-HrmTestEnv
    try {
        $envT.Hooks.DockerMissing = $true
        # manifest с пустыми полями -> fail closed (источник не настроен)
        $err = Assert-HrmThrows {
            Invoke-HrmInstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot -AllowNonWindows -DockerManifestDir $envT.Root
        }
        Assert-HrmTrue ($err.Message -like "*docker-desktop.json*" -or $err.Message -like "*Docker Desktop*") "понятная русская ошибка про Docker"
        Assert-HrmTrue (-not (Test-Path (Join-Path $envT.StateRoot "config.json"))) "без Docker ничего не установлено"
    } finally { Remove-HrmTestEnv -Root $envT.Root }
}

function script:HrmTest-21-PrivateFiles-Mode600 {
    $envT = New-HrmTestEnv
    try {
        $p = Join-Path $envT.StateRoot "topsecret.txt"
        Write-HrmPrivateFile -Path $p -Content "abc"
        Assert-HrmEqual "abc" ([IO.File]::ReadAllText($p))
        if (-not (Test-HrmWindows)) {
            $mode = [Convert]::ToString((Get-Item $p).UnixFileMode, 8)
            Assert-HrmEqual "10600" $mode "chmod 600 применён (тестовый контур)"
        }
    } finally { Remove-HrmTestEnv -Root $envT.Root }
}

function script:HrmTest-22-EnsureRunning-Url-Routing {
    $envT = New-HrmTestEnv
    try {
        $inputFile = Join-Path $envT.Root "installer-input.json"
        Write-HrmJson $inputFile @{ surname = "Гаврилов"; workRole = "hr" }
        Invoke-HrmInstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot -InputFile $inputFile -AllowNonWindows | Out-Null
        # владелец не заявлен -> редирект на страницу first-run
        $r = Invoke-HrmEnsureRunning -AppDir $envT.AppDir -StateRoot $envT.StateRoot
        Assert-HrmContains $r.Url "/first-run" "пока нет владельца — на страницу первого входа"
        Assert-HrmEqual $true $r.Running
        $envT.Hooks.Http = "claimed"
        $r2 = Invoke-HrmEnsureRunning -AppDir $envT.AppDir -StateRoot $envT.StateRoot
        Assert-HrmTrue ($r2.Url -notlike "*first-run*") "владелец есть — в рабочее приложение"
        $config = Get-HrmConfig $envT.StateRoot
        Assert-HrmEqual $true ([bool]$config.ownerClaimed) "ownerClaimed обновился без ручных шагов"
    } finally { Remove-HrmTestEnv -Root $envT.Root }
}

function script:HrmTest-23-Pairing-Issue-Failure {
    $envT = New-HrmTestEnv
    try {
        $inputFile = Join-Path $envT.Root "installer-input.json"
        Write-HrmJson $inputFile @{ surname = "Барсуков"; workRole = "hr" }
        $envT.Hooks.IssueExit = 1  # backend отверг выдачу
        $err = Assert-HrmThrows {
            Invoke-HrmInstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot -InputFile $inputFile -AllowNonWindows
        }
        Assert-HrmContains $err.Message "кода первого входа"
        # при этом состояние и БД не испорчены: повтор безопасного шага с теми
        # же данными (файл входа сохранён до успеха) доводит выдачу до конца
        Assert-HrmTrue (Test-Path $inputFile) "файл входа сохранён после сбоя"
        $envT.Hooks.IssueExit = 0
        $r = Invoke-HrmInstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot -InputFile $inputFile -AllowNonWindows
        Assert-HrmTrue $r.Ok "повторный безопасный шаг довёл pairing до конца"
        Assert-HrmTrue (-not $r.Reused) "выдача выполнена заново"
        $issues = @($envT.Hooks.Calls | Where-Object { ($_.Args -join " ") -like "*pilot-pairing issue*" })
        Assert-HrmEqual 2 $issues.Count "две попытки выдачи"
        Assert-HrmTrue (-not (Test-Path $inputFile)) "файл входа удалён после успеха"
    } finally { Remove-HrmTestEnv -Root $envT.Root }
}

function script:HrmTest-24-Invalid-Input-Exit-Codes {
    $envT = New-HrmTestEnv
    try {
        $bad = Join-Path $envT.Root "in.json"
        Write-HrmJson $bad @{ surname = "X1"; workRole = "hr" }
        Assert-HrmThrows { Invoke-HrmInstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot -InputFile $bad -AllowNonWindows } -ExpectedExitCode 2 | Out-Null
        Write-HrmJson $bad @{ surname = "Иванов"; workRole = "ceo" }
        Assert-HrmThrows { Invoke-HrmInstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot -InputFile $bad -AllowNonWindows } -ExpectedExitCode 2 | Out-Null
        # нет конфигурации — start/uninstall понятные коды
        $root2 = Join-Path ([IO.Path]::GetTempPath()) ("hrmgr-empty-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
        New-Item -ItemType Directory -Path $root2 | Out-Null
        Assert-HrmThrows { Invoke-HrmStart -AppDir $envT.AppDir -StateRoot $root2 } -ExpectedExitCode 5 | Out-Null
        Remove-Item $root2 -Recurse -Force
    } finally { Remove-HrmTestEnv -Root $envT.Root }
}

function script:HrmTest-25-Update-Integrity-Rejected-First {
    $envT = New-HrmTestEnv
    try {
        $inputFile = Join-Path $envT.Root "installer-input.json"
        Write-HrmJson $inputFile @{ surname = "Фомин"; workRole = "hr" }
        Invoke-HrmInstall -AppDir $envT.AppDir -StateRoot $envT.StateRoot -InputFile $inputFile -AllowNonWindows | Out-Null
        $newDir = New-HrmFakeRelease -Dir (Join-Path $envT.Root "new-release") -Version "12.0.1" -Sha "sha-bbbb"
        Add-Content -Path (Join-Path $newDir "README.txt") -Value "внедрение"
        $callsBefore = $envT.Hooks.Calls.Count
        Assert-HrmThrows { Invoke-HrmUpdate -StateRoot $envT.StateRoot -NewReleaseDir $newDir -AllowNonWindows } -ExpectedExitCode 4 | Out-Null
        # проверка целостности — ДО любых вызовов compose и до блокировки
        $newCalls = @($envT.Hooks.Calls | Select-Object -Skip $callsBefore)
        foreach ($c in $newCalls) {
            Assert-HrmNotContains ($c.Args -join " ") "backup" "ничего не бэкапилось"
            Assert-HrmNotContains ($c.Args -join " ") "build" "сборка не начиналась"
        }
        Assert-HrmTrue (-not (Test-Path (Join-Path $envT.StateRoot "update.lock"))) "блокировка не оставалась"
    } finally { Remove-HrmTestEnv -Root $envT.Root }
}
