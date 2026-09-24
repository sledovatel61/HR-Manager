# Offline issuer — evidence from automated Linux checks (redacted)

- generated: 2026-09-24T08:11:37Z
- reviewed commit: `c515a490db354436dbd0d18115e28ef5d8ece132`
- tool: `review-artifacts/gen_issuer_offline_evidence.py` (re-runs every check below)
- host: Linux review sandbox — **not** the owner's Windows VM

> This file proves what could be executed without Windows. It is **not** the clean-Windows
> issuer check: `run-gui.bat`, `run-html.bat`, the Tkinter GUI and the bundle build remain
> `NOT RUN` (see `windows-issuer-bundle-check.md`). No `GO` may be derived from it.

## Summary: {"PASS": 39, "INFO": 3, "GAP": 3, "NOT RUN": 8}

- reviewed commit: `c515a490db354436dbd0d18115e28ef5d8ece132` · baseline (negative control): `e59aa5b7a3df49b61a8b7c599601bfbd4e9b2784`

| step | status | detail |
|---|---|---|
| `issuer_sources_of_reviewed_commit` | PASS | PR-head tree e59aa5b extracted outside the repo, fix commit c515a49 overlaid: .gitignore, tools/license-issuer/build.ps1 |
| `python_issuer_has_no_network_primitives` | PASS | 3 files scanned (cli.py, gui.py, license_issuer.py): no socket/urllib/requests/subprocess/webbrowser usage |
| `html_has_no_network_apis` | PASS | none of ['fetch(', 'XMLHttpRequest', 'WebSocket', 'EventSource', 'sendBeacon', 'importScripts'] appear in license-issuer.html |
| `html_loads_no_external_resources` | PASS | only local <script src="nacl-fast.js">; no http(s) src/href/@import/url() in the page |
| `html_url_strings_are_text_only` | INFO | 4 absolute URL(s) appear in license-issuer.html, all inside human-readable text (jsdelivr fallback hint / documentation), none as a resource reference |
| `nacl_fast_js_has_no_network_apis` | PASS | nacl-fast.js (61966 chars, 2391 lines) contains no fetch/XHR/WebSocket/HTTP access |
| `no_bat_files_committed` | PASS | no run-gui.bat / run-html.bat anywhere in the reviewed commit: they exist only as PowerShell here-strings inside build.ps1 and are written by build.ps1 at build time |
| `run_gui_bat_contract` | PASS | run-gui.bat (from build.ps1): bundled %~dp0..\python\python.exe, gui.py, fail-closed exit /b 1 when the bundle is incomplete (591 bytes) |
| `run_html_bat_contract` | PASS | run-html.bat (from build.ps1, 1041 bytes): bundled python -m http.server on port 8765 serving the bundle dir, then opens http://127.0.0.1:8765/license-issuer.html |
| `run_html_bat_binds_loopback_only` | PASS | run-html.bat starts `python -m http.server %PORT% -b 127.0.0.1 --directory <bundle>` and opens http://127.0.0.1: ... the bundle directory is not exposed to the LAN |
| `run_html_bat_fails_closed_without_system_python` | PASS | run-html.bat has no fallback to a system 'python': a missing bundle interpreter prints a clear error and exits with code 1, like run-gui.bat/run-cli.bat |
| `run_gui_bat_no_system_python_advice` | PASS | run-gui.bat no longer suggests installing system Python; it explains that the issuer never falls back to a system interpreter and exits with code 1 |
| `smoke_test_runs_outside_the_repository` | PASS | the build smoke test creates a temporary directory outside the repository (1 executed gen-keypair invocation(s), all with --out-dir), and removes the directory in a finally block |
| `smoke_test_never_prints_key_material` | PASS | the smoke test compares the captured output with the private key it just created and turns the build into an error if key material ever reaches the build log |
| `build_ps1_here_strings_and_encoding` | PASS | here-strings 4 open / 4 closed, no U+FFFD in the file. The brace/parenthesis counters are only an approximation (see INFO step): the authoritative PowerShell parser is not available here (no pwsh) and runs in CI on windows-latest |
| `build_ps1_brace_paren_balance_approx_not_decisive` | INFO | brace/parenthesis counters outside strings/comments are approximate for PowerShell (measured: {"{": -1, "(": 1}); they can be non-zero for correct code, so they are not used as a verdict - syntax validation happens with PowerShell 5.1 in CI and on the owner VM |
| `no_key_material_committed` | PASS | 505 tracked files: no *.hrmlicense, no infra/license/public_key.b64, no keys/ directory, no 64-hex literal in tools/license-issuer/*.py |
| `gitignore_blocks_key_and_license_material` | PASS | .gitignore covers keys/, private_key.hex, *.hrmlicense, public_key.b64, license-issuer-dist.zip: a stray private key, public key or issued license is no longer staged by `git add -A` |
| `gitignore_patterns_hide_no_tracked_file` | PASS | `git ls-files -ci --exclude-standard` is empty: no already-tracked file matches the new patterns (infra/license/public_key.b64 was never tracked) |
| `cli_gen_keypair_offline` | PASS | cli.py gen-keypair: exit 0, offline (see network harness below) |
| `cli_issue_offline` | PASS | cli.py issue: exit 0, offline (see network harness below) |
| `cli_verify_offline` | PASS | cli.py verify: exit 0, offline (see network harness below) |
| `cli_makes_no_network_calls` | PASS | gen-keypair + issue + verify ran under a socket-denying harness (socket.connect/connect_ex/sendto/getaddrinfo/create_connection raise) and recorded 0 network API attempts |
| `no_private_key_in_cli_output` | PASS | stdout+stderr of gen-keypair/issue/verify contain the file paths and license metadata only; the 64-hex private key never appears |
| `html_webcrypto_keypair` | PASS | priv=64 hex chars, pub=44 base64 chars |
| `html_webcrypto_issue` | PASS | fields=client_name,expires_at,issued_at,license_id,max_active_users,signature |
| `html_webcrypto_used_webcrypto_not_fallback` | PASS | crypto.subtle.sign calls=1, nacl.sign.detached calls=0, nacl keypair calls=0 (Edge 120+/Chrome 120+ path) |
| `html_webcrypto_self_verify` | PASS | issuer verifies its own signature |
| `html_webcrypto_verifies_cli_license` | PASS | HTML issuer accepts a license signed by the Python issuer core |
| `html_nacl_status_line` | INFO | 0 alert(s); page status line: Лицензия выпущена и подписана. |
| `html_nacl_issue` | PASS | fallback signing path without crypto.subtle |
| `html_nacl_verifies_cli_license` | PASS | fallback verify path |
| `html_nacl_used_tweetnacl_fallback` | PASS | crypto.subtle removed: nacl.sign.detached calls=1, nacl verify calls=1, nacl keypair calls=2 (Windows file:// path, Edge without secure context) |
| `html_accepts_expires_at_in_the_past` | GAP | HTML issuer signed a license whose expires_at is in the past (no issued_at<=expires_at check) |
| `html_signs_control_char_client_name` | GAP | HTML issuer signed a client_name containing a control character (backend rejects such payloads) |
| `html_makes_no_network_calls` | PASS | the page's own inline script ran with net.Socket.connect / dns.lookup / http(s).request / fetch / WebSocket denied and recorded 0 attempts, for both the WebCrypto and the TweetNaCl path |
| `emulated_runner_serves_page` | PASS | `-m http.server 8791 -b 127.0.0.1 --directory /tmp/hrm-issuer-evidence-b0we7ope/serverdir` (the fixed launcher's command line): GET /license-issuer.html on 127.0.0.1 -> HTTP 200, 17087 bytes, byte-identical to the file: True. Emulated with the host Linux Python 3.11.2; the Windows launcher run-html.bat itself was NOT executed |
| `run_html_bat_loopback_only_measured` | PASS | same interpreter, same command line as the launcher: with `-b 127.0.0.1` the server process listens on [('127.0.0.1', 8791)] only; the control run without -b (the previous launcher, which the fix removes) listens on [('0.0.0.0', 8792)] - i.e. reachable from the LAN. Listen sockets are attributed to the server's own PID via /proc/<pid>/fd, because this sandbox has a platform proxy that also listens on the host address |
| `backend_verifies_cli_license` | PASS | app.services.license_service.parse_and_verify_license_text + validate_time_consistency accepted it (endpoint path: POST /license/upload -> upload_license_json) |
| `backend_verifies_html_webcrypto_license` | PASS | backend accepted the license produced by the HTML issuer |
| `backend_verifies_html_nacl_license` | PASS | backend accepted the license produced by the HTML issuer |
| `backend_rejects_tampered_field` | PASS | tampered max_active_users -> LicenseError code=bad_signature |
| `backend_rejects_wrong_public_key` | PASS | wrong public key -> LicenseError code=bad_signature |
| `python_cli_refuses_past_expiry` | PASS | refused with ValueError: issued_at date cannot be after expires_at |
| `python_cli_signs_control_char_client_name` | GAP | python issuer core signs a client_name with a control character (backend rejects such payloads); reachable from the CLI via a shell argument |
| `windows_vm_run_gui_bat` | NOT RUN | double-click run-gui.bat on a clean Windows 10/11 VM without Python/pip/internet - needs the owner's VM; NOT executed here (this sandbox has no Windows, no virtualisation and no KVM) |
| `windows_vm_run_html_bat` | NOT RUN | double-click run-html.bat, browser opens http://localhost:8765/license-issuer.html |
| `windows_vm_issue_license_via_gui` | NOT RUN | issue a real .hrmlicense through the Tkinter GUI on the VM |
| `windows_vm_issue_license_via_html` | NOT RUN | issue a real .hrmlicense through the HTML page on the VM's Edge |
| `windows_vm_no_network_capture` | NOT RUN | confirm with Wireshark / Resource Monitor that the issuer performs no outbound connection (loopback HTTP server from run-html.bat excluded) |
| `windows_vm_no_private_key_in_logs_or_artifacts` | NOT RUN | after issuing, grep the VM's Temp/AppData/logs and the bundle dir for the private key hex |
| `gui_tkinter_runtime` | NOT RUN | gui.py executed (Tkinter needs a desktop session; static review only here) |
| `bundle_build_with_embeddable_python` | NOT RUN | build.ps1 executed (downloads python.org embeddable + get-pip at build time; not possible from this sandbox) |

## Fixes verified before/after (baseline = the revision before the patch)

| check | before | after |
|---|---|---|
| `run_html_bat_binds_loopback_only` | FAIL | PASS |
| `run_html_bat_fails_closed_without_system_python` | FAIL | PASS |
| `run_gui_bat_no_system_python_advice` | FAIL | PASS |
| `smoke_test_runs_outside_the_repository` | FAIL | PASS |
| `smoke_test_never_prints_key_material` | FAIL | PASS |
| `gitignore_blocks_key_and_license_material` | FAIL | PASS |

## Files of the reviewed commit (hashes of what was tested)

| file | exists | bytes | lines | sha256 prefix |
|---|---|---|---|---|
| `tools/license-issuer/build.ps1` | yes | 14686 | 320 | `d51a613d6980c127` |
| `tools/license-issuer/cli.py` | yes | 5866 | 118 | `c67948c74d71f277` |
| `tools/license-issuer/gui.py` | yes | 11204 | 207 | `a11efc86a016e9cb` |
| `tools/license-issuer/license_issuer.py` | yes | 5380 | 153 | `07d00689557d5e45` |
| `tools/license-issuer/license-issuer.html` | yes | 17087 | 329 | `d8b39349bf86a2f3` |
| `tools/license-issuer/nacl-fast.js` | yes | 61966 | 2391 | `6bcd37a3b20dce91` |
| `tools/license-issuer/README.md` | yes | 10980 | 163 | `0f02de19eb87fb13` |
| `backend/app/license.py` | yes | 10647 | 296 | `451c1f2a9d1509b6` |
| `backend/app/services/license_service.py` | yes | 9806 | 276 | `3ff96fe555a924d0` |

## Launchers (extracted from `build.ps1`, the only source of `run-*.bat`)

| launcher | bytes | sha256 prefix |
|---|---|---|
| `runGuiBat` | 591 | `9887db204d4dc836` |
| `runCliBat` | 334 | `b345a6a65c851da5` |
| `runHtmlBat` | 1041 | `ef9ae7255409b7f7` |

- committed `.bat` files in the repository: none
- `cryptography` used by the harness: 50.0.1
- backend fingerprint of the ephemeral test key: SHA256:<redacted>... (redacted)
- emulated runner listen addresses: None

## Redaction

private keys, public keys, signatures and license bodies never appear; only fingerprints, lengths and booleans. the test license uses the synthetic client name 'Синтетический Пилот (синтетическое тестовое значение)'; no real client name is used or stored
