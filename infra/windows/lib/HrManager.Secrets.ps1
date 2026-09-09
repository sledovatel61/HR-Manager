# ===========================================================================
# Секреты и локальное состояние пилота (фаза 12).
#
# Правила (контракт): генерируются один раз, хранятся в единственном каталоге
# состояния, защищены ACL текущего пользователя, никогда не печатаются, не
# ротируются при повторном запуске и не попадают ни в git, ни в аргументы,
# ни в диагностику. Никакого внешнего secret-manager — локальный пилот.
# ===========================================================================

function script:New-HrmBackupKeyBase64 {
    $buf = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($buf) } finally { $rng.Dispose() }
    return [Convert]::ToBase64String($buf)
}

function script:Ensure-HrmSecrets {
    <# Возвращает секретный объект. Если secrets.json уже существует —
       используется ОН (без самовольной ротации ключей и паролей — иначе
       будут потеряны backup и сессии). Новый создаётся ровно один раз. #>
    param([Parameter(Mandatory)][string]$StateRoot)
    $path = Join-Path $StateRoot "secrets.json"
    if (Test-Path $path) {
        $existing = Read-HrmJson $path
        foreach ($field in @("secretKey", "postgresPassword", "backupEncKey", "backupKeyId")) {
            if (-not $existing.$field) {
                throw (New-HrmError "Хранилище локальных секретов повреждено: нет поля '$field'." `
                        "Восстановьте каталог состояния из резервной копии; ротация вручную не требуется." 5)
            }
        }
        return $existing
    }
    # Набор генерируется ровно один раз криптографическим RNG:
    # signing key, пароль БД, ключ шифрования backup (base64 32 байта) + id.
    $secrets = [pscustomobject]@{
        secretKey        = (New-HrmRandomHex 32)   # 64 hex символа
        postgresPassword = (New-HrmRandomHex 16)   # пароль БД (используется в URL)
        backupKeyId      = "pilot-" + (New-HrmRandomHex 4)
        backupEncKey     = (New-HrmBackupKeyBase64)
    }
    Write-HrmPrivateFile -Path $path -Content ($secrets | ConvertTo-Json -Compress)
    return $secrets
}

function script:Write-HrmPilotEnv {
    <# pilot.env — входные данные docker compose (только для движка). Файл
       создаётся с ACL «только мой пользователь». Значения никогда не
       логируются. #>
    param(
        [Parameter(Mandatory)][string]$StateRoot,
        [Parameter(Mandatory)]$Secrets,
        [Parameter(Mandatory)]$Config
    )
    $lines = @(
        "# HR Manager pilot environment — локальные секреты. Не коммитить."
        "SECRET_KEY=$($Secrets.secretKey)"
        "POSTGRES_PASSWORD=$($Secrets.postgresPassword)"
        "BACKUP_KEY_ID=$($Secrets.backupKeyId)"
        "BACKUP_ENC_KEY=$($Secrets.backupEncKey)"
        "HRMGR_PILOT_PORT=$($Config.port)"
        "HRMGR_RELEASE_VERSION=$($Config.releaseVersion)"
        "RELEASE_SHA=$($Config.releaseSha)"
    )
    $content = ($lines -join [Environment]::NewLine) + [Environment]::NewLine
    Write-HrmPrivateFile -Path (Join-Path $StateRoot "pilot.env") -Content $content
}

function script:New-HrmInstallConfig {
    param(
        [Parameter(Mandatory)][string]$ReleaseDir,
        [int]$Port = 8081,
        [string]$WorkRole = "",
        [switch]$TestMode
    )
    $manifest = Get-HrmReleaseManifest $ReleaseDir
    if (-not $manifest) {
        throw (New-HrmError "В каталоге релиза нет release-manifest.json — установка из неполного комплекта запрещена." "Перезапустите Setup.exe из официального установочного файла." 5)
    }
    $installId = (New-HrmRandomHex 8)
    $config = [ordered]@{
        profile          = "hr-manager-pilot"
        installId        = $installId
        port             = $Port
        composeProject   = $script:HrmProfileName
        releaseVersion   = $manifest.version
        releaseSha       = $manifest.releaseSha
        releaseDir       = $ReleaseDir
        workRole         = $WorkRole
        ownerClaimed     = $false
        installedAt      = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        testMode         = [bool]$TestMode
    }
    return New-Object psobject -Property $config
}
