# Runbook Phase 14: подготовка и безопасный запуск первого пилотного пользователя

Статус: **подготовлен; реальная production-подпись Authenticode и ручная
Windows-приёмка НЕ выполнялись** (нет сертификата владельца и пилотной
машины). Этот документ — исполняемая процедура: owner-настройки, ceremony
ключей, выпуск, приёмка, эксплуатация и go/no-go.

- Контракт фазы: `prompts/PHASE_14_PROMPT.md`
- Архитектура канала: `docs/phase-13-report-arena.md`, Phase 12 runbook
- Автоматизированный CI-drill: `infra/scripts/pilot-drill.sh` (джоба `pilot-drill`
  в CI; синтетические данные, эфемерные ключи, без production secrets)

## 0. Разделение ответственности CI и ручной приёмки

| Область | CI (автоматически) | Ручная Windows-приёмка (owner) |
| --- | --- | --- |
| Криптография канала (Ed25519, canonical bytes, RFC 8032) | backend suite + `infra/windows/tests/run-tests.ps1` | — |
| Publish pipeline (fail-closed, trust store, SHA256SUMS) | `tests/test_release_pipeline.py` | — |
| Authenticode-контракт (fail-closed политика) | `tests/test_installer_signing.py` + CI `windows-installer` (test-режим, ephemeral cert) | production-подпись реальным сертификатом |
| Независимая Authenticode-проверка фактических байтов PE (без доверия манифесту) | `tests/test_authenticode_verify.py` + гейт в workflow: в installer-джобе сразу после подписи и в channel-джобе непосредственно перед публикацией | владелец может повторить: `authenticode_verify.py release-gate` (§3.3) |
| Серверный e2e (first-run, бэкап, канал, отказы) | джоба `pilot-drill` (живой Compose) | — |
| Установка/обновление/rollback на Windows-машине | — | §5 этого runbook |
| Uninstall без purge / purge / переустановка | — | §6 |

**Классификация уровней проверки** (чтобы не пере- и не до-оценивать):

1. **Automated live Compose drill** (`infra/scripts/pilot-drill.sh`, джоба
   `pilot-drill`): живые backend/frontend/worker/PostgreSQL/backup/channel в
   отдельном compose-проекте, 19 стадий, синтетика через API, бэкап+restore,
   signed channel, tamper-сценарии, restart-переживаемость, отчёты
   JSON+Markdown, non-zero exit при провале. Это **серверный** drill в Linux
   — он НЕ является и не заявляется как полный Windows E2E.
2. **Automated Windows tests**: `infra/windows/tests/run-tests.ps1` (движок
   обновлений/rollback/uninstall-логика, Pester-style контракты) и CI-джоба
   `windows-installer` (сборка+подпись+signtool verify в test-режиме с
   эфемерным сертификатом и локальным RFC 3161 TSA). Реального установщика
   на реальной Windows-машине эти тесты не касаются.
3. **Manual Windows 10/11 lifecycle acceptance** (только владелец, §5–§6):
   установка, first-run, обновление, rollback, uninstall без purge, purge,
   переустановка — на реальной пилотной машине. НЕ выполнялось и не
   заявляется.

CI не содержит production secrets и не может подтвердить production-подпись:
это делает только владелец в environment `installer-signing`.

## 1. Owner-настройки GitHub (однократно, до первого выпуска)

1. **Environment `update-channel-signing`** (Ed25519 канала):
   - secrets: `UPDATE_CHANNEL_SIGNING_KEY` (64 hex закрытого ключа; см. §2),
     `UPDATE_CHANNEL_KEY_ID` (например `pilot-release-2026`),
     `UPDATE_CHANNEL_PUBLIC_KEYS` — trust store production-клиента:
     `{"pilot-release-2026": {"key": "<base64 pub>", "revoked": false}}`;
   - protection: required reviewers (только владелец), НИКОГДА не добавлять
     ветки PR в «Deployment branches».
2. **Environment `installer-signing`** (Authenticode, Phase 14):
   - secrets: `INSTALLER_TRUST_STORE` (тот же trust store ПОЛНОЙ схемой:
     `{"schema_version":1,"environment":"production","keys":{...}}`),
     `INSTALLER_AUTHENTICODE_PFX_BASE64`, `INSTALLER_AUTHENTICODE_PFX_PASSWORD`,
     `INSTALLER_AUTHENTICODE_TIMESTAMP_URL` (RFC 3161, например
     `http://timestamp.digicert.com`), `INSTALLER_AUTHENTICODE_PUBLISHER`
     (Subject сертификата, сверяется после подписи), а также для
     независимого гейта (публичный материал, приватных ключей НЕТ):
     `INSTALLER_AUTHENTICODE_ROOT_PEM` — PEM root CA production-цепочки
     (якорь доверия независимой проверки фактических байтов exe), и по
     желанию `INSTALLER_AUTHENTICODE_TSA_ROOT_PEM` — PEM root CA
     timestamp-сервера (по умолчанию — root'ы подписанта);
   - те же три значения (`INSTALLER_AUTHENTICODE_PUBLISHER`,
     `INSTALLER_AUTHENTICODE_ROOT_PEM`,
     `INSTALLER_AUTHENTICODE_TSA_ROOT_PEM`) добавить и в environment
     `update-channel-signing`: финальный independent-гейт выполняется в
     джобе публикации непосредственно перед `gh release create`;
   - protection: required reviewers (только владелец).
   - Проверка pipeline: `installer_signing.py verify-manifest --mode production`
     отклоняет неподписанный/просроченный по timestamp/чужой publisher/
     тестовый сертификат выпуск (fail closed). Поверх контракта манифеста
     работает НЕЗАВИСИМЫЙ гейт `infra/release/authenticode_verify.py
     release-gate` (см. §3): он не доверяет манифесту и проверяет сами байты
     PE — без `INSTALLER_AUTHENTICODE_ROOT_PEM` production-публикация
     отказывает.
3. **Защита тегов**: tag protection rules — шаблон `v*`, писать могут только
   владелец/CI. SemVer-тег `v*` запускает `update-channel.yml` в production-
   режиме подписи (без выбора); dispatch — только владельцу.
4. Workflow permissions уже минимальны (`contents: read` у installer-джоба,
   `contents: write + id-token + attestations` только у channel-джоба).
   ВСЕ сторонние actions закреплены полными commit SHA (checkout v4.4.0,
   setup-python v5.6.0, setup-node v4.4.0, upload/download-artifact v4,
   attest-build-provenance v2.4.0) — плавающих тегов в signing/release
   джобах нет; concurrency — immutable assets.

## 2. Ceremony ключа подписи канала (Ed25519)

- **Генерация** (офлайн-машина владельца):
  `python -c "import secrets; print(secrets.token_hex(32))"` → закрытый ключ;
  публичный: `python infra/release/sign/verify_channel.py --help` / любая
  утилита Ed25519 из `infra/release/sign`. Открытый ключ публикуется в
  trust store (см. §1) и встраивается в выпуск.
- **Backup**: распечатать/сохранить закрытый ключ в защищённом хранилище
  (пароль-менеджер/HSM владельца). Ключ НИКОГДА не коммитится, не попадает
  в логи/артефакты/CLI-аргументы (pipeline читает его только из secret в env).
- **Ротация (двухключевое окно)**: добавить новый ключ в trust store
  (`revoked: false`), выпустить релиз, подписанный НОВЫМ ключом, дождаться
  установки у пилота, затем пометить старый ключ `revoked: true`. Пока оба
  ключа активны, клиент принимает оба — окно совместимости.
- **Экстренный отзыв**: пометить ключ `revoked: true` в
  `UPDATE_CHANNEL_PUBLIC_KEYS` (+ `INSTALLER_TRUST_STORE`) и выпустить канал
  обновления trust store (или instruct-администратора пилота через
  `Set-HrmChannelConfig`). Отзыв fail closed: manifest, подписанный
  отозванным ключом, отклоняется клиентом (`manifest_revoked_key`).
- **Инвариант**: dev/test ключи (`infra/release/testdata/test_key.*`)
  никогда не входят в production trust store (проверено тестами
  `test_trust_store.py`, `Test-HrmTrustStoreObject`, publish-time mismatch).

## 3. Выпуск обновления (две независимые подписи)

1. Убедиться: CI зелёный на коммите релиза (pipeline сам проверяет CI-runs
   по SHA и отказывает без зелёного CI).
2. Создать SemVer-тег `vX.Y.Z` на коммите → workflow `update-channel.yml`:
   - installer собирается `installer/build.ps1 -Version X.Y.Z -TrustStore ...`
     (trust store из защищённого входа), подписывается Authenticode
     (`sign-installer.ps1 -Mode production`; секреты только через env),
     проверяется `signtool verify /pa /all`, publisher, timestamp,
     пересчитывается SHA256 в `release-manifest.json`, контракт проверяется
     `installer_signing.py verify-manifest --mode production`. Сразу после
     этого — НЕЗАВИСИМЫЙ гейт `authenticode_verify.py release-gate` по
     фактическим байтам подписанного exe (доверие только явным root'ам из
     секретов; манифест — provenance, не источник истины). Installer после
     подписания НЕ пересобирается и не модифицируется;
   - канал: пакет детерминирован, manifest подписан Ed25519-ключом из
     секрета, проверен независимо публичным ключом клиента; встроенный
     trust store сверяется побайтово с trust store релиза
     (`trust_store_mismatch` блокирует выпуск); `trust-store.json` публикуется
     в SHA256SUMS;
   - publication: скачанный из артефакта installer проходит тот же
     независимый гейт ещё раз — непосредственно перед созданием GitHub
     Release (подмена/модификация байтов между подписью и публикацией
     невозможна незамеченной); затем immutable GitHub Release (draft) с
     `--target <SHA>`, provenance attestation пакета.
3. **Независимая проверка владельцем перед promotion** (на своей машине):
   - `sha256sum -c SHA256SUMS` по всем артефактам релиза;
   - `python infra/release/verify_channel.py --manifest update-channel.json
     --public-key <pub>` — Ed25519-подпись;
   - `python infra/release/installer_signing.py verify-manifest
     --manifest release-manifest.json --mode production --exe-dir .`;
   - `python infra/release/authenticode_verify.py release-gate
     --exe HR-Manager-Setup-X.Y.Z.exe --manifest release-manifest.json
     --expected-publisher "<Subject>" --mode production
     --trusted-root <root.pem>` — та же независимая проверка байтов, что в
     pipeline (добавьте `--timestamproot <tsa-root.pem>` при отдельном TSA);
   - `signtool verify /pa /all HR-Manager-Setup-X.Y.Z.exe` (на Windows);
   - сверить `gh api repos/.../attestations` с ожидаемым subject.
4. **Promotion**: `gh release edit vX.Y.Z --draft=false` (только владелец).
   После публикации ассеты неизменяемы — повторная загрузка того же тега
   невозможна.

## 4. Подготовка машины пилота (Windows 10/11)

1. Windows 10 (build ≥19045) или Windows 11; права администратора на время
   установки; ≥10 ГБ свободного места.
2. Установить Docker Desktop (WSL2 backend), включить его; проверить
   `docker compose version` ≥ 2.24 (`compose_ok` в диагностике).
3. Скачать `HR-Manager-Setup-X.Y.Z.exe` из GitHub Release. Проверить
   издателя в свойствах файла (Authenticode) и SHA256 против релиза.
4. Установить (Inno Setup): приложение попадает в
   `%LOCALAPPDATA%\Programs\HRManager`, состояние — в защищённый StateDir
   (ACL только текущий пользователь), секреты генерируются движком один раз.
5. Первый вход: установщик открывает приложение; браузер показывает экран
   first-run (одноразовый exchange token уже погашен установщиком) — задать
   пароль владельца. Проверить: `http://127.0.0.1:8080` открывается, а
   `http://<LAN-IP>:8080` — нет (loopback-only).

## 5. Ручная Windows-приёмка первого пилота (owner, по этому списку)

Выполняется на пилотной машине с синтетическими/стартовыми данными до
допуска реальных данных:

1. **Диагностика готовности**: раздел «Готовность пилота» в приложении
   (admin + `update_channel_manage`): итог «готово | готово с
   предупреждениями | запуск запрещён»; SMTP/Telegram могут быть warning —
   это допустимо.
2. **Синтетические данные**: создать кандидата/кандидатку через UI.
3. **Бэкап + restore drill**: раздел администрирования → создать бэкап,
   запустить restore drill (изолированная БД), убедиться в зелёном статусе.
4. **Обновление**: выпустить тестовый релиз (можно в test-режиме подписи
   installer на отдельном теге dispatch), в приложении «Обновления»:
   проверить → скачать → установить (явное подтверждение). Наблюдать:
   бэкап-ворота → миграция → smoke → перезапуск; данные из п.2 сохранены.
5. **Rollback**:模拟 сбойного обновления (по согласованию, test-канал с
   битым пакетом) — движок обязан откатиться к прежним образам `:previous` с
   сохранением данных; статус в приложении — честный `rolled_back`.
6. **Resume**: прервать наблюдатель (закрыть приложение) во время
   «installing», запустить снова — команда доустанавливается (idempotent),
   без повторной миграции.
7. **Uninstall без purge**: удалить приложение через «Установка и удаление
   программ» → StateDir, том PG (`pilot_pgdata`) и бэкапы (`pilot_backups`)
   СОХРАНЕНЫ (проверить наличие файлов бэкапов).
8. **Переустановка + восстановление**: установить заново → данные на месте
   (та же БД); отдельно: восстановление из бэкапа по `docs/backup-and-restore.md`.
9. **Purge (только по отдельному решению)**: `hr-manager.ps1 action purge` —
   требует фразы `УДАЛИТЬ ДАННЫЕ HR MANAGER` и наличия свежего бэкапа
   (backup-gated); удаляет тома и StateDir.
10. Результаты каждого шага фиксируются в go/no-go (§8) с датой/версией.

## 6. Эксплуатация: RPO/RTO, окно изменений, остановка/откат

- **RPO**: 24 ч (nightly encrypted backup, `BACKUP_SCHEDULE_UTC`); при
  изменчивом дне — ручной бэкап перед обновлением (обязателен, его делает
  движок автоматически).
- **RTO**: ≤ 2 ч (переустановка + авторизверждение данных; restore — по
  `docs/backup-and-restore.md`, проверяется drill'ом).
- **Ответственный**: владелец репозитория (release/rollback/restore);
  пилотный пользователь — только чтение диагностики.
- **Окно изменений**: обновления каналом — в согласованное с пилотом окно,
  не во время активной работы с кандидатами; установка требует явного
  подтверждения администратора в UI.
- **Критерии остановки/отката**: любое `fail` в readiness; статус
  `rolled_back`/`failed` в обновлениях; потеря свежести бэкапа; инцидент
  безопасности → отозвать ключ (§2) и остановить канал.

## 7. Различимость событий и отсутствие телеметрии

События release/update/rollback/restore различимы:
- backend-аудит: `update_check_started/succeeded/failed`,
  `update_download_started/succeeded/failed`, `update_install_requested`,
  `update_engine_reported`, `backup_started/succeeded/failed`,
  `restore_drill_*`, `pilot_readiness_checked` — только коды/версии/job_id,
  без PII, credential-URL, ключей и токенов (проверено тестами).
- Windows-журнал движка (`Write-HrmLog`): `info`-уровень, редакция
  `Redact-HrmText`; события установки/отката/бэкапа различимы по сообщениям.
- Внешней телеметрии нет: единственные исходящие соединения — канал
  обновлений (HTTPS, фиксированные хосты) и SMTP/Telegram, если владелец их
  включил; sender-строки не содержат ключей.

## 8. Go/No-Go чек-лист первого пилотного запуска

Заполняется владельцем; каждый пункт — факт + доказательство (ссылка на CI-
run/лог/скриншот с датой).

| # | Проверка | Факт (дата, SHA, доказательство) |
| --- | --- | --- |
| 1 | Exact SHA и версия релиза | SHA: __________ версия: ______ (дата: ______) |
| 2 | CI зелёный на exact SHA (backend+frontend+integration+stack+pilot-drill+windows) | ссылка: ______ |
| 3 | Production trust store: ≥1 активный Ed25519 ключ, без private material; key_id: ______ | ______ |
| 4 | Authenticode: подписан, `signtool verify /pa /all` OK, timestamp OK, publisher совпал | ______ или «не выполнено — выпуск запрещён» |
| 5 | Независимая проверка Ed25519 manifest + SHA256SUMS владельцем | ______ |
| 6 | Ручная Windows-приёмка §5 шаги 1–9 пройдены на пилотной машине | ______ |
| 7 | Свежий зашифрованный бэкап + успешный restore drill | ______ |
| 8 | Readiness-вердикт на пилотной машине: ready/ready_with_warnings (без fail) | ______ |
| 9 | Owner-настройки §1 выполнены (environments, reviewers, tag protection) | ______ |
| 10 | Контакт/окно изменений/ответственный согласованы с пилотом | ______ |

**Go** = все 10 пунктов закрыты фактическими доказательствами. Любой
«не выполнено» → **No-Go** (исправить и перезаполнить).
