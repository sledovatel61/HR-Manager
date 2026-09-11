# Phase 14 — эксплуатационная готовность и ограниченный запуск Windows-пилота

Отчёт coding-сессии Arena (самостоятельный агент). Phase 14 — hardening/acceptance:
новых HR-функций, второго updater'а, автоустановки, telemetry SaaS, `docker.sock`
и обхода проверки подписи не добавлялось.

* **Baseline**: `de131bf` (merge PR #22, содержит принятую Phase 13, включая
  `.github/workflows/update-channel.yml`; `main` = `origin/main` на момент старта).
  Merge PR #23 (`3f1ae7c`) в графе получения отсутствовал (grafted clone), но его
  файлы уже входят в baseline-дерево — Phase 14 строилась от фактического
  `origin/main`, а не от устаревшей ветки.
* **Branch**: `arena/01a08ff0-hr-manager` (ветка сессии; отдельную
  `arena/phase-14-*` создать нельзя — сессия жёстко привязана к этой ветке,
  поэтому вся работа и PR идут из неё). Merge выполняет владелец.
* **Final SHA (код, зелёный HEAD)**: `d5423906745abbd4eb2756cddf28ba22c8dbb996` — exact final SHA
  (зелёный кодовый HEAD, см. §5). Промежуточные `0313f02` и `7beb84e` — исторические,
  не final. Этот документ синхронизирован с `d542390` (PR #24 head для кода);
  head ветки после синхронизации docs — см. PR head (текущий, на момент этого
  коммита — docs-sync, CI на нём — те же 5 jobs, не полный Phase 14).
* **PR**: https://github.com/sledovatel61/HR-Manager/pull/24

## 1. Что сделано по пунктам промпта

### 1.1 Две независимые подписи и production-политика

* Ed25519-подпись канала остаётся обязательной; Authenticode её не заменяет.
* `infra/release/authenticode.py` — независимый (без Windows/WinAPI) парсер PE,
  PKCS#7, `SpcIndirectDataContent`, цепочки X.509, EKU, RFC3161 timestamp;
  коды отказа: `not_pe`, `unsigned`, `bad_pkcs7`, `digest_mismatch`,
  `missing_eku`, `publisher_mismatch`, `certificate_expired`, `untrusted_root`,
  `missing_timestamp`.
* `infra/release/sign_authenticode.py` — ephemeral тестовый CA и подпись
  тестового PE для CI/fail-closed контракта без production secrets.
* `installer/sign.ps1` — боевая подпись: PFX только из `RUNNER_TEMP`, пароль
  только из `HRM_AUTHENTICODE_PFX_PASSWORD`, `signtool sign /fd SHA256 /tr /td
  SHA256` + обязательный `signtool verify /pa /all`, проверка метки времени и
  издателя, attestation/roots только с публичными фактами.
* `installer/build.ps1` принимает trust store только как уже проверенный вход
  (`-TrustStoreFile` + обязательный `-TrustStoreSha256`), отвергает private
  material и встраивает набор в снимок и манифест.
* `publish_channel.py --release-mode production` fail closed: отсутствие
  installer/attestation/roots/издателя, неподписанный installer, отсутствующая
  метка времени, несовпадение издателя, подмена файла после подписи,
  недоверенный signing key, несовпадение встроенного trust store с релизным,
  private material в пакете, тестовый сертификат в production.
* `.github/workflows/update-channel.yml` переписан (доставлен как review-artifact
  `review-artifacts/update-channel.phase14.yml` — см. ограничение ниже):
  environment `update-channel-signing`, минимальные `permissions`, `concurrency`,
  pinned actions, gate «CI зелёный на этом SHA», CR/LF-reject входов dispatch,
  сборка строго из подтверждённого SHA, `--target $SHA` при создании релиза,
  immutable draft-release, build-provenance attestation.

### 1.2 Доставка trust configuration

* Один строгий контракт в трёх местах: `infra/release/trust_store.py`,
  `backend/app/trust_store.py`, `installer/build.ps1`/`sign.ps1`.
* Проверки: строгая схема `{key_id: {key, revoked}}`, уникальные `key_id`,
  Ed25519 public key ровно 32 байта, `revoked`, отсутствие private material
  (в т.ч. 64-hex), ограничение размера; diagnostics/attestation показывают
  только `key_id`/fingerprint/status.
* `assert_no_fixture_keys` не даёт fixture-ключам стать production-ключом;
  dev/test-набор не может молча стать production trust root (production-релиз
  требует явный trust store из защищённого входа).
* Ротация — двухключевое окно, отзыв — fail closed (`revoked_key`); backend
  намеренно принимает полностью отозванный набор (аварийное состояние,
  канал честно отвечает «ключ отозван»), release-сборка такой набор не создаёт.

### 1.3 Readiness в приложении

* `GET /admin/ops/pilot-readiness` — admin + подтверждённый grant
  `update_channel_manage`, read-only, аудит `pilot_readiness_viewed` только с
  вердиктом и счётчиками.
* `POST /updates/engine-host-report` (машинный токен движка, схема
  `extra="ignore"`) принимает redacted-факты о хосте; хранилище — in-memory
  (`app/host_evidence.py`, max age 24 ч).
* `app/readiness.py` — server-owned проверки: host evidence, platform
  (сборка Windows), docker daemon, compose, published ports (loopback),
  disk space, rollback (образ `:previous`), staging, state_dir ACL, secrets,
  database, migrations, worker, release version/SHA, trust store,
  channel availability, SMTP/Telegram (только warning), backup freshness и
  restore drill. Вердикт `готово | готово с предупреждениями | запуск запрещён`.
* UI: раздел «Готовность пилота» (admin) с loading/empty/offline/error/retry,
  состояниями `pass|warning|fail`, следующим действием, фокусом на вердикте
  после повторной загрузки и клавиатурной доступностью.
* Движок: `-Action diagnostics` отправляет хост-отчёт (best-effort, без путей
  и секретов), поэтому readiness информативен без ручной работы с БД.

### 1.4 Автоматизированный pilot drill

* `infra/scripts/pilot_drill.py` — **automated test aggregator**, не
  полноценный реальный Windows E2E. Запускает реальные компоненты в
  Linux-контуре: release-политику и обе подписи; отказы канала на подделках
  manifest/подписи/пакета и запрещённых хостах; readiness API; backup+restore
  в изолированную БД (PostgreSQL); Windows-движок (install/update/rollback/
  resume/uninstall) — в контейнере без PowerShell/Inno Setup эти шаги
  честно `skipped` (никогда `passed`). Пишет `pilot-drill.json` +
  `pilot-drill.md`, маскирует DSN, падает non-zero при провале.
  **Реальные** clean install / update / rollback / uninstall / reinstall и
  сохранение данных остаются **manual acceptance** владельца на живой
  Windows-машине (см. runbook, go/no-go) — drill их не заменяет.
* Drill встроен в CI-конфигурацию как агрегатор: backend job
  (политика/канал/readiness), integration job (backup/restore на PostgreSQL),
  windows-installer job (движок). Конфигурация доставляется review-artifact и
  активируется после переноса владельцем (ограничение `workflows`-permission,
  см. §5). Разделение «автоматизируемое в CI — aggregator» / «ручная
  Windows-приёмка — mandatory» зафиксировано в Markdown отчёта и в runbook.
* CI не использует production secrets: только fixture/testdata и ephemeral
  тестовые ключ/сертификат.

### 1.5 Runbook, наблюдаемость, go/no-go

* `docs/runbook-pilot-release.md`: подготовка Windows/Docker, установка, первый
  вход, проверка loopback; owner-настройка environment, reviewers, secrets,
  protected `v*`; церемония генерации/бэкапа/ротации/экстренного отзыва ключа;
  выпуск, независимая проверка хешей/подписей/attestation и promotion;
  backup/restore drill, update/rollback/resume, сбор redacted-диагностики;
  RPO 26 ч / RTO 4 ч, ответственный, окно изменений, stop/rollback критерии;
  uninstall с сохранением данных и полный purge; go/no-go checklist с полями
  для даты, exact SHA/version и доказательств.
* События release/update/rollback/restore различимы в существующих логах и
  аудите, без PII/URL с credentials/ключей/токенов; внешней телеметрии нет.

### 1.6 Обязательные тесты

| Файл | Тестов | Что доказывает |
| --- | --- | --- |
| `backend/tests/test_release_policy.py` | 16 | production fail closed на ephemeral сертификате, тестовый сертификат не проходит policy, подмена attestation/файла отклоняется |
| `backend/tests/test_release_authenticode.py` | 15 | независимая проверка Authenticode: digest, EKU, цепочка, timestamp, издатель |
| `backend/tests/test_trust_store.py` | 30 | строгая схема, private material, подмена/отзыв, согласие release- и backend-валидатора |
| `backend/tests/test_readiness_api.py` | 18 | права (401/403/200), redaction и server-owned результат, host-факты, backup-состояния, «не подтверждено» → warning |
| `frontend/src/features/readiness/PilotReadinessPage.test.tsx` | 8 | loading/empty/offline/error/retry, 403, вердикты, клавиатура, фокус |
| `infra/windows/tests/channel.tests.ps1` (добавлено) | 4 | хост-отчёт: токен только в заголовке, отсутствие секретов/путей, офлайн, отсутствие install record |

## 2. Threat model (кратко)

| Угроза | Контрмера |
| --- | --- |
| Подмена обновления (MITM/CDN) | detached Ed25519-подпись manifest, HTTPS-only, проверка каждого redirect-хопа до запроса, запрещённые хосты |
| Подмена installer'а | Authenticode + RFC3161 + цепочка до корня из защищённого входа + совпадение издателя; повторная независимая проверка вне Windows |
| Подмена/утечка trust store | строгая схема, отсутствие private material, fingerprint/key_id в diagnostics, fail closed на отзыв, запрет fixture-ключей в production |
| Компрометация signing key | environment с required reviewers, ключ только в secret, двухключевое окно ротации, экстренный отзыв; неизменяемость релизных активов |
| Выход сервисов наружу | обязательный loopback-binding, readiness-проверка опубликованных портов (наблюдаемые факты движка), prod-overlay без published-портов |
| Утечка секретов через диагностику/логи | redacted diagnostics, схема хост-отчёта `extra="ignore"`, аудит только с вердиктом/счётчиками, drill маскирует DSN и падает на private material в выводе |
| Скрытая автоустановка | установка только по явной команде администратора; фоновый watcher лишь проверяет наличие обновления |
| Потеря данных при сбое | бэкап-ворота перед миграцией, автоматический rollback образов, resume на документированных точках, uninstall без purge сохраняет StateDir/volumes |
| Ложная «готовность» | вердикт считается из server-owned фактов; отсутствие данных — warning, а не pass; ручная приёмка никогда не отмечается как passed автоматически |

## 3. Изменённые и новые файлы

**Release/Authenticode (новое):** `infra/release/der.py`, `infra/release/authenticode.py`,
`infra/release/sign_authenticode.py`, `infra/release/trust_store.py`,
`installer/sign.ps1`.

**Release (изменено):** `infra/release/publish_channel.py`, `installer/build.ps1`,
`infra/release/README.md`, `installer/README.md`.

**Workflow (review-artifacts, переносит владелец):**
`review-artifacts/update-channel.phase14.yml` (+`.patch`),
`review-artifacts/ci.phase14.yml` (+`.patch`), обновлённый
`review-artifacts/README.md`.

**Backend (новое):** `backend/app/readiness.py`, `backend/app/host_evidence.py`,
`backend/app/trust_store.py`, `backend/tests/test_release_authenticode.py`,
`backend/tests/test_release_policy.py`, `backend/tests/test_trust_store.py`,
`backend/tests/test_readiness_api.py`.

**Backend (изменено):** `app/channel.py` (валидатор trust store + `describe_trusted_keys`),
`app/main.py` (`app.state.host_evidence`), `app/models.py`
(`AuditAction.PILOT_READINESS_VIEWED`), `app/routers/ops.py`
(`GET /admin/ops/pilot-readiness`), `app/routers/updates.py`
(`POST /updates/engine-host-report`), `app/schemas.py` (схемы Phase 14).

**Windows-движок:** `infra/windows/engine/Diagnostics.psm1` (хост-отчёт и факты),
`infra/windows/hr-manager.ps1` (`diagnostics` + help),
`infra/windows/tests/channel.tests.ps1` (4 теста), `infra/windows/README.md`.

**Frontend:** `src/features/readiness/PilotReadinessPage.tsx` (+ `readiness.css`,
+ тест), `src/api.ts`, `src/types.ts`, `src/app-shell/Workspace.tsx`,
`src/app-shell/useWorkspaceSection.ts`.

**Drill/docs:** `infra/scripts/pilot_drill.py`, `docs/runbook-pilot-release.md`,
`docs/phase-14-report-arena.md`.

## 4. Тесты и измерения (локально, Linux-контур)

| Команда | Результат |
| --- | --- |
| `pytest -m "not integration" -q` | **674 passed**, 105 deselected (было 595 на baseline) |
| `ruff check .` / `ruff format --check .` | All checks passed / 120 files formatted |
| `mypy app tests` | Success: no issues found in 106 source files |
| `pytest tests/test_readiness_api.py` | 18 passed |
| `pytest tests/test_release_policy.py test_release_authenticode.py test_trust_store.py` | 61 passed |
| `pytest tests/test_release_pipeline.py test_channel_network.py test_staging_recovery.py test_updates_api.py` | 51 passed |
| `npm ci`, `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`, `npm audit --audit-level=high` | lint/typecheck/build OK; **160 tests passed**; **0 vulnerabilities** |
| `python infra/windows/tests/lint-engine.py` | структурная проверка пройдена (16 файлов) |
| `python infra/scripts/pilot_drill.py --out-dir drill` | **aggregator**: signature-policy 61 passed, channel-tamper-refusal 51 passed, readiness-api 18 passed; windows-engine и backup/restore — `skipped` (в контейнере нет PowerShell и PostgreSQL) → вердикт `incomplete`, exit 1 (не E2E, manual приёмка — отдельно) |
| `git diff --check` | чисто (проверено перед коммитом) |

**Локальные ограничения контура (честно):** нет Docker → `docker compose config -q`,
живой Compose smoke и PostgreSQL-интеграция выполнены не были; нет PowerShell/
Inno Setup/dotnet → `infra/windows/tests/run-tests.ps1`, `installer/build.ps1`,
`installer/sign.ps1` и drill-шаг `windows-engine` локально не запускались; локальный
Python 3.11.2 вместо CI 3.12 (CI остаётся контрактом). Эти шаги запускаются в CI на
точном SHA: jobs `backend`, `integration`, `stack`, `windows-installer`,
`channel-release-policy`.

**CI на exact final SHA `d542390` (run
[34595851080](https://github.com/sledovatel61/HR-Manager/actions/runs/34595851080)) и
предыдущий зелёный `7beb84e` (run
[34595185553](https://github.com/sledovatel61/HR-Manager/actions/runs/34595185553)):**
на обоих SHA все *существующие* 5 jobs зелёные — Backend checks (Python 3.12:
ruff/mypy/pytest + `check_env.sh`), Backend integration tests PostgreSQL,
Frontend checks (lint/typecheck/160 tests/build/audit), Compose stack smoke
dev+prod, Windows engine tests + installer smoke (PowerShell-тесты движка,
включая новые host-report тесты, сборка Setup.exe).
**Важно:** это 5 jobs *старых* `.github/workflows/*` (ci.yml, update-channel.yml
до переноса). Файлы `review-artifacts/*.phase14.yml` GitHub Actions **не
исполняет** — они review-artifacts для ручного переноса владельцем (см. §5).
Поэтому текущий зелёный CI **не** является полным Phase 14 CI; полный набор
(включая `channel-release-policy` и drill-шаги) появится только после переноса
workflow владельцем и нового прогона на exact final SHA.

**Ручная Windows-приёмка:** не выполнялась (нет Windows-машины) — это явный
owner-action, статус `passed` ей не присваивался. Чек-лист и процедура — в
`docs/runbook-pilot-release.md` (раздел 10).

## 5. CI на точном SHA (exact final SHA — что реально исполнялось)

**Результаты CI на exact final SHA `d542390` (run
[34595851080](https://github.com/sledovatel61/HR-Manager/actions/runs/34595851080)) и
`7beb84e` (run [34595185553](https://github.com/sledovatel61/HR-Manager/actions/runs/34595185553)):**
все job'ы зелёные — `Backend checks`, `Backend integration tests (PostgreSQL)`,
`Frontend checks`, `Compose stack smoke test (dev + prod overlay)`,
`Windows engine tests + installer smoke`. Тем самым подтверждены на Python 3.12
и реальном PostgreSQL/Compose те проверки, которые локально выполнить было
нельзя.

**Явное ограничение:** это 5 jobs **старых** workflow (без Phase 14
изменений). GitHub Actions **не** исполняет `review-artifacts/*.phase14.yml` —
они лишь review-artifacts для ручного переноса владельцем. Полный Phase 14
набор (включая `channel-release-policy`, drill-шаги, ephemeral Authenticode
проверки) **не** был исполнен в CI; он появится только после переноса
workflow владельцем и нового прогона на exact final SHA `d542390` (или новее).
До переноса CI подтверждает только код политики (backend-тесты) и локальный
drill, но не production-политику релиза.

**Важное ограничение платформы:** GitHub App сессии не имеет разрешения
`workflows`, поэтому изменения `.github/workflows/` в ветку не пушатся
(push отклоняется GitHub). Проверенные версии workflow доставлены как
review-artifacts: `review-artifacts/ci.phase14.yml(.patch)` и
`review-artifacts/update-channel.phase14.yml(.patch)` (+ SHA256 и инструкция
переноса в `review-artifacts/README.md`). До переноса владельцем CI на SHA
работает со старым набором jobs: drill-шаги и production-политика релиза в CI
ещё не запускаются, при этом сам код политики покрыт backend-тестами и
локальным drill. Это же ограничение действовало для Phase 13.

* Запланированный (после переноса владельцем) workflow `CI`
  (`.github/workflows/ci.yml`): backend (ruff/mypy/pytest +
  drill-отчёт), integration (PostgreSQL + drill backup/restore), frontend,
  stack (Compose dev/prod/proxy), windows-installer (движок + installer +
  ephemeral Authenticode + drill), channel-release-policy (release-политика на
  реальном signtool-выходе, tamper → `digest_mismatch`, тестовый сертификат →
  `test_certificate_in_production`, подделанная attestation → `missing_timestamp`,
  отсутствие секретов в артефактах).
* Workflow `Update channel release` запускается только по защищённому тегу
  `v*`/dispatch владельца и требует environment `update-channel-signing`.

Ссылки на прогоны добавляются владельцу после публикации PR (см. PR checks).

## 6. Ограничения и что осталось владельцу

1. **Ручная Windows-приёмка** (чистая установка, loopback, обновление/откат на
   реальной машине, uninstall/purge, заполнение go/no-go) — не выполнена.
2. **Production Authenticode**: настоящий сертификат и environment владельца
   агенту недоступны; проверен полный fail-closed контракт на ephemeral
   сертификате. Реальная production-подпись не объявляется выполненной.
3. **Live Compose/PostgreSQL** и `docker compose config -q` локально не
   запускались (нет Docker) — подтверждение ожидается из CI на final SHA.
4. **Перенос workflow-файлов — owner action (обязательно после `d542390`):**
   GitHub App сессии не может пушить `.github/workflows/`; полные файлы и патчи
   лежат в `review-artifacts/` (`ci.phase14.*`, `update-channel.phase14.*`,
   SHA256 в README). После переноса владельцем **обязательно получить новый
   exact-final-SHA CI** (все Phase 14 jobs на `d542390` или новее) — только он
   считается полным Phase 14 CI; текущий зелёный CI на 5 jobs — не полный.
5. **`installer/sign.ps1`** не исполнялся локально (нет PowerShell): синтаксис
   и структура проверены `lint-engine.py`, поведение — в CI-джобе
   `windows-installer` и `channel-release-policy`.
6. **Trust store в релизном пакете** доставляется через `build.ps1`; на пилоте
   применяется `hr-manager.ps1 -Action channel-config`.
7. Ветка сессии — `arena/01a08ff0-hr-manager` (платформенное ограничение
   сессии); merge и, при необходимости, переименование PR делает владелец.

## 7. Handoff

* Следующая сессия/владелец могут продолжить с ветки `arena/01a08ff0-hr-manager`:
  дерево чистое, изменения закоммичены, ветка опубликована, PR открыт в `main`.
* Полезные точки входа: `docs/runbook-pilot-release.md` (операции и go/no-go),
  `infra/release/README.md` (подписи/политика), `infra/scripts/pilot_drill.py`
  (drill + JSON/Markdown), `backend/tests/test_readiness_api.py` (контракт
  readiness), `infra/windows/tests/channel.tests.ps1` (хост-отчёт).
* Не закрывать PR #23 и его ветку/handoff: они остаются аудируемым контекстом
  Phase 13.

---

**Не выполнять merge, auto-merge, закрытие PR/ветки или завершение coding-сессии без разрешения владельца.**
