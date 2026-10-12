# HR Manager Windows Installer Regression Review

Проведи углублённое расследование Windows installer/engine regression в репозитории:

`C:\Users\User\Documents\HR\HR Manager Desktop\HR-Manager-review-pr54`

## Симптомы

После установки HR Manager не запускается. Иконка tray показывает:

- `Состояние: Запускается`
- `Предпроверка не пройдена: compose. Запустите диагностику для деталей`

В Task Manager наблюдаются:

- десятки процессов `powershell.exe`;
- крупный процесс `vmmemWSL`, примерно `1.8 GB` памяти;
- `Docker Desktop Backend` и несколько процессов `Docker Desktop`.

Нужно установить:

1. Почему создаётся много процессов PowerShell.
2. Не остаются ли зависшие дочерние процессы, jobs, timers, retry loops или polling operations.
3. Есть ли у этих операций timeout и cancellation.
4. Почему Docker/WSL/Compose не переходят в готовое состояние.
5. Может ли это приводить к незавершённой установке и невозможности повторного запуска HR Manager.
6. Является ли высокий расход памяти `vmmemWSL` ожидаемым следствием Docker/WSL или симптомом зависшего workflow.

## Точные данные со скриншота

- Tray: `Состояние: Запускается`.
- Tray: `Предпроверка не пройдена: compose. Запустите диагностику для деталей`.
- `vmmemWSL`, PID `24276`, примерно `1,829,400 K` памяти, архитектура `x64`.
- В системе также видны `Docker Desktop Backend`, несколько `Docker Desktop` и многочисленные `powershell.exe`.

## Контекст предыдущего расследования

Изначально установлено, что это была неполная установка: файлы приложения существовали, но `installed.json` не создавался после прерывания Docker/first-run.

Уже были реализованы:

- recovery для partially completed installation;
- продолжение recovery, если каталог приложения существует, но отсутствует installation metadata;
- возможность tray launch восстановить incomplete installation;
- idempotent stop handling;
- исправление сценария, при котором Stop вызывал необработанное PowerShell/.NET JIT-окно при отсутствии installation metadata.

Результаты предыдущей проверки:

- чистый engine regression run: `237` тестов;
- `EXIT_CODE=0`;
- installer `0.15.0` пересобран;
- исправленные `Install.psm1` и `Tray.psm1` присутствуют в staging;
- manifest hash совпадает с installer.

## Артефакт установщика

Путь:

`C:\Users\User\Documents\HR\HR Manager Desktop\HR-Manager-review-pr54\installer\output\HR-Manager-Setup-0.15.0.exe`

Размер: `2,886,075` bytes  
SHA256: `d1cd66ae281245e0dd169c7d6a80136ace9044cefce9765001c3f4e95cc65e51`

Установщик unsigned и предназначен только для локального pilot-тестирования, не для production release.

## Ограничения

Не удалять и не пересоздавать:

- существующие данные пользователя;
- Docker containers;
- Docker volumes.

Docker является внешней prerequisite и не должен автоматически бандлиться в установщик. Не предлагать удаление пользовательской среды как первичное исправление.

## Что проверить

Проверь:

- код installer/engine/tray;
- `installer/build.ps1`;
- `installer/installer.iss`;
- `Install.psm1`;
- `Tray.psm1`;
- lifecycle запуска и остановки процессов;
- Docker/Compose readiness checks;
- retry/polling loops;
- PowerShell process creation и disposal;
- jobs, timers, runspaces и child processes;
- логи приложения и установщика;
- существующие тесты;
- текущий git diff и branch changes.

Не ограничивайся предыдущим зелёным прогоном из 237 тестов. Ищи race conditions, бесконечные retry/polling без timeout или cancellation, orphaned processes, повторный запуск параллельных startup-задач, а также ошибки lifecycle, которые проявляются только после interruption или relaunch.

## Требуемый результат ревью

1. Findings по severity, сначала самые критичные.
2. Для каждого finding укажи точный абсолютный путь и номер строки.
3. Объясни причинную связь между дефектом и наблюдаемыми симптомами.
4. Отдельно укажи, что именно создаёт лишние `powershell.exe` и что удерживает их живыми.
5. Отдельно оцени роль `vmmemWSL` и Docker Desktop.
6. Укажи, какие исправления необходимы.
7. Укажи недостающие тесты.
8. Не вноси исправления автоматически в рамках ревью.
9. Сформулируй точный ручной checklist для pilot-проверки:
   - закрытие текущего tray process;
   - запуск installer поверх существующей установки;
   - сохранение `%LOCALAPPDATA%\HRManager`, контейнеров и volumes;
   - ожидание Docker `Engine running`;
   - запуск с desktop shortcut;
   - проверка `Stop application`;
   - повторный запуск через shortcut;
   - проверка возврата tray icon;
   - фиксация количества PowerShell-процессов до/во время/после запуска;
   - фиксация состояния `vmmemWSL` и Docker;
   - сбор логов при повторном зависании.

Если для продолжения расследования нужны дополнительные данные из pilot-среды, выдай готовую команду или готовый prompt, который можно выполнить/передать пользователю.