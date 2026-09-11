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
