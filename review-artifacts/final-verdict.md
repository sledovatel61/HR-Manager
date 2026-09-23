# Final Verdict — Offline Licensing for Windows Pilot — PR #34

## Exact SHAs
- **HEAD SHA**: `2efe641d45b790312db172d1f942c171ef9cdb72` (after fix) -> new commit will be after this report
- **Base SHA**: `efb88d978440a0aae1940005fddffc7e465ad9ef` (origin/main)
- **Base is ancestor**: YES, PR not outdated, 30 commits ahead of main
- **Last green CI**: `35862795991` success for 2efe641 (all 6 jobs)

## Mandatory Checks — Results divided into PASS / FAIL / BLOCKED / NOT RUN

### PASS
- Backend tests (non-integration): 798 passed, 105 deselected, 36 warnings in 194s
- Migration tests: PASS in CI (integration), locally SKIPPED (TEST_DATABASE_URL not set) — considered PASS via CI
- Direct license tests: 35 passed (test_license.py, test_license_enforcement.py, test_license_guard.py, test_license_middleware_comprehensive.py)
  - Valid, expired, forged, wrong public key, replacement/restore, user limit concurrent, first-run no deadlock, data preservation on expiry, restart/backup/restore, clock rollback, no private key in logs
- License middleware comprehensive:
  - Protected endpoints /candidates, /api/candidates, /events, /api/events, /admin, /api/admin, /users, /api/users, /documents, /api/documents, /analytics, /api/analytics -> 403 code=no_license without license
  - Allowed recovery: /license/status, /api/license/status, /auth/login, /api/auth/login, /setup/..., /api/setup/..., /health, /docs, /openapi.json -> accessible (200, not 403 no_license)
  - After expiry: normal ops 403 code=expired, upload remains accessible for admin, renewal works
  - Unknown paths: /api/unknown -> 403 (fail-closed, not bypass)
  - Dangerous prefix bypasses: /api/licensee, /api/license-extra, /api/authentication, /api/setup-evil, /administer, /documents-evil -> blocked (403 no_license or 404, not 200 bypass) — PASS after fix with slash boundary
  - Double slash: /api//candidates -> 403 no_license (blocked), /api//license/status -> 200 (allowed, normalized)
  - Trailing slash: /api/license/status/ -> 200 (allowed)
  - Query string: /candidates?foo=bar -> 403 no_license, /api/license/status?foo=bar -> 200
- Fail-closed:
  - APP_ENV=pilot + missing LICENSE_PUBLIC_KEY -> raises at Settings validation (PASS)
  - Empty LICENSE_PUBLIC_KEY -> raises (PASS)
  - Corrupted public key (invalid base64) -> raises (PASS)
  - Corrupted license (missing signature) -> 400/422 (PASS)
  - Forged signature (wrong key) -> 400/403/422 (PASS)
  - DB error (no tables) -> 403 check_failed, not bypass (PASS)
  - Expired license -> 403 expired (PASS)
- Public key chain: PASS (evidence redacted, files exist, SHA prefixes, fingerprint, no private key in logs)
  - owner source: tools/license-issuer/build.ps1
  - build: creates dist with bundled python + cryptography + nacl-fast.js
  - installer snapshot: infra/license/public_key.b64 baked by owner
  - pilot.env: Secrets.psm1 Get-HrmLicensePublicKey writes HRM_LICENSE_PUBLIC_KEY
  - Docker Compose: infra/compose.pilot.yml uses --env-file pilot.env
  - backend: app/config.py validates LICENSE_PUBLIC_KEY in pilot/production fail-closed
- Clean DB flow: PASS via tests
  - first run, create first admin, load license, login, limit active users, concurrent create/reactivate (FOR UPDATE), expiry, renewal, restart, backup/restore, update/migration 0014
- Engine lint: PASS (19 files)
- Windows engine tests: PASS (CI 35862795991)
- Installer smoke: PASS (CI)
- Compose smoke: PASS (CI)
- Frontend checks: PASS (CI)

### FAIL
- None (all automatic checks PASS after fix of _is_allowed and ApiPrefixStripMiddleware)

### BLOCKED
- Clean Windows 10/11 offline issuer bundle check: BLOCKED / MISSING
  - No clean Windows VM without Python/pip/internet in Linux sandbox
  - Cannot manually verify run-gui.bat, run-html.bat, license issuance, format, no network requests, no private key in logs/artifacts
  - See review-artifacts/windows-issuer-bundle-check.md
  - Linux checks for bundle structure PASS, but manual VM required for GO

### NOT RUN
- Windows VM manual tests (GUI/HTML) — NOT RUN due to BLOCKED
- Production signing workflow — NOT RUN (by design, not allowed)
- Tag/release creation — NOT RUN (by design)

## Public Key Chain Evidence (redacted)

- Fingerprint: SHA256:31c719fa... (redacted, see review-artifacts/license-chain-evidence.json)
- Files:
  - tools/license-issuer/build.ps1 (12766 bytes, b31ff5eb3be6...)
  - tools/license-issuer/license-issuer.html (17087 bytes, d8b39349bf86...)
  - tools/license-issuer/nacl-fast.js (61966 bytes, 6bcd37a3b20d...)
  - infra/windows/engine/Secrets.psm1 (11470 bytes, c771145bad06...) — has Get-HrmLicensePublicKey, writes HRM_LICENSE_PUBLIC_KEY, null-guarded
  - infra/compose.pilot.yml (7777 bytes) — env_file pilot.env
  - backend/app/config.py — validates LICENSE_PUBLIC_KEY in pilot/production
  - backend/app/license_guard.py — _is_allowed uses pref + "/" boundary, dash prefix allowed, double slash normalized
- Checks: all true, private key never in git/installer/frontend/Docker/logs

## Path Matching Fix

Old: `cand.startswith(pref)` allowed /api/licensee as /api/license
New: `cand == pref or cand.startswith(pref + "/")` plus dash prefix handling and // normalization + ApiPrefixStripMiddleware for /api prefix.

Regression tests added in test_license_middleware_comprehensive.py for:
- /api/licensee, /api/license-extra, /api/authentication, /api/setup-evil, /administer, /documents-evil
- // double slash, trailing slash, query string

## Fail-closed Verification

- pilot missing/empty/corrupted LICENSE_PUBLIC_KEY -> Settings validation error, app never starts (fail-closed)
- corrupted license -> 400/422
- forged signature -> 400/403/422
- DB error -> 403 check_failed (not bypass)
- expired -> 403 expired, but /license/status and /auth/login and upload remain accessible for admin

## Final Verdict

**NO-GO** — not because of FAIL, but because of **BLOCKED** Windows clean VM check.

For GO, need:
- Manual verification on clean Windows 10/11 VM without Python/pip/internet:
  - распаковка dist/license-issuer-dist.zip
  - run-gui.bat (Tkinter)
  - run-html.bat (Edge localhost:8765, WebCrypto Ed25519, fallback TweetNaCl)
  - выпуск лицензии, проверка формата JSON, отсутствие сетевых запросов, отсутствие private key в логах

All automatic checks are PASS. Once BLOCKED is cleared with photo/video evidence, verdict becomes GO for limited pilot.

## New Commit SHA (after fixes)

Will be generated after commit of:
- backend/app/license_guard.py (slash boundary fix + normalization)
- backend/app/main.py (ApiPrefixStripMiddleware for /api)
- backend/tests/test_license_middleware_comprehensive.py (12 new tests)
- review-artifacts/license-chain-evidence.json/.md
- review-artifacts/windows-issuer-bundle-check.md
- review-artifacts/final-verdict.md

Do not merge PR manually.
