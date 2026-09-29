# Локальная сеть пилота — публикация порта на LAN и правило Firewall (B5).
#
# Действие hr-manager.ps1 -Action lan-access [-Enable|-Disable] (по умолчанию выключено):
#   - включает публикацию frontend на 0.0.0.0 вместо 127.0.0.1,
#   - добавляет правило Windows Firewall только для профилей Private/Domain,
#   - выключатель убирает правило и возвращает 127.0.0.1.
# Настройка переживает обновление (StateDir/lan.json).
#
# Безопасность: loopback-only эндпоинты (/api/setup/*, owner claim и т.п.)
# остаются недоступными по сети. Nginx перезаписывает X-Real-IP на
# $remote_addr, бэкенд проверяет loopback, клиент из сети не может
# подделать X-Real-IP (проверено тестами).
# Отсутствие HTTPS в офисной сети задокументировано как принятый риск пилота.

Set-StrictMode -Version 2.0

$script:LanConfigFile = "lan.json"
$script:FirewallRuleName = "HR Manager Pilot (LAN)"

function Get-HrmLanConfigFile {
    param([string]$StateDir)
    return (Join-Path $StateDir $script:LanConfigFile)
}

function Get-HrmLanConfig {
    param([string]$StateDir)
    $file = Get-HrmLanConfigFile $StateDir
    if (Test-Path $file) {
        try {
            $data = Get-HrmJsonFile $file
            if ($null -ne $data) {
                $enabled = $false
                if ($data.PSObject.Properties["enabled"]) { $enabled = [bool]$data.enabled }
                $rawBind = if ($data.PSObject.Properties["bind"] -and $data.bind) { [string]$data.bind } else { $null }
                $bind = "127.0.0.1"
                if ($enabled) {
                    if ($null -eq $rawBind -or $rawBind -eq "0.0.0.0" -or $rawBind -eq "127.0.0.1") {
                        # legacy 127.0.0.1 with enabled true -> 0.0.0.0
                        $bind = "0.0.0.0"
                    } else {
                        # arbitrary/invalid -> safe fallback to loopback
                        $enabled = $false
                        $bind = "127.0.0.1"
                    }
                } else {
                    $bind = "127.0.0.1"
                }
                return [pscustomobject]@{ enabled = $enabled; bind = $bind }
            }
        } catch {}
    }
    return [pscustomobject]@{ enabled = $false; bind = "127.0.0.1" }
}

function Set-HrmLanConfig {
    param([string]$StateDir, [bool]$Enable)
    $bind = if ($Enable) { "0.0.0.0" } else { "127.0.0.1" }
    $data = [ordered]@{ enabled = $Enable; bind = $bind; updated_at = (Get-Date).ToString("o") }
    Set-HrmJsonFile $StateDir $script:LanConfigFile $data
    return $data
}

function Get-HrmPilotBind {
    param([string]$StateDir)
    $cfg = Get-HrmLanConfig $StateDir
    return $cfg.bind
}

function Test-HrmFirewallRuleExists {
    # Проверка наличия правила (best-effort). Мокабельно через Invoke-HrmExternal.
    try {
        $result = Invoke-HrmExternal -Name "netsh.exe" -Arguments @("advfirewall", "firewall", "show", "rule", ("name=`"{0}`"" -f $script:FirewallRuleName)) -IgnoreExitCode
        return ($result.Stdout -match [regex]::Escape($script:FirewallRuleName))
    } catch { return $false }
}

function Add-HrmFirewallRule {
    # Добавляет правило только для Private/Domain, порт из записи установки.
    # Требует прав администратора: при отсутствии прав netsh вернёт ошибку,
    # пользователь увидит UAC при повторном запуске из-под админа (один запрос).
    param([string]$StateDir)
    $record = Get-HrmInstallRecord $StateDir
    $port = 8080
    if ($null -ne $record -and $record.port) { $port = [int]$record.port }
    $ruleArgs = @("advfirewall", "firewall", "add", "rule", ("name=`"{0}`"" -f $script:FirewallRuleName), "dir=in", "action=allow", "protocol=TCP", ("localport={0}" -f $port), "profile=private,domain", "enable=yes")
    $result = Invoke-HrmExternal -Name "netsh.exe" -Arguments $ruleArgs -IgnoreExitCode
    if ($result.ExitCode -ne 0) {
        $null = Write-HrmLog "warn" "Правило Firewall не добавлено (требуются права администратора). Доступ по сети может быть заблокирован Firewall — запустите 'lan-access -Enable' из-под администратора."
    } else {
        $null = Write-HrmLog "info" "Правило Firewall добавлено: $script:FirewallRuleName (порт $port, Private/Domain)."
    }
}

function Remove-HrmFirewallRule {
    $ruleArgs = @("advfirewall", "firewall", "delete", "rule", ("name=`"{0}`"" -f $script:FirewallRuleName))
    $result = Invoke-HrmExternal -Name "netsh.exe" -Arguments $ruleArgs -IgnoreExitCode
    if ($result.ExitCode -eq 0) {
        $null = Write-HrmLog "info" "Правило Firewall удалено: $script:FirewallRuleName."
    } else {
        $null = Write-HrmLog "warn" "Правило Firewall не удалено (требуются права администратора)."
    }
}

function Get-HrmLanAddresses {
    # Возвращает hostname и список IPv4 для отображения коллеге.
    param([int]$Port = 0)
    if ($Port -eq 0) { $Port = Get-HrmPort }
    $hostname = $env:COMPUTERNAME
    if (-not $hostname) { try { $hostname = [System.Net.Dns]::GetHostName() } catch { $hostname = "PC" } }
    $ips = @()
    try {
        $addrs = [System.Net.Dns]::GetHostAddresses($hostname) | Where-Object { $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and $_.IPAddressToString -ne "127.0.0.1" }
        foreach ($a in $addrs) { $ips += $a.IPAddressToString }
    } catch {}
    if ($ips.Count -eq 0) {
        try {
            $ips = @((Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -ne "127.0.0.1" -and $_.PrefixOrigin -ne "WellKnown" } | Select-Object -ExpandProperty IPAddress))
        } catch {}
    }
    return @{ hostname = $hostname; ips = $ips; port = $Port }
}

function Invoke-HrmLanAccess {
    param(
        [string]$InstallDir = "",
        [string]$StateDir = "",
        [switch]$Enable,
        [switch]$Disable
    )
    if (-not $InstallDir) { $InstallDir = Get-HrmDefaultInstallDir }
    if (-not $StateDir) { $StateDir = Get-HrmStateDir }
    $record = Get-HrmInstallRecord $StateDir
    if ($null -eq $record) { throw "Установка не найдена. Выполните -Action install." }
    $port = if ($record.port) { [int]$record.port } else { Get-HrmPort }
    $current = Get-HrmLanConfig $StateDir
    $wantEnable = $null
    if ($Enable.IsPresent -and $Disable.IsPresent) { throw "Укажите только -Enable или -Disable." }
    if ($Enable.IsPresent) { $wantEnable = $true }
    elseif ($Disable.IsPresent) { $wantEnable = $false }
    else {
        # Без флагов — показать статус и адреса
        $addrs = Get-HrmLanAddresses -Port $port
        $status = if ($current.enabled) { "включён (0.0.0.0:$port)" } else { "выключен (только 127.0.0.1:$port)" }
        $lines = @()
        $lines += "Доступ по локальной сети: $status"
        if ($current.enabled) {
            $lines += "Коллега может открыть:"
            $lines += "  http://$($addrs.hostname):$port"
            foreach ($ip in $addrs.ips) { $lines += "  http://$ip`:$port" }
            $lines += "Если страница не открывается — проверьте, что оба ПК в одной сети (Private) и Firewall разрешает порт $port."
            $lines += "Безопасность: страницы /setup/* остаются доступны только с этого ПК (127.0.0.1)."
        } else {
            $lines += "Чтобы включить доступ коллеге на том же Wi-Fi/офисной сети:"
            $lines += "  powershell -File hr-manager.ps1 -Action lan-access -Enable"
        }
        $lines | ForEach-Object { Write-Output $_ }
        return $current
    }
    if ($wantEnable -eq $current.enabled) {
        $null = Write-HrmLog "info" ("Доступ по сети уже {0}." -f $(if ($wantEnable) { "включён" } else { "выключен" }))
    } else {
        $null = Set-HrmLanConfig $StateDir $wantEnable
        # Перезаписать pilot.env с новым биндом и пересоздать контейнеры
        $releaseSha = if ($record.release_sha) { [string]$record.release_sha } else { Get-HrmReleaseSha $InstallDir $StateDir }
        $null = Write-HrmPilotEnv $StateDir $releaseSha $port
        if ($wantEnable) { Add-HrmFirewallRule $StateDir } else { Remove-HrmFirewallRule }
        # Пересоздать frontend с новым биндом
        Invoke-HrmCompose $InstallDir $StateDir @("up", "-d", "--remove-orphans") | Out-Null
        $null = Write-HrmLog "info" ("Доступ по сети {0}." -f $(if ($wantEnable) { "включён (LAN 0.0.0.0:$port)" } else { "выключен (только 127.0.0.1:$port)" }))
    }
    # Показать адреса после изменения (Write-Host — не попадает в pipeline возврата)
    $addrs = Get-HrmLanAddresses -Port $port
    if ($wantEnable) {
        Write-Host ("Теперь коллега может открыть: http://{0}:{1}" -f $addrs.hostname, $port)
        foreach ($ip in $addrs.ips) { Write-Host ("  http://{0}:{1}" -f $ip, $port) }
        Write-Host "Безопасность: /api/setup/* остаётся доступен только с этого ПК."
    } else {
        Write-Host "Доступ по сети выключен, порт снова только на 127.0.0.1."
    }
    return (Get-HrmLanConfig $StateDir)
}
