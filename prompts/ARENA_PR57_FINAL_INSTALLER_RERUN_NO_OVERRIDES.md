# Финальный перезапуск установщика: без наших костылей, как живой пользователь

Статус: диагностика выполнена, причина найдена и подтверждена чтением кода.
Живой пилот цел (5 контейнеров healthy, `users=1`, frontend 200 на `8081`,
маркер `18B4ACFD…` не тронут).

---

## Исправление критерия приёмки — это была ошибка ревьюера

`setup-run.json` **удаляется при успешном завершении**. Код
`infra/windows/engine/Common.psm1`, функция `Clear-HrmSetupMarker`:

```powershell
function Clear-HrmSetupMarker {
    # Успешная установка завершена: отметка больше не нужна.
```

И `infra/windows/hr-manager.ps1` после успешной установки вызывает её и делает
`exit 0`.

**Значит: отсутствие `setup-run.json` — признак успеха, а не провала.** Прежнее
требование «`setup-run.json` должен быть успешным» противоречило коду и вело к
ложному NO-GO. Правильный критерий — код выхода 0 и завершённая регистрация.

## Почему был exit code 10

Подтверждено чтением `installer/installer.iss`:

| строка | что там |
|---|---|
| `:265` | `HrmStateDir()` = `{localappdata}\HRManager` — жёстко, `HRM_STATE_DIR` игнорируется |
| `:120` | движку при установке передают `-SourceDir "{app}"`, **но не `-InstallDir`** |
| `:115` | трею `-InstallDir "{app}"` передают — то есть возможность есть, до install-действия не доведена |

Последствие: движок убрал маркер из `C:\hrm-accept\state`, а установщик искал его
в стандартном каталоге, не нашёл и вернул 10. **Расхождение создал наш
`HRM_STATE_DIR`, а не починка первого запуска.**

При этом журнал движка фиксирует готовность, успешный claim, передачу адреса для
браузера и завершение установки. `pilot_created=false` — потому что форму
регистрации никто не заполнил, а не потому что первый запуск сломан.

---

## Перезапуск: без наших костылей

Ни `HRM_STATE_DIR`, ни `/DIR=`. Тогда обе половины согласованы на
`%LOCALAPPDATA%\HRManager` и `%LOCALAPPDATA%\Programs\HRManager` — ровно как у
живого пользователя.

### Шаг 1. Резервные копии файлов (быстро, это не тома)

```powershell
New-Item -ItemType Directory -Force C:\hrm-accept\files | Out-Null
Copy-Item "$env:LOCALAPPDATA\HRManager" -Destination "C:\hrm-accept\files\HRManager" -Recurse -Force
Copy-Item "$env:LOCALAPPDATA\Programs\HRManager" -Destination "C:\hrm-accept\files\HRManagerPrograms" -Recurse -Force
```

### Шаг 2. Остановить пилот, архивировать тома

```powershell
docker compose -p hr-manager-pilot down
docker ps -a --filter "label=com.docker.compose.project=hr-manager-pilot" --format "{{.Names}}"   # должно быть пусто

docker run --rm -v hr-manager-pilot_pilot_pgdata:/from -v C:\hrm-accept\backup:/to alpine tar czf /to/pgdata.tar.gz -C /from .
docker run --rm -v hr-manager-pilot_pilot_backups:/from -v C:\hrm-accept\backup:/to alpine tar czf /to/backups.tar.gz -C /from .
```

### Шаг 3. Проверить восстановление, потом удалить том

```powershell
docker volume create hrm-restore-check
docker run --rm -v hrm-restore-check:/to -v C:\hrm-accept\backup:/from alpine tar xzf /from/pgdata.tar.gz -C /to
docker run --rm -d --name hrm-check-db -v hrm-restore-check:/var/lib/postgresql/data postgres:16-alpine
Start-Sleep 25
docker ps --filter name=hrm-check-db --format "{{.Status}}"
docker logs hrm-check-db --tail 5
docker rm -f hrm-check-db; docker volume rm hrm-restore-check
```

Критерий: `Up …` и в логе `database system is ready to accept connections`.
Только после этого:

```powershell
docker volume rm hr-manager-pilot_pilot_pgdata
```

### Шаг 4. Запустить установщик ЧИСТО

Новое окно PowerShell, **никаких переменных окружения**:

```powershell
& "C:\путь\HR-Manager-Setup-0.15.0.exe"
```

Без `/DIR`, без `HRM_STATE_DIR`, без `HRM_INSTALL_DIR`.

### Шаг 5. Проверить — правильные критерии

1. **код выхода установщика 0** (главный признак; `setup-run.json` при успехе
   удаляется — его отсутствие нормально);
2. браузер сам открылся, экран «Первый запуск HR Manager» с окном регистрации
   (скриншот, адресную строку закрыть — в ней тикет);
3. **завершить регистрацию** в браузере;
4. приложение открылось, в трее нет «Ошибка»;
5. `$env:LOCALAPPDATA\HRManager\setup-run.json` отсутствует — так и должно быть;
6. в `installed.json` стоит `pilot_created=true`.

### Шаг 6. Вернуть пилот

```powershell
docker compose -p hr-manager-pilot down
docker volume rm hr-manager-pilot_pilot_pgdata
docker volume create hr-manager-pilot_pilot_pgdata
docker run --rm -v hr-manager-pilot_pilot_pgdata:/to -v C:\hrm-accept\backup:/from alpine tar xzf /from/pgdata.tar.gz -C /to
```

Файлы каталогов:

```powershell
Remove-Item "$env:LOCALAPPDATA\HRManager" -Recurse -Force
Copy-Item "C:\hrm-accept\files\HRManager" "$env:LOCALAPPDATA\HRManager" -Recurse -Force
```

Поднять прежний пилот его штатной командой. Проверить: версия `6ae189a`, 5
контейнеров healthy, `users=1`, frontend 200 на `8081`.

---

## Что записать в долги (не делать сейчас)

1. `installer.iss:120` — передать движку `-InstallDir "{app}"`. Без этого
   нестандартный каталог установки не работает.
2. `installer.iss:265` — `HrmStateDir()` игнорирует `HRM_STATE_DIR`, хотя движок
   его honourит. Две половины должны согласовываться.
3. Отдельно: страница выбора каталога в установщике (вопрос владельца). Это
   следствие пунктов 1-2, а не отдельная задача.

## Не делать

- Не мержить и не релизить. PR #57 остаётся draft / NO-GO.
- Не задавать `HRM_STATE_DIR` и `/DIR=` при перезапуске — именно они создали
  расхождение.
- Не удалять том без успешного шага 3.
- Не использовать `down -v` для живого пилота.
- Секреты не печатать; в скриншотах скрывать адресную строку.
