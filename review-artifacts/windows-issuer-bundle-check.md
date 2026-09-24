# Windows Issuer Bundle Check — статус: **автоматические Windows-проверки PASS (CI, Windows PowerShell 5.1); ручной чек-лист на чистой VM — NOT RUN**

> Обновление 2026-09-24 (ветка `arena/01a0d2c2-hr-manager` = head PR #36 `c8fec38` + исправления, PR #37).
> CI job `license-issuer-windows` на windows-latest под **Windows PowerShell 5.1.26100** зелёный
> (run `36014099272`, sha `1a7b15b`): parser-check, полная сборка, runtime-проверки бандла из свежего
> unzip — все PASS (доказательство ниже, из notice-аннотаций check-run). Ручной чек-лист владельца
> (пункты 1–6: чистая VM без Python/интернета, реальный Edge, интерактивный GUI) **не выполнен**.
> Вердикт GO не выпускается, пока он не выполнен.

Ревьюируемые ревизии: PR #34 head `e59aa5b7a3df49b61a8b7c599601bfbd4e9b2784` +
fix-коммит `c515a490db354436dbd0d18115e28ef5d8ece132` (патч `review-artifacts/license-issuer-fixes.patch`).
Связанные артефакты: `issuer-offline-evidence.json/.md`, `final-verdict.md`, `ci-run-status.json`.

## Цель

Проверить автономный issuer bundle на **чистой Windows 10/11 без установленного Python/pip и без интернета**:

- сборка `license-issuer-dist.zip` (`build.ps1`);
- распаковка и запуск `run-gui.bat` (Tkinter GUI) и `run-html.bat` (страница `http://127.0.0.1:8765/…`);
- выпуск и проверка лицензии обоими способами, загрузка обеих лицензий в backend;
- отсутствие исходящих сетевых запросов;
- отсутствие приватного ключа в логах, `%TEMP%`, `%APPDATA%`, папке бундла и загрузках браузера.

## Почему BLOCKED: у ревью-среды нет Windows и нет способа его получить

Измерено в этой среде (сессия 2026-09-24):

| Проверка | Результат |
|---|---|
| `/dev/kvm` | отсутствует |
| флаги `vmx`/`svm` в `/proc/cpuinfo` | 0 совпадений → аппаратная виртуализация недоступна |
| `qemu-system-x86_64`, `qemu-img`, `wine`, `pwsh` | не установлены |
| установка пакетов (`apt-get`, в т.ч. от root) | не работает: сеть до `deb.debian.org` недоступна |
| скачивание установочных образов/утилит (`software-download.microsoft.com`, `download.microsoft.com`, `archive.ubuntu.com`) | HTTP `000` (нет соединения) |

Вывод: ни Windows-VM, ни wine/pwsh, ни даже возможность скачать ISO — поэтому runtime-тест **не проводился**.
`GO` без него не выпускается, merge не рекомендуется.

## Чек-лист владельца (всё ещё не выполнен)

Подготовка: VM Windows 10/11 без Python/pip; после копирования бундла отключить сетевой адаптер.
`license-issuer-dist.zip` собирается мейнтейнером один раз с интернетом:

```powershell
powershell -ExecutionPolicy Bypass -File tools/license-issuer/build.ps1
# создаёт dist/python/ (embeddable Python 3.12.3 + cryptography), dist/license-issuer/, dist/license-issuer-dist.zip
```

1. **GUI**: распаковать zip (`C:\HR-License\`) → двойной клик `run-gui.bat` → «Сгенерировать новую пару» →
   «Сохранить ключи в папку» (в VeraCrypt/BitLocker/шифрованную флешку) → синтетическое имя клиента,
   `expires_at` в будущем, лимит 5 → «Выпустить и сохранить `.hrmlicense`» → вкладка «3. Проверить» →
   «Подпись корректна».
2. **HTML**: двойной клик `run-html.bat` → в Edge открывается `http://127.0.0.1:8765/license-issuer.html`
   (сервер обязан слушать только loopback) → те же операции → «Скачать `.hrmlicense`» →
   «Проверить подпись текущей лицензии» → «Подпись корректна».
3. **Backend**: отдать публичный ключ (`public_key.b64`, 44 символа) и оба `*.hrmlicense` на загрузку через
   «Рабочее пространство → Лицензия» → видны срок/лимит/дни; попытка загрузить подделанный файл отклоняется.
4. **Сеть**: Wireshark или Resource Monitor → `python.exe` не делает исходящих соединений; допустимо только
   слушание `127.0.0.1:8765` (в исправленном `run-html.bat` это `-b 127.0.0.1`).
5. **Приватный ключ**: поиск 64-символьного hex приватного ключа в `%TEMP%`, `%APPDATA%`, папке бундла,
   папке загрузок браузера и в окне консоли → **не найден**; в git ничего не добавлено
   (`.gitignore` теперь блокирует `keys/`, `private_key.hex`, `public_key.b64`, `*.hrmlicense`).
6. **Логи**: `build.ps1` печатает только пути и отпечатки; если приватный ключ когда-либо попадёт в вывод сборки,
   smoke-тест останавливает билд (`refusing to continue`) — подтвердить по логу.

## Что уже проверено без Windows (Linux, воспроизводимо) — PASS

Генератор `review-artifacts/gen_issuer_offline_evidence.py`: **39 PASS · 0 FAIL · 3 INFO · 3 GAP · 8 NOT RUN**.
Дерево под тестом материализуется из git-объектов **вне репозитория** (дерево PR head + оверлей fix-коммита).

- `cli.py gen-keypair → issue → verify` под harness'ом, запрещающим сокеты → **0 сетевых вызовов**;
  приватный ключ не попадает в stdout/stderr;
- offline-HTML (inline-скрипт страницы в JS VM с заглушкой DOM, сетевые API Node запрещены и подсчитаны):
  пути WebCrypto и TweetNaCl-fallback — 0 сетевых попыток;
- backend **своей** функцией `parse_and_verify_license_text` + `validate_time_consistency` принимает лицензии,
  выпущенные CLI, HTML/WebCrypto и HTML/TweetNaCl; подделка полей и чужой ключ → `bad_signature`;
- исправления проверены «до/после»: на `e59aa5b` соответствующие проверки падают, на `c515a49` — проходят;
- loopback измерен: с `-b 127.0.0.1` процесс слушает только `127.0.0.1`, без `-b` — `0.0.0.0` (контрольный прогон);
- `.gitignore` блокирует ключи и лицензии, при этом `git ls-files -ci --exclude-standard` пуст —
  ни один уже отслеживаемый файл не скрыт.

## Что исправлено (ветка PR #36 → PR #37)

| # | Блокер | Корень | Исправление |
|---|---|---|---|
| 1 | `build.ps1` не парсился Windows PowerShell 5.1 | UTF-8 **без BOM** + не-ASCII: 5.1 читает `.ps1` как ANSI; под CP1251 UTF-8-тире → U+201D, токенизатор видит закрывающую кавычку | **UTF-8 BOM + ASCII-only**; `& $pyExe -c "..."` без вложенных двойных кавычек |
| 2 | `ModuleNotFoundError: No module named 'license_issuer'` | embeddable Python строит `sys.path` только из `python*._pth`, каталог скрипта не добавляется, `PYTHONPATH` игнорируется | детерминированный `python312._pth` (`python312.zip`, `.`, `..\license-issuer`, `Lib\site-packages`, `import site`) — функция `Set-BundledPth`, вызывается сразу после распаковки и для кешированного дерева; self-heal в `cli.py` по конвенции `gui.py` |
| 3 | `python -m pip` в embeddable не мог работать | стоковый `._pth` — `#import site`, нет `Lib\site-packages`; патч применялся после pip | патч до pip (см. 2) |
| 4 | pip мог прерываться на предупреждениях | 5.1 + `EAP=Stop` + перенаправленный host: stderr нативной команды → terminating error | build-time вызовы по exit code (`Invoke-NativeLogged`), fail-closed |
| 5 | `run-gui.bat`: `No module named 'tkinter'` | embeddable Python **без tkinter/Tcl/Tk** | официальный `tcltk.msi` той же версии, `msiexec /a` (без реестра), `_tkinter.pyd`+DLL → `python\`, `tkinter\`, `tcl\`; проверка `tkinter.Tcl()` |
| 6 | `run-html.bat` отдавал 404 | `--directory "%SCRIPT_DIR%"`: `%~dp0` оканчивается на `\`, `\"` в argv Windows — экранированная кавычка → каталог с кавычкой в конце | `--directory "%SCRIPT_DIR%."` |
| 7 | Launchers | — | `cd /d "%~dp0"`, кавычки вокруг путей (пробелы), fail-closed без bundled `python.exe` (`exit /b 1`, без fallback на системный Python), `-b 127.0.0.1`, браузер после старта сервера, `HRM_NO_PAUSE`/`HRM_NO_BROWSER` для автоматизации |
| 8 | Сборка | — | smoke `gen-keypair -> issue -> verify` во временном каталоге вне репозитория (удаляется), утечка приватного ключа в вывод → exit 1, любой сбой → ненулевой код |
| 9 | Документация | — | корректные аргументы CLI (`--client`, `--expires`, `--private-key-file`) |

## Автоматические Windows-проверки — PASS (CI job `license-issuer-windows`)

Код: `tools/license-issuer/ci-windows-checks.ps1` (фазы `parser`, `build`, `runtime`; ASCII + UTF-8 BOM).
Run `36014099272`, sha `1a7b15b`, windows-latest, notice-аннотации check-run (дословно, пути сокращены):

```text
[parser] PASS: build.ps1 - UTF-8 BOM present, ASCII-only, 0 parser errors under Windows PowerShell 5.1.26100.33296
[parser] PASS: ci-windows-checks.ps1 - UTF-8 BOM present, ASCII-only, 0 parser errors under Windows PowerShell 5.1.26100.33296
[parser] PASS: windows-vm-checklist.ps1 - UTF-8 BOM present, ASCII-only, 0 parser errors under Windows PowerShell 5.1.26100.33296
[build] build.ps1 exit code: 0
[build] PASS: zip present, no 64+ hex material in the build log
[runtime] bundle unzipped to: %TEMP%\HRM Issuer PR36 Test\unzipped bundle
[runtime] CLI chain PASS: gen-keypair -> issue -> verify (bundled python only, no system python on PATH)
[runtime] tampered license correctly rejected (exit=2)
[runtime] no private key material in CLI stdout/stderr
[runtime] fail-closed (isolated PATH) PASS: exit=1
[runtime] fail-closed (system python present) PASS: exit=1, no fallback
[runtime] loopback check PASS: LISTENING only on 127.0.0.1:8765
[runtime] listener command line: "...\license-issuer\..\python\python.exe" -m http.server 8765 -b 127.0.0.1 --directory "...\license-issuer\."
[runtime] HTML page served: HTTP 200, 17416 bytes
[runtime] HTML listener PID 5796 runs the bundled python.exe (WMI verified)
[runtime] GUI check PASS: process stayed alive without import errors (killed after check)
[runtime] key-material sweep done: 2514 files scanned, private key not found
[runtime] git tree clean; no tracked file hidden by key-material ignore patterns
[runtime] ALL RUNTIME CHECKS PASS
[runtime] temporary test directory removed: %TEMP%\HRM Issuer PR36 Test
```

Ограничения CI (поэтому ручной чек-лист остаётся обязательным): у раннера есть интернет и
предустановленный Python (runtime-проверки изолируют PATH, но машина не «чистая»); GUI проверяется
только как «процесс стартует без ошибок импорта» — выпуск лицензии кликами не проверен; HTML-страница
отдаётся, но WebCrypto Ed25519 в реальном Edge не проверялся; исходящий трафик не мониторился.

Ручной прогон на Windows-машине: `tools/license-issuer/windows-vm-checklist.ps1` (сборка в
`C:\Users\User\Documents\HR\issuer-pr36-test`, распаковка в `...-output`, точные команды и exit codes в выводе).

## Локальная проверка в Linux sandbox (выполнено, воспроизводимо)

Скрипт: `scratch-issuer-checks/local_checks.py` (вне репозитория), 25/25 PASS:

- BOM + ASCII-only нового `build.ps1`; у старой ревизии `c8fec38` — без BOM, 60 не-ASCII байтов;
- **симуляция ANSI-чтения 5.1**: старая ревизия под CP1251 даёт 19 токенов-кавычек U+201C/U+201D
  (повтор parse-проблемы), новая ревизия — 0 под CP1251/CP866/CP437;
- структурный scan кавычек/here-строк `build.ps1` и всех 3 run-блоков CI-джоба — сбалансированы;
- контракт сгенерированных `.bat` (fail-closed, кавычки, `cd /d`, `-b 127.0.0.1`, `HRM_NO_PAUSE`, ...) — 31 sub-check;
- **эмуляция sys.path embeddable Python** (без каталога скрипта): старый `cli.py` → `ModuleNotFoundError`
  (репродукция блокера), новый `cli.py` с `..\license-issuer`-входом в путь → `gen-keypair -> issue -> verify`
  exit 0, подделка отклоняется (exit 2), приватный ключ не в выводе (реальный cryptography, Ed25519).

## Что публиковать как доказательство (только redacted!)

Можно: HTTP-код и размер отданной страницы, «Подпись корректна», скриншоты GUI без полей ключей, первые
16 hex отпечатка публичного ключа, `expires_at`/лимит, вывод «0 исходящих соединений», вывод grep об отсутствии
приватного ключа, версии (Windows, Edge, версия embeddable Python).

**Нельзя:** приватный ключ ни в каком виде (включая фрагменты), полный публичный ключ, полный JSON лицензии с
подписью, реальные имена/контакты (PII), скриншоты с открытым полем приватного ключа.

## Известные остаточные дельты (не блокируют пилот, кандидаты на follow-up)

| ID | Дельта | Эффект |
|---|---|---|
| G5 | **HTML**-issuer подписывает лицензию с `expires_at` в прошлом (нет проверки `issued_at <= expires_at`); CLI такой ввод отклоняет | backend отклонит загрузку (`bad_date`) — понятная ошибка, данные не теряются |
| G6 | Оба issuer'а подписывают `client_name` с управляющими символами | backend отклонит (`bad_value`); из CLI достижимо через аргумент, в HTML `<input>` обычно не даёт ввести перевод строки |

## Итог

- Блокеры Windows runtime (parser 5.1, импорт `license_issuer`, pip в embeddable, tkinter, 404 HTML)
  **исправлены** и покрыты автоматическими проверками; CI job `license-issuer-windows` под настоящим
  Windows PowerShell 5.1 — **зелёный**.
- Пункты 1–6 чек-листа владельца (чистая VM без Python/интернета, интерактивный GUI, Edge WebCrypto,
  загрузка лицензий в backend, контроль исходящего трафика) **не выполнены**.
- Release-вердикт: **NO-GO до выполнения ручного чек-листа**. PR #34 в main не мержить.
