# Канал доставки обновлений Windows-пилота (Phase 13)

Доверенный release channel поверх уже принятого Phase 12 update engine
(backup gate → миграция → smoke → rollback → resume). Phase 13 не
переписывает установщик и не создаёт второй updater: клиент проверяет
подписанный manifest, безопасно скачивает пакет в staging и вызывает
существующий `hr-manager.ps1 -Action update -ReleaseDir <staging>`.

## Формат manifest (транспорт — JSON)

```json
{
  "schema_version": 1,
  "channel": "stable",
  "version": "0.14.0",
  "release_sha": "<40 hex, полный Git commit>",
  "package_url": "https://updates.example.com/hrm/package.valid.zip",
  "package_size": 123456,
  "package_sha256": "<64 hex>",
  "minimum_supported_version": "0.13.0",
  "published_at": "2026-09-10T12:00:00Z",
  "notes_ru": "— что изменилось (одна строка)",
  "signature": {"key_id": "pilot-release-2026", "scheme": "ed25519", "sig": "<128 hex>"}
}
```

## Канонизация подписываемых байтов (golden-проверяемая)

Подписывается **канонический payload**, а не JSON-текст:

- фиксированный порядок полей: `schema_version`, `channel`, `version`,
  `release_sha`, `package_url`, `package_size`, `package_sha256`,
  `minimum_supported_version`, `published_at`, `notes_ru`;
- каждая строка — `имя:значение`, разделитель `\n` (LF), кодировка UTF-8,
  завершающий перевод строки;
- `signature` в payload **не входит**;
- значения не содержат управляющих символов/переводов строк;
- неизвестные поля, повторяющиеся ключи JSON и неверные типы —
  **отказ** (fail closed); добавление полей = повышение `schema_version`.

Пример канонического payload — `testdata/manifest.canonical.txt`;
эталонные байты закреплены тестами на Python и PowerShell.

## Криптография

- **Схема:** detached **Ed25519** (RFC 8032) подпись канонического payload;
  в клиенте — только публичный ключ; подпись в manifest в hex.
- Проверка в Windows PowerShell 5.1 реализована в
  `infra/windows/engine/Crypto.psm1` (RFC 8032 §5.1.7 на
  System.Numerics.BigInteger + SHA-512) и покрыта golden-тестами против
  эталонных векторов RFC 8032 и fixture, подписанных независимой
  реализацией Python `cryptography`.
- **Доверенные ключи** — набор `{key_id: {key: <base64>, revoked: bool}}`
  из серверной конфигурации (`UPDATE_CHANNEL_PUBLIC_KEYS`). Клиент
  отказывает при: неизвестном `key_id`, `revoked: true`, неверной подписи,
  отсутствии подписи. Никакого «доверять всем» fallback.
- Закрытый ключ: только GitHub Actions secret/environment при публикации
  release; никогда не коммитится, не попадает в installer artifact, логи
  или diagnostics.

## Сборка release

```bash
# 1. Один раз у владельца: генерация ключевой пары
python infra/release/sign_channel.py --gen-key --key-out release-key.hex
#    → публичный ключ печатается; его добавить в UPDATE_CHANNEL_PUBLIC_KEYS
#      (конфигурация сервера) и в environment-секрет
#      UPDATE_CHANNEL_SIGNING_KEY (см. update-channel.yml).

# 2. Сборка пакета из снимка (installer/build.ps1 готовит staging/app)
python infra/release/build_package.py --snapshot installer/staging/app \
    --out dist/hr-manager-windows-0.14.0.zip --version 0.14.0 \
    --release-sha "$(git rev-parse HEAD)"

# 3. manifest без подписи → подпись (только владелец/CI secret)
python infra/release/sign_channel.py --manifest manifest.json \
    --private-key release-key.hex --key-id pilot-release-2026 \
    --out manifest.signed.json

# 4. Независимая проверка тем же публичным ключом, что встроен в клиент
python infra/release/verify_channel.py --manifest manifest.signed.json \
    --public-key <base64>
```

## Полный pipeline — GitHub Actions

Исполняемый workflow: `.github/workflows/update-channel.yml`
(вся политика — в `infra/release/publish_channel.py`; workflow — тонкая
обвязка, шаги 1–4 выше выполняет скрипт, а не копипаст команд).

- Запуск: защищённый SemVer-тег `v<major>.<minor>.<patch>` (tag protection
  rules владельца: только `v*`) или ручной `workflow_dispatch` владельца.
- Шаг сборки поднимает installer (Windows, закреплённый Inno Setup
  6.7.3) и детерминированный пакет (`build_package.py`), генерирует
  `release.json`/внутренний manifest и внешний `update-channel.json`.
- Подпись — только ключом из environment `update-channel-signing`
  (секреты: `UPDATE_CHANNEL_SIGNING_KEY`, `UPDATE_CHANNEL_KEY_ID`,
  `UPDATE_CHANNEL_PUBLIC_KEYS` — тот же trust store, что в конфигурации
  сервера). PR/fork-код не имеет доступа к секретам (нет триггера
  `pull_request`, environment с protection rules).
- Fail closed: нет/неверен signing secret → pipeline падает ДО создания
  unsigned manifest; независимая проверка подписи публичным ключом
  клиента + размер/SHA256 пакета — обязательный шаг до публикации.
- Публикация: immutable GitHub Release (draft для ревью владельца) с
  пакетом, `update-channel.json`, `SHA256SUMS` и installer'ом, плюс
  build provenance (attestation). Развёртывание серверной части —
  отдельный `release.yml` (deploy/rollback), без регрессии Phase 7/12.
- `package_url` детерминированный: `https://github.com/<owner>/<repo>/releases/download/v<version>/hr-manager-windows-<version>.zip`
  (активы релиза неизменяемы после загрузки; клиент дополнительно
  проверяет подпись и SHA256).

## Ротация и отзыв ключей

- **Ротация:** новый ключ добавляется в `UPDATE_CHANNEL_PUBLIC_KEYS`
  рядом со старым; следующий manifest подписывается новым `key_id`;
  старый ключ помечается `revoked: true` после выхода обновления со
  встроенным новым набором. Двухключевое окно исключает отказ клиентов,
  ещё не получивших новый ключ.
- **Отзыв (компрометация):** `revoked: true` немедленно; клиенты, у
  которых ключ уже в доверенном наборе, отклонят подписи этим ключом.
- Клиенты без ключа отзыва в наборе отклоняют manifest с этим `key_id`
  (неизвестный ключ) — fail closed.

## Тестовые fixture

`testdata/` — детерминированные материалы **только для тестов**:
`make_test_fixtures.py` генерирует пару «тестового» и «отозванного»
ключей, подписанный manifest, канонический golden, негативные manifest'ы,
атакующие zip-пакеты (Zip Slip, absolute/UNC, ADS, symlink, backslash,
лишний корень, exe, несовпадение release.json) и общую таблицу сравнений
SemVer. Тестовый закрытый ключ НЕ доверяется production-клиентом и не
публикует release.
