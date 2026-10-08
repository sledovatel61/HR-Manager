# Аудит издателя официального установщика Docker Desktop (для CI и владельца).
#
# Зачем он нужен: движок разрешает автоматическую установку Docker Desktop только
# если Authenticode-подпись установщика действительна И сертификат принадлежит
# ровно Docker Inc. (см. Test-HrmDockerPublisherSubject в engine\Docker.psm1).
# Subject и отпечатки сертификата нельзя выдумывать — этот скрипт снимает их с
# настоящего файла, скачанного с официального адреса desktop.docker.com, и
# сравнивает с тем, что зафиксировано в движке.
#
# Запуск (Windows PowerShell 5.1, нужен доступ в интернет):
#   powershell -NoProfile -ExecutionPolicy Bypass -File infra\windows\tools\Audit-HrmDockerPublisher.ps1
#
# Результат печатается строками "::notice title=..." — они видны в журнале CI и
# в аннотациях прогона, поэтому аудит годится для доказательства без скачивания
# артефактов. Коды возврата:
#   0 — измерение выполнено (и, если издатель уже зафиксирован, он совпал);
#   1 — измерение выполнено, но подпись/издатель не совпали с зафиксированными
#       (тревога: сертификат Docker сменился или файл подменён);
#   2 — измерить не удалось (нет сети/таймаут): это не «издатель изменился»,
#       поэтому CI такой код считает предупреждением, а не провалом.
#
# Скрипт ничего не устанавливает и не меняет на машине: скачивает установщик во
# временный каталог, читает подпись, печатает отчёт и удаляет файл.

param(
    [string]$WorkDir = "",
    [switch]$KeepInstaller
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

function Split-HashForLog {
    # 64+ hex подряд в журнале выглядит как утечка ключевого материала, поэтому
    # длинные хэши печатаются двумя половинами по 32 символа (то же правило
    # действует в сборке portable-issuer).
    param([string]$Hash)
    if ([string]::IsNullOrEmpty($Hash) -or $Hash.Length -ne 64) { return [string]$Hash }
    return ($Hash.Substring(0, 32) + " " + $Hash.Substring(32))
}

$engineDir = Join-Path (Split-Path $PSScriptRoot -Parent) "engine"
foreach ($module in @("Common", "Secrets", "Preflight", "Compose", "Bootstrap", "Update",
        "Diagnostics", "Install", "Crypto", "Channel", "Lan", "SupportBundle",
        "Docker", "Supervisor", "Tray")) {
    $modulePath = Join-Path $engineDir ("$module.psm1")
    if (-not (Test-Path $modulePath)) { throw "Модуль движка не найден: $modulePath" }
    Import-Module $modulePath -Force -ErrorAction Stop
}

if (-not $WorkDir) {
    $WorkDir = Join-Path ([System.IO.Path]::GetTempPath()) ("HRM-docker-audit-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
}
if (-not (Test-Path $WorkDir)) { New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null }

$installerPath = Join-Path $WorkDir "DockerDesktopInstaller.exe"
$uri = Get-HrmDockerInstallerUrl
$pinnedSubjects = @(Get-HrmDockerPublisherSubjects)
$measurementOnly = ($pinnedSubjects.Count -eq 0)

Write-Host ("Аудит издателя Docker Desktop: {0}" -f $uri)
Write-Host ("Зафиксированных издателей в движке: {0}" -f $pinnedSubjects.Count)
if ($measurementOnly) {
    Write-Host "Список пуст: это измерение (движок сейчас отказывает любому установщику)."
}

$downloadError = ""
try {
    $null = Save-HrmDownload -Uri $uri -Destination $installerPath
} catch {
    $downloadError = $_.Exception.Message
}

if ($downloadError) {
    Write-Host ("::warning title=docker publisher audit::не удалось скачать официальный установщик: " + $downloadError)
    Write-Host "::notice title=docker publisher audit::status=not_measured (нет сети или таймаут; это не смена издателя)"
    exit 2
}

$fileInfo = Get-Item $installerPath
$fileHash = (Get-FileHash -Path $installerPath -Algorithm SHA256).Hash.ToUpperInvariant()
Write-Host ("Скачано: {0:N0} байт, SHA256 {1}" -f $fileInfo.Length, (Split-HashForLog $fileHash))

# Основное решение берём не «рядом с движком», а из самого движка: та же функция,
# которую вызовет установка на машине пилота.
$verdict = Test-HrmDockerInstallerTrusted -Path $installerPath
$signature = $null
try { $signature = Get-AuthenticodeSignature -FilePath $installerPath } catch { $signature = $null }

$subject = [string]$verdict.subject
$thumbprint = ""
$certSha256 = ""
$notAfter = ""
if ($null -ne $signature -and $null -ne $signature.SignerCertificate) {
    $thumbprint = [string]$signature.SignerCertificate.Thumbprint
    $certSha256 = [string]$signature.SignerCertificate.GetCertHashString("SHA256")
    $notAfter = $signature.SignerCertificate.NotAfter.ToUniversalTime().ToString("yyyy-MM-dd")
}

Write-Host ("::notice title=docker publisher audit::status={0}; signed_status={1}; subject={2}; thumbprint_sha1={3}; cert_sha256={4}; not_after={5}; size={6}; file_sha256={7}" -f `
        $verdict.status, ([string]$signature.Status), $subject, $thumbprint.ToLowerInvariant(), (Split-HashForLog $certSha256.ToLowerInvariant()), $notAfter, $fileInfo.Length, (Split-HashForLog $fileHash.ToLowerInvariant()))
Write-Host ("Строка для движка (Docker.psm1, `$script:DockerPublisherSubjects): `"{0}`"" -f $subject)
Write-Host ("Отпечатки (Docker.psm1, `$script:DockerPublisherThumbprints): `"{0}`", `"{1}`"" -f $thumbprint.ToLowerInvariant(), $certSha256.ToLowerInvariant())

if (-not $KeepInstaller) {
    Remove-Item $installerPath -Force -ErrorAction SilentlyContinue
    Remove-Item $WorkDir -Force -Recurse -ErrorAction SilentlyContinue
}

if ($measurementOnly) {
    Write-Host "::notice title=docker publisher audit::измерение завершено; вставьте subject/отпечатки в Docker.psm1 и повторите аудит"
    exit 0
}
if (-not $verdict.trusted) {
    Write-Host ("::error title=docker publisher audit::официальный установщик не признан движком: " + $verdict.reason)
    exit 1
}
Write-Host "::notice title=docker publisher audit::издатель совпал с зафиксированным — проверка движка верна"
exit 0
