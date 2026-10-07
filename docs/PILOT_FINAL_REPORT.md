# Итоговый отчёт: финальная пилотная сборка HR Manager 0.15.0 (Windows 10/11 x64)

Дата: 2026-10-07. Ветка: `arena/4be5f952-hr-manager` (от `main` = `9d89add`).
Аудит и план работ: `docs/PILOT_FINAL_AUDIT.md` (таблица A/B/C/D + план P1–P12).

## 1. Вердикт

> **NO-GO — остался только ручной чек-лист на реальной Windows.**
> Всё, что можно проверить машинно, проверено и зелёное. Последние прогоны ветки:
> `37660015542` (голова ветки `df25a39`; это текущий итог, все 9 джобов зелёные или пропущены по замыслу),
> `37657489369` (`4829491`), `37654388276` (`9324f52`), `37646226479` (`5d6aaf3`),
> `37642930511` (`7633eef`), `37640729694` (`7a5ed7a`) — **все обязательные джобы успешны в каждом**, включая сборку пилотного `Setup.exe` 0.15.0, сборку
> `LicenseIssuer-Portable.exe`, Pester-наборы движка, silent install/uninstall и pilot drill
> (обновление/откат/resume) на Windows-раннере.
> Хэши артефактов — в разделе 4 (там же строка «какой файл отдаём Перепечай»).
> Остаётся то, что в принципе нельзя закрыть из CI: прогон `docs/WINDOWS_ACCEPTANCE_CHECKLIST.md` на реальной машине
> (реальный Docker Desktop с UAC и перезагрузкой, трей в пользовательской сессии, сеть/LAN, SmartScreen).
> До прохождения этого чек-листа и письменного owner decision пилотный `Setup.exe` Перепечай не передаётся,
> тег и GitHub Release не создаются.

Что осталось (закрывается только на реальной Windows):

| # | Что блокирует GO | Как закрыть |
| --- | --- | --- |
| 1 | Пункты B, C чек-листа: Docker Desktop отсутствует/установлен, UAC, перезагрузка, движок, порт | Прогон `Setup.exe` на чистой ВМ/ПК по чек-листу |
| 2 | Пункты D: трей, повторный запуск без дублей, автозапуск после перезагрузки | Тот же прогон (трей живёт в пользовательской сессии — в CI его нет) |
| 3 | Пункты G: обновление N → N+1 и откат на реальном Docker | Тот же прогон (2 сборки); в CI уже пройден drill на моках движка |
| 4 | Пункты E, F: живая проверка LAN-переключателя, активация лицензии у Перепечай, окно GUI issuer | Тот же прогон |
| 5 | Письменный owner decision и заполненные отметки/доказательства чек-листа | `docs/runbook-pilot-release.md`, раздел «go/no-go evidence» |

Закрытые блокеры (были в первой редакции отчёта):

| # | Было | Чем закрыто |
| --- | --- | --- |
| 1 | Пункт H1: `run-tests.ps1` на Windows PowerShell 5.1 | Джоб `windows-installer`: 8 наборов Pester зелёные (в т.ч. `pilot-final`, `supervisor`, `stack`, `docker`) |
| 2 | Пункт H2 и сборка артефактов | Джоб `windows-installer` (Setup.exe + silent install/uninstall + drill), новый джоб `pilot-setup` (пилотный `Setup.exe` 0.15.0 + `SHA256SUMS.txt`), джоб `license-issuer-windows` (bundle + portable exe, фазы `parser/build/portable/runtime/accept`) |
| 3 | Пункт A: SHA256 артефактов | Раздел 4 этого отчёта: хэши опубликованы notice-аннотациями CI и лежат в `SHA256SUMS.txt` / `BUILD-INFO.txt` рядом с файлами |

## 2. Изменённые и созданные файлы

### Движок установки/обновления (PowerShell 5.1)
| Файл | Что сделано |
| --- | --- |
| `infra/windows/engine/Docker.psm1` *(новый)* | Определение Docker Desktop/движка/WSL2/виртуализации/прав/перезагрузки/места/порта; официальный установщик (Authenticode + Subject, никогда `--accept-license`), отмена UAC и reboot → `docker-pending.json`; ожидание движка (300 с) с прогрессом; 7-шаговый план прогресса; человеческие сообщения |
| `infra/windows/engine/SupportBundle.psm1` | Отчёт для поддержки: внутрь архива теперь кладётся `README-ПЕРЕД-ОТПРАВКОЙ.txt` — что внутри, что вырезается автоматически (секреты, адреса почты, телефоны), чего автоматика не гарантирует (имена и свободный текст из журналов) и что архив нужно просмотреть и отправить владельцу по закрытому каналу. Шапка модуля приведена в соответствие: прежняя формулировка «без ПДн и секретов» обещала больше, чем делают регулярки |
| `infra/windows/tools/Audit-HrmDockerPublisher.ps1` *(новый)* | Аудит официального установщика Docker: скачивает файл с `desktop.docker.com`, читает Authenticode-подпись, прогоняет её через ту же функцию движка, что и установка, печатает subject/отпечатки notice-аннотациями. Коды возврата: 0 — измерено (и совпало с зафиксированным издателем), 1 — тревога (подпись/издатель не совпали), 2 — измерить не удалось (нет сети — это не смена издателя) |
| `infra/windows/engine/Supervisor.psm1` *(новый)* | Mutex `Local\HRManagerPilotSupervisor`, `supervisor.json`, `action.lock`; запуск трея только в пользовательской сессии или из установщика; безопасный настраиваемый автозапуск (`autostart.json` — источник истины) |
| `infra/windows/engine/Tray.psm1` *(новый)* | NotifyIcon + меню «Открыть HR Manager / Проверить состояние / Перезапустить / Отчёт для поддержки / Остановить / Выйти», окно состояния с кнопками «Повторить / Открыть приложение / Создать отчёт», предупреждения при остановке и выходе |
| `infra/windows/hrm-tray.ps1` *(новый)* | Точка входа supervisor/трея (скрытый запуск, без дублей) |
| `infra/windows/engine/Compose.psm1` | Состояния стека `absent/stopped/partial/running/degraded/unknown`, ремонт и запуск без удаления томов, чтение головы миграций из образа (без зашитой константы) |
| `infra/windows/engine/Update.psm1` | `Get-HrmUpdatePreview` (текущая/новая версия, changelog, проверки места/лицензии/настроек/порта/сети/Docker/тома бэкапов, предупреждение о данных), `update-result.json` (`done/rolled_back/failed`), `Assert-HrmUpdatePreservedState`, фазы prepare→backup→build→switch→migrate→smoke, возврат к прежним образам, запрет даунгрейда БД |
| `infra/windows/engine/Install.psm1` | Повторный `Setup.exe` → обновление поверх; public key → каталог состояния (fail-closed); установка Docker через движок; запуск supervisor; подтверждение purge только по фразе |
| `infra/windows/engine/Common.psm1` | Швы моков, запуск процессов/повышение прав, редакция секретов в выводе и файлах; журнал (`Format-HrmLogLine` + `Write-HrmLog`) не пишет в success stream — иначе строки журнала примешивались бы к возвращаемым объектам и ломали их свойства под StrictMode; фоновый процесс получает `HRM_LOG_FILE` и ведёт журнал сам |
| `infra/windows/hr-manager.ps1` | Новые действия: `prepare`, `docker-status`, `docker-install`, `docker-start`, `supervise`, `tray`, `autostart`, `update-preview` (+ прежние), запись ошибок в `supervisor.json` |
| `installer/installer.iss` | Галочка «Установить Docker Desktop», честный текст про UAC/перезагрузку/лицензию, кодовые страницы, `[Run]` (трей → install), ярлыки, `[UninstallDelete]` без данных; пояснение про официальный установщик Docker вынесено на первый экран мастера (`CustomMessage('DockerNote')`, `%n` → переводы строк через `StringChange`) |
| `installer/build.ps1` | `release.json` с версией/`release_sha`/`built_at`/changelog; версия по умолчанию 0.15.0 |

### Тесты движка (Pester, Windows PowerShell 5.1)
| Файл | Кейсов | Что проверяет |
| --- | --- | --- |
| `infra/windows/tests/docker.tests.ps1` *(новый)* | 12 | нет Docker/UAC, официальный установщик + подпись, reboot-pending, WSL2/виртуализация, порт, права, тексты |
| `infra/windows/tests/stack.tests.ps1` *(новый)* | 8 | состояния стека, ремонт без удаления томов, повторный запуск без дублей |
| `infra/windows/tests/supervisor.tests.ps1` *(новый)* | 13 | mutex/один supervisor, `supervisor.json`, `action.lock`, автозапуск, контракт меню трея |
| `infra/windows/tests/pilot-final.tests.ps1` | 18 (+5) | support-bundle/LAN/X-Real-IP; **предпросмотр обновления**, **сохранность лицензии/порта/LAN/томов**, N→N+1, **откат + `update-result.json`** |
| `infra/windows/tests/static.tests.ps1` | 32 (+9) | статические 0.15.0-контракты: модули, меню трея, запрет `down -v`/`volume prune`, `desktop.docker.com`+подпись, installer.iss, тексты, лаунчер трея |
| `infra/windows/tests/test-harness.ps1`, `run-tests.ps1`, `lint-engine.py` | — | 16 модулей, моки `compose ps/up`, `alembic heads`, `volume inspect`, `wsl`; запуск всех восьми наборов; линтер (0 провалов, 29 файлов) |

### Лицензирование
| Файл | Что сделано |
| --- | --- |
| `tools/license-issuer/launcher/Program.cs` *(новый)* | Самодостаточный launcher одного `.exe`: ровно payload-байты копируются во временный файл и распаковываются в `%LOCALAPPDATA%\HRManager\LicenseIssuer` (ZIP больше не открывается прямо из exe с 32-байтным трейлером — именно на этом падал первый запуск в CI), GUI по двойному клику, CLI с кодом возврата, `--hrm-selfcheck`; в CLI-режиме нет модальных окон (ошибка пишется в `launcher-error.log`, ход работы — в `launcher-trace.log`), ASCII-only, без сети и без работы с ключами |
| `tools/license-issuer/build-portable.ps1` *(новый)* | Сборка `LicenseIssuer-Portable.exe` (payload + 32-байтный трейлер), проверка чтения payload-ZIP сразу после упаковки (fail-closed до сборки exe), фиксированное время записей в ZIP (1980-01-01), `BUILD-INFO.txt` с SHA256 и версиями, хэши в журнале — половинками по 32 символа (гейт «нет 64+ hex» остаётся строгим), запуск exe через `[System.Diagnostics.Process]::Start` с обязательным читаемым кодом возврата, smoke-тест цепочки через сам exe, отказ при утечке ключа, `dist/` без ключей |
| `tools/license-issuer/ci-portable-contract.py` *(новый)* | 77 контрактных проверок (ASCII/BOM, отсутствие сети и ключей в launcher, отдельная копия payload перед `ZipArchive`, совместимость трейлера, top-level layout payload, запрет `Add-Type -TypeDefinition` при обязательном явном `csc`, обязательный читаемый код возврата (`cannot read the exit code`), хэши в журнале половинками, режим `HRM_PORTABLE_LOG`, pin `cryptography==50.0.2` + наличие win_amd64-колеса, .gitignore, документация) |
| `tools/license-issuer/build.ps1` | Пин `cryptography==50.0.2` (воспроизводимость) + параметр `-CryptographyVersion` |
| `tools/license-issuer/ci-windows-checks.ps1` | Новая фаза `portable`: сборка exe под 5.1, независимая проверка трейлера и SHA256, `--hrm-selfcheck`, CLI-цепочка, отказ по подделанной лицензии, отсутствие ключей |
| `backend/app/license.py`, `backend/app/routers/license.py` | Распознавание загрузки приватного ключа (`private_key_upload`), понятные русские причины отказа |
| `backend/tests/test_license_activation_ux.py` *(новый)* | 15 кейсов активации: приватный ключ (hex/PEM/JSON), повреждённый файл, чужая подпись, подмена владельца, истёкшая лицензия, отсутствие ключа в тексте ошибки |
| `backend/tests/test_candidate_event_hooks.py` | Тест напоминаний переведён на даты относительно «сейчас» (был календарно-зависимым и «протухал») |

### CI, документация
* `.github/workflows/ci.yml` — джоб `pilot-setup` (пилотный `Setup.exe` 0.15.0 без подписи + `SHA256SUMS.txt` + артефакт `pilot-setup-0.15.0-unsigned`),
  шаг публикации хэшей notice-аннотацией в джобе `windows-installer`, фаза сборки portable exe (фаза `portable`), шаг контрактных проверок (python),
  артефакт `license-issuer-portable` с `BUILD-INFO.txt`; `pilot-release.yml` — версия по умолчанию 0.15.0 и та же публикация хэшей (для ручного запуска владельцем).
* Новые документы: `docs/PILOT_FINAL_AUDIT.md`, `docs/UPDATE_GUIDE.md`, `docs/RECOVERY_GUIDE.md`, `docs/WINDOWS_ACCEPTANCE_CHECKLIST.md`, `docs/DOCKER_RUNTIME_DECISION.md`, `docs/PILOT_FINAL_REPORT.md` (этот файл).
* Обновлены: `docs/MARIA_GUIDE.md` (0.15.0: один Setup.exe, трей, обновление, честно про UAC/перезагрузку/лицензию Docker), `docs/OWNER_QUICKSTART.md` (один `.exe` для владельца), `tools/license-issuer/README.md`.

Ответ на отдельный вопрос задания («нужен ли Docker Desktop или другой runtime»): **остаётся Docker Desktop**; сравнение вариантов, юридическое обоснование и условия перехода — `docs/DOCKER_RUNTIME_DECISION.md`.

## 3. Выполненные команды и результаты

### В этой среде (Linux, без Windows)

```bash
python3 infra/windows/tests/lint-engine.py
# Проверено источников: 29 (0 провалов); syntax/sanity 29/29; проверено тестов: 1

python3 tools/license-issuer/ci-portable-contract.py
# portable issuer contract: 77 checks passed
#   (в т.ч. PyPI: cryptography 50.0.2 cp311-abi3-win_amd64 — колесо есть)

PYTHONPATH=/tmp/pylibs python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
# ci.yml: 8 джобов, YAML валиден; pilot-release.yml валиден
```

Backend-наборы (1200+ тестов, включая лицензионные сценарии) прогонялись в этой среде ранее — см. раздел 5
аудита; в CI джобы `Backend checks` и `Backend integration tests (PostgreSQL)` зелёные на итоговом коммите.

PowerShell-наборы (Pester), сборка `Setup.exe` (Inno Setup) и portable exe (`csc.exe`) в этой среде
**не запускались** — здесь нет Windows, PowerShell и .NET Framework. Они выполняются в CI, и на итоговом
коммите все они зелёные (ниже).

### В CI (Windows-раннеры GitHub Actions)

Прогоны **`37640729694` (коммит `7a5ed7a`) и `37642930511` (итоговый head ветки `7633eef`),
ветка `arena/4be5f952-hr-manager` — 8 из 8 джобов `success` в каждом;** таблица ниже — состав работ (одинаков в обоих):

| Джоб | Что реально выполнено на Windows |
| --- | --- |
| `Windows engine tests + installer smoke` | Pester-наборы движка под Windows PowerShell 5.1 (`run-tests.ps1`, 8 наборов: static/engine/channel/installer-roots/docker/stack/supervisor/pilot-final); контракт ephemeral trust store; сборка `Setup.exe` (Inno Setup 6.7.3, SHA256 компилятора проверен); `silent install` → проверка установленного движка → `silent uninstall` → проверка, что файлы удалены; Phase 14 pilot drill (обновление/откат/resume/uninstall) |
| `Pilot Setup.exe (unsigned, 0.15.0) + SHA256SUMS` *(новый)* | `installer/build.ps1 -Version 0.15.0` без trust store (пилот не использует канал обновлений), журнал сборки в job summary, `SHA256SUMS.txt`, публикация хэшей notice-аннотацией, артефакт `pilot-setup-0.15.0-unsigned` |
| `License issuer bundle - Windows PowerShell 5.1 checks` | фазы `parser` (все .ps1 через парсер 5.1), `build` (autonomous bundle), **`portable`** (сборка `LicenseIssuer-Portable.exe`, проверка трейлера, сверка SHA256 с `BUILD-INFO.txt`, `--hrm-selfcheck`, CLI-цепочка gen-keypair → issue → verify, отказ по подделанной лицензии, поиск ключей), `runtime` (свежий unzip, путь с пробелами, скрытый системный Python, loopback-only), `accept` (28 проверок: GUI Tk-окно, Edge + WebCrypto Ed25519, firewall/нулевой исходящий трафик, 20-кратная гонка запуска, отсутствие ключей), контракт portable-issuer, backend-проверка выпущенных лицензий (7/7) |
| `Backend checks`, `Backend integration tests (PostgreSQL)`, `Frontend checks`, `Compose stack smoke test (dev + prod overlay)`, `Release pipeline fail-closed policy` | Полные наборы на том же коммите |

Проверки движка дополнительно публикуют итог аннотацией: в прогоне `37660015542` —
`HRM engine tests: ВСЕ ТЕСТЫ ПРОЙДЕНЫ (162); наборы: static, engine, channel, installer-roots, docker, stack,
supervisor, pilot-final`. По числу видно, что наборы действительно выполнялись, а не были пропущены: в этой
итерации добавились пять кейсов (предупреждение про приватность внутри архива отчёта, страховочный тест по
документации, три кейса точной проверки издателя Docker, проверка парсера для `infra/windows/tools/*.ps1`).

Ключевые строки из аннотаций прогона: `[portable] selfcheck PASS: payload 1994 files, 50,810,936 bytes`,
`[portable] portable CLI chain PASS: gen-keypair -> issue -> verify (exit codes 0)`,
`[portable] tampered license correctly rejected (exit=1)`,
`[runtime] ALL RUNTIME CHECKS PASS`, `[accept] SUMMARY: 28 checks, 28 PASS, 0 FAIL, 0 NOT VERIFIED`,
`[backend] PASS: 7/7 backend verification expectations met`.

Отдельно важен предыдущий прогон **`37638116840` (коммит `3df0ae8`, 7 из 7 джобов зелёные)**: он был первым,
где фаза `portable` собрала exe, прошла selfcheck и CLI-цепочку, — именно на нём закрылась причина падения
первого запуска артефакта (чтение ZIP прямо из exe с приклеенным трейлером).

## 4. Артефакты и их SHA256

**Правило: берётся `pilot-setup-0.15.0-unsigned` (и `license-issuer-portable`) последнего зелёного прогона
на голове ветки, а хэш — из `SHA256SUMS.txt` / `BUILD-INFO.txt` именно этого артефакта.** Любой новый коммит
(даже только документация) пересобирает `Setup.exe`, поэтому записанные здесь значения — исторические, а не
«текущий хэш проекта».

| Сборка (коммит, прогон) | `HR-Manager-Setup-0.15.0.exe` | `LicenseIssuer-Portable.exe` |
| --- | --- | --- |
| `df25a39` (последняя записанная сборка, прогон `37660015542`, 9/9 джобов зелёные) | `32A59525914456D76A9F6C3E6EF129E2AF461C2C0582FC6859CB19DDEF8C3D29` | `21DC39A2A233F5F8C36578788DC6BE60E563EAE6EE802EEC1F6A0CA2AD58E789` |
| `7324db3` + `4829491` (код правок ревью 12, прогон `37657489369`) | `F6A2417C024D39F2740FDA327DB6F0E1C0A237DAE14511E8802815465C1A19FF` | `1D507BD9A2EB978C8121BBAE1E626783B93FB7AF3B5FD85FA7061FF879654A37` |
| `5d6aaf3` (до правок ревью 12, прогон `37646226479`) | `084032F54C12F3B96EB38C48DAFBBB3A4825F44EF71280EA5F8ED739E4978349` | `1EEDA1E719B7B2330DECDB5C82A6D1E5D0CE6DC0186E81038B27BFD572457C59` |
| `7633eef` (прогон `37642930511`) | `1EDB1C593B8AE8BEECB7CADB1956A257D3E94CFF73E9C406DBC9CA00E88927B0` | `B4B07CA31F0848156DA01BD73B21758B38F52A39DD4121FCB67C7A7276A37323` |

`release-manifest.json` сборки `df25a39` — `3EC391DD46EEECA7EC5C78323FE52B312F4101E075F6C46E97A9E843F7E096F6`,
сборки `7324db3` — `98C72CF1F3B27E954615740113E9218AF6CF4D85B6548B9BE9D19AF6E448F2BC`.

Обратите внимание: правка этого абзаца тоже пересобирает `Setup.exe` (Inno Setup встраивает данные сборки), поэтому
в отчёте фиксируются значения уже проверенных прогонов, а рабочим источником остаётся `SHA256SUMS.txt` /
`BUILD-INFO.txt` того артефакта, который скачали.

Ниже — сборка коммита `7633eef` (прогон `37642930511`); в скобках рядом сборки того же кода из `7a5ed7a`
(прогон `37640729694`) и `3df0ae8` (`37638116840`), чтобы было видно: разные прогоны одного и того же кода дают
разные хэши — Inno Setup не даёт побайтовой воспроизводимости, а payload portable exe слегка зависит от среды
сборки (см. ограничение 5). Хэши опубликованы
notice-аннотациями CI (шаг «Publish the pilot artifact hashes…») и лежат рядом с файлами в артефактах —
артефакты GitHub Actions скачиваются только через веб-интерфейс, поэтому аннотации и нужны как читаемое
доказательство.

| Артефакт | Где собран | SHA256 | Размер / где лежит |
| --- | --- | --- | --- |
| `HR-Manager-Setup-0.15.0.exe` — **пилотный файл для Перепечай** (без подписи) | джоб `pilot-setup`, артефакт `pilot-setup-0.15.0-unsigned` | `1EDB1C593B8AE8BEECB7CADB1956A257D3E94CFF73E9C406DBC9CA00E88927B0` (сборка `7a5ed7a`: `3C3B4AF3683E97A74CAFF59FEF060E7DADACF60F29F887571A0BD7C3ED3B8051`) | `SHA256SUMS.txt` лежит рядом с exe в том же артефакте |
| `installer/release-manifest.json` (манифест той же сборки) | там же | `C7082EBFFEB3ACF29B6314229EC51ADE0AA5013787E058EC5050FB2E79187508` (сборка `7a5ed7a`: `03AEFBFAE94FBFB2F83268BEF73E98A40743A4435C37D8FC1B14AAE1E4126EB7`) | в том же артефакте |
| `LicenseIssuer-Portable.exe` — portable-выпуск лицензий для владельца | джоб `license-issuer-windows`, фаза `portable`, артефакт `license-issuer-portable` | `B4B07CA31F0848156DA01BD73B21758B38F52A39DD4121FCB67C7A7276A37323` (сборка `7a5ed7a`: `618B0174F154F1F7E9393DE1A85BE56FDC54E584AF9FD959FCF1E8BA534D741A`, сборка `3df0ae8`: `0BC7F5F2E2CE2BEDD2B8440F4F3D42EC064A17C7696321E767A214DE25B33167`) | 21 116 656 байт (payload 21 099 216); `sha256_exe` в `BUILD-INFO.txt` |
| `license-issuer-dist.zip` (резервный вариант для владельца: папка + bat-файлы) | тот же джоб, артефакт `license-issuer-owner` | в `SHA256SUMS`/логе джоба | — |
| **Диагностические** (не для пилота): `HR-Manager-Setup-0.13.0.exe` — smoke-сборка джоба `windows-installer`, перед загрузкой подписана ephemeral-тестовым сертификатом CI | прогон `37638116840`: как собрано `0DDA8261E9483D556327D9CF8D1BB0B9154E90994A6C54FB2DBCF9ACC403B9DD`, как загружено `5498CB2118755D92982BA49B39E941B8FC5CEC0D43F3FE0EC0AD0A01094C99D1` | 2 814 456 байт | артефакт `hr-manager-windows-setup` |

### Подпись официального установщика Docker Desktop (измерено, не выдумано)

Данные сняты с настоящего файла `Docker Desktop Installer.exe` (635 493 296 байт) аудитом
`infra/windows/tools/Audit-HrmDockerPublisher.ps1` в прогоне **`37653666837`** (Windows-раннер) и зафиксированы
в `infra/windows/engine/Docker.psm1`:

| Параметр | Значение |
| --- | --- |
| Статус подписи Windows | `Valid` |
| Subject сертификата | `CN=Docker Inc, O=Docker Inc, L=Palo Alto, S=California, C=US, SERIALNUMBER=4817464, OID.2.5.4.15=Private Organization, OID.1.3.6.1.4.1.311.60.2.1.2=Delaware, OID.1.3.6.1.4.1.311.60.2.1.3=US` |
| Отпечаток (SHA1 thumbprint), зафиксирован в движке | `b6bd29272b07ad4d0f1322a739499d67ca3bac3f` |
| SHA256-хеш сертификата (в модуль не попал: 64 hex подряд запрещены контрактом) | `a1114dec9407df1bf9e52e13917d9a4257d18f2a23198ec3d391674308c30477` |
| Срок действия сертификата | до 2027-06-25 |
| SHA256 файла установщика (сборка на 2026-10-07; меняется с каждым релизом Docker) | `a9814e31049d66156477a86614e83365669677733014ec72f74229623ff3890a` |

Правило доверия в движке теперь тройное: (1) Windows подтверждает подпись (`Valid`), и (2) отпечаток сертификата
совпадает с зафиксированным, **или** (3) CN и O сертификата равны ровно `Docker Inc` (посимвольное сравнение
разобранных атрибутов, без подстрок — прежняя проверка подстрокой «Docker» пропускала любой подписанный файл
со словом Docker в имени). Когда Docker сменит сертификат, движок честно откажется ставить Docker автоматически
(fail-closed) и предложит официальный сайт; владелец запускает аудит (`workflow_dispatch`, `docker_audit=true`),
получает новые subject/отпечатки и обновляет их в `Docker.psm1` перед сборкой релиза.

### Какой файл отдаём Перепечай (одна строка, без вариантов)

| Что именно | Из какого прогона | SHA256 | Откуда скачать |
| --- | --- | --- | --- |
| `HR-Manager-Setup-0.15.0.exe` — **файл для Марии** (без подписи, SmartScreen «Подробнее → Выполнить в любом случае») | последний зелёный прогон головы ветки; на момент сдачи — `37657489369` (коммит `4829491`, код правок ревью — `7324db3`), джоб `Pilot Setup.exe (unsigned, 0.15.0) + SHA256SUMS` | `F6A2417C024D39F2740FDA327DB6F0E1C0A237DAE14511E8802815465C1A19FF` | артефакт `pilot-setup-0.15.0-unsigned`; рядом с exe лежит `SHA256SUMS.txt` — в нём та же строка, и её же печатает notice-аннотация джоба |
| `LicenseIssuer-Portable.exe` — portable-выпуск лицензий для владельца | тот же прогон, джоб `License issuer bundle - Windows PowerShell 5.1 checks` (фаза `portable`) | `1D507BD9A2EB978C8121BBAE1E626783B93FB7AF3B5FD85FA7061FF879654A37` | артефакт `license-issuer-portable`, строка `sha256_exe` в `BUILD-INFO.txt` |

Проверка на машине: `Get-FileHash .\HR-Manager-Setup-0.15.0.exe -Algorithm SHA256` совпадает со `SHA256SUMS.txt`
из того же артефакта. Если владелец собрал релиз сам (`Pilot release`), он сверяет хэш с `SHA256SUMS.txt`
**своей** сборки — он будет другим.

Как проверить у себя (Windows PowerShell):

```powershell
Get-FileHash .\HR-Manager-Setup-0.15.0.exe -Algorithm SHA256   # сверить с таблицей и с SHA256SUMS.txt
Get-Content .\SHA256SUMS.txt                                   # 3C3B4AF3... HR-Manager-Setup-0.15.0.exe
```

**Хэш берётся из `SHA256SUMS.txt` (portable exe — строка `sha256_exe` в `BUILD-INFO.txt`) той сборки,
которую вы скачали**, а не «запоминается» из отчёта: любая новая сборка того же кода даёт другой хэш —
в том числе поэтому. Если владелец запускает workflow `Pilot release` вручную (одна кнопка, версия 0.15.0,
без тега и Release), хэш его файла будет напечатан в `SHA256SUMS.txt` и notice-аннотацией шага
«Publish the pilot artifact hashes…».

## 5. Что реально проверено, а что нет

### Итерация по ревью раунда 12 (P1–P3)

| Пункт ревью | Что сделано | Файлы |
| --- | --- | --- |
| **P1** — три ложных обещания приватности | Переписаны формулировки: обещано только то, что делает код (секреты, адреса почты, телефоны вырезаются; имена и свободный текст из журналов — **не гарантированно**, поэтому архив нужно просмотреть и отправить владельцу по закрытому каналу). В сам архив добавлен `README-ПЕРЕД-ОТПРАВКОЙ.txt` с тем же честным текстом — человек, который не программист, откроет архив, а не документацию | `docs/MARIA_GUIDE.md` (§5), `docs/CURRENT_STATUS.md` (B4), `infra/windows/README.md` (источники ops/status), `infra/windows/engine/SupportBundle.psm1` (шапка + шаг 10 сборки архива), `infra/windows/engine/Compose.psm1:241` |
| **P1** — тест, чтобы обещание не вернулось | Два новых кейса: (1) архив обязан содержать `README-ПЕРЕД-ОТПРАВКОЙ.txt` с упоминанием email/телефонов, словами «гарантирует», «могут остаться», «просмотрите», «закрытому каналу» и **без** фраз «не попадают»/«без PII»; (2) страховочный тест по семи файлам документации: фразы «не попадают» и «без PII» в контексте отчёта поддержки запрещены | `infra/windows/tests/pilot-final.tests.ps1` (2 кейса) |
| **P2** — издатель Docker проверялся подстрокой | Издатель проверяется точно: `Valid` + точный subject из списка **или** отпечаток сертификата, плюс разбор X.500 с посимвольным сравнением (ровно один `CN` и ровно один `O`, оба равны `Docker Inc`). Значения сняты аудитом с настоящего установщика (прогон `37653666837`), сверка с пин-значениями подтверждена прогоном `37654388276`. Добавлен аудит `infra/windows/tools/Audit-HrmDockerPublisher.ps1` (ручной джоб CI `docker-publisher-audit`) | `infra/windows/engine/Docker.psm1` (`Test-HrmDockerPublisherSubject`, `Test-HrmDockerInstallerTrusted`, геттеры), `infra/windows/tools/Audit-HrmDockerPublisher.ps1`, `.github/workflows/ci.yml`, `infra/windows/tests/docker.tests.ps1` (3 кейса), `infra/windows/tests/static.tests.ps1` (парсер+BOM для `tools/*.ps1`) |
| **P3.1** — `git diff --check` падал | Убрана пустая строка в конце `.github/workflows/pilot-release.yml`; гейт `git diff --check origin/main...HEAD` теперь чистый | `.github/workflows/pilot-release.yml` |
| **P3.2** — отчёт называл «итоговым head» устаревший коммит | Указаны последние прогоны и явное правило: хэш берётся из `SHA256SUMS.txt` / `BUILD-INFO.txt` **той сборки, которую скачали**; голова ветки на момент сдачи — `5d6aaf3` (прогон `37646226479`) | `docs/PILOT_FINAL_REPORT.md` (§1, §4) |
| **P3.3** — хэши разбросаны по прогонам | Добавлена таблица «Какой файл отдаём Перепечай» из двух строк: артефакт → прогон → SHA256 → откуда скачать | `docs/PILOT_FINAL_REPORT.md` (§4) |

Что осталось незакрытым из ревью: ничего. Проверка на реальной Windows (разделы A–H чек-листа) по-прежнему
не выполнена — это и есть оставшийся NO-GO, см. §1 и §7.

**Проверено машинно (Linux + Windows-раннеры CI):**
* backend: 1200+ тестов, включая лицензии (срок, подпись, подмена владельца, приватный ключ), сохранность данных
  при истечении, N→N+1 на уровне сервисов; джобы `Backend checks` и `Backend integration tests (PostgreSQL)` зелёные;
* движок установки/обновления: 8 наборов Pester под Windows PowerShell 5.1 (в т.ч. отсутствие Docker, UAC,
  reboot-pending, занятый порт, повторный запуск без дублей, состояния стека, сохранность томов, откат);
* установщик: сборка `Setup.exe` 0.15.0, `silent install` → проверка установленного движка → `silent uninstall`,
  Phase 14 pilot drill (обновление/откат/resume/uninstall);
* portable-выпуск лицензий: сборка exe, трейлер, `--hrm-selfcheck`, CLI-цепочка (gen-keypair → issue → verify),
  отказ по подделанной лицензии, отсутствие ключевого материала; на раннере дополнительно: Tk-окно GUI,
  выпуск лицензии через GUI и Edge (WebCrypto Ed25519), отсутствие исходящих соединений, 20-кратная гонка запуска;
* статические контракты: `lint-engine.py` (29 источников, 0 провалов), `ci-portable-contract.py` (77 проверок),
  валидность workflow-файлов;
* сборки артефактов и их хэши — раздел 4.

**Не проверено и не может быть проверено здесь (только реальная машина с Windows и Docker Desktop):**
* установка Docker Desktop человеком: UAC-запросы, возможная перезагрузка, «движок ещё не готов», реальный
  `docker compose`-проект `hr-manager-pilot`, порт 8080, автозапуск после перезагрузки;
* трей в пользовательской сессии (значок у часов, меню, окно состояния) и повторный запуск без дублей
  supervisor-процессов;
* живая проверка LAN-переключателя (по умолчанию `127.0.0.1`), брандмауэр и реальный второй компьютер;
* активация лицензии у Перепечай (файл `.hrmlicense` → «Лицензия активирована»), продление, истечение;
* реальное обновление N → N+1 с существующей базой и откатом (в CI — drill на моках движка, без настоящих томов);
* SmartScreen при первом запуске `Setup.exe` (сертификата нет — решение владельца от 2026-09-29).

## 6. Известные ограничения

1. **Нет сертификата Authenticode** (решение владельца от 2026-09-29): при первом запуске `Setup.exe` и portable exe
   возможен SmartScreen «Подробнее → Выполнить в любом случае». Production-подпись не выполняется;
   CI-подпись ephemeral-сертификатом — только для диагностики и никогда не попадает в пилотный файл.
2. **UAC и перезагрузка непобедимы программно**: установка Docker Desktop требует подтверждения и иногда
   перезагрузки; мастер об этом предупреждает и продолжает работу после повторного запуска (`docker-pending.json`).
3. **Лицензия Docker Desktop**: для крупных организаций (≥ 250 сотрудников или ≥ 10 млн $ выручки) нужна платная
   подписка; это написано в `docs/MARIA_GUIDE.md` и `docs/DOCKER_RUNTIME_DECISION.md`. Мы не обходим лицензию,
   UAC и политики Windows и не отключаем Defender/брандмауэр.
4. **Откат обновления не откатывает схему БД** (`alembic downgrade` не выполняется): возвращаются прежние образы,
   данные сохраняются; полное восстановление схемы — из бэкапа (`docs/RECOVERY_GUIDE.md`).
5. **Побайтовой воспроизводимости сборки portable exe нет**: состав и версии зафиксированы (Python 3.12.3,
   `cryptography==50.0.2`, Roslyn `csc`), время записей в payload-ZIP фиксировано, но три прогона одного и того же
   кода дали payload 21 099 216 / 21 099 220 / 21 099 216 байт и три разных SHA256 exe — значит, часть содержимого
   зависит от среды сборки. То же у `Setup.exe`: Inno Setup встраивает в файл данные сборки, поэтому два прогона
   одной ревизии дают разные хэши (`3C3B4AF3…` и `1EDB1C59…`). Поэтому SHA256 — характеристика конкретной сборки: он берётся из `BUILD-INFO.txt`
   (или notice-аннотации CI), а не считается «известным заранее». Если понадобится побайтовая
   воспроизводимость, нужно нормализовать то, что зависит от среды (пути/метки времени внутри бандла),
   и добавить в CI сверку повторной сборки.
6. **Автотесты движка работают на моках**: реальные контейнеры и движок в тестах не поднимаются (по замыслу —
   тесты не трогают машину), поэтому зелёные Pester-наборы не заменяют ручной прогон на Windows.
7. **GUI portable-issuer** проверяется на раннере программно (окно Tk, кнопки, диалоги), но живой клик мышью
   в пользовательской сессии — пункт F7 чек-листа.
8. **`workflow_dispatch` из среды разработки недоступен** (токен интеграции получает 403): джоб `pilot-setup`
   в основной CI закрывает потребность в пилотном `Setup.exe` и его хэше, а `Pilot release` остаётся
   ручным путём владельца (одна кнопка в веб-интерфейсе).
9. **Проверка издателя Docker — точная, поэтому её нужно обновлять при смене сертификата Docker.** Сейчас
   зафиксированы subject и отпечаток сертификата, действующего до 2027-06-25; после смены сертификата
   автоматическая установка Docker будет честно отклонена (fail-closed) до обновления значений по аудиту
   (`Actions → CI → Run workflow → docker_audit=true`). Это сознательный выбор: доверять неизвестному
   сертификату хуже, чем попросить владельца обновить пин.
10. **Подпись официального установщика Docker проверена аудитом один раз** (прогон `37653666837`) и повторно
   сверяется ручным запуском, а не на каждом прогоне CI: скачивание 635 МБ на каждый push в CI было бы
   неоправданной тратой. На машине пилота проверка выполняется всегда — перед запуском установщика.

## 7. Как закрыть NO-GO

1. Скачать артефакт `pilot-setup-0.15.0-unsigned` (или собрать `Pilot release` 0.15.0) и артефакт
   `license-issuer-portable`; сверить SHA256 с разделом 4 (`Get-FileHash`, `sha256sum -c`).
2. Выполнить `docs/WINDOWS_ACCEPTANCE_CHECKLIST.md` на чистой Windows 10/11 x64 (лучше ВМ) с этими файлами и
   лицензией `pilot.hrmlicense`, приложив доказательства к каждому пункту.
3. Отдельно пройти пункт G: собрать 0.15.0 и 0.15.1, обновиться поверх работающей установки с данными и
   проверить, что база, вложения, пользователи, настройки, лицензия и бэкапы сохранены; затем сломать обновление
   и убедиться, что вернулась прежняя версия (диагностический отчёт + `update-result.json`).
4. Заполнить отметки и доказательства в чек-листе; при замечаниях — вернуть в работу, а не «принять с оговоркой».
5. Получить письменный owner decision. Только после этого вердикт выше меняется на **GO**, и только тогда
   допустимы tag и GitHub Release.

---

*Отчёт подготовлен по итогам работ P1–P12 и итерации по ревью раунда 12 (P1–P3); подробная таблица состояния и
план — `docs/PILOT_FINAL_AUDIT.md`. Последние CI-прогоны ветки: `37660015542` (голова `df25a39`),
`37657489369` (`4829491`), `37654388276` (`9324f52`), `37646226479` (`5d6aaf3`) — в каждом все обязательные
джобы зелёные (джоб аудита издателя Docker запускается только вручную, поэтому в обычных прогонах он пропущен).*
