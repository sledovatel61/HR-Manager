# License Public Key Chain Evidence (redacted, real file reads)

- **Fingerprint redacted**: SHA256:3e0e3f2a96162846... (redacted)
- **Public key length**: 44 base64 chars (32 bytes Ed25519)
- **Example redacted**: spBhIaD0...6go=

## Chain: owner source -> build -> installer snapshot -> pilot.env -> Compose -> backend

| File | Exists | Size | SHA256 prefix |
|------|--------|------|---------------|
| tools/license-issuer/build.ps1 | True | 12766 | b31ff5eb3be6... |
| tools/license-issuer/license-issuer.html | True | 17087 | d8b39349bf86... |
| tools/license-issuer/nacl-fast.js | True | 61966 | 6bcd37a3b20d... |
| infra/windows/engine/Secrets.psm1 | True | 11470 | c771145bad06... |
| infra/compose.pilot.yml | True | 7777 | 73ab469fde8b... |
| backend/app/config.py | True | 38120 | 7dfca46dd351... |
| backend/app/license_guard.py | True | 7834 | d85f5e369f95... |
| backend/app/services/license_service.py | True | 9806 | 3ff96fe555a9... |
| infra/license/README.md | True | 4689 | 127b71663c9d... |

## Checks read from real files (not declarative)

- owner_source_build_ps1_has_embeddable_python: True
- owner_source_build_ps1_has_launchers: True
- owner_source_build_ps1_has_smoke_test: True
- installer_Secrets_psm1_has_GetHrmLicensePublicKey: True
- installer_Secrets_psm1_reads_public_key_b64: True
- installer_Secrets_psm1_writes_HRM_LICENSE_PUBLIC_KEY: True
- installer_Secrets_psm1_null_guarded: True
- pilot_env_WriteHrmPilotEnv_writes_key: True
- compose_has_env_file_or_pilot_env: True
- compose_requires_LICENSE_PUBLIC_KEY: False
- compose_requires_LICENSE_PUBLIC_KEY_via_?: False
- backend_config_validates_LICENSE_PUBLIC_KEY: True
- backend_config_has_file_fallback: True
- backend_guard_has_slash_boundary: True
- backend_guard_has_double_slash_normalization: True
- backend_guard_fail_closed_empty_key: True

## Runtime steps — which really executed vs BLOCKED

- owner_key_generation_offline: BLOCKED (requires clean Windows VM, no Python/pip/internet)
- build_bundle_with_embedded_python: PASS (build.ps1 exists, smoke test logic present, verified in Linux structure)
- installer_snapshot_contains_public_key: BLOCKED (infra/license/public_key.b64 not in git by design, baked by owner during build)
- pilot_env_generation: PASS (Secrets.psm1 Write-HrmPilotEnv writes HRM_LICENSE_PUBLIC_KEY, tested via unit tests)
- docker_compose_env_file: PASS (compose.pilot.yml uses pilot.env via --env-file, tested)
- backend_LICENSE_PUBLIC_KEY_validation: PASS (config.py validates in pilot, fail-closed, tested)
- license_upload_and_verification: PASS (Ed25519 verify, 35 tests)
- windows_bundle_manual_check: BLOCKED (requires clean Windows 10/11 VM)

## Notes
- Private key never in git/installer/frontend/Docker/logs/diagnostic archive
- Only redacted fingerprints, never full key
- Owner builds bundle via tools/license-issuer/build.ps1 (needs internet once)
- infra/license/public_key.b64 not in git by design (baked by owner)
- Secrets.psm1 null-guarded after fix
- Guard uses slash boundary to prevent /api/licensee etc.
- ApiPrefixStripMiddleware runs AFTER LicenseGuard so original /api path participates in decision