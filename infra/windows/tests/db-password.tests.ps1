# Регрессии рассогласования пароля PostgreSQL и результата Setup (14.10.2026).
param([string]$HarnessPath = $PSScriptRoot)
. (Join-Path $HarnessPath "test-harness.ps1")

function New-HrmPasswordTestContext {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    Set-HrmPreflightOverride @{ windows=$true; powershell=$true; docker=$true; daemon=$true; compose="v2.29.7 (mock)"; port=$true; state_dir=$true; space=$true; config=$true }
    Set-HrmDockerOverride @{ desktop="engine_ready"; engine=$true; wsl="ok"; virtualization="enabled"; free_mb=20480; port_free=$true; admin=$false }
    Initialize-HrmStateDir (Get-HrmTestStateDir) | Out-Null
    $null = Get-HrmSecret (Get-HrmTestStateDir) "HRM_POSTGRES_PASSWORD"
    return $world
}

function Assert-HrmPasswordDataUntouched {
    param($World)
    Assert-HrmEqual 0 @($World.RemovedVolumes).Count
    foreach ($call in $World.Calls) {
        Assert-HrmFalse ($call.Args -contains "down") "восстановление не должно останавливать/удалять проект"
        Assert-HrmFalse ($call.Args -contains "rm") "запрещено удалять данные"
        Assert-HrmFalse ($call.Args -contains "prune") "запрещена очистка томов"
    }
}

foreach ($brokenState in @("partial", "degraded")) {
    Test-Case "DB password: предсуществующий $brokenState восстанавливается до running" {
        $world = New-HrmPasswordTestContext
        $world.DbPasswordMismatch = $true
        $world.DbRevision = "0013"
        $backendState = if ($brokenState -eq "partial") { "exited" } else { "running" }
        $world.ContainersJson = '[{"Name":"db","Service":"db","State":"running","Health":"healthy"},{"Name":"backend","State":"' + $backendState + '","Health":"unhealthy"}]'
        $result = Start-HrmStack (Get-HrmTestInstallDir) (Get-HrmTestStateDir)
        Assert-HrmEqual 1 @($result).Count
        Assert-HrmTrue $result.ok
        Assert-HrmEqual "running" $result.state
        Assert-HrmEqual 1 $world.DbPasswordSyncCount
        # Пароль исправляется до запроса схемы и до первого repair/up.
        $calls = @($world.Calls)
        $syncIndex = -1; $schemaIndex = -1; $upIndex = -1
        for ($i = 0; $i -lt $calls.Count; $i++) {
            if ($calls[$i].Stdin -like '*ALTER ROLE*') { $syncIndex = $i }
            if (($calls[$i].Args -join ' ') -like '*SELECT version_num*' -and $schemaIndex -lt 0) { $schemaIndex = $i }
            if ($calls[$i].Args -contains 'up' -and $upIndex -lt 0) { $upIndex = $i }
        }
        Assert-HrmTrue ($syncIndex -ge 0 -and $schemaIndex -gt $syncIndex -and $upIndex -gt $syncIndex) "нарушен порядок sync -> schema -> repair"
        Assert-HrmPasswordDataUntouched $world
    }
}

Test-Case "DB password: неуспешный initial up с healthy db тоже восстанавливается" {
    $world = New-HrmPasswordTestContext
    $world.DbPasswordMismatch = $true
    $result = Start-HrmStack (Get-HrmTestInstallDir) (Get-HrmTestStateDir)
    Assert-HrmEqual 1 @($result).Count
    Assert-HrmTrue $result.ok
    Assert-HrmEqual "running" $result.state
    Assert-HrmEqual 1 $world.DbPasswordSyncCount
    Assert-HrmTrue ($world.UpCount -ge 2)
    Assert-HrmPasswordDataUntouched $world
}

Test-Case "DB password: successful up с degraded после запуска синхронизируется" {
    $world = New-HrmPasswordTestContext
    $world.ContainersJson = '[{"Name":"db","Service":"db","State":"exited"},{"Name":"backend","State":"exited"}]'
    # up публикует degraded лишь после первой попытки: проверяем вторую ветку.
    $composeModule = Get-Module Compose
    & $composeModule {
        $script:PasswordTestOriginalGetState = ${function:Get-HrmStackState}
        $script:PasswordTestStateCalls = 0
        function script:Get-HrmStackState {
            param([string]$InstallDir, [string]$StateDir)
            $script:PasswordTestStateCalls++
            if ($script:PasswordTestStateCalls -eq 1) {
                return [pscustomobject]@{ state="absent" }
            }
            return (& $script:PasswordTestOriginalGetState -InstallDir $InstallDir -StateDir $StateDir)
        }
    }
    $world.ContainersJson = '[{"Name":"db","Service":"db","State":"running"},{"Name":"backend","State":"running","Health":"unhealthy"}]'
    $result = Start-HrmStack (Get-HrmTestInstallDir) (Get-HrmTestStateDir)
    Assert-HrmTrue $result.ok
    Assert-HrmEqual "running" $result.state
    Assert-HrmEqual 1 $world.DbPasswordSyncCount
    Assert-HrmPasswordDataUntouched $world
}

foreach ($branch in @("existing", "initial-up")) {
    Test-Case "DB password: отказ синхронизации ($branch) закрывает путь успеха" {
        $world = New-HrmPasswordTestContext
        $world.DbPasswordMismatch = $true
        $world.DbPasswordSyncFails = $true
        if ($branch -eq "existing") { $world.ContainersJson = '[{"Name":"db","State":"running"},{"Name":"backend","State":"exited"}]' }
        $result = Start-HrmStack (Get-HrmTestInstallDir) (Get-HrmTestStateDir)
        Assert-HrmEqual 1 @($result).Count
        Assert-HrmFalse $result.ok
        Assert-HrmEqual "database_auth_failed" $result.state
        Assert-HrmContains $result.message "hr_manager"
        Assert-HrmContains $result.message "pilot_pgdata"
        Assert-HrmContains $result.message "Данные не удалялись"
        Assert-HrmPasswordDataUntouched $world
    }
}

Test-Case "DB password: здоровый стек не меняет пароль" {
    $world = New-HrmPasswordTestContext
    $world.Running = $true
    $result = Start-HrmStack (Get-HrmTestInstallDir) (Get-HrmTestStateDir)
    Assert-HrmTrue $result.ok
    Assert-HrmEqual 0 $world.DbPasswordSyncCount
}

Test-Case "DB password: отсутствующий секрет пропускается и не создаётся" {
    Initialize-HrmTestEngine
    $world = New-HrmMockWorld
    $result = Sync-HrmPostgresRolePassword (Get-HrmTestInstallDir) (Get-HrmTestStateDir)
    Assert-HrmEqual 1 @($result).Count
    Assert-HrmTrue $result.ok
    Assert-HrmFalse $result.synced
    Assert-HrmFalse (Test-Path (Get-HrmSecretsFile (Get-HrmTestStateDir)))
    Assert-HrmEqual 0 $world.DbPasswordSyncCount
}

Test-Case "DB password: sync идемпотентен, пароль только stdin, SQL экранируется" {
    $world = New-HrmPasswordTestContext
    $state = Get-HrmTestStateDir
    $password = "fixture'" + [guid]::NewGuid().ToString("N") + "\literal"
    Set-HrmJsonFile $state "secrets.json" @{ HRM_POSTGRES_PASSWORD=$password }
    $env:HRM_LOG_FILE = Join-Path $state "sync.log"
    foreach ($attempt in 1..2) {
        $res = Sync-HrmPostgresRolePassword (Get-HrmTestInstallDir) $state
        Assert-HrmEqual 1 @($res).Count
        Assert-HrmTrue $res.synced
    }
    Assert-HrmEqual 2 $world.DbPasswordSyncCount
    Assert-HrmEqual $password (Get-HrmSecret $state "HRM_POSTGRES_PASSWORD")
    $argsText = $world.DbPasswordSyncArgs -join " "
    Assert-HrmContains $argsText "exec -T db psql -X -w"
    Assert-HrmContains $argsText "ON_ERROR_STOP=1"
    Assert-HrmNotContains $argsText $password
    Assert-HrmNotContains $argsText "PGPASSWORD"
    Assert-HrmNotContains $argsText "ALTER ROLE"
    $call = @($world.Calls | Where-Object { $_.Stdin -like '*ALTER ROLE*' })[0]
    Assert-HrmContains $call.Stdin ("PASSWORD '" + $password.Replace("'", "''") + "';")
    Assert-HrmContains $call.Stdin "SET standard_conforming_strings = on"
    Assert-HrmContains $call.Stdin "SET log_statement = 'none'"
    Assert-HrmNotContains (Get-Content $env:HRM_LOG_FILE -Raw) $password
    Assert-HrmPasswordDataUntouched $world
}

foreach ($kind in @("stderr", "exception")) {
    Test-Case "DB password: SQL/секрет из $kind не попадают в результат и журнал" {
        $world = New-HrmPasswordTestContext
        $world.DbPasswordSyncFails = ($kind -eq "stderr")
        $world.DbPasswordSyncThrows = ($kind -eq "exception")
        $state = Get-HrmTestStateDir
        $password = Get-HrmSecret $state "HRM_POSTGRES_PASSWORD"
        $env:HRM_LOG_FILE = Join-Path $state "sync.log"
        $result = Sync-HrmPostgresRolePassword (Get-HrmTestInstallDir) $state
        Assert-HrmFalse $result.ok
        $text = ($result | ConvertTo-Json) + (Get-Content $env:HRM_LOG_FILE -Raw)
        Assert-HrmNotContains $text $password
        Assert-HrmNotContains $text "ALTER ROLE"
    }
}

Test-Case "DB password: пароль исправлен, но unhealthy не считается успехом" {
    $world = New-HrmPasswordTestContext
    $world.DbPasswordMismatch = $true
    $world.DbPasswordRecoveryStuck = $true
    $result = Start-HrmStack (Get-HrmTestInstallDir) (Get-HrmTestStateDir)
    Assert-HrmFalse $result.ok
    Assert-HrmEqual "stack_not_running" $result.state
    Assert-HrmTrue ($world.Calls.Count -lt 100) "ожидание должно быть ограничено"
    Assert-HrmPasswordDataUntouched $world
}

Test-Case "bundle: последние пять engine logs, tail и редакция с пустым реестром секретов" {
    $null = New-HrmPasswordTestContext
    $state = Get-HrmTestStateDir
    $logsDir = Get-HrmEngineLogsDir $state
    New-Item -ItemType Directory $logsDir -Force | Out-Null
    $secret = Get-HrmSecret $state "HRM_POSTGRES_PASSWORD"
    foreach ($i in 1..6) {
        $path = Join-Path $logsDir ("install-case-" + $i + ".log")
        Set-Content $path @("not-in-tail", "engine-evidence-$i $secret", "fixture@example.com +79161234567") -Encoding UTF8
        (Get-Item $path).LastWriteTime = (Get-Date).AddMinutes(-10 + $i)
    }
    Set-Content (Join-Path $logsDir "not-a-log.txt") "must-not-copy"
    $env:HRM_DESKTOP_DIR = Join-Path $state "desktop"
    New-Item -ItemType Directory $env:HRM_DESKTOP_DIR -Force | Out-Null
    Reset-HrmRedaction # отдельный запуск bundler не наследует реестр секретов
    $zip = New-HrmSupportBundle (Get-HrmTestInstallDir) $state -LogTail 2
    $unpack = Join-Path $state "unpacked"
    [System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $unpack)
    $text = Get-Content (Join-Path $unpack "logs-engine.txt") -Raw -Encoding UTF8
    Assert-HrmContains $text "engine-evidence-6"
    Assert-HrmContains $text "engine-evidence-2"
    Assert-HrmNotContains $text "engine-evidence-1"
    Assert-HrmNotContains $text "not-in-tail"
    Assert-HrmNotContains $text "must-not-copy"
    Assert-HrmContainsRedacted $text
    foreach ($file in Get-ChildItem $unpack -File) {
        Assert-HrmNotContains (Get-Content $file.FullName -Raw -Encoding UTF8) $secret
        Assert-HrmNotContains (Get-Content $file.FullName -Raw -Encoding UTF8) "fixture@example.com"
    }
    Assert-HrmFalse (Test-Path (Join-Path $unpack "secrets.json"))
    Assert-HrmFalse (Test-Path (Join-Path $unpack "pilot.env"))
}

function Invoke-HrmSetupFailureFixture {
    # Настоящий hr-manager.ps1, отдельный Windows PowerShell; только функции
    # Install/Update заменены в ВРЕМЕННОЙ копии. Docker/машина не затрагиваются.
    param([string]$Action = "install", [switch]$Success, [switch]$BrokenLogger, [switch]$ImportFailure)
    Initialize-HrmTestEngine
    $state = Get-HrmTestStateDir
    $package = Join-Path $state "engine-package"
    New-Item -ItemType Directory $package -Force | Out-Null
    Copy-Item (Join-Path $PSScriptRoot "..\hr-manager.ps1") $package
    Copy-Item (Join-Path $PSScriptRoot "..\engine") $package -Recurse
    $body = if ($Success) { 'return' } else { 'Register-HrmSecret $env:HRM_TEST_FAILURE_SECRET; throw ("fixture-install-failure " + $env:HRM_TEST_FAILURE_SECRET)' }
    foreach ($module in @("Install", "Update")) {
        $function = if ($module -eq "Install") { "Install-HrmApp" } else { "Update-HrmApp" }
        Add-Content (Join-Path $package "engine\$module.psm1") ("`nfunction " + $function + ' { param($SourceDir, $InstallDir, $StateDir, $Port, $ReleaseDir) ' + $body + ' }') -Encoding UTF8
    }
    if ($BrokenLogger) {
        Add-Content (Join-Path $package "engine\Common.psm1") '`nfunction Write-HrmLog { param($Level, $Message) throw "fixture-log-unwritable" }'.Replace('`n', "`n") -Encoding UTF8
    }
    if ($ImportFailure) { Remove-Item (Join-Path $package "engine\Crypto.psm1") }
    # Старая успешная запись обновления не должна скрывать НОВЫЙ отказ.
    Set-Content (Join-Path $state "update-result.json") '{"message":"stale-success-message"}' -Encoding UTF8
    $env:HRM_TEST_FAILURE_SECRET = [guid]::NewGuid().ToString("N")
    try {
        $exe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
        $text = (& $exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $package "hr-manager.ps1") -Action $Action -StateDir $state -InstallDir (Get-HrmTestInstallDir) -NonInteractive 2>$null | Out-String)
        $code = $LASTEXITCODE
        Assert-HrmNotContains $text $env:HRM_TEST_FAILURE_SECRET
        $marker = Get-HrmSetupMarker -StateDir $state
        if ($Success) {
            Assert-HrmEqual 0 $code
            Assert-HrmFalse (Test-Path (Get-HrmSetupMarkerFile $state))
        } else {
            Assert-HrmEqual 1 $code
            Assert-HrmEqual "failed" $marker.status
            Assert-HrmNotContains $marker.message $env:HRM_TEST_FAILURE_SECRET
            Assert-HrmNotContains $marker.message "stale-success-message"
            if (-not $ImportFailure) { Assert-HrmContains $marker.message "fixture-install-failure" }
            if (-not $BrokenLogger) {
                $logs = @(Get-ChildItem (Get-HrmEngineLogsDir $state) -Filter "*.log")
                Assert-HrmTrue ($logs.Count -gt 0) "прямой Setup запуск не создал журнал"
                $logText = Get-Content $logs[0].FullName -Raw -Encoding UTF8
                Assert-HrmContains $logText "fatal:"
                Assert-HrmNotContains $logText $env:HRM_TEST_FAILURE_SECRET
            }
        }
    } finally { Remove-Item Env:HRM_TEST_FAILURE_SECRET -ErrorAction SilentlyContinue }
}

Test-Case "Setup CLI: отказ install оставляет failed с настоящей причиной и журналом" { Invoke-HrmSetupFailureFixture }
Test-Case "Setup CLI: отказ update оставляет failed, не старый update-result" { Invoke-HrmSetupFailureFixture -Action update }
Test-Case "Setup CLI: ошибка логирования не мешает failed" { Invoke-HrmSetupFailureFixture -BrokenLogger }
Test-Case "Setup CLI: ошибка импорта внутри общего catch" { Invoke-HrmSetupFailureFixture -ImportFailure }
Test-Case "Setup CLI: успех install удаляет маркер" { Invoke-HrmSetupFailureFixture -Success }
Test-Case "Setup CLI: успех update удаляет маркер" { Invoke-HrmSetupFailureFixture -Action update -Success }

Test-Case "Setup Finish: running нормализуется до выбора текста страницы" {
    $iss = Get-Content (Join-Path $PSScriptRoot "..\..\..\installer\installer.iss") -Raw -Encoding UTF8
    $finish = $iss.Substring($iss.IndexOf("procedure CurPageChanged("))
    Assert-HrmTrue ($finish.IndexOf("MarkHrmSetupEngineFailed();") -lt $finish.IndexOf("if EngineStatus = 'success'"))
}

Test-Case "Setup: отказ удаления маркера не превращается в exit 0" {
    Initialize-HrmTestEngine
    $null = New-HrmMockWorld
    $state = Get-HrmTestStateDir
    $null = Set-HrmSetupMarker -StateDir $state -Status "running" -Message "fixture-running"
    # Ошибка файловой системы имитируется только внутри Common; реальный файл
    # остаётся. Не меняем ACL на машине и не перехватываем исключение в функции.
    & (Get-Module Common) {
        function script:Remove-Item {
            [CmdletBinding()] param([string]$Path, [switch]$Force)
            Write-Error "fixture-marker-delete-denied"
        }
    }
    Assert-HrmThrows "Clear-HrmSetupMarker обязан сообщить отказ удаления" { Clear-HrmSetupMarker $state }
    Assert-HrmTrue (Test-Path (Get-HrmSetupMarkerFile $state))
}
