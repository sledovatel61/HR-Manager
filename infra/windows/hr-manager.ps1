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
        "uninstall", "diagnostics", "resume", "channel", "channel-config", "help")]
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

    # uninstall: удалить данные Postgres и бэкапы (требует фразу-подтверждение).
    [switch]$PurgeData,

    # diagnostics: вывод в формате JSON.
    [switch]$Json,

    # channel: один цикл наблюдателя; -Watch запускает блокирующий цикл.
    [switch]$Watch,

    # channel-config: смена URL канала (-SetUrl) или набора доверенных ключей
    # (-KeysJson <файл JSON {kid:{key,revoked}}>) — ротация/отзыв ключей.
    [string]$SetUrl,
    [string]$KeysJson
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$script:EngineDir = Join-Path $PSScriptRoot "engine"
foreach ($module in @("Common", "Secrets", "Preflight", "Compose", "Bootstrap", "Update", "Diagnostics", "Install", "Crypto", "Channel")) {
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
        "  diagnostics    Агрегированная диагностика (с редакцией секретов; -Json)",
        "  uninstall      Удалить приложение (данные сохраняются; -PurgeData удаляет)",
        "  resume         Продолжить прерванную операцию (после перезагрузки/UAC)",
        "  channel        Цикл канала обновлений (-Watch — блокирующий наблюдатель)",
        "  channel-config Правка канала: -SetUrl <https>, -KeysJson <файл ключей>",
        "",
        "Параметры: -SourceDir, -InstallDir, -StateDir, -Port, -NonInteractive, -OpenBrowser",
        "           -ReleaseDir, -Watch, -SetUrl, -KeysJson",
        "Полная документация: infra/windows/README.md"
    )
    $lines | ForEach-Object { Write-Output $_ }
}

try {
    switch ($Action) {
        "help" { Show-HrmUsage }
        "install" {
            Install-HrmApp -SourceDir $SourceDir -InstallDir $InstallDir -StateDir $StateDir -Port $Port
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
            Get-HrmDiagnostics -InstallDir $InstallDir -StateDir $StateDir -AsJson:$Json.IsPresent
        }
        "resume" {
            Resume-HrmOperation -InstallDir $InstallDir -StateDir $StateDir
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
    }
    exit 0
}
catch {
    $message = Redact-HrmText ($_.Exception.Message)
    Write-Host "ОШИБКА: $message" -ForegroundColor Red
    Write-HrmLog "error" ("fatal: " + $message)
    exit 1
}
