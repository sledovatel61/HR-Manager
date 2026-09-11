# Phase 14 Report — Pilot Readiness (Arena agent)

Дата: 2026-09-11. Ветка сессии: `arena/01a08fef-hr-manager` (PR в `main`;
merge выполняет владелец). Предыдущая сессия/PR #23 не закрывались и не
изменялись.

## 1. Baseline и финальный SHA

- **Baseline (main до работы):** `de131bf53b484ef94642ba8dd0bbef826f3a77e3`.
- **Финальный SHA:** указан в заголовке PR (см. ссылку на PR ниже; CI
  прогоняется на exact SHA).
- База для сравнения тестов: backend 595 passed / 105 deselected,
  frontend 152 tests.

## 2. Что сделано (по областям контракта)

### 2.1 Production release + две независимые подписи

- `infra/release/installer_signing.py` — машиночитаемый fail-closed контракт
  Authenticode-подписи установщика: production-режим требует
  `status=signed`, `mode=production`, `certificate_kind=production`,
  `signtool_verify=passed`, валидный RFC 3161 timestamp, совпадение
  publisher и пересчитанный после подписи SHA256 exe; ephemeral
  тестовый сертификат (`test-self-signed`) НИКОГДА не проходит
  production-политику; тестовый контур CI запрещает заявлять production.
  CLI: `verify-manifest --manifest ... --mode production|test`.
- `installer/sign-installer.ps1` — подпись `signtool` (production-секреты
  ТОЛЬКО через env `HRM_SIGNING_PFX_*`, PFX в пользовательское хранилище,
  пароль никогда в командной строке; test-режим — ephemeral
  CodeSigningCert раннера; disabled — честный unsigned), после подписи —
  `signtool verify /pa /all`, publisher, timestamp, пересчёт SHA256 в
  `release-manifest.json`; секретный материал удаляется в `finally`.
- `.github/workflows/update-channel.yml` (Phase 14-секции): джоб
  `windows-installer` получает environment `installer-signing`, встраивает
  публичный trust store из секрета `INSTALLER_TRUST_STORE`
  (`build.ps1 -TrustStore`), подписывает (тег `v*` → всегда production,
  fail closed; dispatch — явный выбор через env, никогда не инлайнится в
  run-блок), проверяет контракт; channel-джоб перед публикацией сверяет
  контракт подписи, ПОБАЙТОВО встроенный trust store против trust store
  канала и SHA256 подписанного exe; релиз публикует `trust-store.json`.
- Тестовый режим без production secrets: CI `windows-installer` в `ci.yml`
  работает без environment; ephemeral cert помечается
  `certificate_kind=test-self-signed`.

### 2.2 Доставка trust configuration

- `infra/release/trust_store.py` — строгая схема публичного trust store:
  закрытый набор полей (`schema_version/environment/keys`), запрет
  private material (скан имён и значений), `environment=production`
  только, уникальные `key_id` (`^[A-Za-z0-9._-]{1,64}$`), base64 32-байтовые
  Ed25519 ключи, явный `revoked`, ≥1 активный ключ, дубликаты fingerprint
  отклоняются; `stores_match` — каноническое сравнение; `key_fingerprint`,
  `key_status`, `redacted_summary` (только key_id/fingerprint/status).
- `publish_channel.py` — шаг 0 (до любых записей в out_dir): встроенный
  `release-trust-store.json` обязан пройти схему, ПОБАЙТОВО совпасть с
  trust store релиза (`--public-keys-json`) и содержать неотозванный ключ
  подписи; иначе выпуск заблокирован без частичных артефактов. Совпадение →
  `trust-store.json` публикуется и включается в SHA256SUMS.
- Windows-движок (`Channel.psm1`): `Test-HrmTrustStoreObject` (зеркало
  схемы; test-хранилище не становится production root), `Import-HrmTrustStore`
  (публикует только публичные ключи; существующий `channel.json` НИКОГДА не
  перезаписывается — ротация/отзыв админом выигрывают).
- `installer/build.ps1 -TrustStore <path>` — `Test-HrmInstallerTrustStore`
  (тот же контракт), копия в снимок приложения + рядом с манифестом
  (для побайтовой сверки конвейером); блок `trust_store{embedded,sha256}`
  и честный `signing{status=unsigned, mode=disabled, instruction}`.
- `Install.psm1` — `Copy-HrmSnapshot` переносит `release-trust-store.json`,
  `Import-HrmTrustStore` выполняется при первичной установке.

### 2.3 Предпусковая диагностика в приложении

- Backend: `app/host_facts.py` (in-memory host facts, TTL 10 мин),
  `app/readiness.py` — 17 проверок: engine_watcher, os_supported,
  docker_daemon, compose_version, loopback_binding, state_dir_secured,
  disk_free (MIN_FREE_MB=5120), api_health, migrations_state,
  worker_health, backup_fresh, restore_drill, release_identity,
  trust_store (только key_id/fingerprint/status), channel_https
  (timeout 6 c, warning при офлайне), smtp_optional, telegram_optional;
  вердикты ready | ready_with_warnings | blocked; SMTP/Telegram и офлайн-
  канал — warning, не блокируют.
- `POST /api/updates/engine-facts` — машинный токен `X-Engine-Token`,
  закрытая схема (`extra=forbid`, IP-литералы, ограниченные enum), без
  аудита/PII; `GET /api/updates/readiness` — admin + `update_channel_manage`,
  rate limit, аудит `PILOT_READINESS_CHECKED` без секретов. Read-only,
  никаких автоисправлений.
- Windows-движок: `Get-HrmEngineFacts` (redacted факты: windows_version,
  docker_state, compose_ok, published_ports, disk_free_mb, state_dir_acl_ok,
  staging_writable/outside_state, previous_images_present, watcher_running),
  `Send-HrmEngineFacts` (loopback + токен, кеш 300 c, сбой не мешает
  каналу), `Reset-HrmEngineFactsCache` (тестовый шов),
  `Invoke-HrmChannelOnce -SkipFacts`; факты отправляются ДО опроса
  engine-state.
- Frontend: `features/updates/ReadinessPage.tsx` + навигация «Готовность
  пилота»: loading/empty/offline/error/retry, 403/429, вердикт-бейдж,
  список проверок (state/explanation/action/details), клавиатурная
  навигация, read-only («Проверить снова» — только перечитывание).

### 2.4 Автоматизированный e2e pilot drill (CI-контур)

- `infra/scripts/pilot-drill.sh` + `infra/compose.drill.yml` +
  `infra/scripts/drill_channel_server.py` (эпемерный TLS drill-CA,
  отдельный compose-проект `hr-manager-pilot-drill`, без production
  secrets): живой стек пилота, 19 стадий — prerequisites, эфемерные
  секреты, TLS, публикация канала fixture-ключом, compose config/up,
  health, first-run (exchange token → владелец), синтетические данные
  через публичный API, зашифрованный бэкап + restore drill в изолированную
  БД, readiness (проверка отсутствия секретов в ответе), канал
  check→download (staging SHA256 сверен)→явный install→resume (повторная
  выдача той же команды)→engine-report installed, сохранение данных,
  отказ на повреждённую подпись manifest (`manifest_bad_signature`),
  redirect на запрещённый хост (`bad_url` ДО запроса), подмену пакета
  (`package_hash_mismatch`), блокировка установки после отказов,
  «удаление без purge» (down без -v: данные+бэкапы сохранены). Выход
  non-zero при провале; отчёты `pilot-drill.json` (машиночитаемый) и
  `pilot-drill.md` (краткий) — только коды/счётчики, без секретов/PII.
- CI-джоба `pilot-drill` в `ci.yml` (ubuntu, 45 мин timeout, артефакты
  отчётов). Windows-специфика (реальный установщик, rollback образов,
  uninstall/purge, Authenticode) — ручная приёмка по runbook §5.

### 2.5 Runbook + go/no-go

- `docs/phase-14-runbook.md`: owner-настройки (environments
  `update-channel-signing` + `installer-signing`, required reviewers,
  защита тегов `v*`), ceremony генерации/backup/ротации (двухключевое
  окно)/экстренного отзыва ключа, выпуск с двумя подписями и независимой
  проверкой (SHA256SUMS/Ed25519/Authenticode/attestation) перед promotion,
  подготовка машины пилота, ручная Windows-приёмка (10 шагов), RPO/RTO/
  ответственный/окно изменений/критерии остановки, uninstall без purge и
  backup-gated purge, различимость событий в аудите/логах без
  PII/credential-URL/ключей, go/no-go чек-лист из 10 пунктов с датой/
  exact SHA/доказательствами. Реальная production-подпись и ручная
  Windows-приёмка НЕ выполнялись и НЕ заявлены как выполненные.

## 3. Изменённые файлы

Backend (новые): `app/host_facts.py`, `app/readiness.py`,
`tests/test_trust_store.py`, `tests/test_installer_signing.py`,
`tests/test_readiness_api.py`, `tests/test_pilot_drill.py`.
Backend (изменённые): `app/schemas.py` (UpdateEngineFactsPort/Request,
ReadinessCheckOut/ReportOut), `app/routers/updates.py` (engine-facts,
readiness), `app/models.py` (PILOT_READINESS_CHECKED), `app/main.py`
(app.state.host_facts), `app/channel.py` (timeout-параметр),
`tests/test_release_pipeline.py` (+6 embedded trust store, +1 Phase 14
workflow invariants).
Infra/release (новые): `trust_store.py`, `installer_signing.py`;
(изменённый) `publish_channel.py` (шаг 0 + 4b, trust-store.json).
Infra/windows: `engine/Channel.psm1` (trust store + факты + -SkipFacts),
`engine/Install.psm1` (trust store в снимке и установке),
`tests/channel.tests.ps1` (+11 Test-Case), `tests/test-harness.ps1`
(моки docker ps/images + поля DockerPsPorts/DockerImagesOutput).
Installer: `build.ps1` (-TrustStore, манифест-блоки), `sign-installer.ps1`
(новый).
Frontend: `ReadinessPage.tsx`/`.test.tsx` (новые), `api.ts`, `types.ts`,
`Workspace.tsx`, `useWorkspaceSection.ts`, `updates.css`.
Workflows: `update-channel.yml` (Authenticode-джоб + trust store + проверка
перед публикацией), `ci.yml` (джоба `pilot-drill`).
Docs: `docs/phase-14-runbook.md` (новый), `docs/CURRENT_STATUS.md`.

## 4. Тесты и проверки (локально; окружение — Debian 12, Python 3.11.2)

- Backend: `ruff check` OK; `ruff format --check` OK (119 файлов);
  `mypy app tests` OK (105 файлов); `pytest -m "not integration"` —
  **668 passed, 105 deselected** (baseline 595 → +73: trust store 19,
  installer signing 17, readiness API 24, release pipeline +7, drill 6).
- Frontend (Node 22.22.3): lint OK; `tsc --noEmit` OK; vitest —
  **161 passed** (152 + 9 ReadinessPage); production build OK.
- Windows-движок: `lint-engine.py` — 16 файлов OK; `run-tests.ps1`
  локально НЕ запускался (нет Windows/pwsh в песочнице) — CI обязан
  прогнать (31 Test-Case в channel.tests.ps1: 20 Phase 13 + 11 Phase 14).
- `installer/build.ps1 -Version` локально НЕ запускался (нет Windows) — CI.
- Compose: `docker compose -f ... config -q` локально НЕ запускался (нет
  Docker daemon; структура overlay проверена YAML-парсингом и тестами) —
  CI (джобы stack + pilot-drill рендерят и поднимают живой стек).
- `git diff --check` — чисто; все PS1 — UTF-8 BOM (проверено).

## 5. Ограничения и честные оговорки

0. GitHub App сессии не имеет права `workflows`: изменения
   `.github/workflows/ci.yml` и `update-channel.yml` НЕ могут быть запушены
   из ветки агента. Phase 14-версии опубликованы как точные копии + патчи в
   `review-artifacts/` (`update-channel.phase14.yml/.patch`,
   `ci.phase14.yml/.patch`; применение патчей проверено тестом
   `test_workflow_phase14_patch_applies_byte_exact`). Владельцу после merge
   перенести их в-tree (инструкция в `review-artifacts/README.md`) — до
   переноса джоба `pilot-drill` в CI не запускается.
1. Локальный Python — 3.11.2 (3.12 недоступен в песочнице: CDN заблокирован,
   apt недоступен); CI (Python 3.12) — авторитетный прогон type/lint/test.
   Код написан под 3.12 (fromisoformat и пр. не используются несовместимо).
2. PostgreSQL integration (105 тестов), живой Compose-stack, Windows-тесты
   движка, сборка installer и pilot-drill — не выполнялись локально;
   ожидаются от CI на exact SHA PR (джобы: integration, stack, pilot-drill,
   windows-installer).
3. Ручная Windows-приёмка (runbook §5) и реальная production-подпись
   Authenticode НЕ выполнялись — в отчёте и runbook они значатся как
   owner-шаги; go/no-go не заполнен.
4. `gh api` (REST) в сессии возвращает 403 для GitHub App — ссылку на CI-runs
   и финальные артефакты добавит владелец/CI после пуша.
5. В CI-джобе pilot-drill стоит `sudo apt-get update || true` — на
   ubuntu-latest нужен только для OpenSSL-утилит (обычно предустановлены);
   шаг не влияет на воспроизводимость тестов.

## 6. Threat model (кратко, по контракту фазы)

- Подмена trust store (встроенный ≠ release) — блокируется ДО записи
  артефактов (`trust_store_mismatch`, шаг 0 publish).
- Private material в trust store / канале — структурный скан + регресс-тесты
  (Python + PowerShell зеркала).
- Неподписанный/чужой publisher/просроченный timestamp/тестовый сертификат
  installer — production-политика fail closed (`installer_signing.py`).
- Повреждённые manifest/подпись/пакет и redirect на посторонний хост —
  отказ до изменения установки (drill стадии 16–18, unit-тесты Phase 13).
- Утечка секретов через диагностику — readiness/engine-facts отдают только
  коды/ограниченные факты; тесты `no_secrets_or_paths_in_readiness_response`,
  grep секретов в drill.
- Dev/test trust root → production — запрещён схемой (`test_trust_store`)
  на обеих сторонах (Python/PowerShell).
- Извлечение ключей из CI — секреты только в environments с required
  reviewers, без веток PR; ephemeral раннер-секреты drill удаляются.

## 7. Handoff

- Дальше: прогон CI на PR (особенно integration/stack/pilot-drill/
  windows-installer), owner-настройки environments (runbook §1), ceremony
  ключа (§2), первый production-выпуск (§3), ручная Windows-приёмка (§5),
  заполнение go/no-go (§8).
- PR из `arena/01a08fef-hr-manager` в `main`; предыдущие PR/сессии не
  закрывались.

## 8. Доработка (hardening) поверх PR #25, baseline `1c6961f`

Дата: 2026-09-11. Отдельный commit в этой же ветке/PR #25 (финальный SHA —
в заголовке PR и в описании коммита; история не переписывалась).

### 8.1 Главная цель

Публикация больше НЕ доверяет signing manifest/attestation и строковым
полям о результате `signtool` как источнику истины: непосредственно перед
publish выполняется независимая криптографическая Authenticode-проверка
ФАКТИЧЕСКИХ байтов PE.

### 8.2 Что добавлено/изменено

| Файл | Суть |
|---|---|
| `infra/release/authenticode_verify.py` (новый, ~740 строк) | Независимый верификатор: PE-структура, certificate table (WIN_CERTIFICATE), CMS/PKCS#7, SPC_INDIRECT_DATA, digest образа с масками CheckSum/SecurityDir, signedAttrs (content-type/message-digest), подпись подписанта, цепочки до ЯВНЫХ корней (побайтово; никаких системных хранилищ), EKU Code Signing, publisher, RFC 3161 timestamp (messageImprint == хеш подписи, genTime как точка валидности цепочек, EKU Time Stamping у TSA, отдельные TSA-корни), SHA-256+ (SHA-1/MD5 запрещены), legacy MS timestamp отклонён. CLI: `verify` / `release-gate` (`--exe` путь ИЛИ байты; `--json`). Коды ошибок различимы (unsigned_pe, digest_mismatch, untrusted_root, tsa_untrusted, timestamp_invalid, sha1_forbidden, manifest_* и др.), всё fail-closed |
| `infra/scripts/drill_tsa_server.py` (новый) | Эфемерный RFC 3161 TSA для CI/тестов: самоподписанный сертификат с EKU Time Stamping создаётся при старте (PEM+DER), HTTP `application/timestamp-query`→`timestamp-reply`, rejection на мусор/неизвестный digest. Используется windows-installer-джобой в test-режиме (`signtool /tr` на локальный TSA) и юнит-тестами |
| `backend/tests/_authenticode_testkit.py` (новый) | Эфемерные CA/подписанты/TSA (RSA 2048, EKU) в памяти на время прогона; минимальный структурно-корректный PE32+; сборщик Authenticode-подписи (SPC, signedAttrs, RFC 3161 countersignature). Никаких production-ключей нигде |
| `backend/tests/test_authenticode_verify.py` (новый) | 37 тестов: позитивные (production/test, путь==байты, отдельные TSA-корни, JSON-отчёт без key material) + все негативные сценарии ТЗ (см. §8.4) |
| `installer/sign-installer.ps1` | test-режим: `HRM_SIGNING_TIMESTAMP_URL` позволяет подставить локальный эфемерный TSA (default — прежний публичный); после подписи выгружается ПУБЛИЧНЫЙ сертификат подписанта (`installer/output/signer-public.pem`, ASCII-PEM) для независимой проверки цепочки test-режима |
| `review-artifacts/update-channel.phase14.yml` (+`.patch`) | (1) шаг вычисления режима подписи ДО подписи (push→production, fail-closed валидация, production требует TSA-URL); (2) локальный TSA в test-режиме + импорт его сертификата в Root раннера; (3) независимый гейт `authenticode_verify.py release-gate` сразу после подписи (до upload); (4) тот же гейт в channel-release над скачанным артефактом НЕПОСРЕДСТВЕННО перед `gh release create`; (5) ВСЕ сторонние actions запинены полными commit SHA (checkout v4.4.0, setup-python v5.6.0, upload-artifact v4.6.2, download-artifact v4.3.0, attest-build-provenance v2.4.0); (6) артефакт несёт публичные PEM (`signer-public.pem`, `tsa-cert.pem` в test-режиме) для гейта публикации |
| `review-artifacts/ci.phase14.yml` (+`.patch`) | Пиннинг сторонних actions полными SHA (checkout, setup-python, setup-node, upload-artifact). Новые тесты исполняются в существующей backend-джобе (asn1crypto — из requirements-dev) |
| `backend/requirements-dev.txt` | `asn1crypto==1.5.1` (пиннинг) |
| `backend/pyproject.toml` | mypy-override для asn1crypto (нет py.typed) |
| `backend/tests/test_release_pipeline.py` | Инварианты workflow расширены: режим до подписи, гейты до upload/до publish, fail-closed на отсутствие `INSTALLER_AUTHENTICODE_ROOT_PEM`, локальный TSA, порядок шагов |
| `docs/phase-14-runbook.md` | Новые секреты гейта (ROOT_PEM/TSA_ROOT_PEM, публичный материал) в двух environments; независимый гейт в §3; команда владельцу; явная классификация уровней проверки (см. §8.6) |
| `review-artifacts/README.md` | Описаны новые секреты, гейт, перечень негативных тестов |

Installer после подписания не пересобирается и не модифицируется:
единственный путь байтов exe — sign → gate → upload-artifact →
download-artifact → gate → `gh release create` (оба гейта и сверка SHA256
проверяют одни и те же байты).

### 8.3 Негативные сценарии, покрытые тестами (37 passed)

unsigned PE (`unsigned_pe`); non-PE/пустой (`not_pe`); модификация после
подписи в заголовке и в теле (`digest_mismatch`); перенос подписи с другого
файла (`digest_mismatch`); ложный digest в подписи (`digest_mismatch`);
SHA-1 (`sha1_forbidden`); мусорный/обрезанный CMS
(`bad_signature_structure`); чужой publisher (`publisher_mismatch`);
недоверенный root подписанта (`untrusted_root`); подписант без EKU Code
Signing (`eku_missing`); без timestamp (`timestamp_missing`); timestamp с
чужим messageImprint (`timestamp_invalid`); повреждённый токен TSA
(`timestamp_invalid`); недоверенный TSA (`tsa_untrusted`); TSA без EKU Time
Stamping (`tsa_eku_missing`); legacy MS timestamp
(`timestamp_legacy_unsupported`); цепочки вне срока на genTime
(`certificate_expired`); пустые/мусорные корни (`bad_trust_root`);
forged manifest над неподписанным exe (`unsigned_pe`); несовпадение SHA256 в
манифесте (`manifest_sha_mismatch`); ложный publisher в манифесте
(`manifest_publisher_mismatch`); тестовый сертификат в production-режиме
(`untrusted_root`/`tsa_untrusted` + `manifest_mode_mismatch`); отсутствующий
и битый манифест (`manifest_missing`/`manifest_invalid`); самосогласованный
подделанный манифест над изменённым exe (`digest_mismatch`); CLI: exit 0/1 и
JSON-отчёт; TSA-сервер: rejection на мусор, roundtrip через HTTP.

### 8.4 Команды и результаты (локально, Debian 12, Python 3.11.2, venv)

| Команда (cwd=`backend/`) | Результат |
|---|---|
| `pytest -m "not integration" -q` | **707 passed, 105 deselected** (на `1c6961f` собрано/пройдено 670: `--collect-only` 670/775; +37 новых тестов authenticode_verify, включая параметризованные 2×TSA-EKU) |
| `pytest tests/test_authenticode_verify.py -q` | **37 passed** |
| `pytest tests/test_release_pipeline.py -q` | **16 passed** (включая byte-exact применение обоих `.patch`) |
| `ruff check .` / `ruff format --check .` | чисто |
| `mypy app tests` | **Success: no issues in 107 source files** |
| `git diff --check` | чисто |
| `git apply --check review-artifacts/*.phase14.patch` | оба применяются |

### 8.5 Threat model (дополнение к §6)

- Подделанный signing manifest/attestation (утверждает «подписан»,
  корректный SHA256, production) — бессмысленен: гейт проверяет сами байты
  PE, манифест используется только как provenance (сверка полей).
- Подмена/модификация exe между подписью и публикацией (в т.ч. пересборка
  или подмена артефакта) — digest образа не сойдётся (`digest_mismatch`)
  на гейте перед `gh release create`.
- Перенос валидной подписи с другого файла — digest считается по маскам
  (CheckSum, SecurityDir) от фактического образа → отказ.
- Компрометация плавающего тега action'а в signing/release джобах —
  исключена пиннингом полных commit SHA.
- Подмена корней доверия: production-гейт берёт корни ТОЛЬКО из
  owner-секретов environment'ов; их отсутствие — отказ публикации; в
  test-режиме корни — фактические эфемерные сертификаты раннера/артефакта,
  production-политика их не принимает.
- TSA-атаки: недоверенный TSA, TSA без EKU Time Stamping, чужой
  messageImprint, legacy MS timestamp, genTime вне срока сертификатов —
  отдельные коды отказа.
- Production PFX/private keys — только в environment `installer-signing`
  (required reviewers, без веток PR); в репозитории/фикстурах/логах/
  артефактах отсутствуют (фикстуры эфемерные, создаёт тест-кит в памяти;
  `signer-public.pem`/`tsa-cert.pem` — публичный материал).

### 8.6 Классификация уровней проверки (не переоцениваем)

1. **Automated live Compose drill** (`pilot-drill.sh`, 19 стадий) — живой
   серверный стек в Linux. Запуск локально невозможен (Docker в песочнице
   недоступен) — **not validated локально**, не упрощён и не подменён
   pytest-набором; CI-джоба `pilot-drill` — в phase14-патче для владельца.
   Это НЕ полный Windows E2E и так не заявляется.
2. **Automated Windows tests**: `infra/windows/tests/run-tests.ps1` (31
   Test-Case, не запускались локально — нет pwsh) и CI-джоба
   `windows-installer` (сборка+подпись+гейт в test-режиме с локальным TSA) —
   по phase14-патчу; в CI этой ветки НЕ запускались (права на
   `.github/workflows` у GitHub App нет — см. §5.0).
3. **Manual Windows 10/11 lifecycle acceptance** (runbook §5–§6) и реальная
   production-подпись PFX — не выполнялись и не заявляются как выполненные.

### 8.7 Skipped / not validated (честный список)

- PostgreSQL integration (105), Compose stack/pilot-drill, frontend (не
  затронут), PowerShell-тесты — локально не запускались (окружение); CI на
  exact SHA — у владельца после переноса workflow (прав нет).
- `sign-installer.ps1`: правки (env-override TSA, экспорт публичного PEM)
  проверены ревью и структурой, локального pwsh нет — runtime-прогон в CI.
- Ветка `arena/01a08fef-hr-manager` не содержит изменений
  `.github/workflows/*` (заблокировано правами App): все CI-изменения — в
  `review-artifacts/` (+ проверяемые патчи); зелёные checks старых workflow
  НЕ являются evidence новых джоб/гейтов.
