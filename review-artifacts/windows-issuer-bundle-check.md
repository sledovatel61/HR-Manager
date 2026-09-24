# Windows Issuer Bundle Check — статус: **BLOCKED (не выполнено)**

Проверяемый HEAD: `e59aa5b7a3df49b61a8b7c599601bfbd4e9b2784` (PR #34), CI run `35965657324`.
Связанные артефакты: `issuer-offline-evidence.json/.md`, `final-verdict.md`, `ci-run-status.json`.

## Цель

Проверить автономный issuer bundle на **чистой Windows 10/11 без установленного Python, pip и интернета**:

- распаковка `license-issuer-dist.zip`;
- двойной клик `run-gui.bat` (Tkinter GUI) и `run-html.bat` (страница на `http://localhost:8765`);
- выпуск лицензии через GUI и через HTML, проверка формата `*.hrmlicense`;
- отсутствие исходящих сетевых запросов;
- отсутствие приватного ключа в логах, temp, артефактах и в git.

## Почему это по-прежнему BLOCKED

Проверка **не выполнялась**: в среде ревью (Linux sandbox) нет Windows и нет способа его запустить —
`/dev/kvm` отсутствует, флагов `vmx`/`svm` в `/proc/cpuinfo` нет (0 совпадений), нет `qemu`, нет `wine`, нет
`pwsh`. Реальный Windows 10/11 runtime-тест провести невозможно, а `GO` без него выпускать нельзя.

Пункты ниже отмечены `NOT RUN` в `issuer-offline-evidence.json`:

- [ ] `windows_vm_run_gui_bat` — двойной клик `run-gui.bat` без системного Python;
- [ ] `windows_vm_run_html_bat` — двойной клик `run-html.bat`, страница в Edge;
- [ ] `windows_vm_issue_license_via_gui` — выпуск реального `*.hrmlicense` через GUI;
- [ ] `windows_vm_issue_license_via_html` — выпуск реального `*.hrmlicense` через HTML;
- [ ] `windows_vm_no_network_capture` — Wireshark / Resource Monitor: нет исходящих соединений;
- [ ] `windows_vm_no_private_key_in_logs_or_artifacts` — grep по `%TEMP%`, `%APPDATA%`, папке бундла;
- [ ] `gui_tkinter_runtime` — реальный запуск `gui.py` (нужен рабочий стол);
- [ ] `bundle_build_with_embeddable_python` — запуск `build.ps1` (нужен интернет к python.org на этапе сборки).

## Что проверено вместо этого (Linux, воспроизводимо) — PASS

Генератор: `review-artifacts/gen_issuer_offline_evidence.py` (30 PASS / 0 FAIL / 8 GAP / 8 NOT RUN).
Он извлекает код коммита `e59aa5b…` через `git archive` во временный каталог **вне** репозитория и запускает
настоящий код issuer'а.

- `cli.py gen-keypair → issue → verify` — exit 0/0/0 под harness'ом, запрещающим сокеты
  (`socket.connect/connect_ex/sendto/create_connection/getaddrinfo` бросают исключение): **0 сетевых вызовов**;
- приватный ключ **не появился** в stdout/stderr этих трёх команд;
- backend-верификация выпущенной лицензии **настоящей функцией backend'а**
  `app.services.license_service.parse_and_verify_license_text` + `app.license.validate_time_consistency`
  (путь `POST /license/upload`) — принято; подделка `max_active_users` и чужой публичный ключ — отклонены
  (`bad_signature`);
- offline HTML: inline-скрипт страницы запущен в JS VM с заглушкой DOM, сетевые API Node запрещены и
  подсчитаны — **0 попыток**; путь WebCrypto (Edge 120+) и путь TweetNaCl-fallback (без `crypto.subtle`,
  это и есть путь для `file://`) — оба подписывают, и **обе** лицензии принимает backend;
- страница не тянет внешних ресурсов: только локальный `<script src="nacl-fast.js">`, ни одного `http(s)` в
  `src`/`href`/`@import`/`url()`;
- в Python-исходниках issuer'а нет `socket`/`urllib`/`requests`/`subprocess`/`webbrowser`; в `nacl-fast.js`
  нет `fetch`/XHR/WebSocket;
- `python -m http.server` (эмуляция `run-html.bat` на этой машине) отдаёт страницу: HTTP 200, байт-в-байт;
- 525 файлов репозитория: нет `*.hrmlicense`, нет `infra/license/public_key.b64`, нет `keys/`, нет 64-hex
  литералов в `tools/license-issuer/*.py`.

### Особенность: `.bat`-файлов в репозитории нет

`run-gui.bat`, `run-cli.bat` и `run-html.bat` существуют **только** как here-strings внутри
`tools/license-issuer/build.ps1` и пишутся на диск во время сборки бундла (SHA-256 содержимого —
`ea7d76436a6a…`, `b345a6a65c85…`, `0d836f98b025…`). Поэтому «нажать `run-gui.bat`» можно только после
`build.ps1`, а содержимое лаунчеров проверено здесь **статически**, не запуском.

## Находки по лаунчерам (нужны правки владельца/мейнтейнера; в этом ревью код не менялся)

| ID | Находка | Влияние |
|---|---|---|
| G1 | `run-html.bat` при отсутствии `..\python\python.exe` делает `set PY_EXE=python`, то есть **молча падает на системный Python**, хотя комментарий в том же файле, `HOWTO.txt` и README обещают «без системного Python». На чистой VM это даст `'python' is not recognized…` вместо понятной ошибки, которую печатают `run-gui.bat`/`run-cli.bat`. | среднее (поддержка/консистентность) |
| G4 | В `.gitignore` нет `keys/`, `private_key.hex`, `*.hrmlicense`, а `build.ps1` (шаг 5, smoke test) запускает `cli.py gen-keypair` **без `--out-dir`**: ключ пишется в `keys\private_key.hex` относительно текущего каталога — то есть внутрь рабочего дерева git, если `build.ps1` запущен из корня репозитория. `git add -A` застейджит приватный ключ. | среднее (гигиена ключей) |
| G3 | `run-html.bat` запускает `python -m http.server 8765` **без `-b`**: `http.server` слушает `0.0.0.0` (измерено здесь), значит, пока окно открыто, каталог бундла (и любой ключевой файл рядом с ним) доступен из LAN; возможен запрос Windows Firewall. | низкое/среднее (hardening) |
| G2 | `run-html.bat` открывает браузер **до** старта сервера и не проверяет порт: занятый 8765 или медленный старт покажут «site can't be reached», хотя бундл исправен. | низкое (UX) |
| G5 | **HTML**-issuer подписывает лицензию с `expires_at` в прошлом (нет проверки `issued_at <= expires_at`), и backend затем отклоняет загрузку (`bad_date`). Python CLI такой ввод отклоняет. | низкое (паритет) |
| G6 | Оба issuer'а подписывают `client_name` с управляющими символами; backend такие лицензии отклоняет (`bad_value`). Из CLI достижимо через аргумент командной строки. | низкое |

Рекомендуемый минимальный набор правок перед сборкой пилота: G1 (убрать fallback на системный Python) и
G4 (`.gitignore` + `--out-dir` во временный каталог в smoke-тесте). G2/G3/G5/G6 — hardening/паритет.

## Что должен сделать владелец на чистой Windows 10/11 (чек-лист)

Подготовка: VM без Python/pip, без интернета (или отключить сетевой адаптер после копирования бундла).
`license-issuer-dist.zip` собирается мейнтейнером с интернетом один раз (`build.ps1`) и передаётся владельцу.

1. **GUI**
   1. распаковать `license-issuer-dist.zip` (например, в `C:\HR-License\`);
   2. двойной клик `run-gui.bat` → должен открыться GUI (без системного Python);
   3. «Сгенерировать новую пару» → **Сохранить ключи в папку** в зашифрованное хранилище
      (VeraCrypt/BitLocker/шифрованная флешка);
   4. заполнить синтетическое имя клиента, `expires_at` в будущем, лимит 5 → **Выпустить и сохранить
      `.hrmlicense`**;
   5. вкладка «3. Проверить» → файл лицензии → «Подпись корректна».
2. **HTML**
   1. двойной клик `run-html.bat` → откроется `http://localhost:8765/license-issuer.html` в Edge;
   2. «Сгенерировать новую пару», заполнить те же поля, «Выпустить лицензию» → «Скачать `.hrmlicense`»;
   3. «Проверить подпись текущей лицензии» → «Подпись корректна».
3. **Сеть**: Wireshark или Resource Monitor → `python.exe` не делает исходящих соединений; допустимо только
   слушание локального порта 8765 (см. G3 — почему лучше `-b 127.0.0.1`).
4. **Приватный ключ**: поиск 64-символьного hex приватного ключа в `%TEMP%`, `%APPDATA%`, в папке бундла,
   в папке загрузок браузера и в окне консоли → **не найден**; в git ничего не добавлено.
5. **Backend**: отдать публичный ключ (`public_key.b64`, 44 символа) вместе с `*.hrmlicense` на проверку —
   лицензия должна загрузиться через «Рабочее пространство → Лицензия» и показать срок/лимит.

## Что публиковать как доказательство (только redacted!)

Можно: HTTP-код и размер отданной страницы, результат «Подпись корректна», скриншоты окна GUI без ключей,
SHA-256/первые 16 hex отпечатка публичного ключа, `expires_at`/лимит, вывод «0 исходящих соединений»,
вывод grep об отсутствии приватного ключа.

**Нельзя:** приватный ключ (hex/base64) ни в каком виде и любой его фрагмент; полный публичный ключ;
полный JSON лицензии с подписью; реальные имена клиентов/пилотов, e-mail, телефоны и прочие PII; скриншоты
с открытым полем приватного ключа.

## Итог

Пока пункты раздела «Почему это по-прежнему BLOCKED» не выполнены и redacted-доказательства не приложены,
release-вердикт остаётся **NO-GO**; `GO` не выпускается, merge не рекомендуется. После выполнения чек-листа
владельцем и обновления этого файла вердикт можно переводить в `GO` для закрытого пилота `127.0.0.1`.
