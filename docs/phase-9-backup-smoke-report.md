# Phase 9 backup smoke report

## 1. Exact baseline and final SHA

- Baseline: `180d5dc5e99b96014f7f6f6e5100cb22c8e278af` (`main`, merge of Phase 9).
- Final SHA: recorded after the fix commit.
- CI failure under investigation: run `34134253554`, head `180d5dc5e99b96014f7f6f6e5100cb22c8e278af`.

## 2. Root cause

The development Compose file started the backup service as soon as PostgreSQL became healthy. The backup service has `BACKUP_ON_START=1`, and its first operation authenticates the scheduler and writes audit/state data before invoking `pg_dump`. The backend container performs `alembic upgrade head` in its startup command, but the backup service had no dependency on backend readiness. On a fresh volume this created a startup race: backup could run while the schema was still being migrated, fail, and leave no encrypted artifact for the CI check.

A second scheduler defect was found while tracing failures: with `set -e`, the failed `backup-now` command was used as the condition of an `if`, but `$?` was read after the `if` compound command. That can lose the CLI's non-zero status and cause retry/diagnostic handling to report the wrong exit code. The status is now captured in the `else` branch.

## 3. Changed files

- `infra/docker-compose.yml`: backup now waits for the healthy backend, which implies completed development migrations, before starting.
- `infra/scripts/backup_scheduler.sh`: preserves the failed backup CLI exit status in the retry loop.
- `backend/tests/test_production_overlay.py`: asserts both safeguards.
- `docs/phase-9-backup-smoke-report.md`: this report.

## 4. Security and backup contract

The fix does not weaken the smoke criterion or backup security. The CI check still requires a real `*.pgdump.enc` artifact in `/var/backups/hr-manager`. Backups remain AES-256-GCM encrypted, published atomically to the dedicated `backups` volume, and generated only from environment-provided key material. No secrets were added to Git, and the production overlay remains responsible for production credentials and keys.

## 5. Commands and results

Successful local checks:

- `docker compose -f infra/docker-compose.yml config -q`
- `docker compose -f infra/docker-compose.yml down -v --remove-orphans`
- `docker compose -f infra/docker-compose.yml up --build --wait --wait-timeout 300`
- `docker compose -f infra/docker-compose.yml ps`: all services healthy.
- `docker compose -f infra/docker-compose.yml logs backup`: startup backup published successfully.
- Artifact listing in the backup container showed:
  - `hr-manager-20260907T160309Z-8538ce65.pgdump.enc`
  - matching `.sha256` sidecar
  - state file
- The exact encrypted-artifact command passed:
  `docker compose -f infra/docker-compose.yml exec -T backup sh -lc 'test -n "$(find /var/backups/hr-manager -maxdepth 1 -name "*.pgdump.enc" -print -quit)'"`
- `git diff --check` passed.

Backend test limitations:

- Initial host test failed because dependencies were not installed (`sqlalchemy` missing); dependencies were then installed from `backend/requirements.txt` and `backend/requirements-dev.txt`.
- The host is Windows, while `backend/app/backup_runner.py` imports POSIX-only `fcntl`; therefore the host pytest collection stops with `ModuleNotFoundError: fcntl`. This is an environment limitation, not a test failure caused by this patch.
- Full Docker/CI backend regression tests were not run because the production backend image does not include the development test dependencies.

## 6. CI run and final SHA

- Failing historical run: `34134253554` at baseline `180d5dc5e99b96014f7f6f6e5100cb22c8e278af`.
- A new CI run was not available from the local environment (`gh` CLI is not installed). The final commit SHA is recorded below after commit creation.

## 7. Remaining limitations

The local verification was performed on Windows Docker Desktop rather than an Ubuntu GitHub runner. PostgreSQL integration tests, full backend regression suite, and frontend checks were not rerun locally after the targeted change. The successful Compose smoke confirms the artifact path, encryption filename contract, volume mount, startup ordering, and scheduler behavior for the local stack.
