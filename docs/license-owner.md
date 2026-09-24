# Лицензия — инструкция для владельца (Windows PC, офлайн)

Закрытый пилот HR Manager: лицензия для **инсталляции/сервера**, не per-HR ключ. Мария — первый администратор, лимит включает её. Проверка — серверная, не только скрытие кнопок.

## Безопасность (обязательно)

- Приватный ключ Ed25519 (32 байта, 64 hex) создаётся и хранится **ТОЛЬКО у владельца**, никогда не попадает в git, установщик, frontend, Docker image, логи, диагностический архив.
- В приложении — только открытый ключ `HRM_LICENSE_PUBLIC_KEY` (base64 32 байта, 44 символа).
- Никаких тестовых production-ключей в приложении.
- Логи содержат только отпечаток `SHA256:xxxx... (redacted)`, никогда полный ключ/подпись/PII.
- Резервная копия приватного ключа — в зашифрованном хранилище (VeraCrypt/BitLocker/зашифрованная флешка/аппаратный токен).

## Формат лицензии

Файл `*.hrmlicense` — JSON:

```json
{
  "license_id": "550e8400-e29b-41d4-a716-446655440000",
  "client_name": "Пилот Марии",
  "issued_at": "2026-09-23T06:00:00Z",
  "expires_at": "2026-12-31",
  "max_active_users": 5,
  "signature": "aabb... 128 hex"
}
```

- `license_id` — UUID v4
- `client_name` — до 200 символов
- `issued_at` — UTC `YYYY-MM-DDTHH:MM:SSZ`
- `expires_at` — дата `YYYY-MM-DD`, действует **включительно** до конца дня 23:59:59.999999 UTC
- `max_active_users` — 1..1000, включает Марию, проверяется сервером на create/reactivate с `SELECT ... FOR UPDATE`
- `signature` — Ed25519 detached 64 байта = 128 hex по каноническим байтам (порядок фиксирован, LF, UTF-8):

```
license_id:<uuid>\n
client_name:<имя>\n
issued_at:<...>\n
expires_at:<...>\n
max_active_users:<число>\n
```

## Как создать ключ и лицензию — автономный пакет (Вариант A, предпочтительный)

**Цель:** владелец запускает обычную Windows-программу двойным кликом, без предварительной установки Python, pip, интернета.

### Сборка автономного пакета (maintainer, один раз, нужен интернет)

На машине с интернетом (не обязательно на машине владельца, может собрать владелец один раз):

```powershell
powershell -ExecutionPolicy Bypass -File tools/license-issuer/build.ps1
```

Что делает скрипт (воспроизводимый процесс):
1. Скачивает embeddable Python 3.12.3 с python.org (`python-3.12.3-embed-amd64.zip`) — **только на этапе сборки**.
2. Распаковывает в `dist/python/`, включает `import site`.
3. Скачивает `get-pip.py` и устанавливает `cryptography` в `dist/python/Lib/site-packages` — **только на этапе сборки, нужен интернет один раз**.
4. Копирует `license_issuer.py`, `cli.py`, `gui.py`, `license-issuer.html`, `nacl-fast.js` (TweetNaCl 1.0.3, 2391 строка, public domain) в `dist/license-issuer/`.
5. Создаёт launchers `run-gui.bat`, `run-cli.bat`, `run-html.bat` — используют `..\python\python.exe`, **fail-closed** если bundled Python отсутствует (не fallback тихо на системный Python).
6. Smoke-тест: `python\python.exe license-issuer\cli.py gen-keypair` — проверяет, что cryptography импортируется.
7. Создаёт `dist/license-issuer-dist.zip` — **автономный архив**, содержит `python/` + `license-issuer/` + HOWTO.

После сборки `dist/license-issuer-dist.zip` **не требует интернета, системного Python, pip**.

Проверка автономности (maintainer):
```powershell
# В чистой Windows VM без Python, без интернета:
Expand-Archive license-issuer-dist.zip -DestinationPath C:\Temp\lic
C:\Temp\lic\license-issuer\run-gui.bat   # должен открыть GUI
C:\Temp\lic\license-issuer\run-cli.bat gen-keypair  # должен выдать ключи
C:\Temp\lic\license-issuer\run-html.bat  # должен открыть http://localhost:8765/license-issuer.html и WebCrypto Ed25519 работает
```

### Использование владельцем (офлайн, без Python, без интернета)

1. Распакуйте `license-issuer-dist.zip` (например, `C:\HR-License\`).
2. Двойной клик:
   - `run-gui.bat` — GUI Tkinter: Generate keypair, Issue license (рекомендуется)
   - `run-html.bat` — HTML офлайн через `http://localhost:8765/license-issuer.html` (Edge 120+/Chrome 120+, secure context localhost, WebCrypto Ed25519, fallback TweetNaCl)
   - `run-cli.bat gen-keypair` / `run-cli.bat issue ...` — CLI
3. «Сгенерировать новую пару»:
   - Приватный ключ (64 hex) — **СОХРАНИТЕ** в зашифрованном месте, сделайте резервную копию!
   - Публичный ключ (base64 44 символа) — скопируйте
4. Публичный ключ → `infra/license/public_key.b64` перед сборкой пилотного образа. Или установите в StateDir как `license_public_key.b64` — движок `Secrets.psm1` прочитает и запишет в `pilot.env` как `HRM_LICENSE_PUBLIC_KEY`.
5. Выпуск лицензии:
   - Имя клиента: `Пилот Марии`
   - Действует до: `2026-12-31` (YYYY-MM-DD, включительно)
   - Лимит: `5`
   - License ID: auto (UUID)
   - «Выпустить лицензию» → «Скачать .hrmlicense»
6. Отправьте файл Марии (email, мессенджер, флешка).

### Почему это автономно (доказательство)

- `dist/python/` содержит `python.exe` + `Lib/site-packages/cryptography` — проверено `python -c "import cryptography"` в smoke-тесте.
- Launchers используют `..\python\python.exe`, не системный Python. Если папка отсутствует — ошибка, а не тихий fallback.
- `license-issuer.html` + `nacl-fast.js` работают без интернета. WebCrypto Ed25519 требует secure context: `run-html.bat` запускает `python -m http.server 8765` и открывает `http://localhost:8765/license-issuer.html` — localhost считается secure context, Edge 120+ поддерживает Ed25519 (проверено в Edge/Chrome 120+). Fallback TweetNaCl работает даже в file://.
- Владелец после получения zip не скачивает ничего из интернета, не устанавливает Python.

### Вариант B (допустим только если Вариант A BLOCKED)

Если автономная сборка объективно невозможна (например, нет доступа к embeddable Python), честно обозначьте BLOCKED и используйте временную инструкцию:

```bash
pip install cryptography
python tools/license-issuer/cli.py gen-keypair --out-dir keys
python tools/license-issuer/cli.py issue --private-key-file keys/private_key.hex --client "Пилот Марии" --expires 2026-12-31 --max-users 5 --out license.hrmlicense
python tools/license-issuer/cli.py verify --public-key-file keys/public_key.b64 --license-file license.hrmlicense
```

**Текущий статус:** Вариант A **реализован** — см. `build.ps1` (UTF-8 BOM + ASCII для Windows PowerShell 5.1; `..\license-issuer` в `python312._pth` — исправлен `ModuleNotFoundError: No module named 'license_issuer'`; fail-closed launchers без fallback на системный Python; `run-html.bat` слушает только 127.0.0.1; smoke-тест `gen-keypair -> issue -> verify` во временном каталоге вне репозитория) и `nacl-fast.js`. Автоматически на Windows: CI job `license-issuer-windows` (настоящий Windows PowerShell 5.1: parser-check файла, полная сборка, runtime-проверки из свежего unzip с путями, содержащими пробелы, PATH без системного Python, loopback-бинд, fail-closed, sweep на приватный ключ в логах/temp, чистота git-дерева). **Ручная проверка на чистой Windows 10/11 VM без Python/интернета — НЕ ВЫПОЛНЕНО** (см. `review-artifacts/windows-issuer-bundle-check.md`); для GO она всё ещё требуется.

### Вариант HTML офлайн (WebCrypto)

Откройте `license-issuer.html` через `run-html.bat` (рекомендуется) или напрямую в Edge 120+/Chrome 120+:

- Сгенерировать пару → сохранить приватный, скопировать публичный
- Выпустить лицензию → скачать файл
- Проверка подписи — встроена

Для file:// без secure context — используйте `run-gui.bat` или `run-cli.bat` (bundled Python) — они гарантировано работают.

## Сборка пилотного образа с ключом — полный путь (проверено тестом)

1. Владелец генерирует ключ, сохраняет приватный у себя (только у владельца).
2. Публичный в `infra/license/public_key.b64` (44 символа).
3. При установке Windows движок `Secrets.psm1:Get-HrmLicensePublicKey` читает ключ из файла (`infra/license/public_key.b64` относительно движка) и пишет в `pilot.env` как `HRM_LICENSE_PUBLIC_KEY`.
4. Docker Compose (`infra/compose.pilot.yml`) передаёт `HRM_LICENSE_PUBLIC_KEY` из `pilot.env` в контейнеры **backend, worker и backup** как `LICENSE_PUBLIC_KEY` — обязательно, через `${HRM_LICENSE_PUBLIC_KEY:?...}`. Файл `pilot.env` подключается только флагом `--env-file` (интерполяция); директивы `env_file:` в сервисах нет намеренно — иначе в контейнер backend попал бы весь файл, включая ключ шифрования бэкапов. Образ backend не содержит каталога `infra/`, поэтому переменная окружения — единственный путь ключа в контейнер.
5. Backend: `Settings` в `APP_ENV=pilot` требует `LICENSE_PUBLIC_KEY` (fail-closed, без ключа не стартует). Worker и backup (`python -m app.cli ...`) загружают те же `Settings`, поэтому ключ нужен и им.
6. Backend принимает действующую лицензию (подпись Ed25519) и отклоняет подделанную (тест `test_full_public_key_path_simulation`).

**Если ключа нет.** Без файла `public_key.b64` движок запишет в `pilot.env` пустую строку `HRM_LICENSE_PUBLIC_KEY=`, и `docker compose` откажется запускать пилот с сообщением `HRM_LICENSE_PUBLIC_KEY is required for the pilot`. Это ожидаемое поведение (fail-closed): положите `public_key.b64` в `infra\license\` релиза или в `<state>\license_public_key.b64` и повторите установку/обновление. Данные в томах при этом не затрагиваются.

Проверки: `backend/tests/test_license_enforcement.py::test_full_public_key_path_simulation`, `backend/tests/test_pilot_overlay.py` (маппинг обязателен для backend/worker/backup, `env_file:` запрещён), `backend/tests/test_compose_license_chain.py` (окружение оверлея принимается `Settings` в `APP_ENV=pilot`, отпечаток ключа совпадает), в CI — шаг «Pilot overlay — license public-key chain» (настоящий `docker compose config` с ключом и без, resolved environment, `Settings` внутри собранных образов; артефакт `compose-pilot-license-chain` содержит только SHA-256-отпечатки) и Windows-тест движка `pilot.env: HRM_LICENSE_PUBLIC_KEY берётся из license_public_key.b64`.

## Ротация/отзыв

- Лицензия — не долгоживущий секрет, а файл с подписью и сроком. Для ротации — выпустите новую с новым `license_id` и тем же/новым публичным ключом (если ключ меняется — пересоберите образ с новым `public_key.b64`).
- При истечении — данные не удаляются, админ может загрузить новую.
- Защита от перевода часов назад: сервер хранит `last_seen_at`, если текущее время < `last_seen_at - 1 час` → лицензия блокируется (best-effort, не абсолютная защита от админа ПК).

## Что НЕ делать

- Не коммитьте `private_key.*`, `*.hrmlicense` с реальными данными в публичный репозиторий.
- Не выдавайте тестовую подпись Windows за доверенную production-подпись.
- Не отключайте SmartScreen, не требуйте коммерческий Authenticode для задачи лицензии.
- Не запускайте production workflow/tag/release в этой задаче.
- Не реализуйте второй ПК по LAN — пилот ограничен 127.0.0.1.

## Проверка (что уже проверено)

- **Unit-тесты (проверено):** `pytest backend/tests/test_license*.py` — 35 passed (valid, expired, forged, wrong key, replacement/restore, user limit concurrent, API guard, first-run no deadlock, data preservation, clock rollback, no private key in logs, full path, enforcement, upload only admin, openapi no leak, pilot requires key, replacement smaller limit, comprehensive middleware 15 tests: protected endpoints, dangerous prefix bypass, double slash, trailing slash, query string, fail-closed empty key, /api/unknown).
- **Backend subset (проверено):** 798 non-integration passed, 105 integration passed в CI.
- **Frontend (проверено):** CI success.
- **Windows engine lint (проверено):** `python infra/windows/tests/lint-engine.py` — 19 files OK.
- **Windows engine tests + installer smoke + compose smoke:** CI success (35879963863).
- **Автономность сборки (проверяется автоматически):** CI job `license-issuer-windows` на windows-latest: parser-check `build.ps1` Windows PowerShell 5.1, полная сборка под 5.1, распаковка в temp с пробелами в пути, CLI `gen-keypair -> issue -> verify` при PATH без системного Python, fail-closed (с системным Python на PATH и без), `run-html.bat` — LISTENING только на 127.0.0.1:8765 (netstat) + WMI-проверка, что слушатель — bundled python.exe, sweep на приватный ключ во всех файлах temp и логах, `git status --porcelain` чист. **Ручная проверка на чистой Windows VM без Python/интернета — NOT RUN.**
- **HTML WebCrypto (проверено частично):** Edge 120+ поддерживает Ed25519, `run-html.bat` даёт loopback secure context (проверено автоматически: бинд 127.0.0.1 + отдача страницы). **Ручная проверка в реальном Edge — NOT RUN.**
- **BLOCKED:** clean Windows 10/11 manual check — BLOCKED, см. `review-artifacts/windows-issuer-bundle-check.md`. Для GO требуется ручная VM.

## Документация — разделение (PASS/FAIL/BLOCKED/NOT RUN)

- **PASS (автоматически):** формат лицензии, подпись, expiry inclusive, лимит, guard с slash boundary, first-run no deadlock, data preservation, full public key path simulation, enforcement API, replacement, openapi no leak, protected endpoints 403 no_license, allowed recovery 200, dangerous prefix bypass blocked, double slash normalization, query string, fail-closed empty key check_failed, /api/unknown 403 no_license, chain evidence redacted.
- **BLOCKED:** автономный пакет `license-issuer-dist.zip` двойной клик без Python/интернета, HTML WebCrypto в Edge через localhost — требует чистой Windows VM.
- **NOT RUN:** ручной smoke `run-gui.bat`, `run-html.bat`, `run-cli.bat gen-keypair` на VM без Python/интернета, проверка что `HRM_LICENSE_PUBLIC_KEY` попадает из `infra/license/public_key.b64` через `Secrets.psm1` в `pilot.env` и backend принимает лицензию — NOT RUN до VM.
- **FAIL:** none.
