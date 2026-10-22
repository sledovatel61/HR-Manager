# Чистовая приёмка пилота 8081: единственный протокол

Дата: 2026-10-21. Ветка `fix/first-run-claim-6ae189a`, голова `2710f8c`.

Цель одна и не подменяется: **чистовая установка** Setup → регистрация с
кириллической фамилией → вход → повторный запуск. Успешное обновление её не
доказывает и в зачёт не идёт.

Рабочее приложение на **8080** (проект `hr-manager`) не трогаем. Чистим только
пилот на **8081** (проект `hr-manager-pilot`).

## Шаг 0. Сборка. Обязательно до всего остального

Локальная сборка называется `0.15.0-ci625-portfix`. В названии про порт. Если в
ней нет моих починок путей и кодировки — `Owner` вернётся и прогон будет потрачен.

```powershell
$iss = Get-ChildItem 'C:\hrm-accept\ci625-lifecycle-local' -Recurse -Filter installer.iss -ErrorAction SilentlyContinue |
       Select-Object -First 1
if ($iss) {
  "-StateDir  (нужно >= 3): $((Select-String -Path $iss.FullName -SimpleMatch '-StateDir').Count)"
  "UTF8Encode (нужно >= 2): $((Select-String -Path $iss.FullName -SimpleMatch 'UTF8Encode').Count)"
} else { "installer.iss не найден — собираем заново с ветки" }
```

Оба числа сошлись — идём дальше. Не сошлось или файл не нашёлся — пересобрать:

```powershell
git clone --branch fix/first-run-claim-6ae189a https://github.com/sledovatel61/HR-Manager.git C:\hrm-build
cd C:\hrm-build
powershell -NoProfile -ExecutionPolicy Bypass -File installer\build.ps1 -Version 0.15.0
```

и снова шаг 0 на новой сборке. **Никаких `Owner`-прогонов «чтобы посмотреть».**

## Шаг 1. Снимок перед сбросом

```powershell
# живой стек отвечает?
(Invoke-WebRequest -Uri http://127.0.0.1:8080/ -UseBasicParsing).StatusCode   # ждём 200

# какие тома есть — видим ОБА проекта
docker volume ls --format '{{.Name}}' | Select-String '^hr-manager'

# диагностика причины (одной строкой, чтобы не возвращаться к этому вопросу)
"HRM_STATE_DIR=[$env:HRM_STATE_DIR]"
"второй каталог движка: $(Test-Path "$env:LOCALAPPDATA\Programs\HRManager")"
docker ps --format '{{.Names}}' | Sort-Object | Out-String
```

## Шаг 2. Сброс только пилота

Штатной командой движка, **не** `docker volume rm` руками — одна опечатка удалит
рабочий `hr-manager_pgdata`:

```powershell
$env:HRM_PURGE_CONFIRMATION = 'УДАЛИТЬ ДАННЫЕ HR MANAGER'
powershell -NoProfile -ExecutionPolicy Bypass `
  -File '<каталог установки>\infra\windows\hr-manager.ps1' `
  -Action uninstall -PurgeData -NonInteractive `
  -InstallDir '<каталог установки>'
Remove-Item Env:\HRM_PURGE_CONFIRMATION
```

Движок сам создаст и **сам проверит** бэкап, и откажется удалять, если проверка не
пройдёт. Удалит ровно `hr-manager-pilot_pilot_pgdata`. Том бэкапов, Docker
Desktop и WSL2 не трогает.

Потом — **только после успеха** — каталог состояния и запись деинсталлятора:

```powershell
Remove-Item "$env:LOCALAPPDATA\HRManager" -Recurse -Force

$keys = @(
  'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
  'HKCU:\Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
Get-ItemProperty $keys -ErrorAction SilentlyContinue |
  Where-Object DisplayName -like '*HR Manager*' |
  ForEach-Object { Remove-Item $_.PSPath -Recurse -Force }
```

Каталог состояния убирать обязательно: при существующем `installed.json` движок
уходит в ветку обновления и до первичной установки не доходит — регистрация не
вернётся.

Контроль после сброса: 8080 по-прежнему 200, и `hr-manager_pgdata` на месте.
**Если он пропал — стоп, это авария.**

## Шаг 3. Запуск

```powershell
$p = Start-Process -FilePath '<путь к Setup.exe>' `
     -ArgumentList '/DIR=C:\hrm-accept\ci625\install' -Wait -PassThru
"КОД ВОЗВРАТА: $($p.ExitCode)"
```

## Шаг 4. Шесть проверок — это и есть результат приёмки

| # | Проверка | Требуется |
|---|---|---|
| 1 | Код возврата Setup | **0** |
| 2 | Браузер | регистрация открылась **сама**, без ручного открытия ссылки |
| 3 | Экран | форма **регистрации**, а не вход в старую учётную запись |
| 4 | Фамилия | **«Перепечай»** — именно так, без подмены на `Owner` |
| 5 | Регистрация и вход | успешно, с **первого** раза, без падения страницы |
| 6 | Повторный запуск | ярлык/трей открывает **8081**, учётная запись на месте |

Плюс: **8080 не изменился** (тот же код 200, те же контейнеры).

Все шесть — приёмка сдана. Любой провал — приёмка не сдана, и это **не** повод
переходить к проверке обновления.

Лицензия отдельным этапом: загружается файлом `.hrmlicense`, сама не появляется.
До загрузки 403 `no_license` на рабочих разделах — ожидаемо, не сбой регистрации.

## Что писать в отчёте

Ровно четыре строки, без рассуждений:

1. Сборка: путь к `.exe` и её SHA256.
2. Шаг 0: два числа (`-StateDir`, `UTF8Encode`).
3. Шаг 1: вывод `HRM_STATE_DIR`, наличие второго каталога, 8080 до.
4. Шаг 4: таблица шести проверок, плюс 8080 после.

Этого достаточно, чтобы я принял решение без новых кругов.
