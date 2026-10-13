# Установщик HR Manager для Windows (phase 12)

Графический мастер `HR Manager Setup.exe`: русский мастер из нескольких
кликов — «Роль владельца → Фамилия → Часовой пояс → Установить» — для
локального пилота на Windows 10/11 x64. После копирования файлов движок
`infra/windows/hr-manager.ps1` проверяет среду, запускает только уже
установленный Docker Desktop, собирает образы и открывает первый запуск в
браузере. Docker Desktop пользователь устанавливает отдельно и вручную.

Сборка установщика **не требует** установленного Inno Setup заранее:
`installer/build.ps1` сам скачивает закреплённую версию, проверяет её
SHA256 и компилирует `installer.iss`. Бинарный установщик в git
**не хранится** — в репозитории только исходники сборки; готовый exe
собирается в CI (`.github/workflows/ci.yml`, job `windows-installer`)
и публикуется как артефакт с `release-manifest.json` и `SHA256SUMS.txt`.
Если сборщику передан официальный `DockerDesktopInstaller.exe`, он публикуется
отдельным sidecar рядом с Setup.exe; в Setup.exe он не встраивается.

## Закреплённая инструментальная цепочка

| Компонент | Версия | Источник | SHA256 |
|---|---|---|---|
| Inno Setup | **6.7.3** (стабильная) | официальный релиз `jrsoftware/issrc` | `9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732` |
| Язык мастера | Russian (`compiler:Languages\Russian.isl`) | входит в Inno Setup 6.7.3 | — |

URL: `https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe`

Версия и хеш закреплены в двух местах (двойной контроль):
`installer/build.ps1` и winget-манифест
`microsoft/winget-pkgs` → `JRSoftware/InnoSetup/6.7.3`. Если хеш
скачанного файла не совпал — сборка останавливается.

## Сборка

```powershell
powershell -ExecutionPolicy Bypass -File installer\build.ps1 -Version 0.15.0
# Optional: provide the official Docker Desktop installer as a separate sidecar
powershell -File installer\build.ps1 -Version 0.15.0 `
  -DockerDesktopInstallerPath C:\downloads\DockerDesktopInstaller.exe `
  -DockerDesktopInstallerSha256 <64-hex-sha256>
```

Что происходит:

1. Inno Setup 6.7.3 скачивается (если ещё нет в `installer/.cache/`),
   проверяется SHA256 и ставится молча **только на сборочной машине**
   (`/VERYSILENT /CURRENTUSER`). На машину пользователя он не попадает.
2. Формируется снимок приложения `installer/staging/app/`:
   `backend/` (без `tests/` и кешей), `frontend/` (исходники без
   `node_modules/` и `dist/` — контейнеры собираются из исходников через
   `docker build`), `infra/` (включая движок и compose), `release.json`
   (`release_sha` = текущий коммит, `version`).
3. `ISCC.exe` компилирует `installer.iss` →
   `installer/output/HR-Manager-Setup-<Version>.exe`. Необязательный
   `DockerDesktopInstaller.exe` копируется в `installer/output/` после
   компиляции и остаётся отдельным файлом — он не входит в Setup.exe.
4. Пишется `installer/release-manifest.json` и его копия рядом с дистрибутивом:
   версия, `release_sha`, SHA256 Setup.exe, **детерминированные SHA256 всех
   файлов пакета** и `docker_desktop_installer` (official source URL, `available`,
   SHA256, `embedded_in_setup=false`, `automatically_launched=false`).
   `installer/output/SHA256SUMS.txt` фиксирует Setup.exe, manifest и sidecar,
   если он есть. Без sidecar в manifest остаётся официальный URL, `available=false`,
   а локальный путь не показывается.

Сборщик может положить файл в `installer/DockerDesktopInstaller.exe` (имя
игнорируется Git) или передать `-DockerDesktopInstallerPath`. Для release-build
передайте известный SHA256 через `-DockerDesktopInstallerSha256`: при несовпадении
сборка останавливается. Файл-источник не должен находиться в `installer/output/`
(этот каталог пересоздаётся). `build.ps1` никогда не скачивает Docker Desktop.

В ручном GitHub Actions workflow `pilot-release` есть необязательный input
`docker_desktop_installer_sha256`. Если release-оператор явно задаёт 64-символьный
SHA256, workflow скачивает только с фиксированного официального URL, сверяет точный
хеш и передаёт отдельный sidecar в сборку; пустой input исключает загрузку и оставляет
в манифесте только официальный сайт. Обычный CI этот 600-МБ файл не скачивает.

Проверка манифеста: `git diff --exit-code installer/release-manifest.json`
после повторной сборки того же коммита.

## Кодовая подпись (честно о статусе)

Текущая сборка **не подписывает** exe — в этом репозитории нет ни
сертификата, ни ключа подписи, и подделывать подпись нельзя.
Предусмотрен детерминированный хук: определите параметр `SignTool`
в секции `[Setup]` файла `installer.iss` и передайте команду в
`build.ps1` (например, `$ISCC_PARAMS = "/Ssigntool=$signCommand"`).
Инструкция для реального релиза:

1. Выпустить сертификат кодовой подписи (OV/EV, хранится в HSM/закрытом
   хранилище сборщика; в репозиторий не попадает).
2. `signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a "installer\output\HR-Manager-Setup-<Version>.exe"`.
3. После подписи пересчитать SHA256 и обновить `release-manifest.json`;
   публиковать exe вместе с манифестом и ссылкой на инструкцию проверки.

Пользователь может проверить SHA256 опубликованного exe по манифесту
релиза (GitHub Releases / CI-артефакт).

## Docker Desktop: ручная установка и sidecar

HR Manager требует установленный и запущенный Docker Desktop. Если он отсутствует,
движок откажется от запуска приложения, объяснит, что нужны Docker Desktop и Docker
Engine, и покажет официальный сайт:
`https://www.docker.com/products/docker-desktop/`. HR Manager не скачивает и не
запускает Docker installer и не принимает лицензию Docker за пользователя.

Если `DockerDesktopInstaller.exe` находится рядом с Setup.exe при установке,
Inno Setup копирует его в `{app}\docker\DockerDesktopInstaller.exe` через
`external skipifsourcedoesntexist`. Отсутствие файла не мешает Setup скопировать
приложение, а в подсказке остаётся только официальный URL. Даже при наличии sidecar
HR Manager никогда его не запускает; решение и запуск остаются за человеком.

В интерактивном Setup страница Finish читает `%LOCALAPPDATA%\HRManager\setup-run.json`.
Исчезнувшая отметка означает, что движок завершился и подтвердил готовность;
оставшиеся `failed`, `running` или нечитаемая отметка показывают честную страницу
«Файлы установлены, но HR Manager не готов» и возвращают custom Inno exit code `10`.
Это отделяет успешное копирование файлов Setup.exe от успешного запуска приложения.
Если Setup остановился именно из-за отсутствия Docker, после ручной установки и
готовности Engine нажмите ярлык **HR Manager**: когда файлы уже скопированы, но
`installed.json` ещё нет, ярлык завершит первичную настройку из этих файлов.
Docker-specific pending или автоматическое продолжение внешнего installer не
создаются.

## Что мастер делает и чего НЕ делает

- **Делает:** выбор роли (`HR | Руководитель | Администратор`), ввод
  фамилии и часового пояса (по умолчанию `Europe/Moscow`), копирование
  файлов в `%LOCALAPPDATA%\Programs\HRManager`, запись защищённого файла
  `first-run-input.json` в `%LOCALAPPDATA%\HRManager`, запуск движка
  (`-Action install -NonInteractive -OpenBrowser`), который строит образы,
  генерирует секреты, поднимает контейнеры и открывает браузер на странице
  первого запуска. Пароль пользователь задаёт сам в браузере.
- Запуск движка и управляющего компонента трея после копирования файлов
  обязателен и не представлен отключаемыми флажками на финальной странице.
  Трей запускается первым, остаётся отзывчивым во время установки и показывает
  ошибку, если установочный движок завершился неуспешно.
- **Не делает:** не скачивает, не устанавливает и не запускает Docker Desktop;
  не принимает лицензию Docker за пользователя. Docker устанавливается вручную
  с официального сайта. Setup сам не требует прав администратора
  (`PrivilegesRequired=lowest`); отдельный ручной запуск Docker installer может
  вызвать UAC. Секреты не попадают в командную строку или реестр.
- **Канал обновлений (Phase 13):** установщик дополнительно запускает
  скрытый наблюдатель `hr-manager.ps1 -Action channel -Watch`; пакет
  обновлений скачивается и проверяется сервером, а установку выполняет
  движок после явной команды администратора в UI (см.
  `infra/windows/README.md` и `infra/release/README.md`).
- **Повторный запуск** установщика той же или новой версии распознаётся
  движком как «уже установлено»: данные и секреты сохраняются, при
  необходимости приложение поднимается/восстанавливается (обновление —
  штатный путь поставки новой версии; см. `infra/windows/README.md`).
- **Удаление** — через «Параметры → Приложения»: останавливает контейнеры
  и **сохраняет** данные Postgres и зашифрованные бэкапы (тома
  `pilot_pgdata`/`pilot_backups`). Docker Desktop и WSL2 не удаляются
  никогда. Полное удаление данных — отдельное действие движка с
  фразой-подтверждением и предварительным шифрованным бэкапом
  (`-Action uninstall -PurgeData`).

## Структура

```
installer/
  installer.iss        # исходник мастера (Inno Setup, русский язык)
  build.ps1            # воспроизводимая сборка + манифест хешей
  release-manifest.json# результат сборки (генерируется, в CI — артефакт)
  staging/, output/, .cache/  # генерируются; в git не хранятся
  DockerDesktopInstaller.exe # optional sidecar input; ignored, never embedded/launched
```

## Authenticode-подпись и trust store (Phase 14)

- `build.ps1 -TrustStoreFile <файл> -TrustStoreSha256 <sha>` встраивает
  **уже проверенный** публичный trust store канала в снимок
  (`infra/release/trust-store.json`) и в `release-manifest.json`; файл с
  приватным материалом отвергается до сборки.
- `sign.ps1 -Mode test` подписывает installer ephemeral тестовым
  сертификатом (только CI/PR, без секретов) и пишет
  `authenticode-attestation.json`, `authenticode-roots.pem` и
  `authenticode-verification.json`. Test-сертификат создаётся только в
  `Cert:\CurrentUser\My` и **не** добавляется в `Root`/`TrustedPublisher`:
  на GitHub-hosted runner такое добавление показывает диалог подтверждения и
  висит до таймаута job'а. Поэтому Windows честно не доверяет цепочке —
  это единственная причина, которую test-режим принимает, и она фиксируется
  в attestation как `signtool_verify_ok=false` / `windows_chain_trusted=false`.
  Любая другая причина (`HashMismatch`, `NotSigned`, несовместимый формат)
  остаётся отказом. Криптография при этом проверяется по-настоящему:
  `infra/release/authenticode.py verify` пересчитывает Authenticode-хеш PE,
  проверяет CMS-подпись и доводит цепочку до экспортированного корня.
  Production policy такой релиз не пропускает: `mode="test"`.
- `sign.ps1 -Mode production -PfxPath <pfx> -ExpectedPublisher <издатель>
  -TimestampUrl <RFC3161> -TrustStoreFile <файл> -SignerRootsPath <pem>
  -TimestampRootsPath <pem>` до `signtool sign` делает pre-flight двух разных
  PEM (файл есть, не пуст, без BOM, хотя бы один разобранный сертификат).
  Пароль PFX только из `HRM_AUTHENTICODE_PFX_PASSWORD`. Дальше —
  `signtool sign /fd SHA256 /tr /td SHA256`, `signtool verify /pa`, статус
  `Valid`, метка времени, издатель и `infra/release/authenticode.py` с
  `--require-timestamp`. Пароль, ключ и тело PEM в логи не попадают. Цепочку
  PFX → корень владелец проверяет вручную (runbook), скрипт её не строит.
- `sign.ps1` всегда завершается явным кодом возврата (0 — подпись выполнена и
  проверена, 1 — отказ). Без этого `$LASTEXITCODE` вызывающей стороны
  оставался от `signtool verify`, и CI считал успешную подпись провалом.
- Оба скрипта требуют `python` в PATH: `build.ps1` — нет, `sign.ps1` — да
  (независимый верификатор). JSON-артефакты пишутся UTF-8 **без BOM**:
  `Set-Content -Encoding UTF8` в Windows PowerShell 5.1 добавляет BOM, а
  Python-часть release-пайплайна читает их через `json.loads`.
- Production-релиз `publish_channel.py --release-mode production` отказывается
  публиковаться, если installer не подписан, метка времени отсутствует,
  издатель не совпал или trust store в attestation отличается от релизного.
