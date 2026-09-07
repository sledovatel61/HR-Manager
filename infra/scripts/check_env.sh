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
BOOTSTRAP_ADMIN_PASSWORD="${BOOTSTRAP_ADMIN_PASSWORD:-}"
BACKUP_ENABLED="${BACKUP_ENABLED:-}"
BACKUP_KEY_ID="${BACKUP_KEY_ID:-}"
BACKUP_ENC_KEY="${BACKUP_ENC_KEY:-}"
DEV_BACKUP_ENC_KEY="ZGV2LW9ubHktYmFja3VwLWtleS0wMDAwMDAwMDAwMDA="

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
if [ -z "$BOOTSTRAP_ADMIN_PASSWORD" ] || [ "$BOOTSTRAP_ADMIN_PASSWORD" = "AdminAdmin123" ]; then
  fail "BOOTSTRAP_ADMIN_PASSWORD is missing or still set to the development value (AdminAdmin123)"
fi
if [ "${#BOOTSTRAP_ADMIN_PASSWORD}" -lt 12 ]; then
  fail "BOOTSTRAP_ADMIN_PASSWORD must be at least 12 characters long"
fi

# Backup contour (phase 7). By default backup secret problems are WARNINGS:
# the backup service itself refuses to run without a working key (fail-fast
# inside the container, no unencrypted backup is possible). Operators who run
# the backup contour must set BACKUP_ENABLED=true, which turns every backup
# secret problem into a hard failure of this preflight.
backup_problem() {
  if [ "$BACKUP_ENABLED" = "true" ] || [ "$BACKUP_ENABLED" = "1" ]; then
    fail "$*"
  else
    echo "warning: $*" >&2
  fi
}

if [ "$BACKUP_ENABLED" != "false" ] && [ "$BACKUP_ENABLED" != "0" ]; then
  if [ -z "$BACKUP_KEY_ID" ]; then
    backup_problem "BACKUP_KEY_ID is not set; the backup service cannot select an encryption key"
  fi
  if [ -z "$BACKUP_ENC_KEY" ]; then
    backup_problem "BACKUP_ENC_KEY is not set; the backup service cannot encrypt backups"
  elif [ "$BACKUP_ENC_KEY" = "$DEV_BACKUP_ENC_KEY" ]; then
    backup_problem "BACKUP_ENC_KEY is still set to the development-only backup key"
  elif [ "${#BACKUP_ENC_KEY}" -ne 44 ] \
    || ! printf '%s' "$BACKUP_ENC_KEY" | base64 -d >/dev/null 2>&1 \
    || [ "$(printf '%s' "$BACKUP_ENC_KEY" | base64 -d | wc -c)" -ne 32 ]; then
    backup_problem "BACKUP_ENC_KEY must be a base64-encoded 32-byte key (44 characters)"
  fi
fi

# External channels (phase 9). Disabled by default: nothing to check then.
# When a channel is enabled, its configuration must be complete — the
# backend would refuse to start otherwise, so fail early with a clear
# message. Plaintext SMTP in production is a hard failure as well.
TELEGRAM_ENABLED="${TELEGRAM_ENABLED:-false}"
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_BOT_USERNAME="${TELEGRAM_BOT_USERNAME:-}"
SMTP_ENABLED="${SMTP_ENABLED:-false}"
SMTP_HOST="${SMTP_HOST:-}"
SMTP_PORT="${SMTP_PORT:-587}"
SMTP_ENCRYPTION="${SMTP_ENCRYPTION:-starttls}"
SMTP_USERNAME="${SMTP_USERNAME:-}"
SMTP_PASSWORD="${SMTP_PASSWORD:-}"
SMTP_FROM_ADDRESS="${SMTP_FROM_ADDRESS:-}"

if [ "$TELEGRAM_ENABLED" = "true" ] || [ "$TELEGRAM_ENABLED" = "1" ]; then
  if [ -z "$TELEGRAM_BOT_TOKEN" ]; then
    fail "TELEGRAM_ENABLED is true but TELEGRAM_BOT_TOKEN is not set"
  fi
  if [ -z "$TELEGRAM_BOT_USERNAME" ]; then
    fail "TELEGRAM_ENABLED is true but TELEGRAM_BOT_USERNAME is not set"
  fi
fi

if [ "$SMTP_ENABLED" = "true" ] || [ "$SMTP_ENABLED" = "1" ]; then
  if [ -z "$SMTP_HOST" ]; then
    fail "SMTP_ENABLED is true but SMTP_HOST is not set"
  fi
  if [ -z "$SMTP_FROM_ADDRESS" ]; then
    fail "SMTP_ENABLED is true but SMTP_FROM_ADDRESS is not set"
  fi
  if [ "$SMTP_ENCRYPTION" = "none" ]; then
    fail "SMTP_ENCRYPTION=none is forbidden in production (use starttls or tls)"
  fi
  if [ -n "$SMTP_USERNAME" ] && [ -z "$SMTP_PASSWORD" ]; then
    fail "SMTP_USERNAME is set but SMTP_PASSWORD is not set"
  fi
fi

echo "ok: production configuration preflight passed"
