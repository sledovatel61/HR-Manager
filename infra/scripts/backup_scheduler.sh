#!/usr/bin/env bash
# HR Manager backup service entrypoint: scheduler + manual operations.
#
# Runs inside the `backup` service container (backend/Dockerfile.backup).
# ALL schedules and timestamps in this script are UTC — logs are stamped in
# UTC and the daily window is interpreted as UTC. Container operators must
# keep TZ=UTC (set by the Compose files) so `date` agrees.
#
# Behaviour
# ---------
# * `scheduler` (default): waits for the daily BACKUP_SCHEDULE_UTC window,
#   runs the encrypted backup with retry/backoff, then a shallow integrity
#   check; runs the restore drill on a separate cadence
#   (BACKUP_DRILL_INTERVAL_HOURS). A health marker file is touched so the
#   container healthcheck reflects the scheduler, not the last backup.
# * `oneshot`: one immediate backup with retries (use `docker compose run`).
# * `check` / `drill` / `list` / `prune`: delegate to `python -m app.cli`.
#
# Guarantees come from the backend runner (app/backup_runner.py), not from
# this script: flock against parallel runs, 0600 staging, encrypted+verified
# publication via os.replace, retention that never deletes the newest
# BACKUP_MIN_COPIES files when a run fails. Exit codes are the runner's
# EXIT_* codes (0 ok, 4 locked, 5 dump, 6 encrypt, 7 verify, 9 drill, ...).
#
# Secrets are read from the environment only and are never echoed; passwords
# travel through process environment, never through command lines.
set -euo pipefail

SCHEDULE_UTC="${BACKUP_SCHEDULE_UTC:-02:00}"
DRILL_INTERVAL_HOURS="${BACKUP_DRILL_INTERVAL_HOURS:-168}"
RETRY_ATTEMPTS="${BACKUP_RETRY_ATTEMPTS:-3}"
BACKOFF_SECONDS="${BACKUP_RETRY_BACKOFF_SECONDS:-300}"
ON_START="${BACKUP_ON_START:-0}"
REASON="${BACKUP_REASON:-scheduled backup}"
MARKER="${BACKUP_HEALTH_MARKER:-/tmp/backup-scheduler-ready}"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

# One scheduled backup with retry/backoff. The same request id is reused for
# every attempt of one window so the audit trail links the attempts.
run_backup_with_retry() {
  local attempt=0 code=1 delay
  local request_id="${BACKUP_REQUEST_ID_PREFIX:-sched}-$(date -u +%Y%m%dT%H%M%SZ)"
  while :; do
    attempt=$((attempt + 1))
    if python -m app.cli backup-now --as-scheduler --reason "$REASON" --request-id "$request_id"; then
      return 0
    fi
    code=$?
    if [ "$attempt" -ge "$RETRY_ATTEMPTS" ]; then
      log "backup failed after ${attempt} attempt(s), last exit code ${code}; giving up"
      return "$code"
    fi
    delay=$((BACKOFF_SECONDS * attempt))
    log "backup attempt ${attempt} failed (exit ${code}); retrying in ${delay}s"
    sleep "$delay"
  done
}

sleep_until() {
  # Sleep until the next occurrence of SCHEDULE_UTC (HH:MM), in UTC, waking up
  # every minute so SIGTERM stays responsive.
  local now_epoch next_epoch remaining chunk
  next_epoch=$(date -u -d "$(date -u +%Y-%m-%d) ${SCHEDULE_UTC}:00" +%s 2>/dev/null) \
    || { log "invalid BACKUP_SCHEDULE_UTC '${SCHEDULE_UTC}' (expected HH:MM)"; exit 2; }
  now_epoch=$(date -u +%s)
  if [ "$next_epoch" -le "$now_epoch" ]; then
    next_epoch=$((next_epoch + 86400))
  fi
  remaining=$((next_epoch - now_epoch))
  log "next backup window at ${SCHEDULE_UTC} UTC (in ${remaining}s)"
  while [ "$remaining" -gt 0 ]; do
    chunk=$((remaining > 60 ? 60 : remaining))
    sleep "$chunk" || true
    remaining=$((remaining - chunk))
  done
}

scheduler() {
  touch "$MARKER"
  log "backup scheduler started: window=${SCHEDULE_UTC} UTC, drill every ${DRILL_INTERVAL_HOURS}h, retries=${RETRY_ATTEMPTS}"
  trap 'log "received signal, shutting down"; exit 0' TERM INT

  if [ "$ON_START" = "1" ]; then
    run_backup_with_retry || log "startup backup failed; continuing (next window will retry)"
  fi

  local last_drill=0 now
  while :; do
    sleep_until
    run_backup_with_retry || true
    python -m app.cli backup-check --as-scheduler || log "backup check reported a problem"
    now=$(date -u +%s)
    if [ "$((now - last_drill))" -ge "$((DRILL_INTERVAL_HOURS * 3600))" ]; then
      if python -m app.cli backup-drill --as-scheduler; then
        last_drill=$(date -u +%s)
      else
        log "restore drill failed; it will be retried after the next backup window"
      fi
    fi
  done
}

case "${1:-scheduler}" in
  scheduler)
    scheduler
    ;;
  oneshot)
    run_backup_with_retry
    ;;
  check)
    python -m app.cli backup-check --deep --as-scheduler
    ;;
  drill)
    python -m app.cli backup-drill --as-scheduler
    ;;
  list)
    python -m app.cli backup-list --as-scheduler
    ;;
  prune)
    python -m app.cli backup-prune --yes --as-scheduler
    ;;
  *)
    echo "usage: $0 [scheduler|oneshot|check|drill|list|prune]" >&2
    exit 2
    ;;
esac
