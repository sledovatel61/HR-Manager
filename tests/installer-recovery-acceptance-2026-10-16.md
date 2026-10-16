# Windows installer acceptance — 2026-10-16

## Verdict

HOLD: do not approve merge on the basis of this acceptance run. Build and automated tests pass, but the existing installation on port 8081 cannot receive the fix through the standard installer: snapshot guard stops installation before files are replaced. No guard bypass, volume deletion, database reset, or owner password change was performed.

## Build

- Repository: `C:\Users\User\Documents\HR\pr57-purge-private\repo`
- Branch: `pr-src`; HEAD: `97df390ed94bcb2bdbdf1286db8ea624992ad040`.
- Build includes uncommitted changes to Bootstrap.psm1, Secrets.psm1, Windows README and engine tests. HEAD alone does NOT identify these patched bytes.
- Command: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\User\Documents\HR\pr57-purge-private\repo\installer\build.ps1 -Version 0.15.0`
- Inno Setup 6.7.3 downloaded toolchain hash verified.
- Artifact: `C:\Users\User\Documents\HR\pr57-purge-private\repo\installer\output\HR-Manager-Setup-0.15.0.exe`
- Setup SHA256: `a9ad2fc34b23e8b56afa23dfd5458dc960f4759f2836ab8e6dc2835198b9b7a5`.
- Manifest and SHA256SUMS.txt produced next to the executable.
- Local pilot build is unsigned, with no embedded channel trust store and no Docker installer sidecar; not a production release.
- Bootstrap.psm1 and Secrets.psm1 staged copies have identical SHA256 to the modified source files; compiler included the staged files.

## Automated validation

- Full Windows engine suite: 321 tests passed.
- lint-engine.py: structural validation passed, 38 files.
- git diff --check: passed.
- Initial lint invocation had a malformed shell quote; corrected invocation passed.

## Live environment and reproduction

- Existing StateDir: `C:\Users\User\AppData\Local\HRManager`.
- Existing installed record: version 0.15.0, port 8081, pilot_created=false.
- Existing exchange token length is 32; no token values included in this report.
- Docker Desktop was initially stopped. Started the already installed per-user executable at `C:\Users\User\AppData\Local\Programs\DockerDesktop\Docker Desktop.exe`.
- Docker Engine 29.7.2 became available. Existing hr-manager-pilot services became healthy. Named volumes `hr-manager-pilot_pilot_pgdata` and `hr-manager-pilot_pilot_backups` were present.
- Ran the new Setup with `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="C:\Users\User\AppData\Local\Temp\hrm-exchange-recovery-setup.log"`.
- Actual installer exit code: **7**.
- Log: snapshot helper failed, status=failed, reason=files_replaced, run_ok=0, exit=3, stop=1.
- PrepareToInstall stopped before replacement of installed application files. Exchange-token recovery and owner claim were NOT reached.
- Machine-readable evidence: `C:\Users\User\AppData\Local\HRManager\setup-snapshot.json`.
- Previous setup-run.json already recorded an unsuccessful first-run attempt; it is not evidence that the new owner claim ran.

## Required follow-up for implementer

1. Diagnose why the existing installation/previous snapshot is classified as files_replaced and why the standard installer cannot recover it.
2. Provide a safe, documented recovery path that retains a verified rollback snapshot and preserves database password, backup/signing keys and Docker volumes. Do not remove the guard or falsify installed/snapshot metadata merely to pass acceptance.
3. Add a regression test for this real installed-state transition as appropriate.
4. Commit the exchange-token changes and any recovery fix, then rebuild from that exact commit for traceability.
5. Repeat standard Setup recovery on 8081, verify only exchange token is changed while setup is pending, confirm owner claim, password setup and user login.
6. Separately run a genuine clean installation with a new StateDir and an isolated Compose project/volumes. A new StateDir alone does not prove database isolation. This scenario was NOT executed in this run.

## Acceptance not yet established

- Clean installation E2E: not executed.
- Existing-install recovery via Setup: attempted, failed at snapshot guard.
- Live owner claim with repaired token: not reached.
- Password setup and login: not executed.
- Commit/PR/merge: not performed.

The snapshot failure is an observed acceptance blocker, not proof that the exchange-token patch introduced that failure.