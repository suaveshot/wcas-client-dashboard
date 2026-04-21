# Deploy guide

Full production deployment on a Hostinger VPS. Written as a runbook so a future-Sam (or a sub-agency licensing the platform) can reproduce it without asking.

## Prerequisites

- Hostinger VPS (KVM 2 or better, Ubuntu 22.04/24.04 LTS)
- A domain pointed at the VPS  -  we use `dashboard.westcoastautomationsolutions.com`
- Docker + Docker Compose installed on the VPS
- Traefik or nginx running as the reverse proxy on the VPS (we use Traefik for automatic Let's Encrypt)
- `git` installed on the VPS

## First-time setup

```bash
# SSH into the VPS
ssh root@<vps-ip>

# Clone into the wc-solns tree
mkdir -p /opt/wc-solns
cd /opt/wc-solns
git clone https://github.com/suaveshot/wcas-client-dashboard.git dashboard_app
cd dashboard_app

# Create .env from template
cp .env.example .env
# Open .env and fill in real values
#   ANTHROPIC_API_KEY, AIRTABLE_PAT, SESSION_SECRET, HEARTBEAT_SHARED_SECRET,
#   GMAIL_APP_PASSWORD
nano .env
chmod 600 .env

# Install git hooks
git config core.hooksPath .githooks

# First build + run
docker compose build
docker compose up -d
```

## Verify

```bash
curl -fsS https://dashboard.westcoastautomationsolutions.com/healthz
# Expected: {"status":"ok","version":"0.1.0"}
```

## Updates

```bash
cd /opt/wc-solns/dashboard_app
git pull
docker compose build
docker compose up -d
```

## Backups

Tenant data lives in the `tenant-data` Docker volume (mounted at `/opt/wc-solns` inside the container). Daily cron backs this up to secondary storage:

```cron
# /etc/cron.d/wcas-dashboard-backup
0 4 * * * root /opt/wc-solns/dashboard_app/scripts/backup.sh
```

See `scripts/backup.sh` (Day 1 deliverable) for the `tar + gpg + scp` pattern.

## Monitoring

- **UptimeRobot** hits `/healthz` every 5 min from an external monitor
- Docker's built-in healthcheck restarts the container if three consecutive probes fail
- Logs: `docker compose logs -f dashboard`
