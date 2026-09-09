# ============================================================================
# Инфраструктура тестов движка: изолированные临时 каталоги, mock-раннер
# команд (docker/…), mock HTTP, ассерты. Никакого реального docker/реестра.
# ============================================================================

function script:Assert-HrmTrue {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw "assert failed: $Message" }
}

function script:Assert-HrmEqual {
    param($Expected, $Actual, [string]$Message = "")
    if ($Expected -ne $Actual) {
        throw "assert equals failed ($Message): expected '$Expected', got '$Actual'"
    }
}

function script:Assert-HrmContains {
    param([string]$Haystack, [string]$Needle, [string]$Message = "")
    if ($Haystack -notlike "*$Needle*") {
        throw "assert contains failed ($Message): '$Needle' not in [$Haystack]"
    }
}

function script:Assert-HrmNotContains {
    param([string]$Haystack, [string]$Needle, [string]$Message = "")
    if ($Haystack -like "*$Needle*") {
        throw "assert NOT contains failed ($Message): '$Needle' found in [$Haystack]"
    }
}

function script:Assert-HrmThrows {
    <# Возвращает объект ошибки; проверяет код возврата движка, если задан. #>
    param([scriptblock]$Block, [int]$ExpectedExitCode = -1)
    try {
        & $Block | Out-Null
    } catch {
        $e = $_.Exception
        if ($ExpectedExitCode -ge 0) {
            if ($e -is [HrmEngineException]) {
                if ([int]$e.ExitCode -ne $ExpectedExitCode) {
                    throw "wrong exit code: expected $ExpectedExitCode, got $($e.ExitCode) ($($e.Message))"
                }
            } else {
                throw "expected HrmEngineException code $ExpectedExitCode, got $($e.GetType().Name): $($e.Message)"
            }
        }
        return $e
    }
    throw "expected exception, none thrown"
}

function script:New-HrmFakeRelease {
    <# Мини-комплект релиза: manifest+SHA256SUMS, infra-файлы compose, пустые
       контексты сборки. Возвращает путь. #>
    param([Parameter(Mandatory)][string]$Dir, [string]$Version = "12.0.0", [string]$Sha = "sha-aaaa")
    if (-not (Test-Path $Dir)) { New-Item -ItemType Directory -Path $Dir -Force | Out-Null }
    New-Item -ItemType Directory -Path (Join-Path $Dir "infra") -Force | Out-Null
    Set-Content -Path (Join-Path $Dir "infra\docker-compose.yml") -Value "name: hr-manager-dev"
    Set-Content -Path (Join-Path $Dir "infra\compose.pilot.yml") -Value "name: hr-manager-pilot"
    Set-Content -Path (Join-Path $Dir "README.txt") -Value "bundle $Version"
    $manifest = [ordered]@{ product = "hr-manager-pilot"; version = $Version; releaseSha = $Sha }
    $manifestJson = $manifest | ConvertTo-Json -Compress
    [IO.File]::WriteAllText((Join-Path $Dir "release-manifest.json"), $manifestJson)
    $sums = @()
    foreach ($rel in @("infra/docker-compose.yml", "infra/compose.pilot.yml", "README.txt", "release-manifest.json")) {
        $full = Join-Path $Dir ($rel.Replace('/', [IO.Path]::DirectorySeparatorChar))
        $sums += ("{0}  {1}" -f (Get-FileHash -Algorithm SHA256 -Path $full).Hash.ToLowerInvariant(), $rel)
    }
    Set-Content -Path (Join-Path $Dir "SHA256SUMS.txt") -Value ($sums -join [Environment]::NewLine)
    return $Dir
}

function script:New-HrmTestEnv {
    <# Изолированный мир для одного теста: temp state/app, подменённые внешние
       вызовы, записи всех команд. Вызывать ПЕРВЫМ в каждом тесте. #>
    param(
        [string]$ReleaseVersion = "12.0.0",
        [string]$ReleaseSha = "sha-aaaa",
        [string]$HttpScenario = "fresh"
    )
    $root = Join-Path ([System.IO.Path]::GetTempPath()) ("hrmgr-test-" + [Guid]::NewGuid().ToString("N").Substring(0, 12))
    $stateRoot = Join-Path $root "state"
    $appDir = Join-Path $root "app"
    New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
    New-HrmFakeRelease -Dir $appDir -Version $ReleaseVersion -Sha $ReleaseSha | Out-Null

    $env:HRMGR_TEST_STATE_DIR = $root
    $env:LOCALAPPDATA = $root   # ярлыки и bin уходят в temp-дерево

    $h = [ordered]@{
        Root        = $root
        StateRoot   = $stateRoot
        AppDir      = $appDir
        Calls       = New-Object Collections.ArrayList   # {File;Args;StdIn}
        ComposeExit = 0
        Http        = $HttpScenario
        Ops         = [ordered]@{
            status      = "ok"
            database    = @{ status = "ok" }
            migrations  = @{ ok = $true }
            notifications = @{ worker_alive = $true }
            backup      = @{ available = $true; ok = $true; age_seconds = 60 }
            release_sha = $ReleaseSha
        }
        FirstRun    = [ordered]@{
            pending = $false; pending_work_role = $null
            pending_expires_in_seconds = $null
            fresh_install = $true; pilot_owner_exists = $false
        }
        IssueExit   = 0
        BackupExit  = 0
        ComposeVersion = "v2.29.7"
        DockerMissing = $false
        BackupOutput = "backup created`ndeep check ok"
    }

    $tool = {
        param($File, $Args, $StdIn)
        [void]$h.Calls.Add([pscustomobject]@{ File = $File; Args = @($Args); StdIn = $StdIn })
        $joined = @($Args) -join " "
        $call = [pscustomobject]@{ ExitCode = 0; StdOut = @(); StdErr = @(); StdOutText = "" }
        if ($File -eq "docker") {
            if ($h.DockerMissing) {
                return [pscustomobject]@{ ExitCode = 1; StdOut = @(); StdErr = @(); StdOutText = "" }
            }
            if ($joined -match "^version") {
                return [pscustomobject]@{ ExitCode = 0; StdOut = @(); StdErr = @(); StdOutText = "27.1.1" }
            }
            if ($joined -match "^compose version") {
                return [pscustomobject]@{ ExitCode = 0; StdOut = @(); StdErr = @(); StdOutText = $h.ComposeVersion }
            }
            if ($joined -match "^compose ") {
                if ($joined -match "ps -q frontend") {
                    return [pscustomobject]@{ ExitCode = 0; StdOut = @(); StdErr = @(); StdOutText = "abc123" }
                }
                if ($joined -match "run --rm backup") {
                    New-Item -ItemType Directory -Path (Join-Path $h.StateRoot "backup") -Force | Out-Null
                    $stamp = (Get-Date).ToString("HHmmssfff")
                    Set-Content -Path (Join-Path $h.StateRoot "backup/backup-$stamp.sql.gz") -Value "binary-noise"
                    return [pscustomobject]@{ ExitCode = $h.BackupExit; StdOut = @(); StdErr = @(); StdOutText = $h.BackupOutput }
                }
                if ($joined -match "exec -T backend python -m app.cli pilot-pairing issue") {
                    if ($null -eq $StdIn) { throw "pairing issue must receive STDIN" }
                    if ($StdIn -notmatch '"code"') { throw "pairing STDIN must contain code" }
                    return [pscustomobject]@{ ExitCode = $h.IssueExit; StdOut = @(); StdErr = @(); StdOutText = ([Guid]::NewGuid().ToString()) }
                }
                if ($joined -match "exec -T backend sh -c") {
                    return [pscustomobject]@{ ExitCode = 0; StdOut = @(); StdErr = @(); StdOutText = "false|false" }
                }
                return [pscustomobject]@{ ExitCode = $h.ComposeExit; StdOut = @(); StdErr = @(); StdOutText = "" }
            }
            if ($joined -match "^(--version|info)") {
                return [pscustomobject]@{ ExitCode = 0; StdOut = @(); StdErr = @(); StdOutText = "ok" }
            }
            return [pscustomobject]@{ ExitCode = 0; StdOut = @(); StdErr = @(); StdOutText = "" }
        }
        return $call
    }

    $http = {
        param($Url)
        if ($Url -match "/api/health") {
            return [pscustomobject]@{ StatusCode = 200; Json = [pscustomobject]@{ status = "ok" } }
        }
        if ($Url -match "/api/setup/first-run/state") {
            $state = [pscustomobject]$h.FirstRun
            if ($h.Http -eq "claimed") { $state.pilot_owner_exists = $true }
            if ($h.Http -eq "pending") { $state.pending = $true; $state.pending_work_role = "hr" }
            return [pscustomobject]@{ StatusCode = 200; Json = $state }
        }
        if ($Url -match "/api/ops/status") {
            if ($h.Http -eq "ops-down") { return [pscustomobject]@{ StatusCode = 503; Json = $null } }
            return [pscustomobject]@{ StatusCode = 200; Json = [pscustomobject]$h.Ops }
        }
        return [pscustomobject]@{ StatusCode = 0; Json = $null }
    }

    $script:HrmTestHooks["ToolOverride"] = $tool
    $script:HrmTestHooks["HttpOverride"] = $http

    return [pscustomobject]@{
        Root = $root; StateRoot = $stateRoot; AppDir = $appDir; Hooks = $h
    }
}

function script:Remove-HrmTestEnv {
    param([Parameter(Mandatory)][string]$Root)
    $script:HrmTestHooks = @{}
    Remove-Item Env:HRMGR_TEST_STATE_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:LOCALAPPDATA -ErrorAction SilentlyContinue
    if ($Root -and (Test-Path $Root)) { Remove-Item $Root -Recurse -Force -ErrorAction SilentlyContinue }
}

function script:Find-HrmCallFrom {
    <# Как Find-HrmCall, но поиск начиная с индекса $From (например, только
       вызовы, сделанные внутри тестируемого действия). #>
    param($Hooks, [int]$From, [string[]]$Contains)
    for ($i = $From; $i -lt $Hooks.Calls.Count; $i++) {
        $joined = $Hooks.Calls[$i].Args -join " "
        $all = $true
        foreach ($c in $Contains) { if ($joined -notlike "*$c*") { $all = $false; break } }
        if ($all) { return $i }
    }
    return -1
}

function script:Join-HrmCalls {
    param($Hooks)
    return (@($Hooks.Calls | ForEach-Object { "@ " + $_.File + " " + ($_.Args -join " ") }) -join "`n")
}

function script:Find-HrmCall {
    <# Индекс первой команды, чьи аргументы содержат все подстроки. #>
    param($Hooks, [string[]]$Contains)
    $i = -1
    foreach ($call in $Hooks.Calls) {
        $i++
        $joined = $call.Args -join " "
        $all = $true
        foreach ($c in $Contains) { if ($joined -notlike "*$c*") { $all = $false; break } }
        if ($all) { return $i }
    }
    return -1
}
