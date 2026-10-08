# HR Manager — снимок предыдущей версии ПЕРЕД обновлением (вызывает мастер установки).
#
# Зачем отдельный скрипт: Setup.exe обязан сохранить прежнюю версию ДО того, как
# перезапишет файлы {app}, и не имеет права полагаться на УСТАНОВЛЕННЫЙ движок —
# старая версия может не знать новых действий и формата снимка. Поэтому мастер
# распаковывает ЭТОТ файл и модуль Snapshot.psm1 из своего же пакета во временный
# каталог ({tmp}) и запускает их. Модуль берётся рядом со скриптом, поэтому
# распаковка может быть «плоской» (оба файла в одном каталоге).
#
# Использование (вызывает installer/installer.iss):
#   powershell -NoProfile -ExecutionPolicy Bypass -File hrm-snapshot.ps1 `
#       -InstallDir "<{app}>" -StateDir "<каталог состояния>" `
#       -ResultFile "<файл результата>"
#
# Коды возврата (их проверяет мастер; любой ненулевой = останов до перезаписи):
#   0 — можно продолжать: снимок сохранён и проверен ЛИБО установки ещё нет;
#   3 — снимок нужен, но не получился (или файлы уже заменены новой версией);
#   1 — непредвиденная ошибка.
#
# Секретов здесь нет: копируется кодовая часть релиза, секреты и лицензионный
# ключ остаются в каталоге состояния и не читаются.

#requires -Version 5.1

[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [string]$StateDir = "",
    # Файл результата для мастера: строки key=value (status/reason/release_sha/…).
    [string]$ResultFile = "",
    # Каталог с модулями движка; по умолчанию — каталог самого скрипта (так
    # работает распаковка мастером), затем infra\windows\engine (запуск из
    # установленного приложения).
    [string]$ModuleDir = ""
)

$ErrorActionPreference = "Stop"

if (-not $ModuleDir) { $ModuleDir = $PSScriptRoot }

function Import-HrmSnapshotModule {
    # Модуль снимка и его зависимости. Порядок важен: Snapshot.psm1 опирается
    # на Common (журнал, JSON, хеши), Secrets (запись установки) и Install
    # (безопасное копирование снимка).
    param([string]$Dir)
    foreach ($name in @("Common", "Secrets", "Install", "Snapshot")) {
        $path = Join-Path $Dir ($name + ".psm1")
        if (-not (Test-Path $path)) {
            $fallback = Join-Path $Dir ("engine\" + $name + ".psm1")
            if (Test-Path $fallback) { $path = $fallback } else { throw ("Не найден модуль движка: " + $path) }
        }
        Import-Module $path -Force -ErrorAction Stop
    }
}

try {
    Import-HrmSnapshotModule -Dir $ModuleDir
    # PackageReleaseDir = каталог самого скрипта: мастер установки распаковывает
    # рядом release.json своего пакета, и по нему CLI отличает «в {app} прежняя
    # версия» от «мастер уже разложил файлы новой версии».
    $exitCode = Invoke-HrmSnapshotCli -InstallDir $InstallDir -StateDir $StateDir -ResultFile $ResultFile -PackageReleaseDir $ModuleDir
    exit $exitCode
}
catch {
    $message = $_.Exception.Message
    if (Get-Command -Name Redact-HrmText -ErrorAction SilentlyContinue) {
        $message = Redact-HrmText $message
    }
    Write-Host ("Снимок предыдущей версии не подготовлен: " + $message)
    if ($ResultFile) {
        if (Get-Command -Name Write-HrmSnapshotResultFile -ErrorAction SilentlyContinue) {
            Write-HrmSnapshotResultFile -Path $ResultFile -Status "failed" -Reason "helper_error" -Message $message
        }
    }
    exit 1
}
