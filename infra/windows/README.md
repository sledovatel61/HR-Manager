# Движок локального пилота (Windows) — фаза 12

Это внутренняя автоматизация: обычный пользователь запускает только
`HRManager-Setup-<версия>.exe`, собранный в CI. Всё остальное делает движок
`hr-manager.ps1` — идемпотентно, по-русски, без секретов в логах.

## Состав

| Файл | Назначение |
| --- | --- |
| `hr-manager.ps1` | точка входа: `install / ensure-running / start / stop / status / update / diagnostics / uninstall / resume / repair / preflight` |
| `lib/HrManager.Common.ps1` | примитивы: состояние, логи, ACL, запуск процессов (единственная mockable-точка), редакция, контроль целостности релиза |
| `lib/HrManager.Preflight.ps1` | проверки до изменений: ОС, порт, диск, конфигурация, Docker и Compose ≥ 2.24 |
| `lib/HrManager.Secrets.ps1` | генерация локальных секретов (один раз) и `pilot.env` |
| `lib/HrManager.Actions.ps1` | установка, запуск, статус, диагностика, выдача pairing-кода |
| `lib/HrManager.Update.ps1` | обновление с блокировкой, backup-до-миграций, откатом кода |
| `lib/HrManager.Uninstall.ps1` | безопасное удаление (данные сохраняются) и полное — только по фразе |
| `installer/HrManagerPilot.iss` | графический установщик (Inno Setup, версия закреплена в `installer/inno-lock.json`) |
| `docker-desktop.json` | закреплённый официальный источник Docker Desktop (пустой sha256 = скачивание запрещено, fail closed) |
| `release/Build-Release.ps1` | сборка релизного комплекта: манифест + `SHA256SUMS.txt` |
| `tests/Invoke-HrmTests.ps1` | раннер тестов движка (без Pester, штатные средства) |
| `acceptance/Run-Windows-Acceptance.ps1` | чек-лист приёмки на живой Windows-VM (см. ниже) |

## Каталоги на машине пользователя

```
%LOCALAPPDATA%\HR Manager\app\      релизный комплект (compose-файлы, контексты сборки)
%LOCALAPPDATA%\HR Manager\app\previous\   прежние файлы для отката кода (только на время update)
%LOCALAPPDATA%\HR Manager\bin\      VBS-обёртки ярлыков (скрытый запуск)
%LOCALAPPDATA%\HR Manager\state\    config.json, secrets.json, pilot.env,
                                    progress.json, update-history.json,
                                    update.lock, update-stage.json, resume.json,
                                    pairing.json, logs\*, backup\*
```

`state\secrets.json`, `state\pilot.env`, `state\pairing.json` пишутся с ACL
«только текущий пользователь» (на Windows — явный ACL c проверкой, не-Windows
только в тестовом контуре — `chmod 600`). Не удалось ограничить — операция
прерывается (fail closed), файлы не оставляют.

## Безопасность и данные

- Секреты генерируются движком один раз и не показываются никому. Повторные
  запуски (`install`, `repair`, `resume`) их НЕ ротируют; повреждённое
  хранилище — явная ошибка 5, а не «создать заново».
- Фамилия пользователя и pairing-код передаются в backend только через STDIN
  (`docker compose exec -T backend python -m app.cli pilot-pairing issue`),
  никогда — через аргументы командной строки или URL.
- Публикуется единственный порт — `127.0.0.1:8081` (frontend). БД, backend,
  worker и backup-сервис наружу не смотрят (см. `infra/compose.pilot.yml`).
- `diagnostics` и логи пропускаются через `Protect-HrmText`; перед записью
  отчёта движок сверяет, что ни одно секретное значение в него не попало.

## Тесты (без Docker Desktop, на любом pwsh 7)

```powershell
pwsh -NoProfile -File infra/windows/tests/Invoke-HrmTests.ps1
```

Раннер сначала проверяет разбор ВСЕХ `.ps1` (аналог parse-проверки
PSScriptAnalyzer), затем гоняет сценарии: идемпотентность install, порядок
операций update (backup строго до миграций), откат только кода (миграции —
только вперёд), удаление данных только по точной фразе, редацию диагностики,
preflight-отказы. Внешние команды мокются единственной точкой
`Invoke-HrmTool` (`$script:HrmTestHooks["ToolOverride"]`).

## Про Windows PowerShell 5.1

Библиотека написана в совместимом стиле (без `??`, без тернарных). Единственное
место, где 5.1 отличается, — передача аргументов процесса: в `Invoke-HrmTool`
используется `ProcessStartInfo.ArgumentList`, а для 5.1 предусмотрен запасной
путь с экранированием по правилам Win32 (`ConvertTo-HrmCommandLine`).
Тем не менее официально поддерживаемый интерпретатор — **PowerShell 7**
(ставится из Microsoft Store / `winget install Microsoft.PowerShell`);
сценарии CI-приёмки и RunOnce-резюме вызывают `powershell.exe` (5.1),
поэтому fallback протестирован, но рекомендация для живых машин — pwsh 7.
