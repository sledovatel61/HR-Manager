# First-run claim investigation (2026-10-19)

Status: **NO-GO**. Direct claim and a dedicated browser listener are implemented
locally; rebuilt-image and fresh-install runtime acceptance remain unverified.

## Verified evidence

- Reviewer instructions are in commit `199d7f3`, but its application tree is
  substantially older than the tested installer source `6ae189a`. Use the
  instructions as requirements, not that commit as the installer baseline.
- Running pilot backend settings: environment `pilot`, exchange token configured,
  length 64. The host pilot.env also contains an exchange token of length 64.
  Equality was not checked; no token values were printed.
- Running pilot nginx overwrites X-Real-IP with `$remote_addr`. This agrees with
  `6ae189a/frontend/nginx.conf`. The older reviewer tree instead hardcodes
  `127.0.0.1`; that setting must not be restored as a general proxy fix.
- Backend `loopback_client_ok` rejects both disabled setup and non-loopback
  addresses. Thus HTTP 404 does **not** uniquely identify a missing token.
- Pilot PostgreSQL storage is separate from development storage. The pilot
  volume was created on September 10 and contains one user created that day.
  The present pilot is not an empty-install acceptance environment.

## Changes in this patch

- HTTP 404 immediately fails with a configuration/proxy diagnostic, instead of
  repeating the same request twelve times.
- Empty/whitespace and retired exchange tokens fail closed instead of being
  treated as proof that an owner exists.
- Regression tests check the immediate failure, absence of owner confirmation,
  absence of a setup URL, and no HTTP call for unusable tokens.
- Existing HTTP 409 handling and transient retry behavior are preserved.

## Validation

Windows PowerShell 5.1, `infra/windows/tests/run-tests.ps1`:

- Baseline `6ae189a`: 348 passing tests, process exit 0.
- With this patch: 350 passing tests (all twelve suites).
- `git diff --check`: clean.

No running stack was restarted, no credentials were changed, and no Docker
volume was deleted. No installer was built from the older reviewer tree.

## Still required

### Follow-up to reviewer commit 36e5318 (2026-10-19)

Fetched and read the replacement root-cause instruction. Working changes now
publish backend on `127.0.0.1:${HRM_PILOT_CLAIM_PORT:-18001}:8000`, persist the
selected claim port in installed.json, write it to pilot.env, and send claim
directly to `/setup/owner/claim` (the `/api` prefix belongs to nginx, not FastAPI).
Preflight rejects a conflicting port and only permits an occupied port when
Docker confirms this project's backend owns that exact loopback publication.
nginx and backend loopback checks are unchanged.

Start-HrmFirstRun reads the secret map before generation: missing/empty and
legacy 32-hex values cause env regeneration, compose up and readiness; 64-hex
values remain unchanged; whitespace, retired and malformed values fail closed.
Regression cases cover these states, preservation of data keys, and port
persistence. Windows PowerShell 5.1.26100.9711: 353 tests passed (visible runner
summary in the private first-run-direct-ps51.log). The initial pwsh 7.4.6 run
exposed two supervisor date-parsing failures; do not report both hosts green.

**Additional blocking discovery:** claim alone is not the complete flow.
backend/app/routers/setup.py calls loopback_client_ok for preview (line 332)
and redeem (line 361), too. frontend/src/api.ts sends those requests through
the ordinary nginx API path. Therefore a successful direct claim still leaves
the browser setup blocked by the same source-address check. A secure browser
transport is required before installer acceptance; do not remove those checks,
hardcode loopback in the shared nginx, or claim owner creation is fixed.

No installer build, live stack changes, volume changes or owner creation were
performed in this follow-up. NO-GO remains. The existing Setup.exe does not
contain these changes. The items below remain acceptance requirements (token
classification has now been implemented but still needs runtime acceptance).

1. Trace proxy source-address handling in a separate project and new volumes;
   retain spoof-resistant behavior for non-loopback/LAN clients. Do not trust an
   arbitrary client-supplied X-Real-IP or label all clients as loopback.
2. Complete token-state classification around the legacy 32-hex repair in
   Start-HrmFirstRun. Get-HrmSecret can generate missing values; the claim-level
   empty-token guard alone is not a complete missing-state recovery fix.
3. Use an isolated project, state directory, image tags, and an unused host port.
   Changing StateDir alone is not isolation; neither is reusing port 8081.
4. Build an installer with recorded provenance and hashes, verify automatic
   first-run browser opening, create the owner only through the normal screen,
   and verify retained-data behavior without resetting any existing owner.
5. Return to snapshot recovery acceptance only after this baseline is verified.

## Browser transport follow-up to ce8eb72

- Public nginx port 8080 retains `X-Real-IP $remote_addr`. A separate server
  on container port 8081 overwrites IP headers with loopback, based on its
  dedicated loopback-only host publication, not any client assertion.
- Pilot publication is `127.0.0.1:${HRM_PILOT_TRUST_PORT:-18002}:8081`.
  Default 18002 deliberately avoids existing host ports 8080/8081. Never
  publish this container listener on LAN, including in custom deployments.
- The engine persists `trust_port`, writes it to pilot.env, rejects invalid
  and colliding settings, checks occupied-port ownership via the frontend's
  exact Docker binding, and opens the ticket fragment on that origin.
- Structural regression checks both server blocks and loopback publication;
  backend regression rejects LAN/Docker-gateway markers even with forged
  X-Forwarded-For. This is not yet a live remote-client network test.
- pwsh JSON date conversion caused the two supervisor failures: ISO values
  became DateTime objects, then culture-dependent strings. The shared JSON
  reader now normalizes DateTime values back to round-trip ISO strings.
- Latest Windows PowerShell 5.1 run: **354 passed, exit 0**, recorded in private
  `trusted-final-ps51.log`. Final pwsh 7.4.6 run: **354 passed**, confirmed by
  the completed runner summary in `trusted-final-pwsh.log`.
- Native Windows backend pytest collection is blocked by the application's
  POSIX `fcntl` dependency. Do not describe the new backend tests as passed;
  run them in the project's Linux CI/container environment.
- No images or installer rebuilt; no live stack, volumes or credentials changed.
  No claim of browser navigation or owner creation. NO-GO remains.