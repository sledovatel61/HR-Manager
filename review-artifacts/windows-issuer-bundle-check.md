# Windows Issuer Bundle Check — статус: **исправления внесены, автоматические Windows-проверки добавлены в CI; чек-лист на чистой VM — NOT RUN**

> Обновление 2026-09-24 (ветка PR #36, поверх head `c8fec38`): блокеры Windows runtime
> исправлены в `tools/license-issuer/` (см. «Что исправлено» ниже), добавлен CI job
> `license-issuer-windows`, который выполняет на windows-latest **настоящий** Windows
> PowerShell 5.1 и полную runtime-проверку бандла из свежего unzip. Ручной чек-лист
> владельца (пункты 1–6 ниже) на чистой VM без Python/интернета **всё ещё не выполнен**.
> Вердикт GO не выпускается, пока не собрано и то, и другое.

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

## Что исправлено (ветка PR #36)

| # | Блокер | Корень | Исправление |
|---|---|---|---|
| 1 | `build.ps1` не парсился Windows PowerShell 5.1 | файл UTF-8 **без BOM** + не-ASCII: 5.1 читает как ANSI; под CP1251 UTF-8-тире (`E2 80 94`) → U+201D, который токенизатор считает закрывающей кавычкой строки → parse error | файл сохранён **UTF-8 с BOM** и переписан в **ASCII-only** (двойная защита: парсится под любой кодовой страницей, даже если BOM сбросят); в вызовах `& $pyExe -c "..."` — только двойные кавычки PowerShell без вложенных двойных кавычек; в шагах CI устранены неоднозначные вложенные кавычки (строки собираются из одинарных) |
| 2 | `ModuleNotFoundError: No module named 'license_issuer'` при smoke-тесте/`run-cli.bat` | embeddable Python: `sys.path` задан **только** `python*._pth`, каталог скрипта в него **не добавляется**, `PYTHONPATH` игнорируется | build.ps1 детерминированно переписывает `python312._pth`: `python312.zip`, `.`, `..\license-issuer` (каталог приложения — сосед `python/`), `Lib\site-packages`, `import site`; в `cli.py` добавлен self-heal `sys.path.insert(0, parent)` по конвенции `gui.py` (защита в глубину) |
| 3 | Launchers | — | `run-gui/run-cli/run-html.bat`: `cd /d "%~dp0"`, все пути в кавычках (работают с пробелами), `set "PYTHONUTF8=1"` + `set "PYTHONPATH=%SCRIPT_DIR%"`, **fail-closed** (`goto :py_missing` → `exit /b 1`, `pause` только без `HRM_NO_PAUSE` для автоматизации), никакого fallback на системный Python; `run-html.bat` — `python -m http.server 8765 -b 127.0.0.1 --directory "<app>"` (только loopback, не `0.0.0.0`) |
| 4 | Сборка | — | smoke-тест расширен до `gen-keypair -> issue -> verify` во временном каталоге **вне репозитория** (удаляется в `finally`); если приватный ключ появляется в выводе любого шага — `exit 1`; нетипичные состояния (нет `python*._pth`, нет `python.exe`, импорт cryptography упал) — ненулевой код |
| 5 | Документация | — | HOWTO/README/license-owner: корректные аргументы CLI (`--client`, `--expires`, `--private-key-file`, были `--client-name`/`--expires-at`/`--private-key`) |

## Автоматические Windows-проверки (CI job `license-issuer-windows`, windows-latest)

1. **Encoding + parser check (Windows PowerShell 5.1, `shell: powershell`):**
   первые 3 байта = `EF BB BF`; 0 не-ASCII байтов; `Parser::ParseFile` — 0 ошибок.
2. **Полная сборка под 5.1:** `powershell -NoProfile -ExecutionPolicy Bypass -File tools\license-issuer\build.ps1`
   (скачивание embeddable Python + cryptography, smoke-цепочка, zip) → exit 0,
   `license-issuer-dist.zip` существует, в логе сборки **нет** непрерывных hex-обрезков 64+ символов.
3. **Runtime из свежего unzip** (каталог `$TEMP\HRM Issuer PR36 Test\unzipped bundle` — с пробелами в пути):
   - `PATH` обрезан до `C:\Windows\System32;C:\Windows` — системный Python недостижим (`Get-Command python` = пусто);
   - `run-cli.bat gen-keypair -> issue -> verify` → exit 0, поля лицензии и 128-hex подпись корректны;
   - подделанная лицензия (`max_active_users` 5→6) → `verify` **отклоняет** (exit ≠ 0);
   - приватный ключ (64 hex) отсутствует в stdout/stderr всех CLI-шагов и во всех файлах temp/логов;
   - fail-closed: bundled `python/` удалён → exit 1 и «Bundled Python not found» — и при обрезанном PATH,
     и при **наличии** системного Python на PATH (доказательство отсутствия fallback);
   - `run-html.bat`: `netstat` — LISTENING только `127.0.0.1:8765`, `0.0.0.0:8765` отсутствует;
     `GET http://127.0.0.1:8765/license-issuer.html` → HTTP 200; WMI: PID слушателя = bundled `python.exe` из unzip;
   - `run-gui.bat`: best effort (headless) — traceback/ModuleNotFoundError в логе = FAIL, процесс жив без ошибок = PASS;
   - `git status --porcelain` и `git ls-files -ci --exclude-standard` — пусты (ключей в дереве нет);
   - temp-каталог удаляется в `finally`.

## Локальная проверка в Linux sandbox (выполнено, воспроизводимо)

Скрипт: `scratch-issuer-checks/local_checks.py` (вне репозитория), 23/23 PASS:

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

- Windows runtime-блокер 1 (parser 5.1) и блокер 2 (импорт модулей) **исправлены в ветке PR #36** и покрыты
  автоматическими проверками: CI job `license-issuer-windows` выполняет их на настоящем Windows PowerShell 5.1.
- Локальная (Linux) проверка: 23/23 PASS, включая репродукцию обоих блокеров «до» и проверку «после».
- Пункты 1–6 чек-листа владельца (чистая VM без Python/интернета, Edge WebCrypto, ручное GUI) **не выполнены** —
  остаются обязательным условием GO.
- Release-вердикт: **NO-GO до** (а) зелёного CI job `license-issuer-windows` и (б) выполнения чек-листа на
  чистой VM. Merge PR #34 в main до этого не производится. После выполнения обоих условий вердикт можно
  переводить в `GO` для закрытого пилота `127.0.0.1`.
