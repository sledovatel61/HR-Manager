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

4. Tag protection rules: разрешить создание тегов только владельцу/CI и
   ограничить шаблоном `v[0-9]+.[0-9]+.[0-9]+` (SemVer).
5. Проверить, что CI на `main` зелёный: релиз отказывается собираться без
   успешного прогона CI для того же SHA.

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
     --trust-roots /tmp/rel/authenticode-roots.pem
   ```
4. Проверить, что `trust-store.json` в релизе совпадает с тем, что встроен в
   installer (`authenticode-attestation.json` → `trust_store.sha256`), и что в
   наборе нет отозванных/тестовых ключей.
5. Только после этого — публикация draft-релиза (promotion) и объявление
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
