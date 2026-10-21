# Ответ ревьюеру: готовим повторную приёмку, чистим только пилот

Дата: 2026-10-21. Ветка `fix/first-run-claim-6ae189a`, голова `c9d6742`.

## Коротко

**Вариант 1, но формулировка неточная.** Дело не в «очистить БД и состояние».
Есть жёсткое требование, которое надо знать до запуска: **каталог состояния
убирать обязательно, иначе движок уйдёт в ветку обновления и до первичной
установки не дойдёт.**

**И да — противоречие было моё, исправляю его ниже.**

## Почему каталог состояния убирать обязательно

`Install-HrmApp` (`infra/windows/engine/Install.psm1:61-78`):

```powershell
$existing = Get-HrmInstallRecord $StateDir
if ($null -ne $existing -and $existing.release_sha) {
    Write-HrmLog "info" "Повторный запуск установки не меняет данные и секреты."
    ...
    Start-HrmFirstRun -InstallDir $InstallDir -StateDir $StateDir -Port $port
    return
}
# --- Первичная установка ---
```

Если в `%LOCALAPPDATA%\HRManager\installed.json` есть `release_sha` — движок
считает это обновлением, **не трогает данные и секреты** и сразу уходит. До ветки
первичной установки, где пишется `pilot_created = $false` и заново создаётся
одноразовая ссылка первого запуска, он не доходит.

То есть: **почистить только том Postgres недостаточно.** Окно регистрации не
вернётся, и проверить фамилию будет нечем. Каталог состояния надо убрать.

## Почему мой прежний запрет был лишним

Я написал «не удалять тома `hr-manager-pilot_pilot_pgdata` и
`hr-manager-pilot_pilot_backups`». Это был перенос правила с живого стека на
приёмочный, и перенос лишний.

Запрет родился для **рабочего** приложения: `hr-manager` на порту **8080**, том
`hr-manager_pgdata`. Его трогать по-прежнему нельзя. Приёмочный стек — `hr-manager-pilot`
на порту **8081**, тома `hr-manager-pilot_pilot_pgdata` и
`hr-manager-pilot_pilot_backups`. Это другой проект Compose и другие тома.
Смысл приёмочного стека — в том, что его можно сбросить; иначе приёмка
невозможна в принципе.

Уточнение к уточнению: том **бэкапов** (`pilot_backups`) удалять всё равно не
нужно — он не мешает повторной регистрации, и движок его штатно сохраняет.

## Порядок действий

### 0. Контрольные точки — до

```powershell
# живой стек отвечает?
(Invoke-WebRequest -Uri http://127.0.0.1:8080/ -UseBasicParsing).StatusCode   # ждём 200

# какие тома есть — убедиться, что видим ОБА проекта и не путаем их
docker volume ls --format '{{.Name}}' | Select-String '^hr-manager'
```

Ожидаем две разные группы: `hr-manager_pgdata` (живой, **не трогаем**) и
`hr-manager-pilot_pilot_pgdata` / `hr-manager-pilot_pilot_backups` (приёмочные).

### 1. Резервная копия

Штатный purge движка **сам** создаёт шифрованный бэкап и **сам** проверяет его
глубокой проверкой целостности, и отказывается удалять данные, если проверка не
прошла (`Remove-HrmApp`, `Install.psm1:199-226`). То есть требование
«проверяемая резервная копия» движок выполняет лучше, чем ручной дамп.

Если нужна ещё и своя копия (рекомендую, на всякий случай):

```powershell
$dir = 'C:\hrm-accept\backup-20261021'
New-Item -ItemType Directory -Path $dir -Force | Out-Null

# логический дамп (имя контейнера берём из docker ps, не угадываем)
$pg = docker ps --format '{{.Names}}' |
      Where-Object { $_ -like 'hr-manager-pilot-*postgres*' } |
      Select-Object -First 1
"контейнер БД: $pg"
docker exec $pg pg_dumpall -U hrmanager > "$dir\pilot-pg-dump.sql"

# архив тома целиком
docker run --rm -v hr-manager-pilot_pilot_pgdata:/from -v "$dir":/to `
  alpine tar czf /to/pilot_pgdata.tar.gz -C /from .
```

### 2. Снять стек и удалить **только** том данных пилота

**Штатной командой движка, а не `docker volume rm` руками.** В движке есть
отбелённый список (`Compose.psm1:127-135`): удаляется ровно
`hr-manager-pilot_pilot_pgdata`, имя выводится из кода, другие тома и другие
проекты не затрагиваются. Руками писать `docker volume rm` нельзя — одна опечатка
удаляет рабочий том `hr-manager_pgdata`.

```powershell
$env:HRM_PURGE_CONFIRMATION = 'УДАЛИТЬ ДАННЫЕ HR MANAGER'

powershell -NoProfile -ExecutionPolicy Bypass `
  -File 'C:\hrm-accept\ci625\install\infra\windows\hr-manager.ps1' `
  -Action uninstall -PurgeData -NonInteractive `
  -InstallDir 'C:\hrm-accept\ci625\install'

Remove-Item Env:\HRM_PURGE_CONFIRMATION
```

Что делает эта команда: проверяет фразу-подтверждение, создаёт и **проверяет**
бэкап, гасит стек через `down --remove-orphans` (**никогда** не `-v`), затем
удаляет ровно один том. Если бэкап не создался или не прошёл проверку — падает с
ошибкой и **ничего не удаляет**. `pilot_backups`, Docker Desktop и WSL2 не
трогаются.

`-InstallDir` указать обязательно: по умолчанию движок берёт
`%LOCALAPPDATA%\Programs\HRManager`, а установка лежит в другом месте.
Если purge падает с «Установка не найдена» — путь неверный, каталог состояния
ещё не тронут, можно просто исправить путь.

### 3. Убрать каталог состояния

Движок оставляет его намеренно — «чтобы повторная установка восстановила
данные» (`Install.psm1:231-232`). Для приёмки **первичной** установки это ровно
то, что мешает. Убираем **только после** успешного шага 2.

```powershell
Remove-Item "$env:LOCALAPPDATA\HRManager" -Recurse -Force
```

### 4. Запись деинсталлятора

Иначе мастер вспомнит прежний каталог и уйдёт туда вместо `/DIR`:

```powershell
$keys = @(
  'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
  'HKCU:\Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
Get-ItemProperty $keys -ErrorAction SilentlyContinue |
  Where-Object DisplayName -like '*HR Manager*' |
  ForEach-Object { Remove-Item $_.PSPath -Recurse -Force }
```

### 5. Контрольные точки — после

```powershell
# живой стек всё ещё отвечает?
(Invoke-WebRequest -Uri http://127.0.0.1:8080/ -UseBasicParsing).StatusCode   # ждём 200

# том данных пилота ушёл, том бэкапов остался, живой том на месте
docker volume ls --format '{{.Name}}' | Select-String '^hr-manager'
```

**Если `hr-manager_pgdata` пропал — остановиться и сказать мне.** Это авария,
не приёмка.

### 6. Свежая установка

```powershell
$p = Start-Process -FilePath 'C:\hrm-accept\ci625\Setup.exe' `
     -ArgumentList '/DIR=C:\hrm-accept\ci625\install' -Wait -PassThru
"КОД ВОЗВРАТА: $($p.ExitCode)"
```

## Что учесть после сброса

- **Лицензию надо загрузить заново.** Владелец и лицензия живут в сброшенной БД.
  Без загруженного владельцем `.hrmlicense` рабочие разделы (`/candidates`,
  `/events`, `/notifications`) ответят 403 `no_license` — это ожидаемо и не
  дефект, но интерфейс не должен называть это «backend недоступен».
- **Фамилию вводить кириллицей** (например `Иванов`) и показать, что стало в
  профиле. Это проверка починки `54d68fc`.
- Захват первого запуска одноразовый. Свежий движок создаёт новую ссылку, потому
  что `installed.json` удалён и `pilot_created` снова `false`.

## Что НЕ делать

`docker compose down -v`, `docker volume prune`, удаление тома по маске
`hr-manager*`, трогание стека на 8080.
