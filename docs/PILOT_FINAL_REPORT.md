# Итоговый отчёт: финальная пилотная сборка HR Manager 0.15.0 (Windows 10/11 x64)

Дата: 2026-10-07. Ветка: `arena/4be5f952-hr-manager` (от `main` = `9d89add`).
Аудит и план работ: `docs/PILOT_FINAL_AUDIT.md` (таблица A/B/C/D + план P1–P12).

## 1. Вердикт

> **NO-GO — до выполнения ручного чек-листа на Windows.**
> Код, автотесты и документация готовы; Windows-часть (реальный Docker Desktop, трей, Setup.exe, SmartScreen)
> в текущей среде разработки непроверяема: здесь нет Windows, PowerShell, Docker и Inno Setup.
> Пилотный `Setup.exe` нельзя передавать Перепечай, пока не пройден `docs/WINDOWS_ACCEPTANCE_CHECKLIST.md`
> и не заполнен раздел 6 этого отчёта.

Блокирующие пункты (каждый закрывается только доказательством с Windows):

| # | Что блокирует GO | Как закрыть |
| --- | --- | --- |
| 1 | Пункты B, C чек-листа: Docker Desktop отсутствует/установлен, UAC, перезагрузка, движок, порт | Прогон `Setup.exe` на чистой ВМ/ПК по чек-листу |
| 2 | Пункты D: трей, повторный запуск без дублей, автозапуск после перезагрузки | Тот же прогон |
| 3 | Пункты G: обновление N → N+1 и откат на реальном Docker | Тот же прогон (2 сборки) |
| 4 | Пункт A: SHA256 собранных `Setup.exe` и `LicenseIssuer-Portable.exe` | Собрать в CI (`Pilot release`, `license-issuer-windows`) и приложить хэши |
| 5 | Пункт H1: `run-tests.ps1` на Windows PowerShell 5.1 | CI-джоб `windows-installer` (Pester-наборы) |

## 2. Изменённые и созданные файлы

### Движок установки/обновления (PowerShell 5.1)
| Файл | Что сделано |
| --- | --- |
| `infra/windows/engine/Docker.psm1` *(новый)* | Определение Docker Desktop/движка/WSL2/виртуализации/прав/перезагрузки/места/порта; официальный установщик (Authenticode + Subject, никогда `--accept-license`), отмена UAC и reboot → `docker-pending.json`; ожидание движка (300 с) с прогрессом; 7-шаговый план прогресса; человеческие сообщения |
| `infra/windows/engine/Supervisor.psm1` *(новый)* | Mutex `Local\HRManagerPilotSupervisor`, `supervisor.json`, `action.lock`; запуск трея только в пользовательской сессии или из установщика; безопасный настраиваемый автозапуск (`autostart.json` — источник истины) |
| `infra/windows/engine/Tray.psm1` *(новый)* | NotifyIcon + меню «Открыть HR Manager / Проверить состояние / Перезапустить / Отчёт для поддержки / Остановить / Выйти», окно состояния с кнопками «Повторить / Открыть приложение / Создать отчёт», предупреждения при остановке и выходе |
| `infra/windows/hrm-tray.ps1` *(новый)* | Точка входа supervisor/трея (скрытый запуск, без дублей) |
| `infra/windows/engine/Compose.psm1` | Состояния стека `absent/stopped/partial/running/degraded/unknown`, ремонт и запуск без удаления томов, чтение головы миграций из образа (без зашитой константы) |
| `infra/windows/engine/Update.psm1` | `Get-HrmUpdatePreview` (текущая/новая версия, changelog, проверки места/лицензии/настроек/порта/сети/Docker/тома бэкапов, предупреждение о данных), `update-result.json` (`done/rolled_back/failed`), `Assert-HrmUpdatePreservedState`, фазы prepare→backup→build→switch→migrate→smoke, возврат к прежним образам, запрет даунгрейда БД |
| `infra/windows/engine/Install.psm1` | Повторный `Setup.exe` → обновление поверх; public key → каталог состояния (fail-closed); установка Docker через движок; запуск supervisor; подтверждение purge только по фразе |
| `infra/windows/engine/Common.psm1` | Швы моков, запуск процессов/повышение прав, редакция секретов в выводе и файлах |
| `infra/windows/hr-manager.ps1` | Новые действия: `prepare`, `docker-status`, `docker-install`, `docker-start`, `supervise`, `tray`, `autostart`, `update-preview` (+ прежние), запись ошибок в `supervisor.json` |
| `installer/installer.iss` | Галочка «Установить Docker Desktop», честный текст про UAC/перезагрузку/лицензию, кодовые страницы, `[Run]` (трей → install), ярлыки, `[UninstallDelete]` без данных |
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
| `tools/license-issuer/launcher/Program.cs` *(новый)* | Самодостаточный launcher одного `.exe`: распаковка payload в `%LOCALAPPDATA%\HRManager\LicenseIssuer`, GUI по двойному клику, CLI с кодом возврата, `--hrm-selfcheck`; ASCII-only, без сети и без работы с ключами |
| `tools/license-issuer/build-portable.ps1` *(новый)* | Сборка `LicenseIssuer-Portable.exe` (payload + 32-байтный трейлер), `BUILD-INFO.txt` с SHA256 и версиями, smoke-тест цепочки через сам exe, отказ при утечке ключа, `dist/` без ключей |
| `tools/license-issuer/ci-portable-contract.py` *(новый)* | 68 контрактных проверок (ASCII/BOM, отсутствие сети и ключей в launcher, совместимость трейлера, pin `cryptography==50.0.2` + наличие win_amd64-колеса, .gitignore, документация) |
| `tools/license-issuer/build.ps1` | Пин `cryptography==50.0.2` (воспроизводимость) + параметр `-CryptographyVersion` |
| `tools/license-issuer/ci-windows-checks.ps1` | Новая фаза `portable`: сборка exe под 5.1, независимая проверка трейлера и SHA256, `--hrm-selfcheck`, CLI-цепочка, отказ по подделанной лицензии, отсутствие ключей |
| `backend/app/license.py`, `backend/app/routers/license.py` | Распознавание загрузки приватного ключа (`private_key_upload`), понятные русские причины отказа |
| `backend/tests/test_license_activation_ux.py` *(новый)* | 15 кейсов активации: приватный ключ (hex/PEM/JSON), повреждённый файл, чужая подпись, подмена владельца, истёкшая лицензия, отсутствие ключа в тексте ошибки |
| `backend/tests/test_candidate_event_hooks.py` | Тест напоминаний переведён на даты относительно «сейчас» (был календарно-зависимым и «протухал») |

### CI, документация
* `.github/workflows/ci.yml` — шаг сборки portable exe (фаза `portable`), шаг контрактных проверок (python), артефакт `license-issuer-portable` с `BUILD-INFO.txt`; `pilot-release.yml` — версия по умолчанию 0.15.0.
* Новые документы: `docs/PILOT_FINAL_AUDIT.md`, `docs/UPDATE_GUIDE.md`, `docs/RECOVERY_GUIDE.md`, `docs/WINDOWS_ACCEPTANCE_CHECKLIST.md`, `docs/DOCKER_RUNTIME_DECISION.md`, `docs/PILOT_FINAL_REPORT.md` (этот файл).
* Обновлены: `docs/MARIA_GUIDE.md` (0.15.0: один Setup.exe, трей, обновление, честно про UAC/перезагрузку/лицензию Docker), `docs/OWNER_QUICKSTART.md` (один `.exe` для владельца), `tools/license-issuer/README.md`.

Ответ на отдельный вопрос задания («нужен ли Docker Desktop или другой runtime»): **остаётся Docker Desktop**; сравнение вариантов, юридическое обоснование и условия перехода — `docs/DOCKER_RUNTIME_DECISION.md`.

## 3. Выполненные команды и результат (в этой среде)

```bash
python3 infra/windows/tests/lint-engine.py
# Проверено файлов: 29; структурная проверка пройдена (0 провалов)

python3 tools/license-issuer/ci-portable-contract.py
# portable issuer contract: 68 checks passed (в т.ч. PyPI: cryptography 50.0.2 cp311-abi3-win_amd64 — есть)

python3 -m venv /tmp/venv && /tmp/venv/bin/pip install -r backend/requirements-dev.txt
cd backend && /tmp/venv/bin/python -m pytest -q -p no:randomly
# 1200 passed, 146 skipped (146 — интеграционные, требуют PostgreSQL/TEST_DATABASE_URL)

/tmp/venv/bin/python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
# все workflow-файлы: YAML валиден, джоб license-issuer-windows содержит новые шаги
```

Честно: PowerShell-наборы (Pester), сборка `Setup.exe` (Inno Setup + Authenticode-проверки) и сборка portable exe
(`csc.exe`) в этой среде **не запускались** — здесь нет Windows, PowerShell, Docker и Visual Studio/.NET Framework.
Они запускаются в CI: джоб `windows-installer` (`run-tests.ps1`, `installer/build.ps1`) и `license-issuer-windows`
(фазы `parser/build/portable/runtime/accept`).

## 4. Артефакты

| Артефакт | Где собирается | SHA256 |
| --- | --- | --- |
| `HR-Manager-Setup-0.15.0.exe` | CI `Pilot release` (или `installer/build.ps1 -Version 0.15.0`) | заполнить после сборки (пункт A1 чек-листа) |
| `LicenseIssuer-Portable.exe` + `BUILD-INFO.txt` | CI `license-issuer-windows` (фаза `portable`), артефакт `license-issuer-portable` | заполнить после сборки (пункт A2) |
| `license-issuer-dist.zip` (резервный вариант для владельца) | там же, артефакт `license-issuer-owner` | — |

Приватный ключ лицензии в артефакты **не попадает**: сборка падает, если ключевой материал появляется в логе или в `dist/`
(проверяется в `build.ps1`, `build-portable.ps1` и в фазе `portable`).

## 5. Что реально проверено, а что нет

**Проверено здесь (Linux, без Windows):**
* backend: 1200 тестов, включая лицензии (срок, подпись, подмена владельца, приватный ключ), сохранность данных при истечении, N→N+1 на уровне сервисов;
* статический контур движка (`lint-engine.py`): BOM, синтаксические маркеры, запрещённые команды (`down -v`, `volume prune`, `--accept-license`), контракты меню трея, `desktop.docker.com` + Authenticode;
* контракт portable-issuer (68 проверок) и валидность CI-конфигураций.

**Не проверено здесь (только Windows/CI):**
* Pester-наборы движка (88 кейсов в 8 файлах) — `run-tests.ps1`;
* сборка `Setup.exe` (Inno Setup) и portable `.exe` (`csc.exe`);
* реальные Docker Desktop (установка, UAC, перезагрузка, WSL2, движок), WinForms-трей, автозапуск, SmartScreen;
* ручной чек-лист приёмки целиком (разделы A–H).

## 6. Известные ограничения

1. **Нет сертификата Authenticode** (решение владельца от 2026-09-29): при первом запуске `Setup.exe` и portable exe
   возможен SmartScreen «Подробнее → Выполнить в любом случае». В документации это сказано честно; production-подпись не выполняется.
2. **UAC и перезагрузка непобедимы программно**: установка Docker Desktop требует подтверждения пользователя и иногда
   перезагрузки; мастер об этом предупреждает и продолжает работу после повторного запуска (`docker-pending.json`).
3. **Лицензия Docker Desktop**: для крупных организаций (≥ 250 сотрудников или ≥ 10 млн $ выручки) требуется платная подписка;
   это написано в `docs/MARIA_GUIDE.md` и `docs/DOCKER_RUNTIME_DECISION.md`.
4. **Откат обновления не откатывает схему БД** (`alembic downgrade` не выполняется): возвращаются прежние образы,
   данные сохраняются; полное восстановление схемы — из бэкапа (`docs/RECOVERY_GUIDE.md`).
5. **GUI portable-issuer** в headless-раннере не проверяется — проверяются распаковка, CLI-цепочка, отказ по подделанной лицензии;
   окно GUI подтверждается пунктом F7 чек-листа.
6. **Автотесты движка работают на моках**: реальные контейнеры/движок не поднимаются (по замыслу — тесты не трогают машину),
   поэтому «зелёные» Pester-наборы не заменяют ручной прогон на Windows.

## 7. Как закрыть NO-GO

1. Прогнать CI: `windows-installer` (Pester + сборка `Setup.exe`) и `license-issuer-windows` (фаза `portable`).
2. Собрать две версии (`0.15.0` и `0.15.1`) для пункта G чек-листа.
3. Выполнить `docs/WINDOWS_ACCEPTANCE_CHECKLIST.md` на чистой Windows 10/11 x64 и приложить доказательства.
4. Заполнить SHA256 в разделе 4 и результаты в разделе 5–6 этого файла.
5. Только после этого вердикт может быть изменён на **GO** — решением владельца.

---

*Отчёт подготовлен по итогам работ P1–P12; подробная таблица состояния и план — `docs/PILOT_FINAL_AUDIT.md`.*
