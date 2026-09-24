# Offline License Issuer — владелец (Windows PC)

Выпуск лицензий для закрытого пилота HR Manager. Лицензия — для **инсталляции/сервера**, не per-HR ключ. Мария — первый администратор, лимит включает её.

## Безопасность

- **Приватный ключ создаётся и хранится ТОЛЬКО у владельца**, никогда не попадает в git, установщик, frontend, Docker image, логи, диагностический архив.
- В приложении — только открытый проверочный ключ (`HRM_LICENSE_PUBLIC_KEY` / `infra/license/public_key.b64`).
- Никаких тестовых production-ключей в приложении.
- Логи содержат только отпечаток `SHA256:xxxx... (redacted)`, никогда полный ключ/подпись/PII.

## Что внутри лицензии (офлайн, подпись Ed25519)

JSON файл `*.hrmlicense`:

```json
{
  "license_id": "uuid",
  "client_name": "Пилот Марии",
  "issued_at": "2026-09-23T06:00:00Z",
  "expires_at": "2026-12-31",
  "max_active_users": 5,
  "signature": "128 hex Ed25519"
}
```

- `license_id` — UUID v4
- `client_name` — имя пилота/клиента (до 200 символов)
- `issued_at` — UTC ISO8601 `YYYY-MM-DDTHH:MM:SSZ` (время выпуска)
- `expires_at` — дата `YYYY-MM-DD`, действует **включительно** до конца дня 23:59:59.999999 UTC. Пример: `2026-12-31` валидна до `2026-12-31T23:59:59Z`.
- `max_active_users` — лимит активных пользователей 1..1000, включает Марию. Проверяется сервером на `create/reactivate` с `SELECT ... FOR UPDATE` для защиты от гонок.
- `signature` — Ed25519 detached, 64 байта = 128 hex, по каноническим байтам:

```
license_id:<uuid>\n
client_name:<имя>\n
issued_at:<YYYY-MM-DDTHH:MM:SSZ>\n
expires_at:<YYYY-MM-DD>\n
max_active_users:<число>\n
```

Порядок фиксирован, LF, UTF-8.

## Проверка сервером

- Сервер проверяет подпись при каждой загрузке и при каждом запросе к защищённым операциям (кроме `/auth/*`, `/license/*`, `/health`, `/ops/status`, `/ops/backup-health`, `/admin/ops/pilot-readiness`, `/updates/engine-*`, `/setup/*`, `/docs`, `/openapi.json`).
- При истечении — работа останавливается (403), данные не удаляются, админ может войти и загрузить новую лицензию.
- Защита от перевода часов назад: хранится `last_seen_at`, если текущее время < `last_seen_at - 1 час` → лицензия блокируется (best-effort, не абсолютная защита от админа ПК).

## Как владелец создаёт ключ и лицензию — автономный пакет (Вариант A)

### Сборка (maintainer, один раз, нужен интернет)

```powershell
powershell -ExecutionPolicy Bypass -File tools/license-issuer/build.ps1
```

Результат:
- `dist/python/` — embeddable Python 3.12.3 + cryptography (bundled)
- `dist/license-issuer/` — GUI, CLI, HTML, nacl-fast.js
- `dist/license-issuer-dist.zip` — **автономный архив**, без интернета, без системного Python

Что делает build.ps1 (воспроизводимый, fail-closed):
- Скачивает embeddable Python с python.org (только на этапе сборки)
- Устанавливает cryptography в `Lib/site-packages` (только на этапе сборки, нужен интернет один раз)
- Переписывает `python312._pth` детерминированно: `python312.zip`, `.`, `..\license-issuer`, `Lib\site-packages`, `import site`. Без `..\license-issuer` embeddable Python (он игнорирует `PYTHONPATH` и не добавляет каталог скрипта в `sys.path`) не импортирует соседний `license_issuer.py` — `run-cli.bat` падал с `ModuleNotFoundError: No module named 'license_issuer'`
- Копирует `nacl-fast.js` (TweetNaCl 1.0.3, 2391 строка, из npm, public domain) для fallback
- Создаёт launchers `run-gui.bat`, `run-cli.bat`, `run-html.bat` — используют `..\python\python.exe`, **fail-closed** если bundled Python отсутствует (никакого fallback на системный Python), `cd /d "%~dp0"` + кавычки вокруг всех путей (работает с путями, содержащими пробелы), `PYTHONUTF8=1`
- `run-html.bat` слушает **только `127.0.0.1`** (`python -m http.server 8765 -b 127.0.0.1 --directory "<app>"`), а не `0.0.0.0`
- Smoke-тест полной цепочки `gen-keypair -> issue -> verify` в временной директории **вне репозитория**; если приватный ключ появляется в выводе — билд падает; каталог удаляется после завершения
- Создаёт zip
- Любой сбой (скачивание, pip, импорт cryptography, smoke-тест) завершает билд ненулевым кодом

Кодировка `build.ps1`: файл сохранён **UTF-8 с BOM** и содержит только ASCII. Windows PowerShell 5.1 читает `.ps1` без BOM как ANSI; под CP1251 UTF-8-тире (байты `E2 80 94`) декодируется в правую кавычку U+201D, которую токенизатор принимает за закрывающую кавычку строки → parse error на весь скрипт. CI (job `license-issuer-windows`) проверяет BOM, ASCII-only и парсит файл настоящим Windows PowerShell 5.1 Parser'ом.

### Использование владельцем (офлайн, без Python, без интернета)

1. Распакуйте `license-issuer-dist.zip`
2. Двойной клик:
   - `run-gui.bat` — GUI Tkinter (рекомендуется, автономно)
   - `run-html.bat` — HTML через `http://localhost:8765/license-issuer.html` (Edge 120+, WebCrypto Ed25519, secure context localhost, fallback TweetNaCl)
   - `run-cli.bat gen-keypair` — CLI
3. Generate keypair — сохраните приватный (64 hex) в зашифрованном хранилище!
4. Публичный (base64 44 символа) → `infra/license/public_key.b64` перед сборкой пилотного образа
5. Issue license → скачать `.hrmlicense` → отправить Марии

### Доказательство автономности

- `python\python.exe -c "import cryptography"` — проходит (smoke-тест)
- Launchers используют bundled python, не системный
- HTML работает через localhost (secure context) + Edge 120+ WebCrypto Ed25519, fallback TweetNaCl работает в file://
- После получения zip владелец не скачивает ничего из интернета

### Вариант B (только если Вариант A BLOCKED)

Если автономная сборка невозможна, честно обозначьте BLOCKED:

```bash
pip install cryptography
python cli.py gen-keypair --out-dir keys
python cli.py issue --private-key-file keys/private_key.hex --client "Пилот Марии" --expires 2026-12-31 --max-users 5 --out license.hrmlicense
python cli.py verify --public-key-file keys/public_key.b64 --license-file license.hrmlicense
```

**Текущий статус:** Вариант A реализован — `build.ps1` (UTF-8 BOM + ASCII, `..\license-issuer` в `python312._pth`, fail-closed launchers, `run-html.bat` на 127.0.0.1, smoke-тест `gen-keypair -> issue -> verify` вне репозитория) + `nacl-fast.js`. Автоматическая проверка на Windows: CI job `license-issuer-windows` (Windows PowerShell 5.1: parser-check, полная сборка, runtime-проверки из свежего unzip без системного Python, loopback, fail-closed, отсутствие ключей в логах). Ручная проверка на чистой Windows VM без Python/интернета — требуется для GO.

### Вариант HTML офлайн (WebCrypto)

Откройте через `run-html.bat` (рекомендуется, даёт http://localhost:8765, secure context) или напрямую в Edge 120+:

- Сгенерировать пару → сохранить приватный, скопировать публичный
- Выпустить лицензию → скачать файл
- Для file:// без secure context — используйте GUI/CLI (bundled Python)

## Как Мария загружает лицензию (без терминала/GitHub/Docker)

См. `docs/license-maria.md` — раздел Лицензия → загрузить файл или вставить JSON → Сохранить.

## Что делать при истечении

- Обычная работа останавливается (403 с сообщением «Срок лицензии истёк ... Администратор может войти и загрузить новую»).
- Данные не удаляются.
- Админ: Настройки → Лицензия → загрузить новую.
- После обновления — работает без переустановки/потери БД.
- Диагностика/бэкап/health остаются доступны.

## Сборка пилотного образа

1. Владелец генерирует ключ, сохраняет приватный у себя.
2. Публичный ключ в `infra/license/public_key.b64` (44 символа base64).
3. При установке Windows движок `Secrets.psm1` читает ключ из файла и пишет в `pilot.env` как `HRM_LICENSE_PUBLIC_KEY`.
4. Backend читает `LICENSE_PUBLIC_KEY` из env, проверяет лицензию.
5. Никаких приватных ключей в образе!

Полный путь проверен тестом `test_full_public_key_path_simulation` в `test_license_enforcement.py`.

## Тесты

```bash
pytest backend/tests/test_license*.py backend/tests/test_license_enforcement.py -v
# 23 passed

pytest backend/tests/test_license*.py backend/tests/test_users_admin.py backend/tests/test_auth.py -q
# 84 passed

cd frontend && npm test
# 160 passed

python infra/windows/tests/lint-engine.py
# 19 files OK
```

Тесты: valid, expired, forged/modified, wrong public key, replacement/restore, user limit concurrent, API guard, first-run no deadlock, data preservation, clock rollback, no private key in logs, full path, enforcement blocks business after expiry, bypass via /api prefix, unknown routes, query string, trailing slash, upload only admin, openapi no leak, pilot requires key, replacement smaller limit, data not deleted.

## Ограничения и что требует чистой Windows

- Пилот ограничен 127.0.0.1, второй ПК по LAN не реализуется.
- Нет абсолютной защиты от админа чужого ПК, который переведёт часы — best-effort via `last_seen_at`.
- Не выдаём тестовую подпись Windows за доверенную production-подпись, не отключаем SmartScreen, не требуем коммерческий Authenticode для задачи лицензии.
- Не запускаем production workflow/tag/release.
- **Требует чистой Windows:** ручной smoke `run-gui.bat`, `run-html.bat` (Edge 120+ WebCrypto), `run-cli.bat gen-keypair` на VM без Python/интернета, проверка что `HRM_LICENSE_PUBLIC_KEY` из `infra/license/public_key.b64` через `Secrets.psm1` попадает в `pilot.env` и backend принимает лицензию. Отмечено как требует чистой Windows, не BLOCKED.
- **BLOCKED:** нет (Вариант A реализован). Если бы embeddable Python + cryptography не удалось собрать — было бы BLOCKED.

## Документация — разделение

- **Проверено unit-тестами:** формат, подпись, expiry inclusive, лимит, guard, first-run, data preservation, full path simulation, enforcement, replacement, openapi.
- **Проверено только unit-тестами, требует чистой Windows:** автономный пакет `license-issuer-dist.zip` (двойной клик без Python/интернета), HTML WebCrypto в Edge 120+ через localhost.
- **Требует чистой Windows:** ручной smoke на VM без Python/интернета.
- **BLOCKED:** нет.
