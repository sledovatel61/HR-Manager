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

- Сервер проверяет подпись при каждой загрузке и при каждом запросе к защищённым операциям (кроме `/api/auth/*`, `/api/license/*`, `/api/health`, `/api/ops/*`, `/api/setup/*`, `/api/updates/engine-*`).
- При истечении — работа останавливается (403), данные не удаляются, админ может войти и загрузить новую лицензию.
- Защита от перевода часов назад: хранится `last_seen_at`, если текущее время < `last_seen_at - 1 час` → лицензия блокируется (best-effort, не абсолютная защита от админа ПК).

## Как владелец создаёт ключ и лицензию

### Вариант A: GUI (Windows, без установки Python — embeddable)

1. Соберите портативный пакет (один раз, нужен интернет для скачивания Python):
   ```powershell
   powershell -ExecutionPolicy Bypass -File tools/license-issuer/build.ps1
   ```
   Результат: `tools/license-issuer/dist/license-issuer/` + `dist/python/`
2. Запустите `dist/license-issuer/run-gui.bat`
3. Нажмите «Сгенерировать новую пару» — сохраните **приватный ключ (64 hex)** в зашифрованном хранилище (VeraCrypt/BitLocker/зашифрованная флешка). Сделайте резервную копию!
4. Скопируйте **публичный ключ (base64 44 символа)** в `infra/license/public_key.b64` перед сборкой пилотного образа или в `HRM_LICENSE_PUBLIC_KEY` в `pilot.env` (через `Secrets.psm1`).
5. Заполните: имя клиента, действует до (YYYY-MM-DD), лимит пользователей, License ID (auto).
6. «Выпустить лицензию» → «Скачать .hrmlicense файл».
7. Отправьте файл Марии (email, мессенджер, флешка).

### Вариант B: CLI (Python 3.12+)

```bash
python tools/license-issuer/cli.py gen-keypair
# -> private: 64 hex (SECRET), public: base64

python tools/license-issuer/cli.py issue \
  --client-name "Пилот Марии" \
  --expires-at 2026-12-31 \
  --max-users 5 \
  --private-key <64hex> \
  --out license.hrmlicense

python tools/license-issuer/cli.py verify --public-key <base64> --license license.hrmlicense
```

### Вариант C: HTML офлайн (без Python, WebCrypto)

Откройте `tools/license-issuer/license-issuer.html` в современном браузере (Chrome 120+, Edge, Firefox 120+) — работает без интернета, использует WebCrypto Ed25519.

- Сгенерировать пару → сохранить приватный, скопировать публичный.
- Выпустить лицензию → скачать файл.

Для старых браузеров — используйте CLI.

## Как Мария загружает лицензию (без терминала/GitHub/Docker)

1. Войдите как администратор.
2. Настройки → Лицензия (или `/license`).
3. Загрузите файл `.hrmlicense` или вставьте JSON → Сохранить.
4. Увидите: клиент, действует до, лимит, активных пользователей, дней осталось.
5. При истечении — админ всё ещё может войти и загрузить новую лицензию, данные не удаляются.

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

## Тесты

См. `backend/tests/test_license_*.py` и `test_license_guard.py`:

- valid, expired, forged/modified, wrong public key, replacement/restore, user limit including concurrent, API guard, first-run no deadlock, data preservation on expiry.

Запуск:

```bash
cd backend
pytest tests/test_license* tests/test_license_guard.py -v
```

## Ограничения

- Пилот ограничен 127.0.0.1, второй ПК по LAN не реализуется в этой задаче.
- Нет абсолютной защиты от админа чужого ПК, который переведёт часы — best-effort.
- Не выдаём тестовую подпись Windows за доверенную production-подпись, не отключаем SmartScreen, не требуем коммерческий Authenticode для задачи лицензии.
- Не запускаем production workflow/tag/release.
