# Final Verdict — Offline Licensing for Windows Pilot — PR #34 — review fixes ported to the PR branch at `f85a362` (CI run 35977017563)

## TL;DR

* **Verdict: NO-GO. Nothing was merged. Do not tag/release, do not run production workflows.**
* The four confirmed owner-side fixes are now delivered **against the PR #34 branch itself**: this session branch
  merged `arena/01a0ccb9-hr-manager` into itself and PR **[#36](https://github.com/sledovatel61/HR-Manager/pull/36)**
  (`base = arena/01a0ccb9-hr-manager`) contains exactly the fix diff. Merging #36 is what lands the fixes in PR #34 —
  that is the owner's call and it should wait for the Windows evidence. The session is pinned to
  `arena/01a0d255-hr-manager`, so it cannot push to the PR #34 branch directly.
* **CI ran for the new HEAD** `f85a362d34a7d30dea292ba67324781852180d33`:
  [run 35977017563](https://github.com/sledovatel61/HR-Manager/actions/runs/35977017563) — **6/6 jobs `success`**,
  and because the branch now carries PR #34's workflow, this run **also executed the three license-chain steps**
  (`success`) and uploaded the `compose-pilot-license-chain` artifact (id `10798503119`,
  `sha256:0f4772f9b81c9ddd…`). A duplicate run for the same head (`35977008271`) is also green.
* **Windows runtime validation is `NOT RUN`** — no Windows and no way to run one in this environment (no `/dev/kvm`,
  no `vmx`/`svm` flags, no `qemu-*`/`wine`/`pwsh`, no network path to fetch packages or installation media). So
  `build.ps1` was never executed, `license-issuer-dist.zip` was not built, `run-gui.bat`/`run-html.bat` were never
  launched and there is no on-VM evidence about network or key hygiene. This stays `NOT RUN` until the owner
  provides real Windows evidence.
* No `backend/`, Compose, `infra/` or `frontend/` file is modified; no test was rewritten; no merge, tag, release or
  production workflow was triggered.

## Revisions

| Role | Revision / ref | Notes |
|---|---|---|
| PR #34 | [#34](https://github.com/sledovatel61/HR-Manager/pull/34), head `e59aa5b7a3df49b61a8b7c599601bfbd4e9b2784`, open, not merged | its head is unchanged by this pass |
| Baseline (negative control) | `e59aa5b7a3df49b61a8b7c599601bfbd4e9b2784` | every fix check fails here |
| **Ported revision (this pass)** | `f85a362d34a7d30dea292ba67324781852180d33` — merge of `origin/arena/01a0ccb9-hr-manager` into the session branch, conflicts resolved in favour of the fixed files | the tree is PR #34 **plus** the fixes |
| Port pull request | [#36](https://github.com/sledovatel61/HR-Manager/pull/36) → base `arena/01a0ccb9-hr-manager` | **not merged**; merging it puts the fixes into PR #34 |
| Earlier fix commit | `c515a490db354436dbd0d18115e28ef5d8ece132` | the same two-file change before the merge; patch form: `review-artifacts/license-issuer-fixes.patch` (`sha256 daba388b1b99c038…`, verified with `git apply --check` against `e59aa5b`) |

Exact diff of PR #36 against the PR #34 branch (nothing else):

```
 .gitignore                                      |   8 +
 tools/license-issuer/build.ps1                  |  57 +--
 review-artifacts/*                              |  (evidence, generators, verdict)
```

## The four fixes — measured before → after (baseline `e59aa5b`, reviewed `f85a362`)

The generator materialises the **reviewed revision's own tree** from git objects outside the repository (the tree is
complete, so no overlay is needed) and runs the same checks against the baseline tree as a negative control.

| Requested fix | Check (`issuer-offline-evidence.json`) | before | after |
|---|---|---|---|
| `http.server` bound to `127.0.0.1` | `run_html_bat_binds_loopback_only` | FAIL | **PASS** |
| no advice to use a system Python | `run_gui_bat_no_system_python_advice` | FAIL | **PASS** |
| launcher fails closed without the bundle | `run_html_bat_fails_closed_without_system_python` | FAIL | **PASS** |
| smoke test outside git + no key in the build log | `smoke_test_runs_outside_the_repository` / `smoke_test_never_prints_key_material` | FAIL / FAIL | **PASS / PASS** |
| `.gitignore` blocks keys and licenses | `gitignore_blocks_key_and_license_material` | FAIL | **PASS** |

Loopback is measured with socket attribution to the server PID (`/proc/<pid>/fd`), same interpreter and command line:
`-b 127.0.0.1` → listens on `127.0.0.1` only; the previous command line (no `-b`) → `0.0.0.0`. The smoke test runs
`gen-keypair --out-dir <GUID dir under %TEMP%>`, asserts that the private key never appears in the build output
(`refusing to continue`), removes the directory in a `finally` block and exits non-zero on failure.

## CI for the new HEAD

| Revision | Run | Result |
|---|---|---|
| `f85a362` (PR #36 head, = PR #34 tree + fixes) | [35977017563](https://github.com/sledovatel61/HR-Manager/actions/runs/35977017563) | **6/6 jobs `success`**: Backend checks `107559790351` · Frontend checks `107559790445` · Release pipeline fail-closed policy `107559790493` · Backend integration tests (PostgreSQL) `107559790502` · Windows engine tests + installer smoke `107559790507` · Compose stack smoke test `107560788159`. License-chain steps: “Pilot overlay — license public-key chain (real docker compose)” ✅, “Print pilot license-chain report” ✅, “Upload pilot license-chain evidence” ✅; artifact `compose-pilot-license-chain` id `10798503119`, 2254 bytes, `sha256:0f4772f9b81c9ddd…` |
| same head (duplicate) | 35977008271 | `success` (created while the earlier bridge PR #35 existed; #35 is now closed) |
| PR #34 head `e59aa5b` | [35965657324](https://github.com/sledovatel61/HR-Manager/actions/runs/35965657324) | 6/6 `success` + license-chain steps `success` |

Because the branch carries PR #34's `ci.yml`, this run is stronger than the pre-merge runs of this session: it
re-exercises the license chain on real `docker compose`. What it still does **not** do: execute `build.ps1`,
`run-gui.bat` or `run-html.bat` (the `.bat` files exist only after the bundle build). The verbatim chain report body
of run 35977017563 could not be re-imported (artifact zips and job logs answer EOF from this sandbox), so
`compose-pilot-license-chain.ci.json` still holds the body imported from run 35964596589 and
`ci-run-status.json → port.ci_run` records this run's API-verified step conclusions, artifact id and digest.

## NOT RUN — the only remaining blocker (owner's Windows VM)

| Item | Status |
|---|---|
| `build.ps1` executed / `license-issuer-dist.zip` built | **NOT RUN** (needs python.org at build time; unreachable here) |
| `run-gui.bat` on a clean Windows 10/11 VM without Python/pip/internet | **NOT RUN** |
| `run-html.bat` on that VM (Edge at `http://127.0.0.1:8765/…`) | **NOT RUN** |
| Issue + verify a license through the GUI and through the HTML page on the VM | **NOT RUN** |
| Upload both licenses to the backend from the VM | **NOT RUN** (both issuers' output *is* verified against the backend's own verification function on Linux) |
| No outbound connections on the VM (Wireshark / Resource Monitor) | **NOT RUN** |
| No private key in `%TEMP%`, `%APPDATA%`, bundle, browser downloads on the VM | **NOT RUN** |
| PowerShell syntax validated by a real parser | **NOT RUN** here (no `pwsh`); CI validates the repo's PowerShell on `windows-latest`, the bundle still needs the VM run |

Checklist and redaction rules: `review-artifacts/windows-issuer-bundle-check.md`.

## Evidence produced in this pass (redacted only)

| File | Content |
|---|---|
| `issuer-offline-evidence.json` / `.md` (+ generator) | **39 PASS · 0 FAIL · 3 INFO · 3 GAP · 8 NOT RUN** for `f85a362`, with the before/after table, launcher hashes, the loopback measurement and secret-leak assertions |
| `ci-run-status.json` | schema 2: PR #34 head, fix commit, **port PR #36 + its CI run** (job ids, conclusions, chain steps, artifact digests), provenance of the imported chain report, and the explicit statement that the Windows check is `NOT RUN` |
| `license-chain-evidence.{json,md}` | regenerated for the reviewed revision (`HRM_EVIDENCE_REF=HEAD`); `checks_read_from_real_files` values identical to the values the PR branch produced |
| `license-issuer-fixes.patch` | the two-file fix as a patch, still `git apply`-clean against `e59aa5b` |
| `windows-issuer-bundle-check.md` | remaining owner checklist with the measured reasons for `NOT RUN` |

Redaction: only fingerprints, lengths, HTTP codes, blob SHA-256 prefixes and booleans — no private key, no public key
in full, no complete license, no signature, no real client name. Test licenses use the synthetic client name
`Синтетический Пилот (синтетическое тестовое значение)`; the backend's key fingerprint is masked as
`SHA256:<redacted>` (over-redaction on purpose).

## Known remaining product deltas (unchanged, not fixed here)

| ID | Delta | Effect |
|---|---|---|
| G5 | The **HTML** issuer signs a license with `expires_at` in the past (no `issued_at <= expires_at` check); the CLI refuses it | backend rejects the upload with `bad_date` — clear error, no data loss |
| G6 | Both issuers sign a `client_name` containing control characters | backend rejects with `bad_value`; reachable from the CLI via a shell argument |

## Verdict

**NO-GO.** The fixes are ported to the PR #34 branch as PR #36 and CI for the new HEAD is green — including the
license-chain steps — but the **clean Windows 10/11 issuer runtime check has not been performed**, so `GO` is not
issued and merging is not recommended. Do not merge #36 (or PR #34) until the owner completes
`windows-issuer-bundle-check.md` on the VM and publishes the redacted evidence: page HTTP 200 at
`http://127.0.0.1:8765/`, "signature correct" from both issuers, absent private-key hex in
`%TEMP%`/`%APPDATA%`/bundle/downloads, and zero outbound connections.
