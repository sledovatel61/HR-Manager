# Final Verdict — Offline Licensing for Windows Pilot — PR #34 — review fixes at `c515a49` (CI run 35973708182)

## TL;DR

* **Verdict: NO-GO. Do not merge, do not tag/release, do not run production workflows.**
* The four owner-side fixes requested in the review are **implemented and verified** (each one is checked twice:
  against PR head `e59aa5b` — where it fails — and against the fixed revision — where it passes):
  loopback binding of `run-html.bat`, no system-Python fallback/advice, smoke test in a temp directory outside the
  git work tree, and `.gitignore` protection for keys and issued licenses.
* The **clean Windows 10/11 runtime check was NOT RUN**. This review environment has no Windows and no way to run
  one: no `/dev/kvm`, `vmx`/`svm` flags absent from `/proc/cpuinfo` (0 matches), no `qemu-*`, no `wine`, no `pwsh`,
  and no network path to fetch installation media or packages (every outbound fetch, including the Debian mirrors,
  fails). Therefore `run-gui.bat` and `run-html.bat` were **never executed**, no license was issued on Windows, and
  `build.ps1` was **never executed** (it needs python.org at build time) — so `license-issuer-dist.zip` was **not**
  produced. Those items are recorded as `NOT RUN`; see the checklist below.
* Because of that, **no `GO`**, no merge recommendation and **no claim that the task is complete**. The task is
  complete only as far as the environment allows; the Windows part is explicitly open.
* Backend/Compose/infra/frontend code is untouched; no test was rewritten; no merge, tag, release or production
  workflow was triggered.

## Revisions and CI (all values from the GitHub REST API / local git objects)

| Role | Revision | CI |
|---|---|---|
| **Reviewed PR** | [#34](https://github.com/sledovatel61/HR-Manager/pull/34) — open, not merged, base `main` | — |
| **PR head (baseline / negative control)** | `e59aa5b7a3df49b61a8b7c599601bfbd4e9b2784` | run [35965657324](https://github.com/sledovatel61/HR-Manager/actions/runs/35965657324) — 6/6 jobs `success`, license-chain steps `success` |
| **Fix commit (this pass)** | `c515a490db354436dbd0d18115e28ef5d8ece132` | run [35973708182](https://github.com/sledovatel61/HR-Manager/actions/runs/35973708182) — see the CI table below |
| **Review bridge PR** | [#35](https://github.com/sledovatel61/HR-Manager/pull/35) — **draft**, from `arena/01a0d255-hr-manager`, must not be merged as-is | run of the fix commit 35973708182; run [35974427408](https://github.com/sledovatel61/HR-Manager/actions/runs/35974427408) — 6/6 `success` — for the evidence commit `2f67f52` |

The fix commit lives on the session branch (based on `main`), which is why it is not a commit inside PR #34.
`review-artifacts/license-issuer-fixes.patch` (`sha256 daba388b1b99c038…`, 118 lines) contains exactly the two
changed files and was verified to apply cleanly to the PR head:

```
git apply --check -p1 review-artifacts/license-issuer-fixes.patch   # in a worktree at e59aa5b → ok
 .gitignore                     |    8 ++++++
 tools/license-issuer/build.ps1 |   57 +++++++++++++++++++++++++++++-----------
```

## The four fixes (before → after, measured by `review-artifacts/gen_issuer_offline_evidence.py`)

The generator materialises the tree under test **outside the repository** from git objects: the PR-head tree
`e59aa5b` extracted with `git archive`, plus the files of the fix commit overlaid on top
(`.gitignore` `ee464f266e6c3802…`, `tools/license-issuer/build.ps1` `d51a613d6980c127…`). Nothing is read from the
working tree, so the checks cannot silently test the wrong revision.

| Requested fix | Check (`issuer-offline-evidence.json`) | on `e59aa5b` (before) | on `c515a49` (after) |
|---|---|---|---|
| `http.server` bound to `127.0.0.1` | `run_html_bat_binds_loopback_only` | FAIL | **PASS** |
| no advice to use a system Python | `run_gui_bat_no_system_python_advice` | FAIL | **PASS** |
| launcher fails closed without the bundle | `run_html_bat_fails_closed_without_system_python` | FAIL | **PASS** |
| smoke test outside git, no key in the log | `smoke_test_runs_outside_the_repository` / `smoke_test_never_prints_key_material` | FAIL / FAIL | **PASS / PASS** |
| `.gitignore` blocks keys and licenses | `gitignore_blocks_key_and_license_material` | FAIL | **PASS** |

What the fixed launcher does now (extracted from `build.ps1`, the only source of `run-*.bat` — there is no `.bat`
file in the repository):

```bat
REM Server binds to 127.0.0.1 ONLY (loopback): the bundle is never exposed to the LAN
...
"%PY_EXE%" -m http.server %PORT% -b 127.0.0.1 --directory "%SCRIPT_DIR%"
```

Loopback behaviour was **measured** on this host with the same interpreter and the same command line, attributing
the listening socket to the server's own PID via `/proc/<pid>/fd` (the sandbox has a platform proxy on the host
address, so "reachable via the host IP" would not be evidence about our process):

| command line | listening socket of the server process | page over loopback |
|---|---|---|
| `-m http.server 8791 -b 127.0.0.1 --directory <bundle>` | `127.0.0.1:8791` | HTTP 200, 17087 bytes, byte-identical |
| `-m http.server 8792 --directory <bundle>` (previous launcher) | `0.0.0.0:8792` — LAN-reachable | HTTP 200, 17087 bytes |

The build smoke test now runs `gen-keypair --out-dir $smokeDir` where `$smokeDir` is a GUID-named directory under
`[System.IO.Path]::GetTempPath()` (outside the git work tree), verifies both key files were created, fails the
build if the captured output ever contains the private key (`refusing to continue`), removes the directory in a
`finally` block and exits non-zero on any failure — previously it ran `gen-keypair` without `--out-dir`, which
wrote `keys\private_key.hex` relative to the current directory (i.e. inside the work tree when `build.ps1` is
started from the repository root).

## CI status of the fix commit

Run [35973708182](https://github.com/sledovatel61/HR-Manager/actions/runs/35973708182) (`pull_request`, created
2026-09-24T08:10:35Z, finished 08:17:15Z) on exactly this SHA — **all six jobs `success`**:

| Job | id | Conclusion |
|---|---|---|
| Frontend checks | 107549097396 | success |
| Backend integration tests (PostgreSQL) | 107549097502 | success |
| Windows engine tests + installer smoke | 107549097538 | success |
| Backend checks | 107549097590 | success |
| Release pipeline fail-closed policy (ephemeral test signature) | 107549097597 | success |
| Compose stack smoke test (dev + prod overlay) | 107550340154 | success |

**What this run does not cover (checked, not assumed):** the session branch is based on `main`, so the run uses
`main`'s `ci.yml` — it contains none of the three license-chain steps and produced **no**
`compose-pilot-license-chain` artifact (verified in the run's artifact list). The chain evidence for PR #34 therefore
remains the imported report of run 35964596589 plus the step conclusions of run 35965657324. And CI never executes
`build.ps1`/`run-gui.bat`/`run-html.bat`: the `.bat` files exist only after the bundle build, so this run is evidence
that the change does not break the repository CI — **not** evidence about the issuer at runtime.

Scope note: CI does **not** execute `tools/license-issuer/build.ps1`, `run-gui.bat` or `run-html.bat`
(the `.bat` files only exist after `build.ps1` runs). The `Windows engine tests + installer smoke` job runs on
`windows-latest` and covers the PowerShell engine and the installer, i.e. it is PowerShell-5.1 syntax evidence for
the repo, **not** a runtime check of the issuer bundle. PowerShell syntax of the edited `build.ps1` was therefore
verified only structurally here (here-strings balanced, no U+FFFD; the brace/parenthesis counters are recorded as a
non-decisive INFO step) — an authoritative parse needs PowerShell, which is not available in this environment.

## NOT RUN — the only remaining blocker (requires the owner's VM)

| Item | Status |
|---|---|
| `build.ps1` executed / `license-issuer-dist.zip` built | **NOT RUN** — needs python.org (embeddable Python + get-pip) at build time; unreachable here |
| `run-gui.bat` on a clean Windows 10/11 VM without Python/pip/internet | **NOT RUN** — no Windows, no virtualisation (no KVM, no vmx/svm), no qemu/wine |
| `run-html.bat` on that VM (page in Edge at `http://127.0.0.1:8765/…`) | **NOT RUN** |
| Issue + verify a license through the GUI and through the HTML page on the VM | **NOT RUN** |
| Upload both licenses to the backend from the VM | **NOT RUN** (both issuers' output was verified against the backend's own verification function **here**, on Linux — see the evidence file) |
| No outbound connections on the VM (Wireshark / Resource Monitor) | **NOT RUN** |
| No private key in `%TEMP%`, `%APPDATA%`, bundle, browser downloads on the VM | **NOT RUN** (verified here only for the Linux-run CLI/HTML flows: nothing is written to disk except the files the flow itself creates) |
| PowerShell syntax validated by a real parser | **NOT RUN** here (no `pwsh`); covered by CI on `windows-latest` for the repo's PowerShell, and by the VM run for the bundle |

Checklist and evidence rules for the owner: `review-artifacts/windows-issuer-bundle-check.md`.

## Evidence produced in this pass (redacted only)

| File | Content |
|---|---|
| `issuer-offline-evidence.json` / `.md` (+ `gen_issuer_offline_evidence.py`) | **39 PASS · 0 FAIL · 3 INFO · 3 GAP · 8 NOT RUN**, the before/after table above, launcher hashes, the loopback measurement, and every generated secret asserted absent before writing |
| `license-issuer-fixes.patch` | the two-file fix, `git apply`-verified against the PR head |
| `ci-run-status.json` | PR-head run `35965657324`, chain-report provenance, fix-commit run of `c515a49` |
| `windows-issuer-bundle-check.md` | remaining owner checklist (now only runtime items + two known product deltas) |
| `license-chain-evidence.{json,md}`, `compose-pilot-license-chain.ci.json` | unchanged chain evidence of PR #34 (regenerated for `e59aa5b` in the previous pass) |

Both runs of this pass (fix commit and evidence commit) ended with **6/6 jobs `success`**; the evidence commit
changed `review-artifacts/` only, so its run adds no new runtime information.

Redaction: the evidence contains fingerprints, lengths, HTTP codes and booleans only — no private key, no public
key in full, no complete license, no signature, no real client name. The synthetic client name used in the tests is
`Синтетический Пилот (синтетическое тестовое значение)`. In this pass the backend fingerprint of the ephemeral
test key is recorded as `SHA256:…(redacted)` only; the earlier raw `SHA256:<16 hex>` values were masked as well
(over-redaction, so that no key-derived material is published).

## Known remaining product deltas (unchanged, not fixed here)

| ID | Delta | Effect |
|---|---|---|
| G5 | The **HTML** issuer signs a license with `expires_at` in the past (no `issued_at <= expires_at` check); the Python CLI refuses the same input | backend rejects Maria's upload with `bad_date` — owner sees a clear error, no data loss |
| G6 | Both issuers sign a `client_name` containing control characters | backend rejects with `bad_value`; from the CLI it is reachable via a shell argument, in the HTML page the single-line `<input>` normally strips newlines |

They are recorded as `GAP` (not failures of the requested work) and are candidates for a small follow-up.

## Verdict

**NO-GO.** The requested code fixes are done and verified as far as this environment permits, and the PR's own CI
is green at the reviewed head, but **the clean Windows 10/11 runtime check was not performed** — `run-gui.bat`,
`run-html.bat`, the bundle build, the on-VM license issuance and the on-VM network/private-key checks are all
`NOT RUN`. Do not merge, do not tag/release, do not run production workflows. Merge becomes recommendable only after
the owner completes `windows-issuer-bundle-check.md` on the VM and publishes the redacted evidence (HTTP 200 for
the page, "signature correct" for both issuers, absence of the private-key hex in `%TEMP%`/`%APPDATA%`/bundle/
downloads, and no outbound connections).
