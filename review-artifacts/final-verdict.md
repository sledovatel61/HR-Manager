# Final Verdict — Offline Licensing for Windows Pilot — PR #34 — Compose fail-closed pass

## Commits and CI runs (this pass)

| Role | Commit | CI run | Result |
|---|---|---|---|
| **Fix commit X** (code + tests + CI + evidence generator) | `96f12bc8926a6fc8dbce447bbd8b81889e595c09` | [35963793099](https://github.com/sledovatel61/HR-Manager/actions/runs/35963793099) | 6/6 jobs success; chain step 12/12 `[pass]`, verdict PASS; artifact 10793765774 |
| CI-only follow-up (prints the redacted chain report to the log/step summary; no backend/infra delta) | `7c7f64529fec28e17abdf9601bb5055762ece8df` | [35964596589](https://github.com/sledovatel61/HR-Manager/actions/runs/35964596589) | 6/6 jobs success; chain step 12/12 `[pass]`, verdict PASS; artifact 10794046262 |
| **Evidence-import commit Y** (this file, `ci-run-status.json`, `compose-pilot-license-chain.ci.json`, regenerated `license-chain-evidence.*`, README) | the commit that adds `review-artifacts/ci-run-status.json` — `git log --diff-filter=A --format=%H -- review-artifacts/ci-run-status.json`; also named in the PR comment | — (docs only; CI re-runs on it, expected unchanged) | — |
| Superseded verdict commits | `e00ef1d`, `f836088`, `eb276c5` | — | withdrawn: claimed `docker_compose_env_file: PASS` while the overlay mapped nothing |

- **Base SHA:** `efb88d978440a0aae1940005fddffc7e465ad9ef` (origin/main), still the merge base — PR not outdated.
- Job-level results of run 35964596589 (identical set in 35963793099): Backend checks ✅ · Backend integration tests (PostgreSQL) ✅ · Frontend checks ✅ · Windows engine tests + installer smoke ✅ · Release pipeline fail-closed policy (ephemeral test signature) ✅ · Compose stack smoke test (dev + prod overlay) ✅ — see `ci-run-status.json` (job ids, artifact ids, zip SHA-256).

## What the CI actually executed for the chain (verbatim from the job logs)

`stack` job, step "Pilot overlay — license public-key chain (real docker compose)", `docker compose 2.38.2`:

```
[pass] ephemeral_public_key            32 random bytes, base64 44 chars (ephemeral, never persisted)
[pass] public_key_file                 infra/license/public_key.b64 written (CRLF) and read back trimmed
[pass] pilot_env_utf8-lf               HRM_LICENSE_PUBLIC_KEY line present as the last line
[pass] pilot_env_utf8bom-crlf          HRM_LICENSE_PUBLIC_KEY line present as the last line
[pass] compose_available               docker compose 2.38.2
[pass] compose_config_with_key_utf8-lf        resolved LICENSE_PUBLIC_KEY of backend/worker/backup equals the public_key.b64 fingerprint; backend gets no backup key; no env_file
[pass] compose_config_with_key_utf8bom-crlf   (same, Windows-style env file)
[pass] compose_config_without_key_missing     docker compose config exit 1: "required variable HRM_LICENSE_PUBLIC_KEY is missing a value: HRM_LICENSE_PUBLIC_KEY is required for the pilot"
[pass] compose_config_without_key_empty       (same for an empty value, as the engine writes when the owner file is absent)
[pass] runtime_settings_backend        app.config.Settings loaded in APP_ENV=pilot inside the built image; fingerprint equals public_key.b64; no backup key, no raw HRM_* vars in the container env
[pass] runtime_settings_worker         (same)
[pass] runtime_settings_backup         (same; backup container legitimately has the backup key)
verdict: PASS
```

Windows job (Windows PowerShell 5.1, real engine modules):

```
[PASS] пилотный оверлей: открытый ключ лицензии обязателен (${HRM_LICENSE_PUBLIC_KEY:?}) для backend, worker и backup
[PASS] pilot.env: HRM_LICENSE_PUBLIC_KEY берётся из license_public_key.b64 (совпадение SHA-256), без файла — пустое значение
```

The fingerprint in the imported report (`sha256:e2f74e6c761c0684…`) belongs to the **ephemeral** key generated inside that CI job — not to any real key.

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

### PASS (local, fix commit 96f12bc — all re-confirmed by CI below)
- `ruff check`, `ruff format --check`, `mypy app tests` — clean.
- `pytest -m "not integration"`: **825 passed** (798 before + 27 new).
- `python infra/scripts/pilot_drill.py --steps signature-policy,channel-tamper-refusal,readiness-api` — passed.
- `python infra/windows/tests/lint-engine.py` — 19 files, structural check passed.
- License guard suites (40 tests) with deny-by-default — passed, including the strict 403 cases above.
- Offline issuer CLI (manual, Linux sandbox, not CI): `cli.py gen-keypair` → `issue` → `verify` OK, no network.

### PASS (CI, real runners — runs 35963793099 and 35964596589)
- Real `docker compose` chain: config with key (UTF-8/LF and UTF-8-BOM/CRLF), resolved env of backend/worker/backup, config **refused** without key and with empty key, `Settings` inside the built pilot images — 12/12, verdict PASS (`compose-pilot-license-chain.ci.json`).
- Real `Write-HrmPilotEnv` fingerprint case and overlay static case — Windows job.
- Backend checks (ruff/format/mypy/pytest/preflight incl. license cases), backend integration (PostgreSQL), frontend, release-policy, compose smoke, prod/proxy overlay negative check — all success.

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

**NO-GO** for the pilot release — but the reason has changed. The Compose/runtime chain is now
**PASS on real CI** (imported artifact, not declared). What remains:

1. **BLOCKED — clean Windows 10/11 issuer bundle check** (owner VM: `run-gui.bat` / `run-html.bat`,
   issue a license offline, confirm no network and no private key in logs) — `windows-issuer-bundle-check.md`.
2. **BLOCKED by design — `installer_snapshot_contains_public_key`**: `infra/license/public_key.b64` is
   deliberately not in git; the owner bakes it into the release / `<state>\license_public_key.b64`.
   Without it the stack now refuses to start with a clear message instead of crash-looping (verified in CI).

Do not merge, do not tag/release, do not run production workflows. Once the owner completes (1),
this verdict can move to GO for the closed 127.0.0.1 pilot; (2) is an operational step, not a defect.

## CI results
See the table at the top and `ci-run-status.json` (run 35964596589 @ `7c7f645`, run 35963793099 @ `96f12bc`; all six jobs `success` in both).
