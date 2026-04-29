#!/usr/bin/env bash
# WCAS daily platform tasks - cron wrapper.
#
# Crontab entry to install on garcia-vps (alongside the every-minute
# dispatcher entry):
#   5 8 * * * /opt/wc-solns/dashboard_app/docker/platform_daily_cron.sh
#
# This runs:
#   * watchdog_digest -- fingerprint-gated daily summary to Sam
#   * promo_lifecycle.sweep_expired_all_tenants -- physically delete
#     promo rows whose expires_at has passed
#
# Same env-sourcing pattern as dispatcher_cron.sh. See
# memory/lessons/mistake_vps_cron_env_inheritance for why we cannot rely
# on the container env being available to cron-spawned processes.

set -euo pipefail

ENV_FILE="${WCAS_DISPATCHER_ENV:-/etc/wcas/dispatcher.env}"
REPO_DIR="${WCAS_REPO_DIR:-/opt/wc-solns/dashboard_app}"
LOG_DIR="${WCAS_DAILY_LOG_DIR:-/var/log/wcas/daily}"

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date -u +%Y-%m-%d).log"

if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
else
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] WARNING: env file $ENV_FILE not found; running with empty env" >> "$LOG_FILE"
fi

: "${TENANT_ROOT:=/opt/wc-solns}"
export TENANT_ROOT

cd "$REPO_DIR"

if [ -f ".venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    . .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    . venv/bin/activate
fi

PY_BIN="${WCAS_PYTHON:-python}"

{
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] daily start (TENANT_ROOT=$TENANT_ROOT)"
    "$PY_BIN" -m wc_solns_pipelines.platform.daily
    rc=$?
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] daily end rc=$rc"
} >> "$LOG_FILE" 2>&1
