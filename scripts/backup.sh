#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# wcas-client-dashboard  -  daily VPS backup
#
# Tars the /opt/wc-solns/ tree (tenant configs, KBs, decisions log), encrypts
# with GPG, and copies to secondary storage. 14-day retention.
#
# Runs under the root crontab on the Hostinger VPS:
#   0 4 * * * /opt/wc-solns/dashboard_app/scripts/backup.sh
#
# Env vars (from .env):
#   BACKUP_GPG_RECIPIENT    -  GPG key to encrypt to
#   BACKUP_REMOTE_HOST      -  ssh host for secondary storage
#   BACKUP_REMOTE_PATH      -  remote directory for backups
# -----------------------------------------------------------------------------

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT_DIR}/.env"

: "${BACKUP_GPG_RECIPIENT:?BACKUP_GPG_RECIPIENT must be set in .env}"
: "${BACKUP_REMOTE_HOST:?BACKUP_REMOTE_HOST must be set in .env}"
: "${BACKUP_REMOTE_PATH:?BACKUP_REMOTE_PATH must be set in .env}"

TS=$(date -u +%Y%m%dT%H%M%SZ)
TMPDIR=$(mktemp -d)
ARCHIVE="${TMPDIR}/wcas-tenants-${TS}.tar.gz"
ENCRYPTED="${ARCHIVE}.gpg"

cleanup() { rm -rf "${TMPDIR}"; }
trap cleanup EXIT

# Tar the tenant data (everything under /opt/wc-solns except dashboard_app itself)
tar --exclude='/opt/wc-solns/dashboard_app' \
    -czf "${ARCHIVE}" \
    /opt/wc-solns/

# Encrypt
gpg --batch --yes --trust-model always \
    --recipient "${BACKUP_GPG_RECIPIENT}" \
    --output "${ENCRYPTED}" \
    --encrypt "${ARCHIVE}"

# Ship
scp -q "${ENCRYPTED}" "${BACKUP_REMOTE_HOST}:${BACKUP_REMOTE_PATH}/"

# Retention: delete anything older than 14 days on the remote
ssh -q "${BACKUP_REMOTE_HOST}" \
    "find ${BACKUP_REMOTE_PATH} -name 'wcas-tenants-*.tar.gz.gpg' -mtime +14 -delete"

echo "[$(date -u +%FT%TZ)] backup ok: ${ENCRYPTED}" >> /var/log/wcas-backup.log
