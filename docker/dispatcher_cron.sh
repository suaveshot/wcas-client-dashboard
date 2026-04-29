#!/usr/bin/env bash
# WCAS Phase 2D scheduler dispatcher cron wrapper.
#
# Crontab entry to install on garcia-vps:
#   * * * * * /opt/wc-solns/dashboard_app/docker/dispatcher_cron.sh
#
# Why a wrapper? See memory file mistake_vps_cron_env_inheritance:
# Docker cron does NOT inherit container env. Vars set via
# `docker run -e` or compose `environment:` are visible to PID 1
# but NOT to processes spawned by cron. Sourcing /etc/wcas/dispatcher.env
# here (with `set -a`) is what makes TENANT_ROOT, HEARTBEAT_SHARED_SECRET,
# etc. available to the dispatcher subprocess.
#
# The env file should look like:
#   TENANT_ROOT=/opt/wc-solns
#   HEARTBEAT_SHARED_SECRET=...
#   ANTHROPIC_API_KEY=...
#   DISPATCH_DRY_RUN=0
# and live at /etc/wcas/dispatcher.env (chmod 600, root:root).
#
# Logs land under /var/log/wcas/dispatcher/<UTC-date>.log (rotate via
# logrotate or `find ... -mtime +14 -delete` from a daily cleanup cron).

set -euo pipefail

ENV_FILE="${WCAS_DISPATCHER_ENV:-/etc/wcas/dispatcher.env}"
REPO_DIR="${WCAS_REPO_DIR:-/opt/wc-solns/dashboard_app}"
LOG_DIR="${WCAS_DISPATCHER_LOG_DIR:-/var/log/wcas/dispatcher}"

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date -u +%Y-%m-%d).log"

# Source env for the dispatcher. `set -a` exports every var assigned
# inside the file so they reach the python child. We must do this even
# when the container env "looks" right under `docker exec`, because the
# cron-spawned shell does not inherit it.
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
else
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] WARNING: env file $ENV_FILE not found; running with empty env" >> "$LOG_FILE"
fi

# Ensure TENANT_ROOT is set so dispatch_one does not bail out early.
: "${TENANT_ROOT:=/opt/wc-solns}"
export TENANT_ROOT

cd "$REPO_DIR"

# Activate venv if present. We support both .venv and venv naming.
if [ -f ".venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    . .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    . venv/bin/activate
fi

PY_BIN="${WCAS_PYTHON:-python}"

{
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] tick start (TENANT_ROOT=$TENANT_ROOT)"
    "$PY_BIN" -m wc_solns_pipelines.platform.dispatcher
    rc=$?
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] tick end rc=$rc"
} >> "$LOG_FILE" 2>&1
