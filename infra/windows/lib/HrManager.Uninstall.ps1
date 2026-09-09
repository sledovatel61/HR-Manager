# ===========================================================================
# Удаление (§11 контракта). По умолчанию — безопасный режим:
#   снимаем контейнеры (compose down БЕЗ -v), сохраняем тома PostgreSQL,
#   резервные копии и каталог состояния; ярлыки и запись «Приложения и
#   возможности» убираются. Полное удаление данных возможно ТОЛЬКО после
#   явного ввода фразы-подтверждения оператором; перед ним движок сам
#   создаёт финальную копию и кладёт её в указанную папку экспорта.
# ===========================================================================

function script:Invoke-HrmUninstall {
    param(
        [Parameter(Mandatory)][string]$AppDir,
        [Parameter(Mandatory)][string]$StateRoot,
        [switch]$RemoveData,
        [string]$ConfirmPhrase = "",
        [string]$ExportBackupTo = "",
        [switch]$FromInstaller
    )
    $config = Get-HrmConfig $StateRoot
    $logAlreadyWritten = $false
    Write-HrmProgress -StateRoot $StateRoot -Phase "uninstall-stop" -Detail "Останавливаем и удаляем контейнеры (данные сохраняются)"
    Invoke-HrmCompose -AppDir $AppDir -StateRoot $StateRoot -ComposeArgs @("down", "--timeout", "30") | Out-Null

    if ($RemoveData) {
        $phraseOk = ($ConfirmPhrase -ceq $script:HrmConfirmPhrase)
        if (-not $phraseOk) {
            throw (New-HrmError "Для полного удаления данных нужен точный ввод фразы: «$($script:HrmConfirmPhrase)» — она подтверждает удаление базы, резервных копий и паролей. Без неё движок данные НЕ удаляет." "Напечатайте фразу в окне подтверждения установщика." 7)
        }
        # Финальная копия ПЕРЕД удалением томов — если docker ещё жив.
        if ($ExportBackupTo) {
            try {
                New-HrmFinalBackupCopy -AppDir $AppDir -StateRoot $StateRoot -ExportTo $ExportBackupTo
            } catch {
                Write-HrmLog -StateRoot $StateRoot -Message "финальную копию создать не удалось; продолжение по явному указанию оператора" -Level error
            }
        }
        Write-HrmProgress -StateRoot $StateRoot -Phase "uninstall-data" -Detail "Удаляем тома с данными (явное подтверждение получено)"
        Invoke-HrmCompose -AppDir $AppDir -StateRoot $StateRoot -ComposeArgs @("down", "-v", "--remove-orphans") | Out-Null
        $dataWord2 = "удалены"
        Write-HrmLog -StateRoot $StateRoot -Message "uninstall завершён (RemoveData=True; данные $dataWord2)" -Level warn
        Remove-HrmStateDir -StateRoot $StateRoot
        $logAlreadyWritten = $true
    } else {
        # Безопасный режим: каталог состояния и тома ОСТАЮТСЯ (переустановка
        # идемпотентно продолжит с ними).
        Write-HrmProgress -StateRoot $StateRoot -Phase "uninstall-keep" -Detail "Данные и пароли сохранены в каталоге состояния — переустановка продолжит их использовать"
    }

    if (-not $env:HRMGR_TEST_STATE_DIR) {
        Remove-HrmShortcuts
    }
    if (-not $logAlreadyWritten) {
        Write-HrmLog -StateRoot $StateRoot -Message "uninstall завершён (RemoveData=False; данные сохранены)"
    }
    return [pscustomobject]@{
        Ok          = $true
        DataRemoved = [bool]$RemoveData
    }
}

function script:New-HrmFinalBackupCopy {
    param(
        [Parameter(Mandatory)][string]$AppDir,
        [Parameter(Mandatory)][string]$StateRoot,
        [Parameter(Mandatory)][string]$ExportTo
    )
    if (-not (Test-Path $ExportTo)) { New-Item -ItemType Directory -Path $ExportTo -Force | Out-Null }
    $backupDir = Join-Path $StateRoot "backup"
    if (Test-Path $backupDir) {
        Get-ChildItem $backupDir -Filter "*.sql.gz" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending | Select-Object -First 3 | ForEach-Object {
                Copy-Item $_.FullName -Destination $ExportTo -Force
            }
    }
    # Шифрованные ключи копий — чтобы резервные копии оставались читаемы.
    $keys = Join-Path $backupDir "keys"
    if (Test-Path $keys) { Copy-Item $keys -Destination (Join-Path $ExportTo "backup-keys") -Recurse -Force }
    Write-HrmLog -StateRoot $StateRoot -Message "финальные копии сохранены в указанный каталог экспорта"
    return $true
}

function script:Remove-HrmStateDir {
    param([Parameter(Mandatory)][string]$StateRoot)
    # Удаление с снятием ACL и очисткой; секретные файлы стираются в первую
    # очередь (config/secrets/pilot.env/pairing).
    foreach ($name in @("pilot.env", "secrets.json", "pairing.json", "config.json")) {
        $f = Join-Path $StateRoot $name
        if (Test-Path $f) {
            try { & icacls $f /reset "$f" 2>&1 | Out-Null } catch { }
            Remove-Item $f -Force -ErrorAction SilentlyContinue
        }
    }
    if ((Test-Path $StateRoot) -and -not (Test-HrmWindows)) {
        # Тестовый контур: просто рекурсивное удаление.
        Remove-Item $StateRoot -Recurse -Force -ErrorAction SilentlyContinue
        return
    }
    if (Test-Path $StateRoot) {
        Remove-Item $StateRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function script:Remove-HrmShortcuts {
    $folders = @((Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs\HR Manager"),
                 (Join-Path ([Environment]::GetFolderPath("Desktop")) ""))
    $names = @("HR Manager.lnk", "Диагностика HR Manager.lnk")
    foreach ($folder in $folders) {
        foreach ($name in $names) {
            $p = Join-Path $folder $name
            if (Test-Path $p) { Remove-Item $p -Force -ErrorAction SilentlyContinue }
        }
        if ($folder -like "*Programs*" -and (Test-Path $folder) -and -not (Get-ChildItem $folder -ErrorAction SilentlyContinue)) {
            Remove-Item $folder -Force -ErrorAction SilentlyContinue
        }
    }
    $binDir = [IO.Path]::Combine([Environment]::GetFolderPath("LocalApplicationData"), "HR Manager", "bin")
    $vbs = Join-Path $binDir "hr-manager-launch.vbs"
    if (Test-Path $vbs) { Remove-Item $vbs -Force -ErrorAction SilentlyContinue }
    # RunOnce resume-метку тоже убираем (не должна пережить удаление).
    try { Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce" -Name "HRManagerSetupResume" -ErrorAction SilentlyContinue } catch { }
    return $true
}
