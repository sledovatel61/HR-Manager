# Offline issuer — evidence from automated Linux checks (redacted)

- generated: 2026-09-24T07:41:00Z
- reviewed commit: `e59aa5b7a3df49b61a8b7c599601bfbd4e9b2784`
- tool: `review-artifacts/gen_issuer_offline_evidence.py` (re-runs every check below)
- host: Linux review sandbox — **not** the owner's Windows VM

> This file proves what could be executed without Windows. It is **not** the clean-Windows
> issuer check: `run-gui.bat`, `run-html.bat`, the Tkinter GUI and the bundle build remain
> `NOT RUN` (see `windows-issuer-bundle-check.md`). No `GO` may be derived from it.

## Summary: {"PASS": 30, "INFO": 2, "GAP": 8, "NOT RUN": 8}

| step | status | detail |
|---|---|---|
| `issuer_sources_of_reviewed_commit` | PASS | extracted from e59aa5b into a temp dir outside the repo |
| `python_issuer_has_no_network_primitives` | PASS | 3 files scanned (cli.py, gui.py, license_issuer.py): no socket/urllib/requests/subprocess/webbrowser usage |
| `html_has_no_network_apis` | PASS | none of ['fetch(', 'XMLHttpRequest', 'WebSocket', 'EventSource', 'sendBeacon', 'importScripts'] appear in license-issuer.html |
| `html_loads_no_external_resources` | PASS | only local <script src="nacl-fast.js">; no http(s) src/href/@import/url() in the page |
| `html_url_strings_are_text_only` | INFO | 4 absolute URL(s) appear in license-issuer.html, all inside human-readable text (jsdelivr fallback hint / documentation), none as a resource reference |
| `nacl_fast_js_has_no_network_apis` | PASS | nacl-fast.js (61966 chars, 2391 lines) contains no fetch/XHR/WebSocket/HTTP access |
| `no_bat_files_committed` | PASS | no run-gui.bat / run-html.bat anywhere in the reviewed commit: they exist only as PowerShell here-strings inside build.ps1 and are written by build.ps1 at build time |
| `run_gui_bat_contract` | PASS | run-gui.bat (from build.ps1): bundled %~dp0..\python\python.exe, gui.py, fail-closed exit /b 1 when the bundle is incomplete (570 bytes) |
| `run_html_bat_contract` | PASS | run-html.bat (from build.ps1, 738 bytes): bundled python -m http.server on port 8765 serving the bundle dir, then opens http://localhost:8765/license-issuer.html |
| `run_html_bat_autonomy_contradiction` | GAP | run-html.bat silently falls back to a system 'python' when the bundled interpreter is missing, while the same file's comment, HOWTO.txt and README promise 'no system Python needed'; run-gui.bat/run-cli.bat fail closed instead. On a clean Windows VM without Python this yields a confusing 'python is not recognized' error instead of the clear message. |
| `run_html_bat_opens_browser_before_server` | GAP | run-html.bat opens the browser before starting the HTTP server, and does not check the port: a slow start or an occupied port 8765 shows 'site can't be reached' even though the bundle is fine |
| `run_html_bat_binds_all_interfaces` | GAP | run-html.bat runs `python -m http.server 8765` without -b: http.server binds 0.0.0.0, so while the window is open the bundle directory (and any key file the owner saved next to it) is reachable from the LAN. Windows firewall may also prompt. Passing -b 127.0.0.1 fixes it. |
| `no_key_material_committed` | PASS | 525 tracked files: no *.hrmlicense, no infra/license/public_key.b64, no keys/ directory, no 64-hex literal in tools/license-issuer/*.py |
| `gitignore_does_not_cover_owner_material` | GAP | .gitignore has no entry for keys/, private_key.hex or *.hrmlicense. build.ps1 step 5 runs `cli.py gen-keypair` without --out-dir, so the maintainer's smoke test writes keys\private_key.hex relative to the current working directory - inside the git work tree when build.ps1 is started from the repo root - and `git add -A` would stage a private key. |
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
| `emulated_runner_serves_page` | PASS | python -m http.server 8791 --directory <bundle>: GET /license-issuer.html -> HTTP 200, 17087 bytes, byte-identical to the file: True. Emulated with the host Linux Python 3.11.2 (cryptography 50.0.1); the Windows launcher run-html.bat itself was NOT executed |
| `http_server_binds_all_interfaces_measured` | GAP | the runner's `python -m http.server` listens on 0.0.0.0 (measured on this host): the bundle directory is reachable from the LAN for as long as the window is open |
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

## Files of the reviewed commit (hashes of what was tested)

| file | exists | bytes | lines | sha256 prefix |
|---|---|---|---|---|
| `tools/license-issuer/build.ps1` | yes | 12766 | 293 | `b31ff5eb3be672a0` |
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
| `runGuiBat` | 570 | `ea7d76436a6a756c` |
| `runCliBat` | 334 | `b345a6a65c851da5` |
| `runHtmlBat` | 738 | `0d836f98b025edb3` |

- committed `.bat` files in the repository: none
- `cryptography` used by the harness: 50.0.1
- backend fingerprint of the ephemeral test key: SHA256:c257e7746a94362b... (redacted)
- emulated runner listen addresses: ['0.0.0.0']

## Redaction

private keys, public keys, signatures and license bodies never appear; only fingerprints, lengths and booleans. the test license uses the synthetic client name 'Синтетический Пилот (синтетическое тестовое значение)'; no real client name is used or stored
