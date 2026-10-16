# Задание исполнителю: починить workflow «Pilot release (Windows)»

Статус: **открыто**. Приоритет: высокий — это workflow настоящего пилотного
релиза, он сломан независимо от задачи приёмки.

## Что уже сделано и работает (не переделывать)

Ручной запуск CI на ветке `arena/3cadc6a9-hr-manager` (workflow_dispatch,
`github.sha` = `6ae189a6f3cb42ac0f360a4374bafb2d7711085b`):

- прогон `38945912191`, итог **success, 9/9 заданий**
- задание `Pilot Setup.exe (unsigned, 0.15.0) + SHA256SUMS` — success
- тесты pwsh — **348/348**, наборы включают `db-password` и `snapshot-recovery`
- артефакт `pilot-setup-0.15.0-unsigned`, 2 418 440 байт
- `HR-Manager-Setup-0.15.0.exe` sha256
  `E7A397E554744FD81C28B7D95620BB6B8EEC074209761391D68F0ECC46DE1271`
- `release-manifest.json` sha256
  `33CE5A200C342AD0E8BA529A9174A4D2E0A56156F8025C6DF551C5FD20FCC91F`

Проверка в CI жёсткая: `release_sha` в манифесте обязан равняться `GITHUB_SHA`,
иначе задание падает. При ручном запуске на ветке `GITHUB_SHA` — коммит ветки,
так что установщик собран именно из `6ae189a`, не из мёрдж-коммита.

## Что сломано

Прогон `Pilot release (Windows)` `38918703168` (workflow_dispatch на `main`,
`release_sha` = `6ae189a`):

| шаг | итог |
|---|---|
| Resolve version and SHA | success |
| Build autonomous license-issuer bundle | success |
| Prepare optional pinned Docker Desktop sidecar | success |
| **Build HR Manager Setup.exe (pilot, unsigned)** | **failure** |
| Generate SHA256SUMS.txt | skipped |
| Upload pilot artifacts | skipped |

Падает на `installer/build.ps1:280`:

```
Resolving trust store: 0.15.0 (cwd D:\a\HR-Manager\HR-Manager)
Exception: installer\build.ps1:280
  { throw "TrustStoreFile not found: $TrustStoreFile (cwd ...)" }
Error: Process completed with exit code 1.
```

То есть строка версии `0.15.0` попадает в параметр `-TrustStoreFile`, и сборка
отказывается искать «файл хранилища доверия» с именем `0.15.0`. Пилотная сборка
хранилища доверия не использует вовсе.

## Проверенные факты (повторно не проверять)

1. Param-блок `installer/build.ps1` **побайтово одинаков** на `main` и на `6ae189a`:

   ```powershell
   param(
       [string]$Version = "0.15.0",
       [string]$TrustStoreFile = "",
       [string]$TrustStoreSha256 = "",
       [string]$DockerDesktopInstallerPath = "",
       [string]$DockerDesktopInstallerSha256 = ""
   )
   ```

2. `$TrustStoreFile` **нигде не перезаписывается** в `build.ps1`. Поиск без учёта
   регистра даёт только: объявление параметра, `if ($TrustStoreFile)`,
   `Write-Host`, `throw`, `Resolve-Path`.

3. `pilot-release.yml` на `main` передаёт только
   `$buildArgs = @("-Version", $version)` со сплаттингом
   `& ./installer/build.ps1 @buildArgs`. Слова `TrustStore` в файле нет вообще.

4. `pilot-release.yml` **идентичен** на `main` и на ветке (пустой diff). В истории
   `main` у него ровно один коммит — `75cd9c8 Add files via upload`.

5. `.gitignore`, строка 32 — `dist/`, поэтому `tools/license-issuer/dist/`
   игнорируется и сборка лицензионного бандла **не** грязнит дерево. Новый guard
   «чистый checkout» в `build.ps1` проходит.

## Отброшенные гипотезы (не возвращаться)

- «Сборка лицензионного бандла грязнит дерево, и падает guard чистого checkout» —
  отпадает: `dist/` в `.gitignore`.
- «Патч recovery сломал param-блок» — отпадает: он идентичен `main`.
- «Не тот коммит» — отпадает: шаг `Resolve version and SHA` прошёл, checkout = `6ae189a`.

## Что сравнить в первую очередь

Два места вызова одного и того же `build.ps1`:

**Работает** (`ci.yml`, задание `pilot-setup`, отдельное задание
`license-issuer-windows` для лицензионного бандла):

```powershell
& ./installer/build.ps1 -Version 0.15.0 *>&1 | Tee-Object pilot-build.log
```

**Падает** (`pilot-release.yml`, шаг 10, лицензионный бандл собирается
в том же задании ранее):

```powershell
$version = "${{ steps.resolve.outputs.version }}"
$buildArgs = @("-Version", $version)
if ($env:HRM_DOCKER_DESKTOP_INSTALLER_SHA256) {
    $buildArgs += @(
        "-DockerDesktopInstallerPath", (Join-Path $PWD "installer/DockerDesktopInstaller.exe"),
        "-DockerDesktopInstallerSha256", $env:HRM_DOCKER_DESKTOP_INSTALLER_SHA256
    )
}
& ./installer/build.ps1 @buildArgs
```

Разница в двух вещах: способ передачи аргументов (сплаттинг массива против
прямой строки) и то, что лицензионный бандл собирается в том же дереве ранее.

## Оговорка про лог

Текст лога, который удалось прочитать по скриншоту, **дословно не совпадает** с
закоммиченным кодом в двух местах: в логе «No Docker Desktop sidecar
**supported**», в коде «**supplied**»; URL Inno в логе напечатан с точками
(`is-6.7.3`), в коде с подчёркиваниями (`is-6_7_3`). Это может быть моим
неверным чтением скриншота, а может означать, что дерево на момент сборки
отличалось от коммита. **Это надо прояснить на Windows, где лог читается точно.**

## Задача

1. Воспроизвести локально на Windows с pwsh: чистый detached checkout `6ae189a`,
   выполнить точно скрипт шага 10 из `pilot-release.yml`, убедиться в падении.
   Сначала repro, только потом правка.
2. Установить, почему строка версии попадает в `-TrustStoreFile`.
3. Починить минимально. Приоритет — правка workflow, если причина в нём;
   правка `build.ps1` только если причина именно в скрипте.
4. Регрессионный тест, который поймал бы этот дефект: сборка с конструкцией
   аргументов из `pilot-release.yml` не должна заходить в ветку trust store.
   Тест обязан быть красным до правки.
5. Перепрогнать `Pilot release (Windows)` вручную на ветке с `release_sha` =
   новый коммит. Успех = сборка прошла, артефакт опубликован, манифест называет
   именно этот коммит.
6. Убедиться, что задание `pilot-setup` в `ci.yml` по-прежнему зелёное — оно
   работает сегодня, сломать его нельзя.

## Нельзя

- Не ослаблять fail-closed проверку trust store.
- Не ослаблять guard «чистый committed checkout».
- Не трогать резолвинг лицензионного ключа.
- Не переписывать историю, не мержить, не релизить, не публиковать как дистрибутив.
- Не подменять ошибку текстом, чтобы прогон стал зелёным.

## Почему это важно

Приёмка идёт через `ci.yml` — он работает, и Setup.exe уже получен. Но
`Pilot release (Windows)` — это путь, которым владелец делает настоящий пилотный
релиз. Если его не починить, при мерже PR сломается штатный релизный сценарий,
и дефект вылезет уже у владельца.
