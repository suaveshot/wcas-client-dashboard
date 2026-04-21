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

LOG=/var/log/wcas-backup.log
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Load .env SAFELY  -  do not `source` because values may contain shell meta-chars.
# Parse line-by-line, skip comments and blanks, export KEY=VALUE literally.
if [ -f "${ROOT_DIR}/.env" ]; then
  while IFS='=' read -r key value; do
    case "$key" in
      ''|'#'*) continue ;;
    esac
    export "$key=$value"
  done < <(grep -E '^[A-Z_][A-Z0-9_]*=' "${ROOT_DIR}/.env")
fi

: "${BACKUP_GPG_RECIPIENT:?BACKUP_GPG_RECIPIENT must be set in .env}"
: "${BACKUP_REMOTE_HOST:?BACKUP_REMOTE_HOST must be set in .env}"
: "${BACKUP_REMOTE_PATH:?BACKUP_REMOTE_PATH must be set in .env}"

TS=$(date -u +%Y%m%dT%H%M%SZ)
TMPDIR=$(mktemp -d)
ARCHIVE="${TMPDIR}/wcas-tenants-${TS}.tar.gz"
ENCRYPTED="${ARCHIVE}.gpg"

cleanup() { rm -rf "${TMPDIR}"; }
trap cleanup EXIT
trap 'echo "[$(date -u +%FT%TZ)] backup FAILED at line $LINENO exit $?" >> "${LOG}"' ERR

# Change into /opt so tar paths are relative  -  --exclude works with relative patterns.
cd /opt

# Tar tenant data only. The dashboard_app tree lives elsewhere (via git clone
# + docker bind mount). If someone ever co-locates it here, exclude it.
tar --exclude='wc-solns/dashboard_app' \
    -czf "${ARCHIVE}" \
    wc-solns/

# Encrypt with GPG (symmetric or public-key, depending on recipient config).
gpg --batch --yes --trust-model always \
    --recipient "${BACKUP_GPG_RECIPIENT}" \
    --output "${ENCRYPTED}" \
    --encrypt "${ARCHIVE}"

# Ship to secondary storage.
scp -q "${ENCRYPTED}" "${BACKUP_REMOTE_HOST}:${BACKUP_REMOTE_PATH}/"

# Retention: delete anything older than 14 days on the remote.
ssh -q "${BACKUP_REMOTE_HOST}" \
    "find ${BACKUP_REMOTE_PATH} -name 'wcas-tenants-*.tar.gz.gpg' -mtime +14 -delete"

echo "[$(date -u +%FT%TZ)] backup ok: $(basename "${ENCRYPTED}")" >> "${LOG}"
