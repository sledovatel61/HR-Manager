#!/usr/bin/env bash
# HR Manager deployment script (roadmap phase 7).
#
# Single-host Docker Compose deployment with a verified release switch:
#
#   preflight  ->  build + tag images  ->  migrate (one-shot, locked)
#   ->  switch traffic to the new release  ->  smoke checks
#   ->  automatic rollback to the previous release on readiness failure
#
# Image tags are the rollback mechanism: every release is built as
# `release-<full-sha>`; `release-current` points at the running release and
# `release-prev` at the previous one (re-tagged before every switch). The
# script never deletes the previous images, so a manual rollback is always
# possible:
#
#   RELEASE_TAG=release-prev docker compose -f infra/docker-compose.yml \
#       -f infra/compose.prod.yml up -d backend frontend backup
#
# Targets
# -------
# `local` (default): runs on this machine / the CI runner (test contour).
# `ssh`: runs this same local script on DEPLOY_HOST via SSH (operator-owned
#   host; SSH key material comes from the environment/CI secrets and is
#   never stored in the repository).
#
# The script intentionally does NOT auto-downgrade the database schema: code
# rollback uses the previous image, schema rollback follows the documented
# restore-forward procedure in docs/ARCHITECTURE.md.
#
# Usage:
#   infra/scripts/deploy.sh --release <full-git-sha> [--target local|ssh] \
#       [--failure-drill] [--skip-migrate]
#
# Exit codes: 0 success, 1 preflight/build/migrate failure, 2 smoke failure
# (after rollback), 3 rollback failure.
set -euo pipefail

cd "$(dirname "$0")/../.."

RELEASE_SHA=""
TARGET="local"
FAILURE_DRILL=0
SKIP_MIGRATE=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --release)
      RELEASE_SHA="$2"
      shift 2
      ;;
    --target)
      TARGET="$2"
      shift 2
      ;;
    --failure-drill)
      FAILURE_DRILL=1
      shift
      ;;
    --skip-migrate)
      SKIP_MIGRATE=1
      shift
      ;;
    *)
      echo "usage: $0 --release <sha> [--target local|ssh] [--failure-drill] [--skip-migrate]" >&2
      exit 2
      ;;
  esac
done

if [ -z "$RELEASE_SHA" ]; then
  echo "error: --release <full-git-sha> is required" >&2
  exit 2
fi

# Compose interpolates RELEASE_SHA from the process environment; export it
# so /ops/status serves the deployed SHA and the smoke check can verify it.
export RELEASE_SHA

if [ "$TARGET" = "ssh" ]; then
  : "${DEPLOY_HOST:?DEPLOY_HOST is required for the ssh target}"
  echo "[deploy] executing on ${DEPLOY_HOST}"
  exec ssh "${DEPLOY_SSH_USER:-root}@${DEPLOY_HOST}" \
    "RELEASE_SHA=${RELEASE_SHA} FAILURE_DRILL=${FAILURE_DRILL} SKIP_MIGRATE=${SKIP_MIGRATE} \
     APP_ENV=${APP_ENV:-production} \
     infra/scripts/deploy.sh --release ${RELEASE_SHA} \
     $([ "$FAILURE_DRILL" = 1 ] && echo --failure-drill) \
     $([ "$SKIP_MIGRATE" = 1 ] && echo --skip-migrate)"
fi

[ "$TARGET" = "local" ] || { echo "error: unknown target '$TARGET'" >&2; exit 2; }

COMPOSE=(docker compose -f infra/docker-compose.yml -f infra/compose.prod.yml)
PREV_TAG=""

app_exec() {
  "${COMPOSE[@]}" exec -T backend python -c "$1"
}

smoke() {
  # No published ports in production: probe from inside the container.
  if ! app_exec "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5); sys.exit(0 if r.status==200 else 1)"; then
    echo "error: /health did not return 200" >&2
    return 1
  fi
  body="$(app_exec "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/ops/status', timeout=5).read().decode())")"
  sha="$(printf '%s' "$body" | python3 -c 'import json,sys; print(json.load(sys.stdin)["release_sha"])')"
  if [ "$sha" != "$RELEASE_SHA" ]; then
    echo "error: release SHA mismatch: expected ${RELEASE_SHA}, got ${sha}" >&2
    return 1
  fi
  return 0
}

rollback() {
  if [ -z "$PREV_TAG" ]; then
    echo "error: no previous release recorded; cannot roll back" >&2
    return 1
  fi
  echo "[deploy] rolling back to ${PREV_TAG}"
  if ! RELEASE_TAG="$PREV_TAG" "${COMPOSE[@]}" up -d --wait --wait-timeout 180 backend frontend backup; then
    echo "error: rollback failed; manual intervention required" >&2
    return 1
  fi
  echo "[deploy] rollback applied; traffic is back on ${PREV_TAG}"
  return 0
}

echo "[deploy] release ${RELEASE_SHA} (target=${TARGET}, failure-drill=${FAILURE_DRILL})"

# 1. Preflight: production secrets before anything else.
bash infra/scripts/check_env.sh

# 2. Build and tag the release images. The previous release stays tagged so
#    rollback is a one-command traffic switch.
if docker image inspect "hr-manager-backend:release-current" >/dev/null 2>&1; then
  docker tag "hr-manager-backend:release-current" "hr-manager-backend:release-prev"
  docker tag "hr-manager-frontend:release-current" "hr-manager-frontend:release-prev" 2>/dev/null || true
  docker tag "hr-manager-backup:release-current" "hr-manager-backup:release-prev" 2>/dev/null || true
  PREV_TAG="release-prev"
fi
RELEASE_TAG="release-${RELEASE_SHA}" "${COMPOSE[@]}" build --pull
docker tag "hr-manager-backend:release-${RELEASE_SHA}" "hr-manager-backend:release-current"
docker tag "hr-manager-frontend:release-${RELEASE_SHA}" "hr-manager-frontend:release-current"
docker tag "hr-manager-backup:release-${RELEASE_SHA}" "hr-manager-backup:release-current"

# 3. Migrations run exactly once, before the traffic switch, guarded by the
#    PostgreSQL advisory lock in alembic/env.py.
if [ "$SKIP_MIGRATE" = 0 ]; then
  infra/scripts/migrate.sh up
fi

# 4. Traffic switch to the verified release (readiness gate included).
if ! RELEASE_TAG="release-${RELEASE_SHA}" "${COMPOSE[@]}" up -d --wait --wait-timeout 180 backend frontend backup; then
  echo "error: new release did not become healthy" >&2
  rollback || exit 3
  exit 2
fi

# 5. Smoke: /health 200 + /ops/status release_sha must match the deployed
#    commit before the release is considered live.
if ! smoke; then
  echo "error: smoke checks failed after the switch" >&2
  rollback || exit 3
  exit 2
fi
echo "[deploy] smoke passed: /health 200 and /ops/status release_sha=${RELEASE_SHA}"

# 6. Failure drill (test contour only): install a deliberately broken
#    release, verify that readiness fails and that the automatic rollback
#    restores the previous release and its smoke checks.
if [ "$FAILURE_DRILL" = 1 ]; then
  echo "[deploy] FAILURE DRILL: installing a broken release to prove rollback"
  printf 'FROM busybox:1.36\nCMD ["false"]\n' \
    | docker build -q -t hr-manager-backend:release-broken - >/dev/null
  if RELEASE_TAG="release-broken" "${COMPOSE[@]}" up -d --wait --wait-timeout 60 backend frontend backup; then
    echo "error: broken release became healthy; the drill is invalid" >&2
    exit 3
  fi
  echo "[deploy] broken release correctly failed readiness"
  if ! rollback; then
    exit 3
  fi
  RELEASE_TAG="$PREV_TAG" "${COMPOSE[@]}" up -d --wait --wait-timeout 180 backend frontend backup
  smoke || { echo "error: smoke failed after drill rollback" >&2; exit 2; }
  echo "[deploy] failure drill passed: traffic restored on ${PREV_TAG}"
fi

# 7. Release notes for the deploy record (CI uploads them as an artifact).
NOTES="/tmp/release-notes-${RELEASE_SHA}.md"
{
  echo "# Release ${RELEASE_SHA}"
  echo
  echo "- Deployed at: $(date -u +%Y-%m-%dT%H:%M:%SZ) UTC"
  echo "- Migration: $([ "$SKIP_MIGRATE" = 0 ] && echo "applied with infra/scripts/migrate.sh up (advisory-lock guarded)" || echo "SKIPPED (--skip-migrate)")"
  echo "- Rollback target (previous release): ${PREV_TAG:-none (first release)}"
  echo "- Smoke: /health 200, /ops/status release_sha matches"
  echo "- Known limitations: single-host Compose switch is a short-stop recreate,"
  echo "  not blue-green with zero downtime; see docs/ARCHITECTURE.md."
} > "$NOTES"
echo "[deploy] release notes written to ${NOTES}"
echo "[deploy] done"
