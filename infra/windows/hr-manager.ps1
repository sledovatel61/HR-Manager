# HR Manager — локальный пилот для Windows (phase 12).
#
# Единая точка входа автоматизации на одной машине Windows 10/11 x64:
# установка, запуск, остановка, статус, обновление, диагностика, удаление.
# Никаких команд Docker/PostgreSQL/Alembic вручную — всё делает этот движок.
#
# СЕКРЕТЫ НИКОГДА НЕ ПЕРЕДАЮТСЯ В КОМАНДНУЮ СТРОКУ ПРОЦЕССА:
#   - пароль PostgreSQL, ключ подписи сессий, пароль bootstrap-администратора,
#     ключ шифрования бэкапов и одноразовый токен первого запуска хранятся
#     ТОЛЬКО в защищённом каталоге состояния (ACL: только текущий пользователь)
#     и попадают в контейнеры через `docker compose --env-file <state>\pilot.env`;
#   - фамилия/роль/часовой пояс первого запуска передаются из мастера
#     установки через защищённый файл ввода, а не через аргументы процесса;
#   - вывод, журналы и диагностика всегда проходят редакцию секретов.
#
# ИСПОЛЬЗОВАНИЕ:
#   powershell -ExecutionPolicy Bypass -File hr-manager.ps1 -Action install
#   powershell -File hr-manager.ps1 -Action start | stop | status | open
#   powershell -File hr-manager.ps1 -Action update -ReleaseDir D:\hr-manager-1.1.0
#   powershell -File hr-manager.ps1 -Action diagnostics [-Json]
#   powershell -File hr-manager.ps1 -Action uninstall [-PurgeData]
#   powershell -File hr-manager.ps1 -Action resume
#
# НЕИНТЕРАКТИВНЫЙ РЕЖИМ (для тестов и автоматизации; документирован и
# мокабелен — см. infra/windows/tests и README):
#   $env:HRM_NONINTERACTIVE = "1"; $env:HRM_STATE_DIR = "C:\tmp\hrm-state"
#   $env:HRM_INSTALL_DIR = "C:\tmp\hrm-install"
#   powershell -File hr-manager.ps1 -Action diagnostics
# Все внешние вызовы идут через Invoke-HrmExternal (engine\Common.psm1) —
# в Pester-тестах он мокается, реальная машина при этом не трогается.
# В неинтерактивном режиме браузер не открывается; одноразовая ссылка
# первого запуска записывается в защищённый файл, а не в вывод.

#requires -Version 5.1

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("install", "start", "stop", "status", "open", "update",
        "uninstall", "diagnostics", "resume", "channel", "channel-config",
        "support-bundle", "lan-access", "restart", "help",
        "prepare", "docker-status", "docker-install", "docker-start",
        "supervise", "tray", "autostart", "update-preview", "snapshot-previous")]
    [string]$Action = "help",

    # Обновление: доверенный каталог релиза (trust boundary — см. README).
    [string]$ReleaseDir,

    # install: откуда копировать снимок приложения (по умолчанию — каталог
    # самого скрипта). Установщик Inno Setup раскладывает файлы сам и
    # запускает install с -SourceDir равным каталогу установки.
    [string]$SourceDir,

    # Куда устанавливать (по умолчанию %LOCALAPPDATA%\Programs\HRManager).
    [string]$InstallDir,

    # Каталог состояния/секретов (по умолчанию %LOCALAPPDATA%\HRManager).
    [string]$StateDir,

    # Порт публикации фронтенда на 127.0.0.1 (по умолчанию 8080).
    [int]$Port = 0,

    # Неинтерактивно: никаких запросов и окон (см. выше).
    [switch]$NonInteractive,

    # Неинтерактивно + открыть браузер после первого запуска (установщик).
    [switch]$OpenBrowser,

    # uninstall: удалить данные Postgres после backup gate; бэкапы сохраняются.
    [switch]$PurgeData,

    # diagnostics: вывод в формате JSON.
    [switch]$Json,

    # channel: один цикл наблюдателя; -Watch запускает блокирующий цикл.
    [switch]$Watch,

    # channel-config: смена URL канала (-SetUrl) или набора доверенных ключей
    # (-KeysJson <файл JSON {kid:{key,revoked}}>) — ротация/отзыв ключей.
    [string]$SetUrl,
    [string]$KeysJson,

    # lan-access: -Enable включает публикацию на LAN, -Disable выключает
    [switch]$Enable,
    [switch]$Disable,

    # docker-install / install / resume: разрешить установку Docker Desktop
    # официальным установщиком Docker (UAC и лицензию принимает человек).
    [switch]$InstallDocker,

    # autostart: -Enable включает автозапуск, -Disable выключает.
    # prepare/supervise: -Interactive разрешает диалоги/UAC/установку Docker.
    [switch]$Interactive
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$script:EngineDir = Join-Path $PSScriptRoot "engine"
# Владеет ли этот процесс отметкой установки (setup-run.json): снимаем её
# только при успешном завершении установки/обновления.
$script:SetupMarkerOwner = $false
foreach ($module in @("Common", "Secrets", "Preflight", "Compose", "Bootstrap", "Update",
        "Diagnostics", "Install", "Crypto", "Channel", "Lan", "SupportBundle",
        "Docker", "Supervisor", "Tray")) {
    Import-Module (Join-Path $script:EngineDir "$module.psm1") -Force -ErrorAction Stop
}

if ($env:HRM_NONINTERACTIVE -eq "1") { $global:HrmNonInteractive = $true } else { $global:HrmNonInteractive = [bool]$NonInteractive }
$global:HrmOpenBrowser = [bool]$OpenBrowser

function Show-HrmUsage {
    $lines = @(
        "HR Manager — локальный пилот Windows (phase 12)",
        "",
        "Действия:",
        "  install        Установка/восстановление: проверки, секреты, образы, первый запуск",
        "  start          Запустить приложение (http://127.0.0.1:<port>)",
        "  stop           Остановить приложение (данные и бэкапы сохраняются)",
        "  status         Состояние: контейнеры, готовность, версия",
        "  open           Открыть приложение в браузере",
        "  update         Обновление из доверенного каталога релиза (-ReleaseDir)",
        "  diagnostics    Диагностика (редакция секретов; -Json) + хост-отчёт для readiness",
        "  uninstall      Удалить приложение (данные сохраняются; -PurgeData удаляет)",
        "  resume         Продолжить прерванную операцию (после перезагрузки/UAC)",
        "  channel        Цикл канала обновлений (-Watch — блокирующий наблюдатель)",
        "  channel-config Правка канала: -SetUrl <https>, -KeysJson <файл ключей>",
        "  support-bundle Создать архив диагностики на рабочем столе",
        "  lan-access     Доступ по локальной сети: -Enable / -Disable (без флагов — показать адрес)",
        "  restart        Перезапуск приложения (stop + start)",
        "  prepare        Подготовить рабочую среду (Docker, WSL2, место, порт) и показать состояние",
        "  docker-status  Состояние Docker Desktop, WSL2, виртуализации, места, порта и прав",
        "  docker-install Установить Docker Desktop официальным установщиком (-Interactive)",
        "  docker-start   Запустить Docker Desktop и дождаться готовности Docker Engine",
        "  supervise      Один цикл управляющего компонента: среда → приложение → готовность",
        "  tray           Показать значок HR Manager в системном трее",
        "  autostart      Автозапуск после входа в Windows: -Enable / -Disable",
        "  update-preview Показать текущую/новую версию и проверки перед обновлением",
        "  snapshot-previous Сохранить снимок установленной версии перед обновлением (вызывает мастер установки)",
        "",
        "Параметры: -SourceDir, -InstallDir, -StateDir, -Port, -NonInteractive, -OpenBrowser",
        "           -ReleaseDir, -Watch, -SetUrl, -KeysJson, -Enable, -Disable",
        "Полная документация: infra/windows/README.md"
    )
    $lines | ForEach-Object { Write-Output $_ }
}

try {
    switch ($Action) {
        "help" { Show-HrmUsage }
        "install" {
            # Отметка «идёт установка/обновление»: значок в трее, запущенный
            # мастером установки, показывает ход и не выходит с ошибкой
            # «HR Manager не установлен» (дефект P2 ревью).
            if (-not $StateDir) { $StateDir = Get-HrmStateDir }
            $isUpdate = ($null -ne (Get-HrmInstallRecord $StateDir))
            $setupMessage = if ($isUpdate) { "Обновляем HR Manager…" } else { "Устанавливаем HR Manager…" }
            $null = Set-HrmSetupMarker -StateDir $StateDir -Status "running" -Message $setupMessage
            $script:SetupMarkerOwner = $true
            Set-HrmSupervisorState -StateDir $StateDir -State "starting" -Message "Подготавливаем рабочую среду…" -Busy $true
            Install-HrmApp -SourceDir $SourceDir -InstallDir $InstallDir -StateDir $StateDir -Port $Port -AllowDockerInstall:($InstallDocker -or $Interactive)
        }
        "start" {
            Start-HrmApp -InstallDir $InstallDir -StateDir $StateDir
        }
        "stop" {
            Stop-HrmApp -InstallDir $InstallDir -StateDir $StateDir
        }
        "status" {
            Get-HrmAppStatus -InstallDir $InstallDir -StateDir $StateDir
        }
        "open" {
            Open-HrmApp -InstallDir $InstallDir -StateDir $StateDir
        }
        "update" {
            Update-HrmApp -ReleaseDir $ReleaseDir -InstallDir $InstallDir -StateDir $StateDir
        }
        "uninstall" {
            Remove-HrmApp -InstallDir $InstallDir -StateDir $StateDir -PurgeData:$PurgeData.IsPresent
        }
        "diagnostics" {
            # Сначала redacted хост-отчёт для «Проверки готовности пилота»
            # (best-effort, ничего не печатает), затем сама диагностика.
            Send-HrmHostReport -InstallDir $InstallDir -StateDir $StateDir | Out-Null
            Get-HrmDiagnostics -InstallDir $InstallDir -StateDir $StateDir -AsJson:$Json.IsPresent
        }
        "resume" {
            Resume-HrmOperation -InstallDir $InstallDir -StateDir $StateDir
        }
        "snapshot-previous" {
            # Быстрое действие без Docker и без диалогов: сохранить снимок
            # УСТАНОВЛЕННОЙ версии до того, как мастер установки заменит файлы.
            # Вызывает Setup.exe (см. installer/installer.iss, CurStep=ssInstall).
            $snapshotResult = Save-HrmInstalledSnapshotForSetup -InstallDir $InstallDir -StateDir $StateDir
            if ($snapshotResult.saved) {
                Write-HrmLog "info" "Снимок предыдущей версии сохранён для отката."
            }
            else {
                # Это подготовка к обновлению, а не сама установка: молча
                # сообщаем причину и продолжаем (решение принимает движок).
                Write-HrmLog "warn" ("Снимок предыдущей версии не сохранён: " + $snapshotResult.message)
            }
        }
        "channel" {
            if ($Watch) {
                Start-HrmChannelWatch -InstallDir $InstallDir -StateDir $StateDir
            }
            else {
                Invoke-HrmChannelOnce -InstallDir $InstallDir -StateDir $StateDir
            }
        }
        "channel-config" {
            if (-not $SetUrl -and -not $KeysJson) {
                throw "Укажите -SetUrl или -KeysJson (см. help)."
            }
            $keys = $null
            if ($KeysJson) {
                $keysData = Get-HrmJsonFile $KeysJson
                if ($null -eq $keysData) { throw "Не удалось прочитать -KeysJson: $KeysJson" }
                $keys = @{}
                foreach ($prop in $keysData.PSObject.Properties) {
                    $entry = $prop.Value
                    if ($null -eq $entry.key) { throw "В -KeysJson нет key для $($prop.Name)" }
                    $keys[$prop.Name] = @{ key = [string]$entry.key; revoked = [bool]$entry.revoked }
                }
            }
            Set-HrmChannelConfig -StateDir $StateDir -Url $SetUrl -PublicKeys $keys
            Write-HrmLog "info" "Конфигурация канала обновлена."
        }
        "support-bundle" {
            $zip = New-HrmSupportBundle -InstallDir $InstallDir -StateDir $StateDir
            if ($zip) {
                $stateDirResolved = if ($StateDir) { $StateDir } else { Get-HrmStateDir }
                Set-HrmJsonFile $stateDirResolved "last-support-bundle.json" ([ordered]@{
                        path = [string]$zip
                        name = [string](Split-Path $zip -Leaf)
                        created_at = (Get-Date).ToString("o")
                    })
            }
            Write-HrmLog "info" ("Архив диагностики создан: {0}" -f $zip)
        }
        "lan-access" {
            Invoke-HrmLanAccess -InstallDir $InstallDir -StateDir $StateDir -Enable:$Enable.IsPresent -Disable:$Disable.IsPresent
        }
        "restart" {
            Write-HrmLog "info" "Перезапуск приложения..."
            Stop-HrmApp -InstallDir $InstallDir -StateDir $StateDir
            Start-HrmApp -InstallDir $InstallDir -StateDir $StateDir
            Write-HrmLog "info" "Перезапуск завершён."
        }
        "prepare" {
            $prepare = Invoke-HrmDockerPrepare -InstallDir $InstallDir -StateDir $StateDir -Port $Port -AllowInstall:($InstallDocker -or $Interactive) -Interactive:$Interactive
            foreach ($line in (Format-HrmDockerReadiness -InstallDir $InstallDir -StateDir $StateDir -Port $Port)) { Write-Output $line }
            if (-not $prepare.ok) {
                Set-HrmSupervisorState -StateDir $StateDir -State "error" -Message $prepare.message
                throw $prepare.message
            }
            Write-HrmLog "info" "Рабочая среда готова."
        }
        "docker-status" {
            foreach ($line in (Format-HrmDockerReadiness -InstallDir $InstallDir -StateDir $StateDir -Port $Port)) { Write-Output $line }
        }
        "docker-install" {
            $result = Install-HrmDockerDesktop -StateDir $StateDir -Interactive:($Interactive -or (Test-HrmInteractive)) -AllowSilent
            Write-Output $result.message
            if ($result.status -eq "reboot_required") { exit 2 }
            if ($result.status -eq "uac_declined" -or $result.status -eq "failed" -or $result.status -eq "manual_required") { exit 1 }
        }
        "docker-start" {
            $result = Start-HrmDockerDesktop
            Write-Output $result.message
            if (-not $result.started) { exit 1 }
        }
        "supervise" {
            $result = Invoke-HrmSupervisorCycle -InstallDir $InstallDir -StateDir $StateDir -Port $Port -AllowInstall:($InstallDocker -or $Interactive) -Interactive:$Interactive
            Write-Output $result.message
            if (-not $result.ok) { exit 1 }
        }
        "tray" {
            Start-HrmSupervisor -InstallDir $InstallDir -StateDir $StateDir | Out-Null
            Write-Output "HR Manager работает в системном трее."
        }
        "autostart" {
            if ($Enable.IsPresent -and $Disable.IsPresent) { throw "Укажите только -Enable или -Disable." }
            if ($Disable.IsPresent) {
                $state = Disable-HrmAutostart -StateDir $StateDir
                Write-Output "Автозапуск выключен."
                return
            }
            if ($Enable.IsPresent) {
                $state = Enable-HrmAutostart -InstallDir $InstallDir -StateDir $StateDir
                Write-Output "Автозапуск включён: HR Manager появится в трее после входа в Windows."
                return
            }
            $state = Get-HrmAutostartState -StateDir $StateDir
            if ($state.enabled) { Write-Output "Автозапуск включён." } else { Write-Output "Автозапуск выключен." }
        }
        "update-preview" {
            if (-not $ReleaseDir) { throw "Укажите -ReleaseDir (каталог новой версии)." }
            $preview = Get-HrmUpdatePreview -ReleaseDir $ReleaseDir -InstallDir $InstallDir -StateDir $StateDir
            foreach ($line in (Format-HrmUpdatePreview $preview)) { Write-Output $line }
        }
    }
    if ($script:SetupMarkerOwner) {
        # Установка/обновление завершились успешно: отметка больше не нужна,
        # значок в трее переходит в обычный режим.
        $markerStateDir = $StateDir
        if (-not $markerStateDir) { $markerStateDir = Get-HrmStateDir }
        Clear-HrmSetupMarker -StateDir $markerStateDir
    }
    exit 0
}
catch {
    $message = Redact-HrmText ($_.Exception.Message)
    Write-Host "ОШИБКА: $message" -ForegroundColor Red
    Write-HrmLog "error" ("fatal: " + $message)
    try {
        # Значок в трее должен остаться и показать понятную причину: отметка
        # установки переводится в failed. Если движок успел записать результат
        # обновления («прежняя версия восстановлена и отвечает»), показываем
        # именно его — это текст для человека, а не техническая ошибка.
        $stateDirForError = if ($StateDir) { $StateDir } else { Get-HrmStateDir }
        $markerMessage = $message
        try {
            $updateResult = Get-HrmUpdateResult $stateDirForError
            if ($null -ne $updateResult -and $updateResult.message) { $markerMessage = [string]$updateResult.message }
        }
        catch { }
        Set-HrmSupervisorState -StateDir $stateDirForError -State "error" -Message $markerMessage
        $null = Set-HrmSetupMarker -StateDir $stateDirForError -Status "failed" -Message $markerMessage
    }
    catch { }
    exit 1
}
