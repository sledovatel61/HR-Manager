# Windows Issuer Bundle Check — статус: **строгая автоматическая приёмка на Windows PASS (28/28 + backend 7/7); ручные пункты на чистой VM / машине владельца — NOT RUN → NO-GO**

> Обновление 2026-09-24, вечер (ветка `arena/01a0d2c2-hr-manager`, PR #37, sha `29091fce67da024103700a2ba3333cd0b7884593`).
> Независимая перепроверка отчёта: добавлен CI-шаг строгой приёмки `tools/license-issuer/ci-windows-acceptance.ps1`
> (скрытый системный Python, очищенное окружение, firewall-правило на bundled `python.exe` с доказательством
> эффективности, захват сети по PID (WFP 5156/5157 + DNS-Client), CLI через `.bat`, GUI с реальным Tk mainloop,
> Edge WebDriver + WebCrypto, 20 запусков `run-html.bat`, скан утечек ключа) и шаг проверки выпущенных на Windows
> лицензий backend-кодом. Приёмка выявила и закрыла **три реальных дефекта продукта** (гонка `run-html.bat`,
> DNS-запросы `http.server`, сохранение приватного ключа в профиле Edge) — подробности в разделе
> «Строгая приёмка в CI». Ручные пункты (чистая VM, физические клики, машина владельца, загрузка в backend по
> HTTP с БД) **не выполнены** — вердикт **NO-GO**.
>
> Более раннее обновление: run `36014655214` (sha `4107838`, PR #37) — 7/7 jobs success; упоминавшийся ранее
> run `36014099272` относится к родительскому docs-коммиту `1a7b15b`, а не к `4107838`.

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

## Строгая приёмка в CI (run `36056102935`, job `107823315753`, sha `29091fc`) — 28/28 PASS

Хост: GitHub `windows-latest` (Windows Server 2025, build 26100), **Windows PowerShell 5.1.26100**, admin.
Job: https://github.com/sledovatel61/HR-Manager/actions/runs/36056102935/job/107823315753
(2026-09-24T20:37:23Z–20:44:38Z). Доказательства — notice-аннотации check-run (строки `[accept] ...`, `[backend] ...`).

| Проверка | Результат |
|---|---|
| Свежий unzip в `C:\HRM Acceptance Test\unzipped bundle` (пробелы), 2494 файла, без ключей/лицензий внутри | PASS |
| Системный Python скрыт: 25 `python*/py*` переименованы (toolcache, `C:\Windows\py.exe`, WindowsApps); с полным PATH раннера ничего не резолвится | PASS |
| Очищенное окружение: USERPROFILE/APPDATA/LOCALAPPDATA/TEMP/Downloads → отдельный профиль, PATH=System32, без PYTHON*/proxy, `cmd /d` | PASS (ограничение: та же учётная запись Windows) |
| Firewall: outbound Block на bundled `python.exe`; bundled python → 140.82.113.3:443 = WSAEACCES 10013, то же соединение из PowerShell проходит | PASS |
| Позитивный контроль захвата: WFP 5157 и 4 события DNS-Client атрибутированы PID пробника | PASS |
| Прокси: WinINet/WinHTTP direct, переменных нет, python видит `{}` | PASS |
| CLI через `run-cli.bat`: gen-keypair=0, повтор без `--force`=1, issue=0, verify=0, tampered=2, без аргументов=2 | PASS |
| GUI (`run-gui.bat`): окно за 313 мс; Tk 8.6.13 win32, mainloop; generate / save keys / issue / verify / tampered отклонена (кнопки через `ttk.Button.invoke()`) | PASS |
| HTML: HTTP 200; LISTEN только 127.0.0.1:8765; нет 0.0.0.0/::; UDP 0; подключение к внешнему IP хоста отклонено; процесс — bundled python | PASS |
| Гонка `run-html.bat` ×20: первый запрос после `[ready]` = HTTP 200 во всех 20; launch→ready 573/624/698 мс | PASS |
| Браузер по умолчанию открыт шлюзом готовности; страница + `nacl-fast.js` = 200 | PASS |
| Edge 152 (headed, WebDriver): secure context, WebCrypto Ed25519 generate/sign/verify | PASS |
| Edge UI: ключи WebCrypto → выпуск (TweetNaCl отключён) → проверка в странице → скачивание → `run-cli.bat verify` = 0 | PASS |
| Edge UI: загрузка CLI `private_key.hex` через выбор файла → выпуск → скачивание → verify = 0, tampered = 2 | PASS |
| Страница грузит только `http://127.0.0.1:8765/*` | PASS |
| Сеть (окно всех сценариев): 56 процессов bundled python; WFP 73 события, **все loopback**; 0 outbound/inbound не-loopback; **0 DNS-событий**; журнал Security покрывает окно | PASS |
| stdout/stderr (37 логов: CLI, GUI, сервер ×22, msedgedriver, транскрипт) — ни одного из 3 ключей | PASS |
| Скан утечек (2 прохода: с назначенными key-файлами и после их удаления): 9779 файлов / 1412 МБ в 6 корнях (стенд, `%LOCALAPPDATA%`, `%APPDATA%`, Downloads, репозиторий, RUNNER_TEMP), 3 ключа × 9 кодировок (hex/HEX/base64/base64url × ASCII/UTF-16LE + raw) — **0 совпадений** | PASS (пропущено: 2 файла >50 МБ и 35 заблокированных в `%LOCALAPPDATA%`) |
| Backend (`parse_and_verify_license_text` + `validate_time_consistency`): 7/7 — лицензии CLI/GUI/HTML приняты, tampered и чужой ключ → `bad_signature` | PASS |

### Дефекты продукта, найденные приёмкой и исправленные

1. **Гонка `run-html.bat`**: браузер открывался через фиксированные ~2 с. Теперь `open_when_ready.py` опрашивает
   страницу на 127.0.0.1 и открывает браузер только после HTTP 200 с маркером страницы (локально: сервер,
   стартующий через 1,8 с → 8× `ConnectionRefusedError`, затем 200). На раннере сервер стартует за ~0,6 с,
   поэтому старая задержка там не срабатывала бы — гонка реальна по построению, но на раннере не воспроизводится.
2. **DNS из HTML-сервера**: `python -m http.server` при старте вызывает `getaddrinfo`/`socket.getfqdn()` —
   44 события DNS-Client у bundled python (2 × 22 запуска). Заменён на `serve_loopback.py` (127.0.0.1 зашит,
   без разрешения имён, без листинга каталогов, `Cache-Control: no-store`); шлюз готовности использует сырой
   IPv4-сокет. После исправления — 0 DNS-событий.
3. **Приватный ключ в профиле Edge**: найден в `Web Data` (автозаполнение, несмотря на `autocomplete="off"`),
   `Sessions` (восстановление вкладок) и в таблице Edge `autofill_edge_field_values` (набранный текст, даже в
   `contenteditable`). Теперь ключ в HTML **не вводится с клавиатуры**: генерируется на странице или загружается
   из `private_key.hex` через выбор файла; поля ключа только для чтения и не являются элементами формы.

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
| G7 | `build.ps1` не пиннит SHA-256 для embeddable ZIP и `tcltk.msi`; `cryptography` без фиксированной версии; в бандле остаётся pip | воспроизводимость/цепочка поставки; follow-up |
| G8 | `run-html.bat`: занятый порт 8765 → понятная ошибка и exit 1, но без выбора другого порта | владелец закрывает второй экземпляр |
| G9 | HTML: `revokeObjectURL` сразу после `click()` при скачивании | в Edge 152/153 скачивание проходит (проверено в CI); теоретически хрупко |
| G10 | Не issuer: `backend/tests/test_integration_worker.py::test_end_to_end_event_to_notification` падает, если CI идёт в тихие часы 21:00–08:00 Europe/Moscow (18:00–05:00 UTC) | тест зависит от времени суток; код уведомлений и тест не менялись относительно `main` |

## Итог

- Автоматическая приёмка на настоящем Windows (PS 5.1, скрытый системный Python, firewall + захват сети по PID,
  CLI/GUI/HTML/Edge WebCrypto, скан утечек, проверка backend-кодом) — **28/28 PASS, backend 7/7** на `29091fc`.
- Найдены и исправлены 3 дефекта продукта (гонка `run-html.bat`, DNS из `http.server`, приватный ключ в профиле Edge).
- **NOT RUN:** чистая отдельная VM (CI — та же учётная запись, образ раннера с предустановленным ПО); физические
  клики человека (GUI — `invoke()`, Edge — WebDriver); машина владельца и его реальный профиль Edge; загрузка
  лицензии в backend по HTTP с БД (проверен только сервисный слой); блокировка DNS firewall-правилом (DNS идёт
  через службу DNS Client — вместо блокировки мониторинг, 0 событий).
- Release-вердикт: **NO-GO до выполнения ручных пунктов**. PR #34 в main не мержить.
