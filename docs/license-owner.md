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

## Как создать ключ и лицензию

### Вариант 1: GUI (рекомендуется для Windows, офлайн после сборки)

1. Сборка портативного пакета (один раз, нужен интернет для скачивания Python embeddable 3.12.3):
   ```powershell
   powershell -ExecutionPolicy Bypass -File tools/license-issuer/build.ps1
   ```
   Результат: `tools/license-issuer/dist/license-issuer/` и `dist/python/`

2. Запуск: `dist/license-issuer/run-gui.bat` (Tkinter, без установки Python)

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

### Вариант 2: CLI (Python 3.12+)

```bash
pip install cryptography
python tools/license-issuer/cli.py gen-keypair
# private: 64 hex (SECRET), public: base64

python tools/license-issuer/cli.py issue \
  --client-name "Пилот Марии" \
  --expires-at 2026-12-31 \
  --max-users 5 \
  --private-key <64hex> \
  --out maria_2026-12-31.hrmlicense

python tools/license-issuer/cli.py verify \
  --public-key <base64> \
  --license maria_2026-12-31.hrmlicense
```

### Вариант 3: HTML офлайн (WebCrypto, без Python)

Откройте `tools/license-issuer/license-issuer.html` в Chrome 120+/Edge/Firefox 120+ — работает без интернета, использует WebCrypto Ed25519.

- Сгенерировать пару → сохранить приватный, скопировать публичный
- Выпустить лицензию → скачать файл

Для старых браузеров — CLI.

## Сборка пилотного образа с ключом

1. Сгенерировать ключ, сохранить приватный у себя.
2. Публичный в `infra/license/public_key.b64` (44 символа).
3. Собрать образ/установщик — ключ попадёт в `pilot.env` через `Secrets.psm1:Get-HrmLicensePublicKey`.
4. Никаких приватных ключей в образе!

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

## Проверка

```bash
cd backend
pytest tests/test_license* -v
```

Тесты: valid, expired, forged/modified, wrong public key, replacement/restore, user limit including concurrent, API guard, first-run no deadlock, data preservation on expiry.
