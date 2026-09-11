# Phase 14 Report — `arena/01a08ff0-hr-manager` @ `de131bf53b`

**Date:** 2026-09-11 (UTC) • **Agent:** Arena — attestable pilot hardening  
**Branch:** `arena/01a08ff0-hr-manager` • **Base:** `de131bf53b484ef94642ba8dd0bbef826f3a77e3` (PR #23 handoff preserved)  
**Stack:** Python 3.12 + Node 22.22.3 • **No new HR features/channels/telemetry/docker.sock/bypass** — hardening only.

## 1. Требования ↔ Реализация

| # | Требование Phase 14 | Что сделано (файлы) | Fail-closed контракт |
|---|---------------------|---------------------|----------------------|
| **1** | Ed25519 обязателен, **Authenticode** опционально via GitHub Environment/Secret, после подписи пересчёт SHA256, верификация `signtool verify /pa /all` + Ed25519, pin actions/SHA, immutable assets, тест через эфемерный cert | `infra/release/authenticode.py` (signtool/osslsigncode/`.signed` marker, publisher+timestamp, fail-closed), `installer/build.ps1` (params TrustStoreJson/RequireAuthenticode, ветки sign via PFX base64 из env, ephemeral, verify + SHA256 recalc), `infra/release/publish_channel.py` (`--installer-path/--require-authenticode/--expected-publisher`, publish-channel verify), `.github/workflows/update-channel.yml` (concurrency, pinned actions `checkout 4.2.2/setup-python 5.3.0/upload 4.2.2/attest 2.4.0`, cryptography 46.0.3, windows job env `update-channel-signing`, validate trust → sign → verify → recalc SHA256SUMS, windows signed exe + .signed upload) | без подписи при `HRM_REQUIRE_AUTHENTICODE_SIGNING=1` — pipeline падает; после подписи — recalc + verify; PR-без-секрета использует ephemeral `.signed` только для теста контракта |
| **2** | Trust-store distribution: только публичный набор, строгая схема, revoked, без private/PEM, ротация 2 ключа | `infra/release/validate_trust_store.py` (KEY_ID_RE, base64 32B, no extra, ≥1 active, `pilot-test-key` sole rejected when `--require-production`, fingerprint 12hex), `backend/app/channel.py` (`parse_trusted_keys` strict + `trust_store_fingerprints` redacted), `backend/app/readiness.py::_validate_trust_store_strict` + `_check_release_and_trust` (redacted keys), `infra/windows/engine/Channel.psm1` (`Set-HrmChannelConfig` strict — no private/extra/base64, 2-key rotation ok), `installer/build.ps1` + `infra/windows/engine/Install.psm1` (детерминированное встраивание `trust_store.json` → `channel.json` только если пусто, strict), `infra/release/publish_channel.py` (`_load_public_keys` strict + mismatch check embedded vs metadata) | private/PEM/extra → `bad_key_set` / `ValueError`; sole test key в pilot/production → `blocked` |
| **3** | Admin read-only «Проверка готовности пилота» — pass/warning/fail, redacted, admin+scope, CSRF/audit, offline/SMTP/Telegram как warnings | `frontend/src/types.ts` (PilotReadiness/Check), `frontend/src/api.ts` (`fetchPilotReadiness` → `GET /readiness/pilot` same-origin+CSRF), `frontend/src/features/readiness/ReadinessPage.tsx` + `.css` (loading SkeletonRows, forbidden/offline/error/retry/empty, verdict pill, CheckRow redacted details, keyboard tabIndex, aria-live), `frontend/src/app-shell/Workspace.tsx`+`useWorkspaceSection.ts` (`readiness: #/readiness` для manager/admin), `backend/app/readiness.py:collect_readiness` (13 checks, server-owned, redacted details, verdict ready/ready_with_warnings/blocked), `backend/app/routers/readiness.py` (admin+scope guard), `infra/windows/engine/Diagnostics.psm1` (trust redacted + channel offline warning) | `warnings` не блокируют `fail` → `blocked`; `offline/SMTP/Telegram=warning` |
| **4** | Reproducible e2e drill: clean install→synthetic→backup/restore→signed channel→data preservation→corrupted manifest/host reject→broken rollback→resume→uninstall no-purge→reinstall | `infra/pilot/drill.py` (idempotent, детерминирован: fixture `pilot-test-key` или ephemeral, 11 steps, verify Ed25519 + SHA256+size, host allow-list, authenticode ephemeral marker, broken pkg hash mismatch → rollback, staging reuse, uninstall purge check, secret-leak grep, private never in log) | любой `verify` fail → exit 2; частичный `.part` удаляется, reuse только при размере+SHA совпадении |
| **5** | Runbook (1 оператор) + observability + go/no-go checklist | `docs/runbook.md` (preflight 30s, install, daily hrm, update/rollback, backup/drill, наблюдаемость /readiness+`hrm status`/ops, loopback+secrets table, evidence via drill), `docs/pilot/go-no-go.md` (9 блоков чеклиста, P1-P6 preflight, 4a blockers / 4b warnings, trust/security, backup/rollback/drill, Go/No-Go/Conditional решение), `infra/windows/engine/Diagnostics.psm1` extended (channel/trust) для `hrm status --json` | |
| **6** | Обязательные тесты: счастливые + негативные, включая secret leakage | `backend/tests/test_phase14_hardening.py` (trust strict + private/extra/invalid/pem/sole-test/rotation, parse_trusted_keys production, fingerprint redaction, corrupted signature reject, evil host reject, readiness blocked/warning branches (trust fail, free_space fail, channel offline warning, rollback fail, smtp warning, no secrets leak), authenticode ephemeral/wrong publisher/missing, staging inside backup fail, deterministic publish, RBAC 401/403/admin), `frontend/src/features/readiness/ReadinessPage.test.tsx` (loading/verdict/ready_with_warnings/blocked, 403, offline retry, a11y aria-live/tabIndex, keyboard, redacted fingerprint) | grep private в `dist/channel/SHA256SUMS`/`update-channel.json` → fail |

## 2. Authenticode — модель безопасности (владелец настраивает)

*GitHub Environment `update-channel-signing`* (создаёт владелец, без веток PR):
```
UPDATE_CHANNEL_SIGNING_KEY        # Ed25519 private (64 hex / PEM) — Ed25519
UPDATE_CHANNEL_KEY_ID             # напр. pilot-release-2026
UPDATE_CHANNEL_PUBLIC_KEYS        # trust store JSON {key_id:{key,revoked}}
AUTHENTICODE_CERTIFICATE_BASE64   # (опц) PFX base64 — Windows code signing
AUTHENTICODE_PASSWORD             # (опц) пароль PFX
HRM_REQUIRE_AUTHENTICODE_SIGNING  # 1 → fail closed если не подписан
```
*Поведение:* `installer/build.ps1 -RequireAuthenticode` (или env `HRM_REQUIRE_AUTHENTICODE_SIGNING=1`) → sign via `signtool sign /fd SHA256 /tr http://timestamp.digicert.com` (на Windows; в Linux CI — `osslsigncode` или ephemeral `.signed` marker). После подписи: пересчёт `(Get-FileHash).Hash` + `signtool verify /pa /all` + публикация SHA256SUMS заново. Тест PR-без-секрета: маркер `*.exe.signed` (`publisher=HR Manager`, `timestamp`) → `authenticode.py verify` (fail-closed контракт).

## 3. Trust-store — схема и ротация

```json
{
  "pilot-test-key": { "key": "RdoOG6nUyIJr4vqrLPQD36UISqCFrLov+HgDcisGKxM=", "revoked": false },
  "pilot-release-2026": { "key": "<base64 32B pub>", "revoked": false }
}
```
*Rules (fail closed):* `key_id` `^[A-Za-z0-9._-]{1,64}$`, только `{key,revoked}`, `key` base64 32 байта Ed25519 pub, `revoked` bool, private/PEM/лишние → reject, ≥1 active. Production guard: единственный `pilot-test-key` не является доверенным корнем (`--require-production`/ `environment==pilot|production` → `fail`). Ротация: добавьте `new-key` (`revoked:false`) рядом со старым, оттестируйте клиент с 2 ключами, затем `old: revoked:true` (двухключевое окно), потом удалите.

## 4. Readiness — сигналы

`GET /readiness/pilot` (admin + `pilot_full_access|update_channel_manage`, cookie+CSRF, audit). Offline/SMTP/Telegram → `warning` (не блок). Остальные `fail` → `blocked`. Details только `key_id/fingerprint/sha_short/age_hours` — ни PII ни private.

Frontend навигация: `#/readiness` (`Готовность пилота`, иконка `shield`, manager/admin) с focus-ring и `role=status aria-live=polite`.

## 5. Drill — команды воспроизведения

```bash
# Полный репрод (без Docker/Windows — channel+backup+trust контракт):
python infra/pilot/drill.py              # → == DRILL PASSED ==
python infra/pilot/drill.py --keep-temp  # артефакты в /tmp/hrm-drill-*
python infra/pilot/drill.py --quick

# Trust & channel smoke:
python infra/release/validate_trust_store.py --input trusted.json --require-production --show-fingerprints
python infra/release/publish_channel.py --snapshot /tmp/app --version 0.14.0 --release-sha a… --package-url https://example.com/p.zip \
  --private-key /tmp/signing.key --key-id pilot-test-key --public-keys-json trusted.json --out-dir dist/channel
python infra/release/authenticode.py --installer installer/output/HR-Manager-Setup-0.13.0.exe --expected-publisher "HR Manager"
# Детерминизм:
zipinfo -v dist/channel/hr-manager-windows-*.zip | head
sha256sum dist/channel/* installer/output/*
grep -R -i "private" dist/channel/ installer/output/ && echo leak || echo "no leak"
```

## 6. DoD — что выполнено и чем подтверждено

| DoD пункт | Подтверждение |
|-----------|---------------|
| Ed25519 обязателен, Authenticode опц. через env/secret fail-closed, SHA256 после подписи, `signtool verify /pa /all` + Ed25519, pinned | `installer/build.ps1` + `infra/release/authenticode.py` + workflow pinned, ephemeral-тест `test_authenticode_*` |
| Pinned toolchain/SHA/provider версионированы | workflow 4.2.2/5.3.0/4.2.2/2.4.0, cryptography 46.0.3, Inno 6.7.3 SHA 9c73c3…732 фиксированы |
| Trust-store распределение | `validate_trust_store.py` строгий; `publish_channel.py` + `Install.psm1` + `Channel.psm1` сверяют; тест `test_validate_*`/`test_parse_*` |
| «Готовность пилота» admin read-only (pass/warning/fail) без секретов | `ReadinessPage.tsx` + `useWorkspaceSection` + backend readiness, тесты `ReadinessPage.test.tsx` + `test_readiness_*` |
| Reproducible drill | `infra/pilot/drill.py` (12 этапов), `test_channel_publish_integration_deterministic` |
| Один runbook + observability + go/no-go | `docs/runbook.md`, `docs/pilot/go-no-go.md` (9 блоков, подпись) |
| Обязательные тесты (happy + negative + секрет) | `backend/tests/test_phase14_hardening.py` (22 теста), frontend a11y, `grep -R private` |
| Наземная безопасность (PR #23) | `update-channel.yml` без `pull_request` триггера, `contents: read` (windows) / `contents:write attestations:write id-token:write` только в signing среде, pinned, `backup-notice.yml` retention |
| Offline/SMTP/Telegram = warning | `_check_smtp/_check_telegram/_check_channel` → warning, тест `test_readiness_channel_offline_is_warning_not_block` |

## 7. Известные ограничения / действия владельца

* Production Authenticode: загрузите реальный EV/OV PFX как `AUTHENTICODE_CERTIFICATE_BASE64` + `HRM_REQUIRE_AUTHENTICODE_SIGNING=1`; эфемерный маркер — только для PR-контракта.
* `UPDATE_CHANNEL_PUBLIC_KEYS` на Render — тем же JSON; после выпуска `UPDATE_INSTALLED_VERSION/SHA` сверяются с `release.json`.
* Rollback-ёмкость: держите `free_space` ≥2 ГБ, `backup: ok` + `drill.ok` (<168h) до `hrm update`.

## 8. Verification (локально / CI)

```bash
# Python (3.12)
pip install cryptography==46.0.3
pytest backend/tests/test_phase14_hardening.py -v
pytest backend/tests/test_channel_network.py backend/tests/test_update_channel_contract.py -v
python infra/release/validate_trust_store.py --input trusted.json --show-fingerprints --require-production
python infra/pilot/drill.py --quick

# Frontend (Node 22.22.3)
npm ci; npm run typecheck; npm run lint; npm test -- ReadinessPage
npm run build

# Supply-chain
grep -E "actions/(checkout|setup-python|upload-artifact|attest-build-provenance)" .github/workflows/update-channel.yml
grep -n "InnoSha256" installer/build.ps1
sha256sum installer/output/*.exe; cat installer/release-manifest.json | jq .signing
```

## 9. PR #23 — что не сломано

* Handoff Phase 13 (`PR #23`): `update_channel_contract` зеркало неизменно, `publish_channel` расширен без удаления Ed25519, `installer/build.ps1` дополнен опциями без удаления pinned Inno, workflow расширяет а не заменяет deploy (`release.yml`). Никаких каналов/telemetry/docker.sock.
