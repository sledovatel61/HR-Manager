# License public-key chain — evidence (generated from real files)

- generated: 2026-09-24T06:10:33Z
- generator: `review-artifacts/gen_license_chain_evidence.py (reads real files; CI statuses only from imported artifacts)`
- CI import: {"imported": false}

## Chain

1. owner PC: tools/license-issuer → private key (owner only) + public_key.b64
2. release: infra/license/public_key.b64 (baked by owner, not in git)
3. engine: Secrets.psm1 Get-HrmLicensePublicKey → Write-HrmPilotEnv → pilot.env HRM_LICENSE_PUBLIC_KEY (last line; empty when missing)
4. compose: --env-file pilot.env → ${HRM_LICENSE_PUBLIC_KEY:?} → LICENSE_PUBLIC_KEY in backend, worker, backup (no env_file directive)
5. backend image: no infra/ inside → env is the only path; app.config.Settings fail-closed in APP_ENV=pilot
6. guard: deny by default — everything outside the recovery allowlist needs a valid license

## Files (real reads)

| file | exists | size | lines | sha256 prefix |
|---|---|---|---|---|
| `tools/license-issuer/build.ps1` | yes | 12766 | 293 | `b31ff5eb3be6...` |
| `tools/license-issuer/license-issuer.html` | yes | 17087 | 329 | `d8b39349bf86...` |
| `tools/license-issuer/nacl-fast.js` | yes | 61966 | 2391 | `6bcd37a3b20d...` |
| `tools/license-issuer/cli.py` | yes | 5866 | 118 | `c67948c74d71...` |
| `tools/license-issuer/license_issuer.py` | yes | 5380 | 153 | `07d00689557d...` |
| `infra/windows/engine/Secrets.psm1` | yes | 11470 | 233 | `c771145bad06...` |
| `infra/compose.pilot.yml` | yes | 9324 | 179 | `eab4f81abd05...` |
| `infra/compose.prod.yml` | yes | 8436 | 161 | `404369062acf...` |
| `infra/docker-compose.yml` | yes | 7059 | 191 | `0fee08e27a23...` |
| `infra/scripts/check_env.sh` | yes | 5413 | 128 | `5deb7172be69...` |
| `infra/scripts/compose_pilot_license_chain.py` | yes | 22598 | 566 | `e79985241310...` |
| `backend/Dockerfile` | yes | 804 | 28 | `a2eaa1386ed3...` |
| `backend/app/config.py` | yes | 38120 | 722 | `7dfca46dd351...` |
| `backend/app/license_guard.py` | yes | 6888 | 183 | `aa196a5b191c...` |
| `backend/tests/test_pilot_overlay.py` | yes | 9353 | 238 | `a4f1bc7a3f09...` |
| `backend/tests/test_production_overlay.py` | yes | 22868 | 531 | `6460fe20fce7...` |
| `backend/tests/test_compose_license_chain.py` | yes | 13645 | 336 | `e44a77d28445...` |
| `backend/tests/test_license_middleware_comprehensive.py` | yes | 19485 | 554 | `e4dc2f5421a8...` |
| `infra/windows/tests/engine.tests.ps1` | yes | 42813 | 678 | `bc224d6d6bae...` |
| `infra/windows/tests/static.tests.ps1` | yes | 20855 | 286 | `243be428cc51...` |
| `.github/workflows/ci.yml` | yes | 48645 | 818 | `6ada396906e5...` |

## Checks (computed)

| check | value |
|---|---|
| `owner_source_build_ps1_has_embeddable_python` | true |
| `owner_source_build_ps1_has_launchers` | true |
| `owner_source_build_ps1_has_smoke_test` | true |
| `installer_Secrets_psm1_has_GetHrmLicensePublicKey` | true |
| `installer_Secrets_psm1_reads_public_key_b64` | true |
| `installer_Secrets_psm1_writes_HRM_LICENSE_PUBLIC_KEY` | true |
| `installer_Secrets_psm1_license_line_is_last` | true |
| `installer_Secrets_psm1_empty_key_written_empty_not_omitted` | true |
| `compose_pilot_uses_env_file_interpolation` | true |
| `compose_pilot_has_no_service_env_file_directive` | true |
| `compose_requires_LICENSE_PUBLIC_KEY` | true |
| `compose_requires_LICENSE_PUBLIC_KEY_via_?` | true |
| `compose_pilot_services_with_mapping` | ["backend", "backup", "worker"] |
| `compose_pilot_has_no_private_key_variable` | true |
| `compose_prod_requires_LICENSE_PUBLIC_KEY_via_?` | true |
| `check_env_sh_validates_LICENSE_PUBLIC_KEY` | true |
| `backend_image_does_not_contain_infra_license_dir` | true |
| `backend_config_validates_LICENSE_PUBLIC_KEY` | true |
| `backend_config_has_file_fallback` | true |
| `backend_guard_deny_by_default` | true |
| `backend_guard_has_slash_boundary` | true |
| `backend_guard_has_double_slash_normalization` | true |
| `backend_guard_fail_closed_empty_key` | true |
| `backend_guard_allows_ops_metrics_diagnostics` | true |
| `tests_pilot_overlay_require_mapping_for_backend_worker_backup` | true |
| `tests_pilot_overlay_forbid_env_file_directive` | true |
| `tests_chain_script_env_lines_synced_with_Secrets_psm1` | true |
| `tests_settings_accept_overlay_env_and_keep_fingerprint` | true |
| `tests_windows_engine_pilot_env_fingerprint_case` | true |
| `tests_windows_static_overlay_mapping_case` | true |
| `ci_stack_job_runs_compose_pilot_license_chain` | true |
| `ci_uploads_redacted_chain_artifact` | true |
| `ci_prod_overlay_negative_check_without_key` | true |

## Runtime steps

| step | status |
|---|---|
| `owner_key_generation_offline_windows` | BLOCKED (requires clean Windows 10/11 VM without Python/internet) |
| `issuer_cli_offline_linux` | MANUAL PASS, not CI-verified (Linux sandbox, this session: cli.py gen-keypair → issue → verify, offline; see final-verdict.md) |
| `build_bundle_with_embedded_python` | PASS (static: build.ps1 structure) / BLOCKED (runtime on clean Windows VM) |
| `installer_snapshot_contains_public_key` | BLOCKED (infra/license/public_key.b64 is not in git by design; owner bakes it) |
| `pilot_env_generation_real_engine_writer` | NOT RUN (no CI artifact imported yet — see ci-run-status.json) |
| `docker_compose_config_with_key_resolved_env` | NOT RUN (no CI artifact imported yet — see ci-run-status.json) |
| `docker_compose_config_without_key_refused` | NOT RUN (no CI artifact imported yet — see ci-run-status.json) |
| `backend_settings_inside_pilot_images` | NOT RUN (no CI artifact imported yet — see ci-run-status.json) |
| `backend_LICENSE_PUBLIC_KEY_validation` | PASS (config.py fail-closed in pilot/production; pytest) |
| `license_upload_and_verification` | PASS (Ed25519 verify; pytest license suites) |
| `windows_bundle_manual_check` | BLOCKED (requires clean Windows 10/11 VM) |

> Private key never in git/installer/frontend/Docker/logs. Only redacted fingerprints. Compose/runtime steps are PASS only when an imported CI artifact says so.
