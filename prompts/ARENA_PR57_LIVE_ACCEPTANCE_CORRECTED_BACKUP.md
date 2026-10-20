# Живая приёмка: исправленная процедура (архив снимается после остановки)

Статус: push выполнен, удалённый head ветки `fix/first-run-claim-6ae189a` —
`ca87e133c160212c8b3ca8ea8f55906968019df2` (проверено `git ls-remote`).

Замечание локального агента по моей предыдущей версии **принимаю полностью**:
`tar` каталога PostgreSQL снимать при работающей БД нельзя — архив не
гарантирует согласованность. Порядок исправлен: сначала остановка, потом архив,
и перед удалением исходного тома — проверка восстановления на отдельном томе.

Дополнительно учтён второй том: `hr-manager-pilot_pilot_backups`
(`compose.pilot.yml`, `/var/backups/hr-manager`). В пилоте их два, не один.

---

## 0. Забрать установщик из CI

Артефакт непросроченный, прогон `39254726079`, размер 2 420 879 байт.

```powershell
gh run download 39254726079 -n pilot-setup-0.15.0-unsigned -D $HOME\hrm-artifact-80c3cb9
```

Если `gh` снова даст сетевую ошибку — взять через браузер:
`https://github.com/sledovatel61/HR-Manager/actions/runs/39254726079`,
справа блок «Artifacts», клик по `pilot-setup-0.15.0-unsigned`.

Записать provenance: коммит `80c3cb9`, дату, SHA256 `Setup.exe` и манифеста.

---

## 1. Остановить пилот — ДО любого архивирования

```powershell
docker compose -p hr-manager-pilot down
```

Без `-v`. Контейнеры снимаются, тома остаются. Именно после этого том PostgreSQL
находится в согласованном состоянии.

Проверить, что ничего не осталось:

```powershell
docker ps -a --filter "label=com.docker.compose.project=hr-manager-pilot" --format "{{.Names}}"
```

Должно быть пусто.

## 2. Архив обоих томов

```powershell
mkdir $HOME\hrm-backup-20261019
docker run --rm -v hr-manager-pilot_pilot_pgdata:/from -v ${HOME}\hrm-backup-20261019:/to alpine tar czf /to/pilot-pgdata-20261019.tar.gz -C /from .
docker run --rm -v hr-manager-pilot_pilot_backups:/from -v ${HOME}\hrm-backup-20261019:/to alpine tar czf /to/pilot-backups-20261019.tar.gz -C /from .
```

Архивы остаются на ПК. Не загружать никуда — внутри данные живого пилота.

## 3. ПРОВЕРИТЬ восстановление, прежде чем удалять исходный том

Это шаг, которого не хватало. Развернуть архив на отдельный том и поднять на нём
одноразовый PostgreSQL. Если каталог данных повреждён или архив снят криво,
контейнер не поднимется — и мы узнаем об этом **до** удаления оригинала.

```powershell
docker volume create hrm-restore-check
docker run --rm -v hrm-restore-check:/to -v ${HOME}\hrm-backup-20261019:/from alpine tar xzf /from/pilot-pgdata-20261019.tar.gz -C /to

docker run --rm -d --name hrm-restore-check-db -v hrm-restore-check:/var/lib/postgresql/data postgres:16-alpine
Start-Sleep 25
docker ps --filter name=hrm-restore-check-db --format "{{.Status}}"
docker logs hrm-restore-check-db --tail 5

docker rm -f hrm-restore-check-db
docker volume rm hrm-restore-check
```

Критерий: статус `Up ...` и в логе `database system is ready to accept
connections`. Если контейнер падает или перезапускается — **стоп, исходный том не
удалять**, разобраться почему.

## 4. Убрать том данных, чтобы установщик создал свежий

Только после успешного шага 3.

```powershell
docker volume rm hr-manager-pilot_pilot_pgdata
```

Том `pilot_backups` можно оставить: на экран регистрации он не влияет. Если
хочется полной чистоты — удалить и его тоже, он тоже в архиве.

## 5. Установить и проверить

Установщик из шага 0, **отдельный InstallDir и StateDir** (файлы живого пилота не
трогаем; имя проекта всё равно общее — от этого и защищаемся шагами 1-4).

1. код выхода установщика **0**;
2. браузер **сам** открылся на `#setup=<ticket>` — экран «Первый запуск HR
   Manager» с **окном регистрации**, а не сразу ввод логина/пароля;
3. владелец создан **только через этот экран**;
4. приложение открылось и работает;
5. в трее нет «Ошибка»;
6. `setup-run.json` — успешный;
7. с другой машины в той же сети пилот **недоступен**.

## 6. Вернуть живой пилот

```powershell
docker compose -p hr-manager-pilot down                 # снять контейнеры приёмки, без -v
docker volume rm hr-manager-pilot_pilot_pgdata          # том приёмки, он одноразовый
docker volume create hr-manager-pilot_pilot_pgdata
docker run --rm -v hr-manager-pilot_pilot_pgdata:/to -v ${HOME}\hrm-backup-20261019:/from alpine tar xzf /from/pilot-pgdata-20261019.tar.gz -C /to
```

Если удаляли и том бэкапов — вернуть и его тем же способом из
`pilot-backups-20261019.tar.gz`.

Затем поднять прежний пилот его штатной командой и убедиться: прежний владелец на
месте, пароль не изменён.

## 7. Повторный проход

После отката ещё раз открыть приложение: второго владельца быть не должно, пароль
не сбрасывается.

---

# Альтернатива без остановки живого пилота

Я проверил: движок **нигде не обращается к Docker Desktop** — во всём
`infra/windows` нет ни одной ссылки на него. Значит он просто вызывает CLI
`docker`, а тот подчиняется `DOCKER_HOST`. Поэтому можно поднять вторую демоне и
вообще ничего не трогать:

```powershell
wsl --install -d Ubuntu-24.04          # вторая копия Ubuntu рядом с существующей
wsl -d Ubuntu-24.04
# внутри: sudo apt-get update && sudo apt-get install -y docker.io
#          sudo service docker start
#          в /etc/docker/daemon.json прописать hosts: ["unix:///var/run/docker.sock","tcp://127.0.0.1:2375"]
exit
$env:DOCKER_HOST = "tcp://127.0.0.1:2375"
docker version                           # убедиться, что отвечает вторая демоне
# запустить Setup.exe в том же окне PowerShell
```

Тогда шаги 1-4 и 6 не нужны: своя демоне — свои контейнеры, тома и образы.
Установщик соберёт образы заново, это дольше, но живой пилот не трогается вообще.

**Что выбрать:** если есть возможность подождать или пилот сейчас не нужен —
проще и надёжнее путь с архивом (шаги 1-7). Если простой недопустим — вторая
демоне, но там больше мест, где что-то может не завестись.

---

## Не делать

- Не мержить, не релизить. PR #57 остаётся draft / NO-GO.
- Не снимать архив при работающей БД.
- Не удалять том без успешной проверки восстановления (шаг 3).
- Не использовать `down -v`.
- Не загружать архивы томов куда-либо.
- Секреты не печатать.
