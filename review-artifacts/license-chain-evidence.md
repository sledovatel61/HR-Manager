# License Public Key Chain Evidence (redacted)

- **Fingerprint**: SHA256:31c719faf68d0cb2... (redacted)
- **Public key length**: 44 base64 chars (32 bytes Ed25519)
- **Example redacted**: MGMg3zxP...7hM=

## Chain: owner source -> build -> installer snapshot -> pilot.env -> Docker Compose -> backend

| Step | File | Exists | SHA256 prefix | Notes |
|------|------|--------|---------------|-------|
| tools/license-issuer/build.ps1 | True | 12766 bytes | b31ff5eb3be6... | |
| tools/license-issuer/license-issuer.html | True | 17087 bytes | d8b39349bf86... | |
| tools/license-issuer/nacl-fast.js | True | 61966 bytes | 6bcd37a3b20d... | |
| infra/windows/engine/Secrets.psm1 | True | 11470 bytes | c771145bad06... | |
| infra/compose.pilot.yml | True | 7777 bytes | 73ab469fde8b... | |
| backend/app/config.py | True | 38120 bytes | 7dfca46dd351... | |
| backend/app/license_guard.py | True | 7998 bytes | 2406ba776bff... | |
| backend/app/services/license_service.py | True | 9806 bytes | 3ff96fe555a9... | |

## Checks

- tools/license-issuer/build.ps1: True
- tools/license-issuer/license-issuer.html: True
- tools/license-issuer/nacl-fast.js: True
- infra/windows/engine/Secrets.psm1: True
- infra/compose.pilot.yml: True
- backend/app/config.py: True
- backend/app/license_guard.py: True
- backend/app/services/license_service.py: True
- Secrets.psm1 has Get-HrmLicensePublicKey: True
- Secrets.psm1 writes HRM_LICENSE_PUBLIC_KEY: True
- compose has pilot.env reference: True
- config validates LICENSE_PUBLIC_KEY: True
- guard has _is_allowed with slash boundary: True
- guard blocks prefix bypass: False

## Notes

- Private key never in git/installer/frontend/Docker/logs/diagnostic archive
- Only public key fingerprint shown, never full key
- Owner builds bundle via tools/license-issuer/build.ps1 (requires internet once)
- On clean Windows, bundle works offline without system Python/pip/internet (BLOCKED for now, needs manual VM)
- Secrets.psm1 reads public_key.b64 from infra/license/ and writes to pilot.env HRM_LICENSE_PUBLIC_KEY
- Compose uses --env-file pilot.env
- Backend validates LICENSE_PUBLIC_KEY in pilot/production (fail-closed)
- Guard uses slash boundary to prevent /api/licensee, /api/license-extra, etc.
