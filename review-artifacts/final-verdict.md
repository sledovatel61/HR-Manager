# Final Verdict — Offline Licensing for Windows Pilot — PR #34 — release re-check at HEAD `e59aa5b` (CI run 35965657324)

## TL;DR

* **Verdict: NO-GO.** Do not merge, do not tag/release, do not run production workflows.
* The blocking reason is **unchanged and is not a code defect**: the **clean Windows 10/11 issuer check was not
  performed**. This review environment is a Linux sandbox with **no Windows, no virtualisation/KVM and no Wine**,
  so `run-gui.bat` and `run-html.bat` were **never launched** and no license was issued on a Windows VM. Per the
  release rules, a `GO` may not be issued while that runtime test is missing.
* What *is* new in this pass: everything about the issuer that can be executed without Windows is now **automated
  and reproducible** (`review-artifacts/gen_issuer_offline_evidence.py` → `issuer-offline-evidence.json/.md`),
  including the proof that licenses produced by the Python issuer core **and** by the offline HTML page verify with
  the **backend's own verification function**. Result: **30 PASS · 0 FAIL · 2 INFO · 8 GAP · 8 NOT RUN**.
* CI for the reviewed HEAD is green and was re-verified through the GitHub API (run **35965657324**, all six jobs
  `success`, the three license-chain steps `success`, artifact digest recorded).
* This pass changed **only** files under `review-artifacts/`. No backend, Compose, infra, frontend or `tools/`
  file was touched, no test was rewritten, and no merge/tag/release/production workflow was triggered.

## Reviewed revision and CI (verified via the GitHub REST API, not from memory)

| Item | Value |
|---|---|
| PR | [#34](https://github.com/sledovatel61/HR-Manager/pull/34) — open, not merged (`mergedAt: null`, base `main`) |
| **Reviewed HEAD** | `e59aa5b7a3df49b61a8b7c599601bfbd4e9b2784` (merge-base `efb88d978440a0aae1940005fddffc7e465ad9ef` = origin/main) |
| **CI run** | [35965657324](https://github.com/sledovatel61/HR-Manager/actions/runs/35965657324) — `pull_request`, created 2026-09-24T06:40:45Z, conclusion `success` |
| Jobs (all `success`) | Backend checks `107523536847` · Backend integration tests (PostgreSQL) `107523536837` · Frontend checks `107523536545` · Windows engine tests + installer smoke `107523536888` · Release pipeline fail-closed policy `107523536790` · Compose stack smoke test (dev + prod overlay) `107524244972` |
| License-chain steps in that run | “Pilot overlay — license public-key chain (real docker compose)” ✅ · “Print pilot license-chain report” ✅ · “Upload pilot license-chain evidence” ✅ |
| Chain artifact of that run | `compose-pilot-license-chain` id `10793868415`, 2252 bytes, `sha256:34a42a7960c5fd7d667224314fb269d3cb3f26298f8db5846a7b0b8031bf5b21` |
| Previous runs (history) | 35964596589 @ `7c7f645` · 35963793099 @ `96f12bc` — both 6/6 success |
| PR head is the newest commit with runs | yes: 35965657324 is the latest run for `e59aa5b…`, and `e59aa5b…` is the current `headRefOid` of PR #34 |

**Independent validation of the previously hand-written record:** the Artifacts API now exposes a `digest`
(SHA-256 of the artifact zip). For both earlier runs it matches the values recorded by hand in
`ci-run-status.json`: `93cb7628…a4e2` (run 35963793099) and `dd7747c1…d2588` (run 35964596589). The record was
therefore correct and is now machine-checkable.

**Import limitation (stated, not hidden):** on 2026-09-24 this sandbox can no longer read GitHub log blobs or
artifact zips (`gh run view --job … --log` and `gh run download …` both fail with EOF on the storage host).
Therefore the verbatim chain report body in `compose-pilot-license-chain.ci.json` remains the one imported from
run **35964596589** (which was readable), while the head run **35965657324** re-executed the same three steps
with `success` (jobs API) and produced an artifact of the same size. This is recorded in
`ci-run-status.json → chain_report.why_not_reimported_from_the_head_run`.

`ci-run-status.json` and `license-chain-evidence.{json,md}` were regenerated for this HEAD. The chain generator
now supports `HRM_EVIDENCE_REF=<sha>` so it reads the **reviewed commit's tree** (extracted with `git archive`)
instead of whatever branch it happens to sit on:

```bash
HRM_EVIDENCE_REF=e59aa5b7a3df49b61a8b7c599601bfbd4e9b2784 \
  python3 review-artifacts/gen_license_chain_evidence.py
```

Regenerating it produced **identical** values for every entry of `checks_read_from_real_files` compared with the
version generated on the PR branch — independent confirmation that the reviewed tree matches what the PR branch
tested; only the CI-import block, the generated timestamp and two `runtime_steps` lines changed.

## What this pass actually executed for the issuer (Linux sandbox — **not** Windows)

Reproduce with (the interpreter needs `cryptography` + the backend runtime requirements, plus `node`):

```bash
python3 -m venv .venv && .venv/bin/pip install cryptography -r /tmp/pr34/backend/requirements.txt
.venv/bin/python review-artifacts/gen_issuer_offline_evidence.py --python .venv/bin/python
python3 -m http.server --help        # (optional) shows -b/--bind, used in the recommendation below
```

The harness extracts `tools/license-issuer/**` and `backend/app/**` of commit `e59aa5b…` into a temp directory
outside the repository, then runs the real code:

| # | Check | Result |
|---|---|---|
| 1 | `cli.py gen-keypair` → `issue` → `verify`, synthetic client, under a **socket-denying harness** (`socket.connect/connect_ex/sendto/create_connection/getaddrinfo` raise) | **PASS** — exit 0/0/0, **0 network API attempts** |
| 2 | Private key never in captured output of those three commands | **PASS** — 64-hex key absent from stdout+stderr |
| 3 | Backend verification of the CLI-issued license via the real `app.services.license_service.parse_and_verify_license_text` + `app.license.validate_time_consistency` (the `POST /license/upload` path) | **PASS** |
| 4 | Negative controls through the same backend function | **PASS** — tampered `max_active_users` → `bad_signature`; wrong public key → `bad_signature` |
| 5 | `license-issuer.html` inline script run in a JS VM with a DOM shim, **Node network APIs denied and recorded** | **PASS** — 0 attempts |
| 6 | HTML WebCrypto path (Edge 120+/Chrome 120+): keypair, issue, self-verify, cross-verify of a CLI-issued license | **PASS** — instrumented: `crypto.subtle.sign` used, TweetNaCl not used |
| 7 | HTML TweetNaCl fallback (no `crypto.subtle`, the Windows `file://` path): issue + cross-verify | **PASS** — instrumented: `nacl.sign.detached` used for sign and verify, 2 keypair calls |
| 8 | Backend verification of **both** HTML-issued licenses | **PASS** — backend accepts WebCrypto- and TweetNaCl-produced licenses |
| 9 | Launcher content extracted from `build.ps1` (the only source of `run-*.bat`) | **PASS** for the contracts, 3 **GAP** findings — see below |
| 10 | Emulated `run-html.bat` server on this host: `GET /license-issuer.html` | **PASS** — HTTP 200, byte-identical page; measured listen address `0.0.0.0` (GAP) |
| 11 | Static: no socket/urllib/requests/subprocess in the Python issuer; no `fetch`/XHR/WebSocket in the page; no external resource in the page; no network code in the bundled `nacl-fast.js` | **PASS** |
| 12 | No key material committed: 525 tracked files — no `*.hrmlicense`, no `infra/license/public_key.b64`, no `keys/`, no 64-hex literal in the issuer sources | **PASS** |

**What this does NOT prove** (and why the verdict is still NO-GO): that `run-gui.bat` / `run-html.bat` start on a
clean Windows 10/11 VM, that the bundle works without system Python (`build.ps1` was never executed — it needs
python.org at build time), that the Tkinter GUI works, or that Windows makes no outbound connection. Those eight
items are `NOT RUN` in `issuer-offline-evidence.json`.

## Findings from this pass (owner-side tooling; no backend/Compose impact)

None of these changes the server-side guard or the Compose chain. They are filed as `GAP` in
`issuer-offline-evidence.json` (eight `GAP` steps map to six findings — G3 and G6 are each recorded twice, as a
static and a measured/reproduced step); **G1 and G4 are worth fixing before the owner builds the pilot bundle**.

| ID | Finding | Severity | Suggested fix (owner/maintainer, not applied here) |
|---|---|---|---|
| G1 | `run-html.bat` sets `set PY_EXE=python` when the bundled interpreter is missing, i.e. it **silently falls back to system Python**, contradicting its own comment plus `HOWTO.txt`/README (“no system Python needed”). On a clean VM this yields `'python' is not recognized…` instead of the clear fail-closed message that `run-gui.bat`/`run-cli.bat` print. | medium (support/consistency) | remove the fallback or make it print the same clear error and `exit /b 1` |
| G4 | `.gitignore` has **no entry** for `keys/`, `private_key.hex` or `*.hrmlicense`, while `build.ps1` step 5 runs `cli.py gen-keypair` **without `--out-dir`**: the maintainer's smoke test writes `keys\private_key.hex` relative to the current directory — inside the git work tree when `build.ps1` is started from the repo root — and `git add -A` would stage a private key. | medium (key hygiene) | add `keys/`, `private_key.hex`, `public_key.b64`, `*.hrmlicense` to `.gitignore`; pass `--out-dir` to a temp path in the smoke test |
| G3 | `run-html.bat` runs `python -m http.server 8765` **without `-b`**: `http.server` binds `0.0.0.0` (measured on this host), so while the window is open the bundle directory — and any key file the owner saved next to it — is reachable from the LAN; the Windows firewall may also prompt. | low/medium (hardening) | `-b 127.0.0.1` |
| G2 | `run-html.bat` opens the browser **before** starting the server and does not check the port: a slow start or an occupied 8765 shows “site can't be reached” even though the bundle is fine. | low (UX) | start the server first (or wait for the port), then `start` the browser |
| G5 | The **HTML** issuer signs a license whose `expires_at` is in the past (no `issued_at <= expires_at` check); the backend then rejects Maria's upload with `bad_date` (“issued_at не может быть позже expires_at”). The Python CLI refuses the same input. | low (parity) | add the same check in `issueLicense()` |
| G6 | Both issuers sign a `client_name` containing control characters; the backend rejects such payloads (`bad_value`). Reachable from the CLI via a shell argument; in the HTML page a single-line `<input>` normally strips newlines, so it is mainly a defence-in-depth gap. | low | validate `client_name` in `license_issuer.issue_license()` |

Everything else that was checked is `PASS`, and the server side of the chain (Compose overlay, fail-closed
`Settings`, guard deny-by-default, upload verification) is unchanged and still covered by CI pytest/Compose runs.

## Mandatory checks — PASS / FAIL / BLOCKED / NOT RUN

### PASS — CI at the reviewed HEAD (run 35965657324, API-verified)
Six jobs `success`: backend (ruff/format/mypy/pytest/preflight), backend integration on real PostgreSQL
(migrations + license suites), frontend (lint/typecheck/tests/build/audit), Windows engine + installer smoke,
release-pipeline fail-closed policy with an ephemeral test signature, Compose stack smoke (dev + prod overlay)
including the pilot license-chain script (`--require-runtime`, 12/12 `[pass]` in the imported report).

### PASS — issuer checks executed here (Linux, reproducible; full detail in `issuer-offline-evidence.json`)
`cli.py` offline end-to-end with 0 network attempts · no private key in CLI output · backend verifies CLI-, 
HTML/WebCrypto- and HTML/TweetNaCl-issued licenses · negative controls reject tampering and a wrong key ·
launcher content contracts · page loads with no external resource · emulated local server serves the page ·
no key material committed.

### FAIL
* none observed in this pass.

### BLOCKED — must still be done by the owner on a clean Windows 10/11 VM
1. unzip the built bundle and double-click **`run-gui.bat`** (Tkinter GUI without system Python);
2. double-click **`run-html.bat`** (page opens at `http://localhost:8765/license-issuer.html` in Edge);
3. issue a test license through **both** issuers and check the JSON/`.hrmlicense` fields;
4. confirm with Wireshark / Resource Monitor that the issuer makes **no outbound connection** (the loopback HTTP
   server started by `run-html.bat` is local only, but note G3);
5. grep the VM (`%TEMP%`, `%APPDATA%`, the bundle dir, browser download folder) to show the **private key is not
   in logs/artifacts**;
6. paste the redacted evidence (fingerprints, HTTP 200, absence of the key hex) into
   `windows-issuer-bundle-check.md`.

Checklist and redaction rules: `review-artifacts/windows-issuer-bundle-check.md`.
Also still BLOCKED and **not a defect**: `installer_snapshot_contains_public_key` — `infra/license/public_key.b64`
is deliberately not in git; the owner bakes it into the release.

### NOT RUN (by design / out of scope, unchanged)
`release.yml`, `update-channel.yml`, `workflow_dispatch`, tags/releases `v0.14.0`, production signing. This pass
performed no merge, tag, release or production workflow call.

## Private key and PII confirmation (this pass)

* 525 tracked files scanned: no `*.hrmlicense`, no `infra/license/public_key.b64`, no `keys/`, no 64-hex literal in
  `tools/license-issuer/*.py`.
* All key material generated during the checks lived in a temporary directory **outside** the repository; the
  private key never appeared in any captured stdout/stderr, and the evidence files were written only after an
  assertion that no generated secret appears in them.
* The only UI path that ever shows the private key is the explicit “Показать” button of `gui.py` (the field is
  masked by default); nothing logs it.
* PII: the checks used the synthetic client name `Синтетический Пилот (синтетическое тестовое значение)`; no real
  client/pilot name is used or stored. Evidence carries fingerprints, lengths and booleans only — no keys, no
  license bodies, no signatures in full.
* The ephemeral test key fingerprint seen by the backend in this pass: `SHA256:c6bcb82005f52e45… (redacted)`
  (ephemeral, generated inside the check, never persisted outside the temp dir).

## How these artifacts reach PR #34

The review session is pinned to its own branch (`arena/01a0d255-hr-manager`, based on `main`), therefore these
files cannot be pushed to the PR branch from here. They contain only `review-artifacts/` changes:

```bash
git checkout arena/01a0ccb9-hr-manager                      # PR #34 head branch
git checkout arena/01a0d255-hr-manager -- review-artifacts/ # cherry-pick the updated evidence
git diff --stat                                             # expect review-artifacts/ only
```

## Verdict

**NO-GO for the pilot release.** The Compose/runtime chain and the issuer logic are in good shape — the server-side
chain is PASS on real CI, and the issuer was proven offline and cross-compatible with the backend verifier on this
host — but the **clean Windows 10/11 issuer check (GUI + HTML, no network, no private key in logs) has not been
performed**, so `GO` **cannot** be issued and merging is **not** recommended yet. Do not merge, do not tag/release,
do not run production workflows. Once the owner completes the checklist in `windows-issuer-bundle-check.md` and
records the redacted evidence, this verdict can move to `GO` for the closed `127.0.0.1` pilot (G1/G4 fixes
recommended first; G2/G3/G5/G6 are hardening/parity follow-ups).
