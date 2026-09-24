# Final Verdict — Offline Licensing for Windows Pilot — PR #34 — Compose fail-closed pass

## Commits

- **Fix commit (code + tests + CI + evidence generator + this file):** `FIX_COMMIT_SHA_PENDING`
  — a file cannot contain the SHA of the commit that introduces it; the value is filled in by the
  evidence-import commit below and repeated in the PR. Until then identify it as
  `git log --diff-filter=A --format=%H -- backend/tests/test_compose_license_chain.py`.
- **Evidence-import commit:** `EVIDENCE_COMMIT_SHA_PENDING` (adds the CI artifact
  `compose-pilot-license-chain.ci.json` + `ci-run-status.json`, regenerates
  `license-chain-evidence.*`, fills the SHAs here). Docs/evidence only — no code delta vs the fix commit.
- **Previous verdict commits (superseded):** `e00ef1d`, `f836088`, `eb276c5` — they claimed
  `docker_compose_env_file: PASS` while `compose.pilot.yml` did not map the key at all. Withdrawn.
- **Base SHA:** `efb88d978440a0aae1940005fddffc7e465ad9ef` (origin/main), ancestor — PR not outdated.
- **CI run for the fix commit:** `CI_RUN_PENDING` — job-level results are recorded in
  `ci-run-status.json` and in the section "CI results" below once imported.

## Defects fixed in this pass (code, not docs)

### 1. `infra/compose.pilot.yml` did not pass the license public key at all — FIXED
- Before: no `LICENSE_PUBLIC_KEY` mapping. The backend image copies only `app/` and `alembic/`, so
  the `infra/license/public_key.b64` file fallback of `Settings` does not exist inside a container.
  Result: `APP_ENV=pilot` backend, **worker** and **backup** (`python -m app.cli backup-*`) would refuse
  to start (`LICENSE_PUBLIC_KEY must be set in pilot`) — the documented chain was broken at the Compose hop.
- After: `LICENSE_PUBLIC_KEY: ${HRM_LICENSE_PUBLIC_KEY:?HRM_LICENSE_PUBLIC_KEY is required for the pilot}`
  in **backend, worker and backup** (the three services that load `app.config.Settings`). Required form
  (`:?`), no default. Unset **or empty** value (the engine writes `HRM_LICENSE_PUBLIC_KEY=` when the
  owner file is missing) makes `docker compose` refuse with that message — fail-closed instead of a
  crash-looping stack.
- **`env_file:` deliberately NOT added.** `pilot.env` is consumed only through `--env-file`
  (interpolation), like every other pilot secret. A service-level `env_file:` would copy the whole
  file into the backend container, including `HRM_BACKUP_KEY` (backup encryption key) — a
  least-privilege regression. This is now a tested invariant
  (`test_pilot_overlay_never_uses_env_file_directive`, Windows `static.tests.ps1`).

### 2. `infra/compose.prod.yml` had the same gap — FIXED
- `Settings` is fail-closed in production too, so production could not start either. Added
  `LICENSE_PUBLIC_KEY: ${LICENSE_PUBLIC_KEY:?...}` to backend/worker/backup, header usage updated,
  `infra/scripts/check_env.sh` now validates `LICENSE_PUBLIC_KEY` (44 chars, base64, 32 bytes; value
  never echoed). CI prod-overlay validation exports an ephemeral value and asserts that `config`
  **fails without it**.

### 3. License guard heuristic could be bypassed — FIXED (deny by default)
- Before: `LicenseGuardMiddleware` protected only paths under a hard-coded `protected_roots` list or
  with an `/api/` prefix; everything else passed through. Behind nginx the prefix is already stripped,
  so e.g. `/ops/metrics` or any future router under a new root (`/reports`) was served **without a
  license**. `test_unknown_paths_blocked_without_license` was correspondingly soft ("not 200") and the
  previous verdict overstated it as "strict 403".
- After: everything not on the recovery allowlist requires a valid license — known route or not,
  with or without `/api`. `/ops/metrics` (aggregate counters, no PII) added to the allowlist as
  diagnostics. Tests are now strict: `/unknown`, `/api/unknown`, `/api/unknown/child`, `/reports`,
  `/api/reports` × GET/POST/PUT/PATCH/DELETE → **403 `no_license`** with `X-License-Status`;
  a real route mounted under an unknown root is blocked; look-alike prefixes strictly 403;
  `/api//candidates` strictly 403; expired license blocks unknown paths with 403 `expired`.

### 4. Chain evidence was still partly declarative — FIXED
- `review-artifacts/license-public-key-chain.json` (declarative `compose_passes_env_file: true`) removed.
- `review-artifacts/gen_license_chain_evidence.py` (committed) recomputes every check from the real
  files and takes Compose/runtime statuses **only** from an imported CI artifact; without it they are
  `NOT RUN`. Redacted fingerprints only.

## New checks

| Layer | What | Where |
|---|---|---|
| pytest (static) | mapping verbatim in backend/worker/backup, required form, no default, no `env_file:`, no private-key name; prod overlay same; `check_env.sh` rejects missing/malformed key | `test_pilot_overlay.py`, `test_production_overlay.py` |
| pytest (runtime, no Docker) | overlay `environment:` interpolated with Compose `${VAR:?}`/`${VAR:-}` semantics from an ephemeral pilot.env → `app.config.Settings` in `APP_ENV=pilot` for backend/worker/backup → same SHA-256 fingerprint; without key Settings refuses; env-file line set == `Secrets.psm1:Write-HrmPilotEnv` (names+order); BOM/CRLF handling; report leak guard | `test_compose_license_chain.py` (17 tests) |
| CI `stack` job (real `docker compose`) | `public_key.b64` → pilot.env (UTF-8/LF **and** UTF-8-BOM/CRLF as Windows PowerShell 5.1 writes it) → `docker compose --env-file … config --format json` → resolved `LICENSE_PUBLIC_KEY` of backend/worker/backup == file fingerprint; backend env has no backup key; no `env_file`; **negative:** missing key and empty key → `config` exits non-zero with the overlay message; then `docker compose run` of the built pilot images: `get_settings()` in `APP_ENV=pilot` prints the fingerprint → equal | `infra/scripts/compose_pilot_license_chain.py --require-runtime`, artifact `compose-pilot-license-chain` (fingerprints only) |
| CI Windows job (real engine writer) | `Write-HrmPilotEnv` with `<state>\license_public_key.b64` (BOM+CRLF) → `HRM_LICENSE_PUBLIC_KEY` last line, SHA-256 equal to the file, 44 chars; without the file → empty value (so Compose refuses) | `engine.tests.ps1` |
| CI Windows job (static) | overlay mapping ×3, no default, no `env_file:`, no private key name | `static.tests.ps1` |
| CI backend job | production preflight: fails without / with malformed `LICENSE_PUBLIC_KEY`, passes with a 44-char value | `ci.yml` |

## Mandatory checks — PASS / FAIL / BLOCKED / NOT RUN

### PASS (local, this commit; CI confirmation pending)
- `ruff check`, `ruff format --check`, `mypy app tests` — clean.
- `pytest -m "not integration"`: **825 passed** (798 before + 27 new).
- `python infra/scripts/pilot_drill.py --steps signature-policy,channel-tamper-refusal,readiness-api` — passed.
- `python infra/windows/tests/lint-engine.py` — 19 files, structural check passed.
- License guard suites (40 tests) with deny-by-default — passed, including the strict 403 cases above.
- Offline issuer CLI (manual, Linux sandbox, not CI): `cli.py gen-keypair` → `issue` → `verify` OK, no network.

### PENDING CI (must NOT be read as PASS until `ci-run-status.json` is imported)
- Real `docker compose` chain (with key / without key / empty key / resolved env / Settings in images) — `stack` job.
- Real `Write-HrmPilotEnv` fingerprint case — Windows job.
- Backend integration (PostgreSQL, migration 0014), frontend, release-policy, compose smoke — re-run on the fix commit.

### FAIL
- none known.

### BLOCKED
- Clean Windows 10/11 offline issuer bundle (run-gui.bat / run-html.bat, issuance, no network, no
  private key in logs) — needs the owner's VM; see `windows-issuer-bundle-check.md`.
- `installer_snapshot_contains_public_key` — `infra/license/public_key.b64` is not in git by design.

### NOT RUN (by design / constraints)
- `update-channel.yml`, `workflow_dispatch`, tags/releases `v0.14.0`, production signing — not run.

## Private key confirmation
- Git: no private key material; every test key is `Ed25519PrivateKey.generate()` / `secrets.token_bytes(32)` per run.
- Overlays name only `LICENSE_PUBLIC_KEY` / `HRM_LICENSE_PUBLIC_KEY`; tests assert no `PRIVATE_KEY`/`private_key` token appears.
- CI artifact and `license-chain-evidence.*`: SHA-256 fingerprints (16 hex) only; the chain script has a
  leak guard that turns the verdict into FAIL and masks the report if 44-char base64 or ≥32-hex
  material ever appears.
- Logs: guard logs redacted fingerprints only (`test_no_private_key_in_logs_and_redacted`).

## Verdict

**NO-GO.** Reasons, in order: (1) the Compose/runtime chain is **PENDING CI** in this commit — the
previous PASS claim is withdrawn and must be re-earned by the imported artifact; (2) the clean-Windows
issuer bundle check remains **BLOCKED**. Do not merge, do not tag/release, do not run production workflows.

## CI results
_Filled by the evidence-import commit from `ci-run-status.json`._
