#!/usr/bin/env bash
# Preflight check for production configuration.
#
# Ensures every secret the production overlay requires is present in the
# environment before `docker compose` is invoked, so that no shell ever
# silently substitutes an empty string or a development default.
#
# Usage:
#   ( set -a; . .env; set +a; infra/scripts/check_env.sh )   # export .env first
#   docker compose -f infra/docker-compose.yml -f infra/compose.prod.yml up -d
set -euo pipefail

fail() {
  echo "error: $*" >&2
  exit 1
}

APP_ENV="${APP_ENV:-}"
SECRET_KEY="${SECRET_KEY:-}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"

if [ "$APP_ENV" != "production" ]; then
  fail 'APP_ENV must be exactly "production" for a production run (got: "'"$APP_ENV"'")'
fi
if [ -z "$SECRET_KEY" ] || [ "$SECRET_KEY" = "dev-only-secret-key-not-for-production" ]; then
  fail "SECRET_KEY is missing or still set to the development value"
fi
if [ "${#SECRET_KEY}" -lt 32 ]; then
  fail "SECRET_KEY must be at least 32 characters long"
fi
if [ -z "$POSTGRES_PASSWORD" ] || [ "$POSTGRES_PASSWORD" = "hr_manager_dev_password" ]; then
  fail "POSTGRES_PASSWORD is missing or still set to the development value"
fi

echo "ok: production configuration preflight passed"
