# Runbook первого пилота (Windows 10/11) — Phase 14

Единый операторский документ: подготовка, установка, подпись релиза, обновление,
откат, восстановление, диагностика и go/no-go. Адресован владельцу пилота и
дежурному администратору; ручные шаги выполняются только на пилотной машине.

Связанные документы:

* `infra/windows/README.md` — команды движка (`hr-manager.ps1`);
* `infra/release/README.md` — release-пайплайн и trust store;
* `installer/README.md` — установщик Inno Setup;
* `docs/backup-and-restore.md` — бэкапы и restore drift;
* `docs/phase-14-report-arena.md` — отчёт фазы: что автоматизировано, а что
  остаётся owner-action.

Роли: **владелец** (owner) — владеет environment `update-channel-signing`,
выполняет церемонию ключей и финальный go/no-go; **администратор пилота** —
устанавливает/обновляет приложение и снимает диагностику; **дежурный** —
следит за бэкапами и реагирует на инциденты.

---

## 1. Подготовка машины (Windows 10/11 x64)

| Что | Требование | Проверка |
| --- | --- | --- |
| Windows | 10 сборка 19041+ (2004) или Windows 11 | `winver` |
| PowerShell | Windows PowerShell 5.1, 64-разрядный | `$PSVersionTable.PSVersion` |
| Docker Desktop | официальный установщик с docker.com, WSL2 backend, лицензия принята вручную | `docker version` |
| Docker Compose | v2.24+ (плагин `docker compose`) | `docker compose version` |
| Свободное место | ≥ 5 ГБ на диске установки | `Get-PSDrive C` |
| Порт | 8080 (или явно выбранный `-Port`) свободен на 127.0.0.1 | `Get-NetTCPConnection -LocalPort 8080 -State Listen` |

Ограничения контура: приложение публикуется **только на loopback**
(`127.0.0.1`). Порты базы данных и backend наружу не публикуются; если
`docker compose ps` показывает `0.0.0.0:...` для `db`/`backend` — это инцидент,
стек останавливается.

## 2. Установка и первый вход

1. Владелец собирает и подписывает релиз (раздел 4) либо передаёт
   администратору подписанный `HR-Manager-Setup-<version>.exe` и
   `update-channel.json` + `trust-store.json` из GitHub Release.
2. Перед запуском установщика: сверить SHA256 с `SHA256SUMS` из релиза.
3. Запуск `HR-Manager-Setup-<version>.exe` → согласие UAC → мастер Inno.
   Установщик раскладывает приложение в `%LOCALAPPDATA%\Programs\HRManager`,
   встраивает trust store (`infra/release/trust-store.json`) в снимок и
   запускает `hr-manager.ps1 -Action install`.
4. Первый вход: движок создаёт одноразовый тикет и открывает браузер по
   `http://127.0.0.1:8080/#setup=<ticket>`. Владелец задаёт пароль
   администратора, часовой пояс, рабочие дни и тихие часы.
   Тикет одноразовый и не пишется в логи/URL-параметры.
5. Проверка loopback:
   ```powershell
   Get-NetTCPConnection -State Listen |
     Where-Object { $_.LocalPort -in 8080, 8000, 5432 } |
     Select-Object LocalAddress, LocalPort
   ```
   Ожидается только `127.0.0.1` (8080 — фронтенд; 8000/5432 не публикуются).
6. Запуск диагностики и хост-отчёта для readiness:
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File hr-manager.ps1 -Action diagnostics
   ```
   Команда печатает redacted-диагностику и отправляет хост-отчёт на loopback
   (`POST /api/updates/engine-host-report`) — после этого раздел
   «Готовность пилота» в UI становится информативным.

## 3. Owner: настройка подписи и защиты релиза

1. GitHub → Settings → Environments → создать `update-channel-signing`.
2. Добавить **Required reviewers** (минимум один человек, кроме владельца
   автозапуска) и ограничить ветки/теги, из которых разрешён деплой
   (`v*`).
3. Secrets environment (значения не покидают GitHub и не печатаются в логах):

   | Secret | Назначение |
   | --- | --- |
   | `UPDATE_CHANNEL_SIGNING_KEY` | закрытый Ed25519-ключ канала (64 hex или PEM) |
   | `UPDATE_CHANNEL_KEY_ID` | `key_id` подписи (например `pilot-release-2026`) |
   | `UPDATE_CHANNEL_PUBLIC_KEYS` | публичный trust store `{key_id:{key,revoked}}` |
   | `UPDATE_CHANNEL_AUTHENTICODE_PFX_BASE64` | PFX сертификата кодовой подписи (base64) |
   | `UPDATE_CHANNEL_AUTHENTICODE_PFX_PASSWORD` | пароль PFX |
   | `UPDATE_CHANNEL_EXPECTED_PUBLISHER` | ожидаемый издатель (`O`/`CN` сертификата) |
   | `UPDATE_CHANNEL_TIMESTAMP_URL` | RFC3161 TSA владельца |
   | `UPDATE_CHANNEL_AUTHENTICODE_SIGNER_ROOTS` | PEM закреплённых корней издателя (code signing). Не корни TSA |
   | `UPDATE_CHANNEL_AUTHENTICODE_TIMESTAMP_ROOTS` | PEM закреплённых корней TSA. Другой набор, не копия signer roots |

4. Tag protection rules: разрешить создание тегов только владельцу/CI и
   ограничить шаблоном `v[0-9]+.[0-9]+.[0-9]+` (SemVer).
5. Проверить, что CI на `main` зелёный: релиз отказывается собираться без
   успешного прогона CI для того же SHA.

### PEM-корни Authenticode — два разных набора

`UPDATE_CHANNEL_AUTHENTICODE_SIGNER_ROOTS` и
`UPDATE_CHANNEL_AUTHENTICODE_TIMESTAMP_ROOTS` — это **разные** PEM-наборы.
Корни издателя не подставляются вместо корней TSA и наоборот: у signer CA и
TSA CA разные центры доверия. Повтор одного и того же корня в обеих ролях
production-выпуск отклоняет.

Формат каждого секрета:

- текст PEM, один или несколько блоков `-----BEGIN CERTIFICATE-----` /
  `-----END CERTIFICATE-----`;
- UTF-8 **без BOM** (ни UTF-8 `EF BB BF`, ни UTF-16);
- значение непустое: пустой секрет, файл из одних пробелов и обрыв блока —
  отказ;
- хотя бы один сертификат, который разбирается как X.509;
- **без** приватного материала (`PRIVATE KEY`, PFX, пароль). В секрет кладётся
  только публичный сертификат корня.

Не брать trust roots из артефакта, который создал сам signing job
(`installer/authenticode-roots.pem`, asset релиза, сертификат из подписи
`Setup.exe`). Этот файл фиксирует, какой якорь уже использован; если положить
его же в `--trust-roots`, проверка цепочки становится тавтологией. Источник
корней — офлайн-копия владельца, записанная в environment secrets **до**
запуска workflow.

Fail closed, до публикации и до подписи:

- `installer/sign.ps1 -Mode production` вызывает `Assert-HrmPinnedRootsPem`
  **до** `signtool sign`. Нет файла, файл пуст, есть BOM, нет разобранного
  сертификата или в PEM есть приватный материал — exit 1, installer не
  подписывается, успешная attestation не создаётся. В лог попадает только роль
  (`signer-roots` / `timestamp-roots`), не тело PEM, не отпечаток и не пароль.
- `publish_channel.py --release-mode production` читает те же два PEM.
  Отсутствующий файл — `ОШИБКА[missing_pem]`, недоступный —
  `ОШИБКА[unreadable_pem]`, пустой/битый/BOM/приватный материал —
  `ОШИБКА[bad_root]`. Код возврата 1, traceback нет, `update-channel.json` не
  создаётся.
- Test-режим CI (`sign.ps1 -Mode test`) operator PEM не требует: там
  ephemeral-сертификат, и production policy такой релиз не пропускает.

### Ручной production pre-flight цепочки

Полную проверку `PFX → signer root` и `TSA → timestamp root` скрипт подписи
**не** делает. Её выполняет владелец на своей машине до записи секретов.
Пароль PFX берётся из менеджера паролей и не попадает в репозиторий, логи и
артефакты. Приватный ключ на диск не выписывается (`-nokeys`).

1. Два локальных файла вне репозитория: `signer-roots.pem` и
   `timestamp-roots.pem`. `sha256sum` у них должен различаться.
2. Публичный leaf из PFX, без ключа:

   ```bash
   openssl pkcs12 -in owner.pfx -clcerts -nokeys -out leaf.pem
   openssl verify -CAfile signer-roots.pem leaf.pem
   ```

   Если между leaf и корнем есть промежуточные CA, добавить
   `-untrusted intermediates.pem`. Успех — строка `leaf.pem: OK`. Иной результат
   — секрет не записывать и релиз не запускать.
3. Сертификат TSA получить у оператора TSA (или отдельным запросом к
   `UPDATE_CHANNEL_TIMESTAMP_URL`), **не** из подписанного `Setup.exe` и не из
   `authenticode-roots.pem`:

   ```bash
   openssl verify -CAfile timestamp-roots.pem tsa.pem
   ```

   Успех — `tsa.pem: OK`, и цепочка заканчивается на корне из timestamp PEM, а
   не на корне издателя.
4. Только после обеих проверок вставить текст PEM в два environment secret.
   Значения в runbook, git и логи не копировать.

## 4. Церемония ключей канала (Ed25519)

Разовая генерация (офлайн, на машине владельца; закрытый ключ не попадает в
репозиторий, логи и артефакты):

```bash
python - <<'PY'
import base64, secrets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
key = Ed25519PrivateKey.generate()
raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                        serialization.NoEncryption())
pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
print("UPDATE_CHANNEL_SIGNING_KEY=", raw.hex())
print("UPDATE_CHANNEL_PUBLIC_KEYS=", '{"pilot-release-2026":{"key":"%s","revoked":false}}'
      % base64.b64encode(pub).decode())
PY
```

1. Закрытый ключ → в secret `UPDATE_CHANNEL_SIGNING_KEY`; публичный набор → в
   `UPDATE_CHANNEL_PUBLIC_KEYS`. Отпечаток (`SHA256:<hex16>`) записать в
   журнал ключей.
2. Резервная копия закрытого ключа — в защищённом хранилище владельца (не в
   репозитории и не в GitHub Actions artifacts).
3. **Ротация** (двухключевое окно): добавить новый `key_id` рядом со старым,
   выпустить релиз, подписанный новым ключом, убедиться, что пилот обновился,
   и только затем пометить старый ключ `"revoked": true` и перевыпустить
   trust store.
4. **Экстренный отзыв**: пометить скомпрометированный `key_id`
   `"revoked": true`, выпустить trust store, применить его на пилоте
   (`hr-manager.ps1 -Action channel-config -KeysJson <файл>`) и перевыпустить
   релиз новым ключом. Отозванный ключ отклоняется fail closed: обновление с
   ним не устанавливается никогда.
5. Правила неизменяемости: fixture-ключи из `infra/release/testdata` НИКОГДА не
   попадают в production trust store (проверяется в release-пайплайне), тестовый
   сертификат никогда не проходит production-политику.

## 5. Выпуск релиза и независимая проверка

1. Тег `v<version>` (SemVer) на коммите, для которого CI зелёный, либо ручной
   запуск workflow `Update channel release` с `version` и полным `release_sha`.
2. Workflow собирает installer, подписывает его Authenticode + RFC3161
   (signtool `/pa /all`), проверяет Ed25519-подпись канала независимой
   реализацией и создаёт **draft** Release с активами:
   `hr-manager-windows-<version>.zip`, `update-channel.json`, `SHA256SUMS`,
   `release-metadata.json`, `trust-store.json`, `authenticode-verification.json`,
   `authenticode-attestation.json`, `HR-Manager-Setup-<version>.exe`.
3. Владелец проверяет (не доверяя логам сборки):
   ```bash
   gh release download v0.14.0 --dir /tmp/rel
   (cd /tmp/rel && sha256sum -c SHA256SUMS)
   python infra/release/verify_channel.py --manifest /tmp/rel/update-channel.json \
     --public-key <base64 публичного ключа>       # Ed25519 канала
   python infra/release/authenticode.py verify --file /tmp/rel/HR-Manager-Setup-0.14.0.exe \
     --expected-publisher "<издатель>" --require-timestamp \
     --trust-roots /path/to/owner-signer-roots.pem \
     --timestamp-roots /path/to/owner-timestamp-roots.pem
   ```
4. `--trust-roots` и `--timestamp-roots` — локальные копии двух secrets из
   раздела 3, не файл `authenticode-roots.pem` из скачанного релиза и не
   артефакт signing job. Совпадение SHA256 релизного `authenticode-roots.pem`
   с owner signer PEM допустимо как аудит «подписали тем якорем, который
   закрепили», но доверие задаёт owner PEM, а не артефакт сборки.
5. Проверить, что `trust-store.json` в релизе совпадает с тем, что встроен в
   installer (`authenticode-attestation.json` → `trust_store.sha256`), и что в
   наборе нет отозванных/тестовых ключей.
6. Только после этого — публикация draft-релиза (promotion) и объявление
   версии пилоту.

## 6. Обновление, откат, resume

1. Обновление инициирует **только администратор** в разделе «Обновления»
   (кнопка «Установить»). Фоновой автоустановки нет.
2. Порядок в движке: проверка подписи manifest доверенным ключом → размер и
   SHA256 пакета → безопасная распаковка в staging (Zip Slip/ADS/UNC/symlink
   отклоняются) → бэкап-ворота (проверенный шифрованный бэкап) → миграции →
   smoke-проверка → фиксация версии.
3. **Откат**: при провале миграции/smoke движок автоматически возвращает
   `:previous` образы и прежнюю версию; данные не откатываются (миграции должны
   быть обратно совместимыми). В UI появляется честный итог «rolled_back».
4. **Resume**: если операция прервана (перезагрузка, UAC, сбой питания),
   выполнить
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File hr-manager.ps1 -Action resume
   ```
   Безопасные точки возобновления: `prepare` → `backup` → `images` → `migrate`
   → `smoke` → `finalize`; журнал — `%LOCALAPPDATA%\HRManager\update-journal.json`.
5. Проверка работоспособности после обновления: `-Action status`, вход в UI,
   `-Action diagnostics`, затем «Готовность пилота» в UI.

## 7. Бэкапы и restore drill

1. Шифрованный бэкап создаётся планировщиком (RPO ≤ 26 часов, хранение 7
   дней, ротация при превышении 512 МБ) и вручную: `make backup-now`.
2. Restore drill (в изолированную БД) — не реже одного раза перед крупным
   обновлением и после смены окружения: `make backup-drill`.
   Результат виден в `%LOCALAPPDATA%\HRManager\backup-state.json`
   (`last_drill`) и в readiness-проверке `backup_drill`.
3. **RPO**: 26 часов. **RTO**: 4 часа (восстановление последнего бэкапа в чистый
   контур и проверка входа). Ответственный за восстановление — администратор
   пилота, за решение о запуске — владелец.
4. Окно изменений: вне рабочих часов пилота; при обновлении — не более 60
   минут недоступности.
5. Критерии остановки (stop criteria): нет свежего бэкапа, провален drill,
   открытый не-loopback порт, дрейф миграций, недоступный Docker daemon,
   отозванный/пустой trust store. Критерий отката: провал smoke-проверки или
   ошибки в течение 30 минут после обновления.

## 8. Удаление и полная очистка

1. `uninstall` через «Программы и компоненты» (или `hr-manager.ps1 -Action
   uninstall`) **сохраняет** StateDir, данные PostgreSQL, бэкапы и настройки:
   данные можно вернуть повторной установкой/обновлением.
2. Полный purge (удаление данных) выполняется только явно:
   ```powershell
   powershell -File hr-manager.ps1 -Action uninstall -PurgeData
   ```
   Purge требует подтверждения и свежего проверенного бэкапа (`-PurgeData`
   отказывается работать без бэкап-ворота); том с бэкапами удаляется отдельным
   шагом с явным подтверждением.
3. Перед purge выгрузить диагностику и `backup-state.json` как доказательство.

## 9. Сбор диагностики и наблюдаемость

```powershell
powershell -File hr-manager.ps1 -Action diagnostics -Json > diagnostics.json
```

Диагностика редакции: секреты, токены и пароли не попадают в вывод.
События релиза/обновления/отката/восстановления различимы в существующих
логах и аудите (`update_*`, `pilot_readiness_viewed`, backup-события) и не
содержат PII, URL с credentials, ключей и токенов. Внешней телеметрии нет:
любая отправка данных третьим лицам — отдельное решение владельца.

## 10. Go/no-go checklist

Заполняется владельцем перед первым запуском (и перед каждым крупным
обновлением). В таблицу вписываются **факты**, а не «ожидаемо».

| # | Критерий | Как проверено (evidence) | Итог |
| --- | --- | --- | --- |
| 1 | Дата и версия: `<YYYY-MM-DD>`, версия `<x.y.z>` | — | — |
| 2 | Exact SHA релиза (40 hex) и он же — коммит тега | `git rev-parse v<version>^{commit}` | — |
| 3 | CI зелёный на этом SHA (backend, PostgreSQL, frontend, Windows engine+installer, Compose) | ссылки на прогоны CI | — |
| 4 | Ed25519-подпись канала проверена независимо | вывод `verify_channel.py` | — |
| 5 | Authenticode + timestamp проверены, издатель совпал | вывод `authenticode.py verify` | — |
| 6 | `SHA256SUMS` совпадают с активами релиза | `sha256sum -c` | — |
| 7 | Trust store без private material, без отозванных/тестовых ключей | `trust_store.py validate --require-unrevoked` | — |
| 8 | Установка на чистой машине и первый вход выполнены | скриншот/лог установщика | — |
| 9 | Loopback: публикуются только 127.0.0.1 | `Get-NetTCPConnection` | — |
| 10 | «Готовность пилота» = «готово» или «готово с предупреждениями» без fail | скриншот отчёта readiness | — |
| 11 | Свежий шифрованный бэкап и успешный restore drill | `backup-state.json` | — |
| 12 | Тестовое обновление подписанным релизом и откат проверены | журнал update-journal + UI | — |
| 13 | Данные сохраняются после обновления/отката/переустановки | ручная проверка карточек | — |
| 14 | Ручная Windows-приёмка пройдена (или явно отложена owner-action) | подписанный чек-лист | — |
| 15 | Ответственный за пилот и окно изменений зафиксированы | этот документ, раздел 7 | — |

Итог: **go** — все строки без fail; **no-go** — любой fail; при
предупреждениях решение принимает владелец письменно (дата, подпись,
перечень принятых рисков).
