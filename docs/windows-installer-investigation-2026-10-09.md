# Windows installer investigation: 2026-10-09

## Handoff scope

This branch publishes the previously uncommitted Windows installer changes
on top of `3e90cd58486a830e443b4f1d9a8eac2f4d51425a` (PR #52 merge).
The runtime failure is NOT established as fixed. Investigate the current
source and distinguish source-level mock tests from installed EXE behavior.

Included changes:

- `Install.psm1`: restore the runtime license public key and write `pilot.env`
  before Compose in the existing-install/same-release path.
- `Update.psm1`: restore the public key before generating the update environment.
- `Secrets.psm1`: read optional `pilot_created` through
  `Get-HrmInstallRecordField`, avoiding StrictMode property access failures.
- `Snapshot.psm1`: reuse a verified previous snapshot before rejecting files
  already replaced by the new installer.
- `Tray.psm1`: truncate NotifyIcon tooltip before assigning it (63-character limit).
- `installer.iss`: mandatory tray/engine startup; wait for the install engine.
- Regression/static tests and `installer/Test-Setup.cmd` / `Test-Setup.ps1`.

## Observed failures

The following is a curated summary of local logs, not a new reproduction:

| Engine log | Time | Observation |
| --- | --- | --- |
| `setup-engine-20261009-101808.log` | 10:18:29 | `PropertyNotFoundStrict` for `pilot_created` after environment generation moved before Compose. Current source has a guarded field read; embedded EXE modules were not compared. |
| `setup-engine-20261009-105625.log` | 10:56:43 | Compose rejected empty `HRM_LICENSE_PUBLIC_KEY` for backend, backup and worker. Root cause remains unknown. |
| `setup-engine-20261009-115648.log` | 11:57:05 | Docker Desktop Linux API named pipe `dockerDesktopLinuxEngine` unavailable. This is a separate infrastructure blocker, not proof of a license recovery failure or success. |

The last run used `Test-Setup` against the local 0.15.0 installer.
Its SHA256, measured during this handoff, is
`7e0c1f48815fc795ddf9d393540291c3f638ea512956629c1a163a71d6a0abf7`.
This identifies a local artifact only; it does not prove that the EXE contains
the current working-tree modules. The EXE is not committed or uploaded here.

## Investigation request

1. Trace both ordinary Setup and `Test-Setup` through `installer/installer.iss`,
   `installer/build.ps1`, `infra/windows/engine/Install.psm1`, `Update.psm1`,
   `Secrets.psm1` and `Compose.psm1`.
2. Establish the order: trusted public-key recovery, environment generation,
   every first Compose call (including status and backup), and any later rewrite.
3. Check all public-key source/fallback paths and error handling. Determine why
   an empty value can reach Compose, rather than assuming the latest patch works.
4. Add focused regression tests for the confirmed cause, including legacy state
   without `pilot_created`, missing runtime public-key file, same-release retry,
   and update retry as applicable. Prefer existing test harness and conventions.
5. Apply a minimal fix and run `infra/windows/tests/run-tests.ps1` using Windows
   PowerShell 5.1. Record test results and remaining live-test limitations.
6. On the original Windows host, compare embedded/installed EXE modules with
   source, ensure Docker Desktop Linux daemon is available, and reproduce both
   normal EXE and `Test-Setup.cmd` paths. A remote agent without that artifact or
   host must explicitly mark these checks unavailable, not claim success.

## Evidence boundaries

Raw logs, `pilot.env`, secret stores, private signing/license keys, databases,
backups and generated installer artifacts are deliberately not published.
The original local installation and StateDir are not accessible to a GitHub-only
agent. Use curated observations above for initial analysis; do not delete or
reset user state to obtain a clean test. Never print secret values while checking
environment files. The tracked license verification public key is not a secret.

Earlier context reported 211 passing tests and an installer/manifest hash match.
Those historical results do not establish current embedded module freshness or
successful live installation.

Fresh Windows PowerShell 5.1 validation on 2026-10-09 completed all suites with
one reported failure: the supervisor uniqueness test could not acquire its first
lock (`supervisor.tests.ps1`, line 123). The cause was not investigated in this
publication task. Do not report this run as fully passing. `git diff --check`
passed before publication; no live installation or EXE rebuild was performed.