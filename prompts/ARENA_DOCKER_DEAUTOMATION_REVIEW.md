# Аудит Docker-автоматизации (результат проверки head `1dec06d`)

Дата аудита: 2026-10-13. Проверенный коммит: `1dec06d` (head PR #55, база `5f17242`).
Все пути в документе — **относительно корня репозитория**; номера строк соответствуют
ровно `1dec06d` (для свежих коммитов требуется перепроверка `git grep -n`).

Метод: клонирование `1dec06d` в чистый каталог и чтение кода + `git grep` по всем
поверхностям (`infra/windows`, `installer`, `tests`, `.github/workflows`, `docs`, `tools`).
Ни одно утверждение ниже не основано на отчётах агентов — только на коде коммита.

Сопутствующий документ для исполнителя: `prompts/ARENA_DOCKER_DEAUTOMATION_PROMPT.md`.

---

## 1. Что сейчас делает система с Docker (фактическая архитектура)

### 1.1 Установщик и первый запуск

1. `installer/installer.iss:122` — в `[Run]` без `nowait` запускается
   `hr-manager.ps1 -Action install` (`runhidden`). Inno **не пробрасывает код возврата**
   `[Run]`, поэтому мастер всегда завершается `exit=0` (зафиксировано в
   `infra/windows/tests/ci-silent-smoke.ps1:306`); все ошибки установки видны только
   через трей и `setup-run.json`.
2. `installer/installer.iss:580-587` (`GetEngineDockerArgs`) добавляет движку
   `-Interactive -InstallDocker`, если в мастере отмечена задача `dockerinstall`
   (`installer/installer.iss:44-48`), и всегда печатает `DockerNote` (`:61-63`):
   «Docker Desktop понадобится…» / «Отмечено: установить Docker Desktop…».
3. `infra/windows/hr-manager.ps1:169,266,292` пробрасывает `AllowInstall`
   (`-InstallDocker -or $Interactive`) в `Install-HrmApp`, `Invoke-HrmDockerPrepare`,
   `Invoke-HrmSupervisorCycle`.
4. `infra/windows/engine/Install.psm1:327,392,483,607,696` принимает
   `-AllowInstall`/`-AllowDockerInstall` и вызывает `Invoke-HrmDockerPrepare`
   (`Install.psm1:483`). При `state.needs_install` и разрешении печатает гид
   `Get-HrmDockerInstallGuide` (`Install.psm1:397` → `Docker.psm1:966-976`), который
   содержит текст «Нажмите «Установить Docker Desktop» — будет скачан официальный
   установщик…».
5. `infra/windows/engine/Docker.psm1:1372-1503` — `Invoke-HrmDockerPrepare`: проверка
   порта (`:1409-1448`, сохраняемая), состояния Docker (`Get-HrmDockerReadiness`
   `:1343-1371`), при отсутствии и разрешении — `Install-HrmDockerDesktop` (`:1387`).

### 1.2 Автоустановка Docker Desktop (то, что предлагается удалить)

- `infra/windows/engine/Docker.psm1:1113-1261` — `Install-HrmDockerDesktop`:
  скачивание `Save-HrmDownload $script:DockerInstallerUrl` (`:1156`,
  `Common.psm1:467`), проверка Authenticode + издателя
  (`Test-HrmDockerInstallerTrusted` `:1186`, `Test-HrmDockerPublisherSubject`
  `:1050-1060`), запуск через `Start-HrmElevatedAndWait` (`Common.psm1:762-779`,
  единственный вызывающий в движке) без `--accept-license` (`:1177-1214`),
  fallback-визард с UAC-текстами (`:1181`, `:1226`), статусы `uac_declined`,
  `manual_required`, `installer_timeout`, `reboot_required` (`:1244-1261`),
  ожидание движка (`:1254`, `Wait-HrmDockerEngine` `:854`), pending-маркеры
  (`:1136`, `:1147`, `:1202`, `:1205`).
- `infra/windows/engine/Docker.psm1:1091-1111` — `Get-HrmDockerInstallerWaitSeconds`,
  `Get-HrmDockerInstallerTimeoutMessage` (текст таймаута `:1109`), переменная
  `$script:DockerInstallerWaitSeconds` (`:45`).
- `infra/windows/engine/Docker.psm1:1062-1089` — `Get/Set/Clear-HrmPendingDockerOperation`
  и файл `docker-pending.json`. Читатели: `Docker.psm1:1368` (поле `pending_docker`),
  `:1387-1395` (reboot-ветка подготовки), `:1450`, `:1470`, `:1501`;
  `infra/windows/engine/Install.psm1:689-701` (`Resume-HrmOperation`, вызов из
  `Install.psm1:665`); `infra/windows/engine/Tray.psm1:154` (resume при появлении трея).
- `infra/windows/engine/Docker.psm1:1453-1459` — ветка `not_installed` без разрешения:
  `state = "docker_missing"; needs_install = $true` и текст
  «Для работы HR Manager нужен Docker Desktop. Нажмите «Установить Docker Desktop» — это
  официальная программа Docker, установка займёт несколько минут.» (`:1458`);
  вызов `Install-HrmDockerDesktop` — `:1460`. В `Get-HrmDockerReadiness` то же состояние
  ставится на `:1351`, сообщение «Docker Desktop не установлен.» — `:1357`.
  **Такой кнопки нет ни в трее, ни в CLI-помощи** — UX-дефект (см. §4). При этом
  `Preflight.psm1:79` уже содержит корректный текст «Установите Docker Desktop только с
  официального сайта docker.com (лицензия принимается вручную)» — движок противоречит
  сам себе: `Invoke-HrmDockerPrepare` велит нажать кнопку, preflight — поставить вручную.
- `infra/windows/engine/Docker.psm1:1292` — шаг плана `Get-HrmSetupProgressPlan`
  «Ищем Docker Desktop», detail «мастер предложит установить» (`:1331` — про права:
  «разрешение потребуется только для установки Docker Desktop и доступа по сети»).
- CLI: `infra/windows/hr-manager.ps1:42` (`docker-install`, `docker-start` в списке
  действий), `:141-142` (справка), `:277-285` (`docker-install` → `Install-HrmApp
  -AllowDockerInstall`).
- Супервизор: `infra/windows/engine/Supervisor.psm1:647,674,688,706` — параметр
  `-AllowInstall` в цикле, передаётся в `Invoke-HrmDockerPrepare`.
- Гейт обновления: `infra/windows/engine/Update.psm1:834-840` —
  `Invoke-HrmDockerPrepare -AllowInstall:$false` (здесь автоустановка уже выключена,
  но сам параметр исчезнет).
- Аудит издателя: `infra/windows/tools/Audit-HrmDockerPublisher.ps1` (весь файл)
  качает установщик через `Save-HrmDownload` (`:69`) и проверяет
  `Test-HrmDockerInstallerTrusted`; `.github/workflows/ci.yml:955-1004` — job
  `docker-publisher-audit` скачивает ~600 МБ при каждом прогоне.

### 1.3 Проверки среды (сохраняемые, претензий нет)

- `Docker.psm1:592-627` `Get-HrmDockerDesktopState`; `:628-678` `Get-HrmWslState`;
  `:679-725` `Get-HrmVirtualizationState`; `:726-738` `Test-HrmAdministrator`;
  `:179-220` CLI-резолвер (`Get-HrmDockerCliDirs`, `Get-HrmDockerCliCandidatePaths`);
  `:436-470` `Get-HrmDockerCliPath` + кэш `:462`.
- `Docker.psm1:823-853` `Test-HrmDockerEngineReady` / `Test-HrmDockerLinuxEngineReady`;
  `:854-883` `Wait-HrmDockerEngine`; `:884-965` `Start-HrmDockerDesktop`
  (авто-запуск **уже установленного** Docker ≠ установка — рекомендуется сохранить,
  на нём держится `ci-silent-smoke`).
- `Docker.psm1:1343-1371` `Get-HrmDockerReadiness`; `:1372-1503` `Invoke-HrmDockerPrepare`
  (кроме install-ветвей); `:1505-1526` `Format-HrmDockerReadiness` (кроме `docker_missing`).
- `infra/windows/engine/Preflight.psm1:62-103` — `Test-HrmDockerCli` (текст «Установите
  Docker Desktop только с официального сайта docker.com (лицензия принимается
  вручную)» — `:79`; таймаут-проба — `:75`) и `Test-HrmDockerDaemon` (`:84-102`,
  сообщения `:96`, `:100`); `:105-231` — каталоги плагинов Compose и классификация
  вердиктов (`Get-HrmComposePluginDirs`, `Get-HrmComposePluginFacts`,
  `Get-HrmComposeVersionVerdict`); `:232-315` — `Test-HrmComposeVersion`;
  `:394-419` — `Invoke/Assert-HrmPreflight`. Это **единственная точка**, где движок
  уже говорит правильные слова про ручную установку Docker — новые тексты
  `Invoke-HrmDockerPrepare` должны быть согласованы с `:79`/`:100`.
- `infra/windows/engine/Compose.psm1` — топология, `compose`/`config`, порт-проверки
  (не трогать).
- Диагностика: `SupportBundle.psm1:368-392` (`docker_cli`, `compose_*`, `wsl_memory` —
  только чтение), `Update.psm1:955,966,970` (`docker_compose_ready` для Ctrl+F5),
  `Tray.psm1:366` (routing по «docker»).

---

## 2. Полная карта удаляемого (с точками вызова)

| Область | Файл:строки | Что удалить/переписать |
|---|---|---|
| Скачивание + запуск чужого установщика | `infra/windows/engine/Docker.psm1:1113-1261` | вся функция `Install-HrmDockerDesktop` |
| Elevated-запуск (только для Docker) | `infra/windows/engine/Common.psm1:762-779` | `Start-HrmElevatedAndWait` (проверено: `grep Start-HrmElevatedAndWait` по `infra/` даёт 1 вызов — `Docker.psm1:1177`; тестовых моков режима `elevated_wait` нет) |
| Таймаут установщика | `Docker.psm1:45`, `:1091-1111` | `$script:DockerInstallerWaitSeconds`, `Get-HrmDockerInstallerWaitSeconds`, `Get-HrmDockerInstallerTimeoutMessage` |
| Pending-резюме установки | `Docker.psm1:1062-1089`, `:1368`, `:1387-1395`, `:1450`, `:1470`, `:1501` | `Get/Set/Clear-HrmPendingDockerOperation`, файл `docker-pending.json` |
| Resume-цепочка | `infra/windows/engine/Install.psm1:689-701`, `:665` | docker-ветка `Resume-HrmOperation` |
| Резюме из трея | `infra/windows/engine/Tray.psm1:154` | чтение `docker-pending.json` |
| Гид «нажмите кнопку» | `Docker.psm1:966-976` | `Get-HrmDockerInstallGuide` (или полная замена текста) |
| Ложное состояние | `Docker.psm1:1453-1459` (текст `:1458`), `:1351`, `:1357` | `docker_missing` → новый текст без несуществующей кнопки |
| Текст про «мастер предложит» | `Docker.psm1:1290`, `:1331` | шаги плана/прав |
| CLI-действия | `infra/windows/hr-manager.ps1:42,95-97,141,277-285` | `docker-install`, `-InstallDocker`; `docker-start` (`:286-291`) оставить |
| Проброс флага | `infra/windows/hr-manager.ps1:169,266,292`; `Install.psm1:327,392,483,607,696`; `Supervisor.psm1:647,674,688,706`; `Update.psm1:834-840` | параметры `-AllowInstall`/`-AllowDockerInstall` |
| Задача мастера | `installer/installer.iss:44-48`, `:61-63`, `:122`, `:580-587` | `[Tasks] dockerinstall`, `DockerNote`, `GetEngineDockerArgs` |
| Тесты автоустановки | `infra/windows/tests/docker.tests.ps1` :74, :103, :117, :133, :155, :170, :188, :250, :281 | install-специфичные кейсы |
| Статические инварианты | `infra/windows/tests/static.tests.ps1:380-392,416-424`; `infra/windows/tests/lint-engine.py:500-509,530` | переписать под новые инварианты |
| CI-job скачивания | `.github/workflows/ci.yml:955-1004` | `docker-publisher-audit` → аудит вложенного файла, тяжёлое скачивание только на `workflow_dispatch` |
| Документация | `infra/windows/README.md:24-28,150-158,174-199,428-450,489-503`; `docs/MARIA_GUIDE.md:12-14,28,35,134`; `docs/WINDOWS_ACCEPTANCE_CHECKLIST.md:26-48`; `docs/DOCKER_RUNTIME_DECISION.md`; `docs/CURRENT_STATUS.md:225-227,238-242`; `docs/PILOT_FINAL_REPORT.md:59-60`; `docs/windows-installer-stability-2026-10-12.md` | убрать «установщик скачивает/ставит Docker» |

**Сохраняемое и используемое повторно** (не удалять!): вся диагностика окружения §1.3;
`Test-HrmDockerPublisherSubject` (`Docker.psm1:1050-1060`) и
`Test-HrmDockerInstallerTrusted` (`:978-1048`) — их нужно **перенацелить с URL-скачивания
на приложенный к дистрибутиву файл**; `Get-HrmDockerInstallerUrl` (`:580-584`) —
перенести в пиннинг URL/SHA256; `Save-HrmDownload` (`Common.psm1:467`) остаётся (есть
внешний потребитель — `tools/Audit-HrmDockerPublisher.ps1:69`); портовая логика
(`Docker.psm1:1409-1448`); allowlist тома `Remove-HrmPilotDataVolume`
(`Compose.psm1:590-597`, сторожа `static.tests.ps1:402`) — **не затрагивать**;
`Resume-HrmOperation` для не-Docker операций.

---

## 3. Проверенные инварианты тестов, которые придётся переписать

- `infra/windows/tests/static.tests.ps1:380-392` — кейс «Docker: официальный
  установщик, проверка подписи и отказ от автоматического принятия лицензии»:
  `desktop.docker.com` (`:382`), `Get-AuthenticodeSignature` (`:383`),
  запрет `--accept-license` (`:388`) — плюс полезные проверки WSL/виртуализации/
  `Wait-HrmDockerEngine`/отсутствия `rm -rf` (`:389-392`, сохранить).
  `:416-424` — кейс «установщик: значок в трее, галочка установки Docker и автозапуск
  supervisor'а»: требует `dockerinstall` (`:421`) и `GetEngineDockerArgs` (`:422`) —
  переписать под новую реальность.
- `infra/windows/tests/lint-engine.py:500-509` — гвард `--accept-license` (`:504-505`,
  сохранить), требования `desktop.docker.com` (`:506-507`) и `Get-AuthenticodeSignature`
  (`:508-509`) в `Docker.psm1` (перенацелить с «скачанный установщик» на «пин URL +
  проверка приложенного файла»); `:530` — требует в `installer.iss` наличия
  `dockerinstall` и `GetEngineDockerArgs` (переписать).
- `infra/windows/tests/ci-silent-smoke.ps1:200-215` — `Test-HrmSmokeDockerRefusal`
  (`:200`, паттерны `:206`: «Docker Desktop»/«Docker Engine»/«Linux-движок»/…),
  используется на `:298` и `:362`; описание ограниченного режима — `:253-260`.
  **Новый текст отказа без Docker обязан содержать «Docker Desktop»/«Docker Engine»**,
  иначе красный CI.
- `infra/windows/tests/docker.tests.ps1` — 34 кейса (`grep -c '^Test-Case'` = 34).
  Install-специфичные (удалить или перенацелить): `:21` (prepare «предлагает
  установку»), `:74` (официальный адрес/подпись/UAC/лицензия), `:103`
  (непроверенная подпись → fail-closed), `:117` (точность издателя), `:133`
  (subject/отпечаток — логику доверия сохранить, скачивание убрать), `:155`
  (end-to-end проверка издателя), `:170` (отмена UAC), `:188` (P3 потолок ожидания
  установщика), `:250` (P3 таймаут мастера), `:281` (авто-продолжение после
  перезагрузки). Переформулировать: `:325` (права/UAC «только для Docker и LAN»),
  `:347` (инструкция для пользователя). Сохранить: `:36`, `:60`, `:302`, `:315`,
  `:334`, `:356`, `:369`, `:380`, `:390`, `:400`, `:409`, `:420`, `:443`, `:467`,
  `:513`, `:541`, `:557`, `:591`, `:603`, `:639`, `:652`, `:677`.
- `infra/windows/tests/supervisor.tests.ps1` — кейс P2 использует
  `desktop=not_installed`/`DockerMissing`; убедиться, что после правки планов/сообщений
  кейс по-прежнему корректен (либо переименовать состояние).
- `infra/windows/tests/engine.tests.ps1:811,836` — Docker-гейты обновления
  (сохранить как есть, если текст сообщения остаётся распознаваемым).

---

## 4. UX-дефекты, которые обязаны быть закрыты в этой работе

1. **Несуществующая кнопка.** `Docker.psm1:1453-1459` (и гид `:966-976`,
   `Install.psm1:397`) требуют «Нажмите «Установить Docker Desktop»», но в трее такой
   кнопки нет, а CLI-действие `docker-install` удаляется. Ожидание: честный текст с
   официальной ссылкой и, если файл приложен к дистрибутиву — с локальным путём.
2. **Противоречие в README.** `infra/windows/README.md:157-158` утверждает
   «установщик НЕ скачивает и не ставит Docker Desktop», а `:189` и `:24-28` —
   обратное (описывают скачивание официального установщика). Ожидание: единая
   формулировка «проверяем наличие, ставить вручную».
3. **«Неготовый daemon» ≠ «Docker не установлен».** Сейчас в текстах это смешано;
   после правки состояния должны различать: не установлен / установлен, но движок не
   запущен / Compose недоступен.
4. **Setup всегда exit=0.** Inno не пробрасывает код `[Run]`
   (`ci-silent-smoke.ps1:306`), поэтому на машине без Docker пользователь видит
   успешно завершившийся мастер. Нужно, чтобы финальная страница/заметка мастера
   честно сообщала о требовании Docker (минимум — текст `DockerNote` обновить;
   максимум — читать `setup-run.json` в Pascal и показывать состояние).

---

## 5. Архитектурные выводы и рекомендации

1. **Граница ответственности.** Единственная легитимная роль продукта в отношении
   Docker — *проверка и понятная диагностика*: CLI найден? daemon готов? compose
   доступен? WSL2/виртуализация/место/порт/права? Установка Docker Desktop — ручное
   действие пользователя.
2. **Распространение установщика Docker.** Рекомендация: **не встраивать** в
   `Setup.exe` (Inno + сжатие дают ~600 МБ к дистрибутиву и требуют лицензирования
   программы в мастере); вместо этого класть `DockerDesktopInstaller.exe` **рядом** с
   `HR-Manager-Setup-<v>.exe` в `installer/output/`, фиксировать URL + SHA256 в
   `SHA256SUMS.txt` (`pilot-release.yml:139-160`) и `release-manifest.json`
   (`build.ps1:263-306`), копировать в `{app}\docker\` только если файл присутствует
   (`[Files] external` + `Check:`), и **никогда не запускать автоматически**; движок
   при отсутствии Docker указывает локальный путь (если файл на месте и SHA256 совпал)
   и официальный URL `https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe`.
3. **Переиспользование кода доверия.** `Test-HrmDockerInstallerTrusted` /
   `Test-HrmDockerPublisherSubject` / пиннинг `Get-HrmDockerInstallerUrl` сохраняются,
   но применяются к приложенному файлу (подпись + Subject + SHA256), а не к скачанному.
4. **Отсутствие файла — не ошибка.** Нет `DockerDesktopInstaller.exe` → обычное
   сообщение с официальной ссылкой, установка HR Manager не падает с «broken»-ошибкой
   раньше проверки Docker (порядок шагов сохранён: порт → Docker → preflight → env →
   compose).
5. **Pending-резюме.** `docker-pending.json` удаляется вместе с автоустановкой;
   остальная resume-механика (`update-journal`, обычные операции) сохраняется.
   «Установка продолжается сама после перезагрузки» больше не относится к Docker —
   это ожидаемо и зафиксировано в промпте.
6. **Destructive guardrails (не регрессия).** `down -v`/`volume prune` в движке
   отсутствуют (проверено); allowlist удаления тома (`Compose.psm1:590-597`) не
   трогать; пароли/секреты не должны попадать в argv, логи и диагностику.

---

## 6. Риски реализации (для контроля исполнителя)

| # | Риск | Контроль |
|---|---|---|
| R1 | Новый текст отказа не попадёт под паттерны `ci-silent-smoke.ps1:200-215` | в тексте отказа обязательно «Docker Desktop»/«Docker Engine»; локально проверить паттерны |
| R2 | Статические инварианты `static.tests.ps1` / `lint-engine.py` завалят сборку | переписать инварианты в том же коммите; сохранить гвард «без `--accept-license`» |
| R3 | Удаление pending-механики затронет режимы трея и resume | проверять все режимы `Get-TrayPlanMode` и `ci-silent-smoke` сценарии «install failed» |
| R4 | Мастер выглядит успешным без Docker | обновить `DockerNote`/финальную заметку; при наличии — состояние из `setup-run.json` |
| R5 | Встраивание 600 МБ в Setup | запрещено; только внешний файл + пин SHA256 |
| R6 | Лицензирование Docker Desktop | не принимать лицензию, не запускать без явного действия пользователя; честные категории в справке |
| R7 | Пароль PostgreSQL в volume (следствие пилота) | вне этой задачи; guardrails: тома не пересоздавать, `down -v` запрещён, пароли не в argv/логах |
| R8 | Существующие установки после обновления | `docker-pending.json` в старом state должен просто игнорироваться (resume больше не существует) |

---

## 7. Что проверено и признано корректным (не менять)

- `Get-HrmDockerDesktopState`, `Get-HrmWslState`, `Get-HrmVirtualizationState`,
  CLI-резолвер и его тесты (`docker.tests.ps1:420-513`, `:541-700`).
- `Start-HrmDockerDesktop` + `Wait-HrmDockerEngine` (авто-запуск установленного Docker).
- Портовая логика `Invoke-HrmDockerPrepare:1409-1448`, занятый порт, `Test-HrmAdministrator`.
- `Preflight.psm1` (CLI/daemon/compose) и человекочитаемые отказы `:62-103`, `:166-315`.
- `Compose.psm1` (топология, `config`, порт), `Remove-HrmPilotDataVolume` allowlist.
- `Update.psm1` Docker-гейт (остаётся проверкой, просто без параметра установки).
- Диагностика `SupportBundle.psm1` (read-only факты docker_cli/compose/wsl_memory).
- Отсутствие в движке `down -v`, `volume prune`, любых удалений томов вне allowlist.
