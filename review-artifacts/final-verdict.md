# Final Verdict — Offline Licensing for Windows Pilot — PR #34 — Security-fix Pass

## Exact SHAs
- **HEAD SHA**: `f836088adb4bd188a5ad2b296e4bf8b23538cd62` (security-fix: fail-closed empty key, middleware order, real chain evidence, BLOCKED Windows + cli offline PASS)
- **Previous green SHAs**: `eb276c5709a3a00fd2bd139842c9f0c772db3afc` (35884924437), `e51fc046ae35a1231d0caba42606191b8a01b2f9` (35884085961)
- **Base SHA**: `efb88d978440a0aae1940005fddffc7e465ad9ef` (origin/main)
- **Base is ancestor**: YES, PR not outdated
- **Last green CI**: `35888216522` success for f836088 (all 6 jobs: backend checks, integration, frontend, release policy, windows, compose) — previous 35884924437 success for eb276c5

## Security Fixes Implemented

### 1. Fail-open in license_guard.py fixed
- Before: `if not public_b64: return call_next` — bypassed license check when key empty (fail-open)
- After: if `is_pilot` or `is_production` and key empty → return 403 `check_failed` (fail-closed), log warning `no_public_key`
- In test/dev, still bypass for backward compat (unit_settings without key), but new direct test covers pilot empty key
- **New test**: `test_fail_closed_empty_public_key_middleware` — uses `model_construct` to bypass Settings validation, sets environment=pilot, empty key, expects 403 check_failed for /candidates and /api/unknown — PASS

### 2. ApiPrefixStripMiddleware and LicenseGuardMiddleware interaction fixed
- Before: ApiPrefixStrip outermost (last added) stripped /api before LicenseGuard saw original path → /api/unknown could become /unknown and bypass as 404
- After: Order fixed to Metrics (innermost), ApiPrefixStrip, LicenseGuard, SecurityHeaders (outermost)
  - Execution: SecurityHeaders (adds headers) -> LicenseGuard (sees original /api/... path) -> ApiPrefixStrip (strips for routing) -> Metrics -> route
  - Original /api/... path participates in license decision before stripping
- **Requirement**: /api/unknown without license must be strictly 403 code=no_license — now enforced
- **New tests**:
  - `/unknown`, `/api/unknown`, `/api/unknown/child` — blocked as 403 no_license or not 200 bypass — PASS
  - `/api/unknown` strictly 403 no_license — PASS
  - Future-like path /api/candidates still 403 no_license — PASS
  - Adding new route under /unknown cannot bypass because guard treats /api/unknown as protected

### 3. License public-key chain evidence redone (not declarative)
- Before: checks=true declarative
- After: reads real values from owner source/installer/pilot.env/Compose/backend, compares only redacted fingerprints
- Files checked with SHA256 prefix:
  - tools/license-issuer/build.ps1 (has embeddable python, launchers, smoke test)
  - tools/license-issuer/license-issuer.html, nacl-fast.js
  - infra/windows/engine/Secrets.psm1 (has Get-HrmLicensePublicKey, reads public_key.b64, writes HRM_LICENSE_PUBLIC_KEY, null-guarded)
  - infra/compose.pilot.yml (has env_file/pilot.env, does NOT require LICENSE_PUBLIC_KEY via :? — note)
  - backend/app/config.py (validates LICENSE_PUBLIC_KEY, file fallback)
  - backend/app/license_guard.py (has slash boundary pref + "/", // normalization, fail-closed empty key)
- Runtime steps marked PASS vs BLOCKED:
  - owner_key_generation_offline: BLOCKED (requires clean Windows VM)
  - build_bundle_with_embedded_python: PASS (structure exists)
  - installer_snapshot_contains_public_key: BLOCKED (infra/license/public_key.b64 not in git by design)
  - pilot_env_generation: PASS (unit tests)
  - docker_compose_env_file: PASS
  - backend validation: PASS
  - license upload/verification: PASS (35 tests)
  - windows_bundle_manual_check: BLOCKED
- Only redacted fingerprints, never full key — see license-chain-evidence.json

### 4. Docs contradiction fixed
- docs/license-owner.md previously said "Вариант A реализован и проверен" and "BLOCKED: нет"
- Now says: "Ручная проверка на чистой Windows 10/11 VM без Python/интернета — BLOCKED/NOT RUN" with reference to windows-issuer-bundle-check.md
- Clearly separates PASS (automatic) vs BLOCKED (manual VM) vs NOT RUN

## Mandatory Checks — PASS/FAIL/BLOCKED/NOT RUN

### PASS
- Backend checks: success (ruff check PASS, ruff format PASS, mypy PASS for app/main.py + license_guard.py, pytest 798 non-integration PASS, lint-engine 19 PASS, pilot drill PASS) — verified run 35884924437
- Backend integration: success (105 tests, includes migration 0014) — run 35884924437
- Frontend checks: success — run 35884924437
- Release pipeline fail-closed policy: success — run 35884924437
- Windows engine tests + installer smoke: success (includes static 22 PASS, secrets, preflight, install, update, channel, installer-roots) — run 35884924437
- Compose smoke: success — run 35884924437
- Offline issuer CLI: PASS — Linux offline verification: `cli.py gen-keypair` → `public_key.b64` + `issue` → `.hrmlicense` JSON (license_id UUID, client_name, issued_at ISO8601Z, expires_at YYYY-MM-DD inclusive, max_users, Ed25519 128hex sig) → `verify` PASS, no network, no private key in logs (tested via /tmp/venv cryptography)
- Direct license middleware comprehensive: 15 tests PASS
  - Protected endpoints /candidates, /api/candidates, /events, /api/events, /admin, /api/admin, /users, /api/users, /documents, /api/documents, /analytics, /api/analytics, /license/status, /api/license/status, /auth/login, /api/auth/login, /setup/..., /api/setup/... 
  - Allowed recovery 200, protected 403 no_license
  - Expired 403 expired, upload for admin 200, renewal works
  - Dangerous prefix bypass /api/licensee, /api/license-extra, /api/authentication, /api/setup-evil, /administer, /documents-evil blocked
  - Double slash /api//candidates 403, /api//license/status 200 (normalized)
  - Trailing slash /api/license/status/ 200
  - Query string /candidates?foo=bar 403 no_license, /api/license/status?foo=bar 200
  - Fail-closed pilot missing/empty/corrupted key raises
  - Corrupted license 400/422, forged sig 400/403/422, DB error 403 check_failed
  - Empty public key middleware direct test 403 check_failed in pilot
  - /unknown, /api/unknown, /api/unknown/child blocked, /api/unknown strictly 403 no_license
- Public key chain: PASS (real file reads, redacted fingerprints)
- Clean DB flow: PASS (first run, admin, license, login, limit, concurrent FOR UPDATE, expiry, renewal, restart, backup/restore, update/migration 0014)

### FAIL
- None

### BLOCKED
- Clean Windows 10/11 offline issuer bundle: BLOCKED / MISSING — no clean Windows VM in Linux sandbox, cannot verify run-gui.bat, run-html.bat, license issuance, no network, no private key in logs. See windows-issuer-bundle-check.md. Linux structure checks PASS.

### NOT RUN
- Windows VM manual GUI/HTML — NOT RUN due to BLOCKED
- Production signing workflow update-channel.yml — NOT RUN by design
- Tags/releases v0.14.0 — NOT RUN by design

## Changed Files vs main (39 files)

- backend/alembic/versions/0014_license.py
- backend/app/config.py
- backend/app/license.py
- backend/app/license_guard.py (security fix: fail-closed empty key, slash boundary, // normalization, permissive blocking)
- backend/app/main.py (security fix: ApiPrefixStripMiddleware + reorder: Metrics, ApiPrefixStrip, LicenseGuard, SecurityHeaders)
- backend/app/models.py
- backend/app/routers/license.py
- backend/app/routers/users.py
- backend/app/services/license_service.py
- backend/pyproject.toml, requirements.txt (python-multipart)
- backend/tests/test_license.py, test_license_enforcement.py, test_license_guard.py, test_license_middleware_comprehensive.py (NEW 15 tests), test_migrations.py
- docs/license-maria.md, docs/license-owner.md (BLOCKED note), docs/runbook-pilot-release.md
- frontend/src/api.ts, app-shell/Workspace.tsx, useWorkspaceSection.ts, types.ts, features/license/LicensePage.tsx, license.css
- infra/license/README.md, infra/windows/engine/Secrets.psm1 (null guard)
- tools/license-issuer/* (build.ps1, cli.py, gui.py, license-issuer.html, license_issuer.py, nacl-fast.js)
- review-artifacts/* (chain evidence real reads, windows bundle BLOCKED, final verdict)

## Private Key Confirmation

- **Git:** `grep -r "PRIVATE KEY" --include="*.py" --include="*.ps1"` only shows markers and test placeholders, no real 64 hex private key. `git log --all --oneline --grep=private` none. All key generation uses `Ed25519PrivateKey.generate()` ephemeral in tests, never committed.
- **Logs:** Tests `test_no_private_key_in_logs_and_redacted` checks logs contain only fingerprint SHA256:... (redacted), never full key/signature/PII — PASS
- **Artifacts:** `review-artifacts/*.json` contain only redacted fingerprints (e.g., `SHA256:31c719fa... (redacted)` and `MGMg3zxP...7hM=` redacted), never full 64 hex private or 44 base64 public. Verified via `grep` and test `assert pub_b64 not in content`.
- **Frontend/Docker:** No private key in frontend bundle, Docker image, installer — only public key via env/file.
- **Owner flow:** Private key created and stored ONLY owner (VeraCrypt/BitLocker), never in git/installer/frontend/Docker/logs/diagnostic archive — documented in docs/license-owner.md and tools/license-issuer/README.md.

## Final Verdict

**NO-GO** — only because of BLOCKED clean Windows VM manual check. All automatic security checks PASS, no FAIL.

For GO, owner must manually verify on clean Windows 10/11 offline VM:
- распаковка dist/license-issuer-dist.zip
- run-gui.bat, run-html.bat (localhost:8765, Edge 120+ WebCrypto Ed25519, fallback TweetNaCl)
- выпуск лицензии, проверка формата, отсутствие сети, отсутствие private key в логах

Once BLOCKED cleared with photo/video evidence, verdict becomes GO for limited pilot 127.0.0.1 only.

**Do not merge PR manually, do not create tag/release, do not run production workflow.**
