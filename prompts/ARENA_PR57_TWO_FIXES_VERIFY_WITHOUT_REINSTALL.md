# Проверка двух починок без переустановки

Дата: 2026-10-21. Ветка `fix/first-run-claim-6ae189a`, голова `6eb080b`.
Обе причины установлены точно, обе исправлены. **Переустановка пока не нужна.**

## Что было сломано

### Дефект 1 — кириллица портилась на транспорте, а не в бэкенде

Ответ сервера был:

```json
{"detail":"Фамилия может содержать только буквы, пробелы, дефис или апостроф."}
```

Это **не** ошибка схемы FastAPI, а сообщение серверной функции
`validate_surname` (`backend/app/setup_owner.py:140`). Её проверка юникодная:

```python
if not (char.isalpha() or char in " -'"):     # 'П'.isalpha() == True
```

То есть **кириллица сервер пропускает** — и специально: рядом живут
`transliterate_surname` (`:103`) и `build_owner_username` (`:126`), которые
строят логин `owner.<транслит>` именно из кириллической фамилии.

Отказ на `char.isalpha()` означает, что сервер получил **не букву**. Каким
символом бывает не-буква вместо кириллицы — это U+FFFD, символ замены, который
подставляет UTF-8-читалка на невалидных байтах.

**Где портились байты:** `Invoke-HrmHttp` отдавал тело строкой в `-Body`.
Windows PowerShell 5.1 кодирует такую строку **системной кодировкой**, а сервер
читает тело как UTF-8. Движок при этом запускается через `SysNative\...\powershell.exe`,
то есть это именно 5.1.

Починка (`6eb080b`): тело отдаётся **явными байтами UTF-8**, и в заголовке
объявляется кодировка.

```powershell
$params["ContentType"] = "application/json; charset=utf-8"
$json = ($Body | ConvertTo-Json -Compress -Depth 8)
$params["Body"] = [System.Text.Encoding]::UTF8.GetBytes($json)
```

### Дефект 2 — гонка за файлом состояния

Трейс был однозначен:

```
Объект по указанному пути ...\supervisor.json.tmp не существует.
в Set-HrmJsonFile, Common.psm1: строка 633      ← Move-Item
```

`Set-HrmJsonFile` писал через **общее** имя `$target.tmp`. Мастер поднимает трей
с флагом `nowait`, и трей в своей стартовой ветке вызывает
`Set-HrmSupervisorState` в тот же момент, когда это делает движок
(`hr-manager.ps1:176`). Один пишет `.tmp` — второй пишет тот же `.tmp` и
переносит его на место — и `Move-Item` первого падает: файла уже нет.

Время подтверждает: **трей стартовал 15:50:47, fatal в 15:50:49** — две секунды.

Починка (`18e2905`): имя временного файла уникально для процесса.
Тот же приём в проекте уже применён — `Snapshot.psm1` пишет
`previous-snapshot.tmp-<guid>`.

```powershell
$tmp = "$target.$PID.tmp"
```

## Проверка дефекта 1 — прямо сейчас, без установки

Пилот поднят, токен на диске. Повторить claim так, как его теперь посылает
движок:

```powershell
$state = "$env:LOCALAPPDATA\HRManager"
$tok = [string]((Get-Content "$state\secrets.json" -Raw | ConvertFrom-Json).HRM_EXCHANGE_TOKEN)
$port = (Get-Content "$state\pilot.env" -Raw |
         Select-String 'HRM_PILOT_TRUST_PORT=(\d+)').Matches.Groups[1].Value
$uri = "http://127.0.0.1:$port/api/setup/owner/claim"

# ТО ЖЕ, что делает движок после починки: JSON -> байты UTF-8 + charset
$json = @{ exchange_token = $tok
           surname        = 'Перепечай'
           working_mode   = 'hr'
           timezone       = 'Europe/Moscow' } | ConvertTo-Json -Compress -Depth 8

"--- тело запроса ---"
$json
"--- есть ли в теле сырые escape-последовательности \\u ---"
$json -match '\\u'

try {
    $r = Invoke-WebRequest -Uri $uri -Method POST `
           -Body ([System.Text.Encoding]::UTF8.GetBytes($json)) `
           -ContentType 'application/json; charset=utf-8' -UseBasicParsing `
           -Headers @{ 'X-Real-IP' = '127.0.0.1' }
    "СТАТУС: $($r.StatusCode)"
} catch {
    "СТАТУС: $([int]$_.Exception.Response.StatusCode)"
    "ТЕЛО: $($_.ErrorDetails.Message)"
}
```

Как читать:

| Результат | Что значит |
|---|---|
| **201** | Починка подтверждена. Тикет создан, регистрация откроется. |
| **409** | Это **тоже успех** — владелец уже создан, тикет одноразовый и израсходован. |
| **422** | Починка не достала. Смотрим строку «тело запроса»: если там `\u041f...` — значит `ConvertTo-Json` экранирует кириллицу, и порча была в другом месте. Тогда пришлите тело запроса целиком, и я посмотрю. |

Токен в отчёт **не включать**, только статус и тело ответа.

## Проверка дефекта 2

Повторять сбой не нужно — время в трейсе (трей за две секунды до fatal) и общее
имя `.tmp` в коде однозначно описывают гонку. Проверяется автоматически: CI
запускает `Windows engine tests + installer smoke`, там ветка с треем и движком.
Плюс линт теперь падает, если общее имя `$target.tmp` вернётся.

## Что дальше

1. **Прогнать CI** на ветке `6eb080b` — кнопка «Run workflow», ветка
   `fix/first-run-claim-6ae189a`. Нужен зелёный `Windows engine tests + installer smoke`.
2. Собрать Setup.exe с этой ветки.
3. Сбросить пилот и провести чистовую приёмку по протоколу
   `ARENA_PR57_CLEAN_INSTALL_ACCEPTANCE_PROTOCOL.md` — те же шесть проверок.
4. Фамилию вводить **«Перепечай»** и убедиться, что в профиле она читается.

Дефект 1 при этом можно подтвердить **до** всего этого — одной командой выше.
Если она даст 201, обе причины закрыты фактом, и чистовой прогон станет
подтверждением, а не поиском.
