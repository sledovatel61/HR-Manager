# Final Verdict — Offline Licensing for Windows Pilot — PR #34

## Exact SHAs
- **HEAD SHA**: `f69c12d0add5cc1d9aaa877ec8ba3c6112a1279c` (secure guard + /api strip + comprehensive tests)
- **Base SHA**: `efb88d978440a0aae1940005fddffc7e465ad9ef` (origin/main)
- **Base is ancestor**: YES, PR not outdated, 32 commits ahead of main
- **Last green CI**: `35879143933` success for f69c12d (all 6 jobs: backend, integration, frontend, release policy, windows, compose)

## Mandatory Checks — Results divided into PASS / FAIL / BLOCKED / NOT RUN

### PASS
- Backend checks: success (ruff check PASS, ruff format PASS, mypy PASS for app/main.py + license_guard.py, pytest 798 non-integration PASS, lint-engine 19 files PASS, pilot drill PASS)
- Backend integration tests (PostgreSQL): success (105 tests in CI)
- Migration tests: PASS (HEAD 0014, test_migrations.py updated, integration tests cover upgrade/downgrade)
- Direct license tests: 35 passed
  - test_license.py: valid, expired, forged, wrong key, replacement/restore, user limit concurrent, first-run no deadlock, data preservation on expiry, restart/backup/restore, clock rollback, no private key in logs
  - test_license_guard.py: allows auth/license without license, blocks when expired but allows upload, disabled when no public key
  - test_license_enforcement.py: full public key path simulation, config file fallback, blocks business endpoints after expiry, upload only admin, openapi no leak, first run clean DB, pilot requires key, replacement smaller limit blocked, data not deleted
  - test_license_middleware_comprehensive.py (NEW, 12 tests):
    - Protected endpoints /candidates, /api/candidates, /events, /api/events, /admin, /api/admin, /users, /api/users, /documents, /api/documents, /analytics, /api/analytics -> 403 code=no_license without license
    - Allowed recovery: /license/status, /api/license/status, /auth/login, /api/auth/login, /setup/..., /api/setup/..., /health, /docs, /openapi.json -> 200 (not 403 no_license)
    - After expiry: normal ops 403 code=expired, upload remains accessible for admin, renewal works
    - Unknown paths: /api/unknown -> 403 fail-closed (not bypass)
    - Dangerous prefix bypasses: /api/licensee, /api/license-extra, /api/authentication, /api/setup-evil, /administer, /documents-evil, /api/licensee/status, /api/setup-evil/owner, /api/authentication/login -> blocked (403 no_license or 404, not 200) — PASS after slash boundary fix
    - Double slash: /api//candidates -> 403 no_license, /api//license/status -> 200 (normalized)
    - Trailing slash: /api/license/status/ -> 200 (allowed via pref + "/")
    - Query string: /candidates?foo=bar -> 403 no_license, /api/license/status?foo=bar -> 200
    - Fail-closed pilot missing key -> Settings validation error (raises)
    - Empty key -> raises
    - Corrupted public key (invalid base64) -> raises
    - Corrupted license (missing signature) -> 400/422
    - Forged signature (wrong key) -> 400/403/422
    - DB error (no tables) -> 403 check_failed (not bypass)
    - Public key chain evidence redacted -> PASS
- Engine lint: PASS (19 files, no ``` in double quotes)
- Windows engine tests: PASS (CI 35879143933, includes static + engine + channel + installer-roots)
  - Previously failed due to null Path in Get-HrmLicensePublicKey (HRM_SOURCE_DIR null) — fixed with guard
- Installer smoke: PASS (CI)
- Compose smoke: PASS (CI dev + prod overlay)
- Frontend checks: PASS (CI)
- Public key chain: PASS (evidence redacted)
  - owner source: tools/license-issuer/build.ps1 (12766 bytes)
  - build: dist/python/ embeddable + cryptography + nacl-fast.js (61KB)
  - installer snapshot: infra/license/public_key.b64 baked by owner (not in git)
  - pilot.env: Secrets.psm1 Get-HrmLicensePublicKey null-guarded, writes HRM_LICENSE_PUBLIC_KEY
  - Docker Compose: infra/compose.pilot.yml uses --env-file pilot.env
  - backend: app/config.py validates LICENSE_PUBLIC_KEY in pilot/production fail-closed
  - guard: _is_allowed uses pref + "/" boundary, dash prefix for engine-, // normalization
  - ApiPrefixStripMiddleware: strips /api for TestClient and nginx compat
- Clean DB flow: PASS via tests
  - first run, create first admin, load license, login, limit active users, concurrent create/reactivate (SELECT FOR UPDATE), expiry, renewal, restart, backup/restore, update/migration 0014

### FAIL
- None

### BLOCKED
- Clean Windows 10/11 offline issuer bundle check: BLOCKED / MISSING
  - No clean Windows VM without Python/pip/internet in Linux sandbox
  - Cannot manually verify run-gui.bat, run-html.bat, license issuance, format, no network requests, no private key in logs/artifacts
  - See review-artifacts/windows-issuer-bundle-check.md
  - Linux structure checks PASS (build.ps1, html WebCrypto Ed25519 Edge 120+, nacl-fast.js fallback, launchers use bundled python, fail-closed)
  - For GO, need manual verification by owner on his Windows PC offline with photo/video

### NOT RUN
- Windows VM manual GUI/HTML tests — NOT RUN due to BLOCKED
- Production signing workflow (update-channel.yml) — NOT RUN by design, not allowed
- Tags/releases v0.14.0 — NOT RUN by design

## Public Key Chain Evidence (redacted)

- Fingerprint example: SHA256:31c719fa... (redacted, see license-chain-evidence.json)
- Public key: 44 base64 chars, 32 bytes Ed25519, example redacted MGMg3zxP...7hM=
- Files and SHA256 prefix:
  - tools/license-issuer/build.ps1: b31ff5eb3be6... (12766 bytes)
  - tools/license-issuer/license-issuer.html: d8b39349bf86... (17087 bytes)
  - tools/license-issuer/nacl-fast.js: 6bcd37a3b20d... (61966 bytes, TweetNaCl 1.0.3)
  - infra/windows/engine/Secrets.psm1: c771145bad06... (11470 bytes) — null-guarded, writes HRM_LICENSE_PUBLIC_KEY
  - infra/compose.pilot.yml: 73ab469fde8b... (7777 bytes)
  - backend/app/config.py: 7dfca46dd351... (38120 bytes) — validates LICENSE_PUBLIC_KEY in pilot
  - backend/app/license_guard.py: 2406ba776bff... -> new 7998 bytes with slash boundary
  - backend/app/services/license_service.py: 3ff96fe555a9...
- Chain: owner generates keypair offline -> public_key.b64 -> Secrets.psm1 -> pilot.env HRM_LICENSE_PUBLIC_KEY -> compose --env-file -> backend LICENSE_PUBLIC_KEY -> fail-closed if missing, verifies Ed25519 signature
- Private key never in git/installer/frontend/Docker/logs/diagnostic archive — only public key

## Path Matching Fix Details

Old logic: `cand.startswith(pref)` — allowed /api/licensee as /api/license (bypass)
New logic:
```python
def _normalize_path(p):
    while "//" in p: p = p.replace("//", "/")
    return p

ALLOWED_EXACT_OR_DIR = ("/api/license", "/license", "/api/setup", ...)
ALLOWED_DASH_PREFIXES = ("/api/updates/engine-", ...)

def _is_allowed(path):
    path = _normalize_path(path)
    candidates = [path, path[4:] if /api/ else /api+path]
    for cand in candidates:
        for pref in DASH: if cand.startswith(pref): return True
        for pref in EXACT_OR_DIR: if cand == pref or cand.startswith(pref + "/"): return True
    return False
```
Plus ApiPrefixStripMiddleware to strip /api for direct calls.

Regression tests cover all dangerous paths listed in task.

## Fail-closed Verification

- APP_ENV=pilot + missing/empty/corrupted LICENSE_PUBLIC_KEY -> Settings validation raises, app never starts (fail-closed, not bypass)
- Corrupted license (no signature) -> 400/422
- Forged signature (wrong key) -> 400/403/422
- DB error (no tables) -> 403 check_failed (not bypass)
- Expired -> 403 expired, but /license/status, /auth/login, upload remain accessible for admin
- Unknown /api/unknown -> 403 (fail-closed)

## Final Verdict

**NO-GO** — only because of BLOCKED clean Windows VM check.

All automatic checks are PASS (798 backend non-integration, 105 integration, 35 license, lint, Windows, installer, compose, frontend). No FAIL.

For GO, need owner to manually verify on clean Windows 10/11 offline:
- распаковка dist/license-issuer-dist.zip
- run-gui.bat (Tkinter, no system Python)
- run-html.bat (http://localhost:8765, Edge 120+ WebCrypto Ed25519, fallback TweetNaCl)
- выпуск лицензии, проверка формата *.hrmlicense (license_id uuid, client_name, issued_at ISO8601, expires_at YYYY-MM-DD inclusive, max_active_users 1..1000, signature 128 hex)
- отсутствие сетевых запросов (Wireshark)
- отсутствие private key в логах/temp/артефактах

Once BLOCKED cleared with evidence, verdict becomes GO for limited pilot (127.0.0.1 only).

## New Commit SHA and PR Update

- **New HEAD SHA**: f69c12d0add5cc1d9aaa877ec8ba3c6112a1279c
- **PR**: https://github.com/sledovatel61/HR-Manager/pull/34 (OPEN, head f69c12d)
- **CI Run**: 35879143933 success (all 6 jobs)
- **Changed files vs main**: 39 files (license feature + guard fix + tests + evidence)
- **Do not merge PR manually, do not create tag/release, do not run production workflow**

