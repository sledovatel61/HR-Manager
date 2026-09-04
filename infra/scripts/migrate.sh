#!/usr/bin/env bash
# One-shot Alembic migration job for deployments.
#
# Migrations NEVER run automatically in the production backend container
# (the production overlay overrides the image CMD with plain uvicorn).
# Operators (and the deploy pipeline) run this script BEFORE switching
# traffic so the schema is at the expected revision first:
#
#   infra/scripts/migrate.sh up        # apply migrations (the deploy gate)
#   infra/scripts/migrate.sh check     # fail if the schema lags alembic head
#   infra/scripts/migrate.sh current   # show the current revision
#
# Concurrency guard: backend/alembic/env.py takes a PostgreSQL advisory
# transaction lock (pg_advisory_xact_lock) for the whole migration run, so
# two overlapping migration jobs serialize instead of racing — the second
# runner waits, then observes that head is already applied.
#
# There is intentionally NO `downgrade` subcommand: automatic production
# downgrades are forbidden. Rollback strategy is documented in
# docs/ARCHITECTURE.md (code rollback vs schema rollback, restore-forward).
set -euo pipefail

cd "$(dirname "$0")/../.."

COMPOSE=(docker compose -f infra/docker-compose.yml -f infra/compose.prod.yml)

case "${1:-up}" in
  up)
    "${COMPOSE[@]}" run --rm backend alembic upgrade head
    ;;
  check)
    "${COMPOSE[@]}" run --rm backend alembic check
    ;;
  current)
    "${COMPOSE[@]}" run --rm backend alembic current
    ;;
  history)
    "${COMPOSE[@]}" run --rm backend alembic history
    ;;
  downgrade | down)
    echo "error: automatic production downgrade is not supported." >&2
    echo "See docs/ARCHITECTURE.md for the documented rollback strategy" >&2
    echo "(code rollback; schema downgrade only with an explicit safe plan)." >&2
    exit 2
    ;;
  *)
    echo "usage: $0 [up|check|current|history|downgrade]" >&2
    exit 2
    ;;
esac
