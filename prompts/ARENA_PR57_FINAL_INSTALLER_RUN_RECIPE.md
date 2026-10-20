# Финальный прогон установщика: архивный путь, изолированные каталоги

Статус: дефект доказан живьём (claim 201 + тикет, окно регистрации, один админ,
сетевой отказ 404, повторный claim 409, уборка выполнена). Живой пилот не тронут.

**Остался один шаг: прогон `Setup.exe` из `ca87e13` на чистом томе.**

Выбран **архивный путь**. Вторая Docker daemon отложена: движок резолвит
конкретный путь к CLI (`Resolve-HrmDockerCli`), а не берёт из PATH, поэтому
поведение `DOCKER_HOST` не гарантировано без дополнительной проверки.

---

## Что даёт изоляцию (проверено в коде)

- **StateDir** — переменная окружения `HRM_STATE_DIR`, читается в
  `Common.psm1:23` (`Get-HrmStateDir`), описана в `infra/windows/README.md:355`.
  От notё туда `setup-run.json`, `pilot.env`, `secrets.json`, `first-run-url.txt`.
  Старый `failed` в `%LOCALAPPDATA%\HRManager\setup-run.json` остаётся нетронутым.
- **Каталог установки** — задаётся **самим установщику** ключом `/DIR=`
  (Inno Setup). Переменную `HRM_INSTALL_DIR` для этого использовать нельзя: её
  читает движок, и при расхождении сработает защита `install_dir_mismatch`.
- **Имя проекта Compose** — захардкожено (`hr-manager-pilot`), изоляции нет.
  От этого защищает процедура с томами ниже.

Версии: живой пилот `6ae189a`, установщик `ca87e13`. Установщик новее и содержит
починку — расхождение ожидаемо и не является препятствием.

---

## Шаг 1. Подготовить каталоги

```powershell
New-Item -ItemType Directory -Force C:\hrm-accept\state, C:\hrm-accept\backup | Out-Null
$env:HRM_STATE_DIR = "C:\hrm-accept\state"
```

Переменная должна быть выставлена **в том же окне PowerShell**, из которого
запускается установщик, — окружение наследуется дочерними процессами.

## Шаг 2. Остановить пилот

```powershell
docker compose -p hr-manager-pilot down
```

Без `-v`. Контейнеры снимаются, тома остаются. Именно после этого том PostgreSQL
согласован.

```powershell
docker ps -a --filter "label=com.docker.compose.project=hr-manager-pilot" --format "{{.Names}}"
```

Должно быть пусто.

## Шаг 3. Архив ОБОИХ томов

```powershell
docker run --rm -v hr-manager-pilot_pilot_pgdata:/from -v C:\hrm-accept\backup:/to alpine tar czf /to/pgdata.tar.gz -C /from .
docker run --rm -v hr-manager-pilot_pilot_backups:/from -v C:\hrm-accept\backup:/to alpine tar czf /to/backups.tar.gz -C /from .
```

Архивы остаются на ПК. Внутри данные живого пилота — не загружать никуда.

## Шаг 4. Проверить восстановление, прежде чем удалять исходный том

```powershell
docker volume create hrm-restore-check
docker run --rm -v hrm-restore-check:/to -v C:\hrm-accept\backup:/from alpine tar xzf /from/pgdata.tar.gz -C /to
docker run --rm -d --name hrm-check-db -v hrm-restore-check:/var/lib/postgresql/data postgres:16-alpine
Start-Sleep 25
docker ps --filter name=hrm-check-db --format "{{.Status}}"
docker logs hrm-check-db --tail 5
docker rm -f hrm-check-db
docker volume rm hrm-restore-check
```

Критерий: статус `Up ...` и в логе `database system is ready to accept
connections`. Иначе — **стоп, том не удалять.**

## Шаг 5. Убрать том данных

```powershell
docker volume rm hr-manager-pilot_pilot_pgdata
```

Только после успешного шага 4. Том `pilot_backups` можно оставить.

## Шаг 6. Запустить установщик

```powershell
& "C:\путь\HR-Manager-Setup-0.15.0.exe" /DIR="C:\hrm-accept\install"
```

`HRM_STATE_DIR` из шага 1 всё ещё выставлен в этом окне.

Проверить:

1. код выхода установщика **0**;
2. браузер сам открылся — экран «Первый запуск HR Manager» с **окном
   регистрации** (скриншот, адресную строку скрыть — в ней тикет);
3. владелец создан через этот экран;
4. приложение открылось, в трее нет «Ошибка»;
5. `C:\hrm-accept\state\setup-run.json` — статус успешный;
6. старый `%LOCALAPPDATA%\HRManager\setup-run.json` не изменён.

## Шаг 7. Вернуть живой пилот

```powershell
docker compose -p hr-manager-pilot down
docker volume rm hr-manager-pilot_pilot_pgdata
docker volume create hr-manager-pilot_pilot_pgdata
docker run --rm -v hr-manager-pilot_pilot_pgdata:/to -v C:\hrm-accept\backup:/from alpine tar xzf /from/pgdata.tar.gz -C /to
```

Если удаляли том бэкапов — вернуть и его из `backups.tar.gz`.

Затем поднять прежний пилот его штатной командой и убедиться: прежний владелец на
месте, пароль не изменён, движок прежней версии `6ae189a`.

## Шаг 8. Повторный проход

После возврата открыть приложение: второго владельца нет, пароль не сброшен.

---

## Что прислать

1. Код выхода установщика.
2. Скриншот окна регистрации (адресная строка закрыта).
3. `C:\hrm-accept\state\setup-run.json` — статус.
4. Подтверждение, что прежний пилот и его владелец восстановлены.
5. Подтверждение, что старый `setup-run.json` в `%LOCALAPPDATA%` не изменён.

## Не делать

- Не мержить, не релизить. PR #57 остаётся draft / NO-GO.
- Не снимать архив при работающей БД.
- Не удалять том без успешного шага 4.
- Не использовать `down -v` для живого пилота.
- Секреты не печатать.
