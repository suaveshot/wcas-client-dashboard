#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# wcas-client-dashboard - VPS first-time bootstrap
#
# Run this ONCE on the Hostinger VPS (srv1568946) to stand up the dashboard
# container, Traefik reverse proxy, and Let's Encrypt cert for
# dashboard.westcoastautomationsolutions.com.
#
# How to run:
#   Paste this script's output into the Hostinger hPanel VPS web terminal, or
#   ssh in and run it directly as root:
#     curl -fsSL https://raw.githubusercontent.com/suaveshot/wcas-client-dashboard/main/scripts/vps-bootstrap.sh | sudo bash
#
# What it does:
#   1. Installs Docker + Docker Compose if missing
#   2. Clones the repo to /opt/wc-solns/dashboard_app
#   3. Creates .env from the template (PROMPTS for secrets interactively)
#   4. Spins up Traefik (reverse proxy + Let's Encrypt auto-cert)
#   5. Starts the dashboard container
#   6. Verifies the /healthz endpoint responds
# -----------------------------------------------------------------------------

set -euo pipefail

DOMAIN="dashboard.westcoastautomationsolutions.com"
REPO="https://github.com/suaveshot/wcas-client-dashboard.git"
INSTALL_DIR="/opt/wc-solns/dashboard_app"
TRAEFIK_DIR="/opt/wc-solns/_traefik"
ACME_EMAIL="sam@westcoastautomationsolutions.com"

log() { printf '\n\033[0;36m==>\033[0m %s\n' "$*"; }
die() { printf '\n\033[0;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run as root (or with sudo)."

# 1. Docker + Compose
if ! command -v docker >/dev/null 2>&1; then
  log "Installing Docker"
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
fi
docker --version
docker compose version || die "Docker Compose plugin missing"

# 2. Clone or update repo
mkdir -p /opt/wc-solns
if [ -d "${INSTALL_DIR}/.git" ]; then
  log "Repo exists, pulling"
  git -C "${INSTALL_DIR}" pull --ff-only
else
  log "Cloning ${REPO}"
  git clone "${REPO}" "${INSTALL_DIR}"
fi
cd "${INSTALL_DIR}"

# 3. .env
if [ ! -f .env ]; then
  log "Creating .env from template (you will be prompted for secrets)"
  cp .env.example .env
  chmod 600 .env
  printf '\n\033[0;33mIMPORTANT:\033[0m edit .env and fill in real values before proceeding.\n'
  printf '  nano %s/.env\n\n' "${INSTALL_DIR}"
  printf 'Required at minimum:\n'
  printf '  ANTHROPIC_API_KEY, AIRTABLE_PAT, SESSION_SECRET, HEARTBEAT_SHARED_SECRET, GMAIL_APP_PASSWORD\n\n'
  read -rp "Press Enter once .env is saved to continue..." _
fi

# 4. Traefik (reverse proxy + automatic Let's Encrypt)
log "Setting up Traefik"
mkdir -p "${TRAEFIK_DIR}/letsencrypt"
touch "${TRAEFIK_DIR}/letsencrypt/acme.json"
chmod 600 "${TRAEFIK_DIR}/letsencrypt/acme.json"

cat > "${TRAEFIK_DIR}/docker-compose.yml" <<EOF
services:
  traefik:
    image: traefik:v3
    restart: unless-stopped
    command:
      - --api.dashboard=false
      - --providers.docker=true
      - --providers.docker.exposedByDefault=false
      - --entrypoints.web.address=:80
      - --entrypoints.web.http.redirections.entrypoint.to=websecure
      - --entrypoints.web.http.redirections.entrypoint.scheme=https
      - --entrypoints.websecure.address=:443
      - --certificatesresolvers.letsencrypt.acme.email=${ACME_EMAIL}
      - --certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json
      - --certificatesresolvers.letsencrypt.acme.httpchallenge=true
      - --certificatesresolvers.letsencrypt.acme.httpchallenge.entrypoint=web
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./letsencrypt:/letsencrypt
    networks:
      - proxy

networks:
  proxy:
    name: proxy
    driver: bridge
EOF

(cd "${TRAEFIK_DIR}" && docker compose up -d)

# 5. Dashboard container - attach to the proxy network
cd "${INSTALL_DIR}"
# Patch docker-compose.yml to use the shared proxy network
if ! grep -q "proxy" docker-compose.yml; then
  log "Adding proxy network to dashboard compose"
  cat >> docker-compose.yml <<'EOF'

networks:
  default:
    name: proxy
    external: true
EOF
fi

log "Building + starting dashboard"
docker compose pull 2>/dev/null || true
docker compose build
docker compose up -d

# 6. Wait for healthz + verify
log "Waiting for /healthz to respond (up to 60 s)"
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:8000/healthz" >/dev/null 2>&1; then
    log "Local healthz: OK"
    break
  fi
  sleep 2
done

log "Waiting for HTTPS cert (up to 90 s for Let's Encrypt first issue)"
for i in $(seq 1 45); do
  if curl -fsS "https://${DOMAIN}/healthz" >/dev/null 2>&1; then
    log "Public HTTPS: OK"
    curl -fsS "https://${DOMAIN}/healthz"
    printf '\n\n\033[0;32mSUCCESS.\033[0m Dashboard is live at https://%s\n' "${DOMAIN}"
    exit 0
  fi
  sleep 2
done

die "Public HTTPS did not come up in 90 s. Check: docker compose logs -f, and the Traefik logs at ${TRAEFIK_DIR}."
