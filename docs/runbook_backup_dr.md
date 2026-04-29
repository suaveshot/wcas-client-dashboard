# Backup + Disaster Recovery Runbook

Audience: Sam at 2 a.m. when something is on fire.
Format: pilot's checklist. Stop reading and start doing.

Last updated: 2026-04-29 (W7 of Phase 1 sprint).

VPS topology reference: see `memory/reference_vps_aliases.md`. The host that matters for WCAS dashboard + AP automations + Garcia is `garcia-vps` (93.127.216.242). The other VPS `ap-vps` (31.97.147.220) hosts the WCAS n8n stack only.

---

## 1. Inventory: What we back up

| Asset | Lives at | Sensitivity | Recoverable from elsewhere? |
|-------|----------|-------------|------------------------------|
| Per-tenant heartbeat snapshots | `/opt/wc-solns/<tenant_id>/state_snapshot/*.json` | Low (telemetry only) | No, lost = lost |
| Per-tenant OAuth refresh tokens | `/opt/wc-solns/<tenant_id>/credentials/*.json` (chmod 600) | High (refresh tokens, App Passwords, paste creds) | Yes, by re-running `/activate` for each provider |
| Per-tenant pipeline state | `/opt/wc-solns/<tenant_id>/state/*.json` (sales_pipeline, email_assistant, etc.) | Medium (replay risk) | Partial; some state can be reconstructed from GHL/Airtable |
| Platform-master credentials | `/opt/wc-solns/_platform/<provider>/{master,agency,workspace,api}.json` | CRITICAL (BrightLocal, Twilio master, GHL agency, Airtable PAT, Hostinger API) | Yes via re-issue, but blast radius is huge |
| Dashboard code (wcas-client-dashboard) | GitHub: `suaveshot/wcas-client-dashboard` | Low (public, no secrets) | Yes, GitHub is source of truth |
| AP code | GitHub: `suaveshot/americal-patrol-vps` | Low | Yes, GitHub is source of truth |
| Garcia code | GitHub: `suaveshot/garcia-folklorico` | Low | Yes, GitHub is source of truth |
| Caddy config + TLS state | VPS volume on `garcia-vps` (`wcas-caddy` container) | Medium | Yes, regenerated on first request via Let's Encrypt |
| Airtable bases (CRM, Clients, ChatConversations) | Airtable cloud | High | Airtable's own snapshot system + our nightly export |
| DNS records | Hostinger DNS (per-domain) | Medium | Yes via Hostinger DNS snapshot API |
| Docker compose / project definitions | Hostinger VPS Project state, mirrored in repo | Low | Yes via redeploy from GitHub |
| `.env` style runtime secrets | Hostinger VPS Project `environment` parameter (NOT in repo) | High | Manual; kept in 1Password (or wherever Sam tracks them) |

What is NOT in this table: anything on Sam's PC. The PC is treated as ephemeral. If it dies, the VPS is the source of truth for live operations; GitHub is the source of truth for code.

---

## 2. Backup cadence + retention

| Cadence | What | Where it lands | Retention |
|---------|------|----------------|-----------|
| Daily 02:00 UTC | Tenant data tarball per tenant (state_snapshot + state, NOT credentials) | `garcia-vps:/opt/backups/tenant-data/<tenant>/<YYYY-MM-DD>.tar.gz` | 14 days local |
| Daily 02:30 UTC | rsync of `/opt/backups/tenant-data/` to Sam's PC OneDrive | `C:\Users\bball\OneDrive\Desktop\Claude\WC Solns\backups\tenant-data\` | 90 days OneDrive |
| Weekly Sun 03:00 UTC | Hostinger VPS snapshot via `VPS_createSnapshotV1` | Hostinger snapshot store | Last 4 (Hostinger limit; rotate by deleting oldest) |
| Weekly Sun 03:30 UTC | Airtable export per base (CSV bundle) | OneDrive `backups\airtable\<base>\<YYYY-MM-DD>\` | 12 weeks |
| Monthly 1st 04:00 UTC | Encrypted platform-master credential bundle | OneDrive `backups\_platform\<YYYY-MM>.tar.gz.gpg` | 12 months |
| On-demand | Hostinger DNS snapshot per domain | Hostinger native DNS snapshot | Hostinger keeps last 5 |

Rule of thumb: anything that could lose Sam money if it disappeared (refresh tokens, platform creds, Airtable customer data) is backed up off-VPS at least weekly. Anything reconstructable (telemetry, logs) lives only on-VPS.

---

## 3. Hostinger snapshot strategy

Hostinger's snapshot is a full-disk image of the VPS. It is the single best "oh god revert everything" button. Limit: Hostinger keeps a small number of snapshots per VPS (verify current count via `VPS_getBackupsV1`); rotate manually.

Take a snapshot BEFORE:
- Any `apt upgrade` or kernel change
- Any redeploy of the dashboard, AP automations, or Caddy proxy
- Any `docker compose down` that touches a volume
- Adding or removing a tenant directory under `/opt/wc-solns/`
- Any platform-master credential rotation

Take a snapshot AFTER:
- A clean weekly Sunday window (cron'd)
- Any successful new-tenant onboarding (so we have a known-good baseline with that tenant)

Commands (run from Sam's PC against the Hostinger MCP):

```
# Take snapshot of garcia-vps
VPS_createSnapshotV1(virtualMachineId=1568946)

# List existing snapshots
VPS_getBackupsV1(virtualMachineId=1568946)

# Restore (DESTRUCTIVE - wipes current disk)
VPS_restoreSnapshotV1(virtualMachineId=1568946, snapshotId=<id>)
```

The VPS ID `1568946` is `garcia-vps`. Confirm in `reference_vps_aliases.md` before issuing destructive ops.

Restore implication: a snapshot restore reverts the entire VPS, including any work between snapshot and restore. If you only need one tenant's data restored, prefer the tenant tarball path (Section 4) and leave the snapshot alone.

---

## 4. Per-tenant data backup

Per-tenant state lives under `/opt/wc-solns/<tenant_id>/`. Layout (verified in `dashboard_app/services/heartbeat_store.py` and `credentials.py`):

```
/opt/wc-solns/<tenant_id>/
    credentials/<provider>.json     # chmod 600, refresh tokens / paste creds
    state_snapshot/<pipeline>.json  # heartbeat receiver writes here
    state/                          # pipeline state (sales, email assistant, etc.)
```

Daily tarball script (lives on `garcia-vps` at `/opt/backups/scripts/tenant_backup.sh`, runs from container cron):

```
#!/bin/bash
set -euo pipefail
DATE=$(date -u +%Y-%m-%d)
ROOT=/opt/wc-solns
DEST=/opt/backups/tenant-data
for tenant in $(ls "$ROOT" | grep -v '^_'); do
  mkdir -p "$DEST/$tenant"
  tar -czf "$DEST/$tenant/$DATE.tar.gz" \
    -C "$ROOT/$tenant" \
    state_snapshot state 2>/dev/null || true
done
# Prune older than 14 days
find "$DEST" -name '*.tar.gz' -mtime +14 -delete
```

Note the leading underscore filter: `_platform` is intentionally excluded here. It has its own (more careful) backup pipeline in Section 5.

Pull to PC OneDrive (run nightly from Sam's PC via Task Scheduler):

```
# From Git Bash on Sam's PC
rsync -avz --delete garcia-vps:/opt/backups/tenant-data/ \
  "/c/Users/bball/OneDrive/Desktop/Claude/WC Solns/backups/tenant-data/"
```

Restore one tenant's data:

```
ssh garcia-vps
cd /opt/wc-solns/<tenant_id>
# Sanity-check what's in the tarball before extracting
tar -tzf /opt/backups/tenant-data/<tenant_id>/<YYYY-MM-DD>.tar.gz | head -30
# Extract over current contents (will overwrite state/ + state_snapshot/)
tar -xzf /opt/backups/tenant-data/<tenant_id>/<YYYY-MM-DD>.tar.gz
# Restart the dashboard so it re-reads state
docker restart wcas-dashboard
```

What this does NOT restore: credentials. Credentials are not in this tarball on purpose (see Section 5).

---

## 5. Platform-master credential backup

The `/opt/wc-solns/_platform/` directory holds the credentials that let WCAS act as itself across all tenants. From `dashboard_app/services/platform_master.py`:

```
/opt/wc-solns/_platform/
    brightlocal/master.json
    twilio/master.json
    ghl/agency.json
    airtable/workspace.json
    hostinger/api.json
```

These are root-owned, chmod 600, provisioned out-of-band by Sam. Rule: never put them in any GitHub repo, never include them in the daily tenant tarball, never rsync them in cleartext.

Monthly backup procedure (manual, with Sam present):

```
# On garcia-vps as root
DATE=$(date -u +%Y-%m)
tar -czf /tmp/platform-$DATE.tar.gz -C /opt/wc-solns _platform

# Encrypt with GPG using Sam's personal key (passphrase, not key file)
gpg --symmetric --cipher-algo AES256 \
    --output /tmp/platform-$DATE.tar.gz.gpg \
    /tmp/platform-$DATE.tar.gz

# Wipe the cleartext copy
shred -u /tmp/platform-$DATE.tar.gz

# Pull to OneDrive from Sam's PC
scp garcia-vps:/tmp/platform-$DATE.tar.gz.gpg \
  "/c/Users/bball/OneDrive/Desktop/Claude/WC Solns/backups/_platform/"

# Wipe the VPS-side encrypted copy after confirming the OneDrive copy lands
ssh garcia-vps "shred -u /tmp/platform-$DATE.tar.gz.gpg"
```

The encrypted bundle on OneDrive is the only off-VPS copy. The passphrase lives in 1Password; the encrypted file lives in OneDrive; neither is useful alone.

Restore:

```
gpg --decrypt /path/to/platform-YYYY-MM.tar.gz.gpg > /tmp/platform.tar.gz
scp /tmp/platform.tar.gz garcia-vps:/tmp/
ssh garcia-vps
cd /opt/wc-solns
sudo tar -xzf /tmp/platform.tar.gz
sudo chown -R root:root _platform
sudo chmod -R 600 _platform/*/*
sudo chmod 700 _platform _platform/*
sudo shred -u /tmp/platform.tar.gz
```

If a single platform credential is compromised: do NOT restore from backup. Rotate the credential at the vendor first (see Section 8.3).

---

## 6. Code repo recovery

GitHub is the source of truth for all code. Repos are public (`suaveshot/<project>`), per `feedback_vps_deploy_method.md`. No secrets in repo.

Repos that matter:
- `suaveshot/wcas-client-dashboard`
- `suaveshot/americal-patrol-vps`
- `suaveshot/garcia-folklorico`

Redeploy from GitHub via the canonical deploy method:

```
VPS_createNewProjectV1(
  virtualMachineId=1568946,
  project_name="<existing-name>",
  content="https://github.com/suaveshot/<repo>",
  environment="KEY1=value1\nKEY2=value2\n..."
)
```

The `environment` blob is the runtime secrets (OAuth client IDs, API keys, base64-encoded tokens). Sam keeps the canonical copy of each project's environment block in 1Password under "VPS env: <project>". If 1Password has it and GitHub has the code, the project can be reconstructed from zero.

Hard rule: do NOT propose Cloudflare Workers / Pages / Tunnel as part of recovery. Hostinger VPS only. (`feedback_no_cloudflare.md`)

---

## 7. DNS recovery

Hostinger holds DNS for every domain Sam owns there. Each domain has independent records.

Snapshot DNS for a single domain:

```
# Lists existing snapshots
DNS_getDNSSnapshotListV1(domain="<domain>")

# Restore a snapshot
DNS_restoreDNSSnapshotV1(domain="<domain>", snapshotId=<id>)
```

If a domain is registered outside Hostinger (rare, check with `domains_getDomainListV1`), DNS may live elsewhere. Verify per-domain before assuming.

For the AP migration plan: as of `project_ap_hostinger_migration.md`, the live `americalpatrol.com` is still GHL DNS. If GHL goes away, that DNS goes with it. Migration to Hostinger DNS for AP is plan-only; do not assume Hostinger snapshot covers AP today.

Recovery flow if DNS is wrong:
1. Compare current `DNS_getDNSRecordsV1` output against the most recent known-good snapshot.
2. If a small number of records are wrong, edit them with `DNS_updateDNSRecordsV1`.
3. If everything is wrong, `DNS_restoreDNSSnapshotV1` to the last good snapshot.
4. Wait 5 to 60 minutes for propagation (TTL-dependent).

---

## 8. Recovery runbooks (decision trees)

### 8.1 Single tenant data corruption

Symptom: one tenant's dashboard tiles show stale or wrong data; everything else is fine.

```
1. ssh garcia-vps
2. ls -la /opt/wc-solns/<tenant>/state_snapshot/
3. Is the directory empty or full of zero-byte files?
   YES -> go to step 4 (restore)
   NO  -> Look at most recent file timestamps. Older than 24h?
            YES -> Pipeline isn't pushing heartbeats. NOT a backup issue;
                   diagnose pipeline (PC Task Scheduler or VPS cron).
            NO  -> Inspect the JSON itself for corruption.
4. Pick the freshest tarball:
     ls -la /opt/backups/tenant-data/<tenant>/
5. Restore (from Section 4):
     cd /opt/wc-solns/<tenant>
     tar -xzf /opt/backups/tenant-data/<tenant>/<YYYY-MM-DD>.tar.gz
6. docker restart wcas-dashboard
7. Hit https://<dashboard-url>/<tenant> to confirm tiles populate
8. If tiles still wrong, the bug is in the dashboard or in the pipeline,
   not the backup. Stop here, debug code path.
```

Credentials are NOT touched by this flow. If credentials are also corrupted, treat as Section 8.3.

### 8.2 Whole VPS down (Hostinger outage or VPS unreachable)

Symptom: `ssh garcia-vps` times out; dashboard URLs all return connection refused.

```
1. Check Hostinger status:
     - Hostinger status page (open in browser, no auth needed)
     - VPS_getVirtualMachineDetailsV1(virtualMachineId=1568946)
2. Is Hostinger reporting an outage?
   YES -> Wait. Recovery is Hostinger's job. Communicate to clients
          (manual; no automation handles this yet -- see Section 10).
   NO  -> Continue.
3. Try VPS_restartVirtualMachineV1(virtualMachineId=1568946)
4. Wait 5 minutes. ssh garcia-vps again.
   WORKS  -> Run docker ps and verify all 4 containers are up:
                americal-patrol-automations-1, garcia-folklorico-app-1,
                wcas-dashboard, wcas-caddy.
                Restart any that are down: docker restart <name>.
   FAILS  -> Continue to step 5.
5. VPS_startRecoveryModeV1(virtualMachineId=1568946) and SSH in via the
   recovery image to inspect the disk.
6. If disk is intact: fix what broke, exit recovery, restart.
   If disk is corrupt: VPS_restoreSnapshotV1 to the most recent weekly
   snapshot. Accept the data loss between snapshot and now (tenant
   tarballs in OneDrive can fill some of that gap).
7. After recovery, verify:
     - All 4 containers running
     - Caddy is serving TLS for all domains
     - Dashboard /api/heartbeat accepts POSTs (curl test)
     - Take a fresh snapshot via VPS_createSnapshotV1
```

If the VPS is permanently unrecoverable: provision a new VPS via `VPS_purchaseNewVirtualMachineV1`, redeploy each project from GitHub via the canonical method (Section 6), restore tenant tarballs from OneDrive, restore platform creds from the encrypted bundle, repoint DNS A records to the new IP.

### 8.3 Compromised platform-master credential

Symptom: unexpected charges, unexpected SMS, weird BrightLocal scans, GHL records changing without explanation.

```
1. Identify which credential. Likely candidates by symptom:
     - Twilio charges/SMS spikes  -> twilio/master.json
     - BrightLocal scan spikes    -> brightlocal/master.json
     - GHL data changes           -> ghl/agency.json
     - Airtable record changes    -> airtable/workspace.json
     - VPS / DNS changes          -> hostinger/api.json
2. ROTATE AT THE VENDOR FIRST. Old key dies the moment you do this:
     - Twilio: console -> rotate auth token
     - BrightLocal: account -> regenerate API key
     - GHL: agency settings -> regenerate API key
     - Airtable: account -> revoke + create new PAT (same scopes)
     - Hostinger: API tokens -> revoke + create new
3. ssh garcia-vps as root
4. Edit the relevant /opt/wc-solns/_platform/<provider>/<file>.json
   with the new credential. Keep chmod 600, owner root.
5. Restart any container that reads it:
     docker restart wcas-dashboard
     docker restart americal-patrol-automations-1
6. Verify the relevant pipeline still works (e.g. send a test SMS,
   trigger a BrightLocal scan).
7. Audit the vendor's log for what the attacker did between compromise
   and rotation. Document. Notify any tenant whose data was touched.
8. Take a fresh monthly platform-master backup (Section 5) so the
   next backup contains the new key, not the dead one.
9. Do NOT restore the platform-master backup from OneDrive. Backups
   are last-resort for "the file is gone," not for "the key is hot."
```

This is the single highest-blast-radius failure mode. Treat it as a P0.

### 8.4 Lost Airtable base / accidental record purge

Symptom: a base or table is empty or missing. Airtable's own trash holds deleted records for 7 days for paid plans.

```
1. Airtable web UI -> base -> Trash. Restore individual records there.
2. If the entire table or base is gone:
     - Paid plan: Airtable support can usually restore within 7 days.
       Open a support ticket immediately; clock is ticking.
     - If past 7 days: pull the most recent weekly export from
       OneDrive backups\airtable\<base>\<YYYY-MM-DD>\
       and re-import via Airtable's CSV import.
3. Re-imported records get NEW record IDs. Anything that referenced
   the old IDs (n8n workflows, dashboard hardcoded links) needs an
   audit. Search the n8n workflows for hardcoded recXXXXX values.
4. Do not assume linked-record fields will reconnect themselves;
   they often won't. Plan to fix manually.
```

The weekly Airtable CSV export is the only off-Airtable copy. Until that runs, Airtable's native 7-day window is the only safety net.

---

## 9. Test cadence (DR drill)

Quarterly, on a Saturday morning, with no client-facing impact expected:

1. Pick one tenant at random (not Garcia, not the staging tenant; pick a real one).
2. From OneDrive backup, restore that tenant's tarball into a scratch directory on Sam's PC: `~/dr-drill-<date>/`.
3. Confirm the JSON files inside parse and look like real heartbeat / state data.
4. Time how long step 2 takes from "click" to "data on disk."
5. Pretend the platform-master backup needs to be opened: decrypt the most recent monthly bundle, list contents, verify file sizes are non-zero, re-shred the cleartext.
6. Take a fresh Hostinger snapshot of `garcia-vps` so the next incident starts from a known-good point.
7. Update the Last updated date at the top of this runbook, even if nothing else changed.

Drill log: append a row to `docs/runbook_dr_drill_log.md` (create on first drill) with date, tenant tested, timings, and anything that didn't work.

If a quarterly drill reveals a step in this runbook is wrong, fix the runbook in the same session. The runbook is only useful if it's accurate at 2 a.m.

---

## 10. What we are NOT yet protected against

Be honest about the gaps:

- **OneDrive itself disappearing.** The PC's OneDrive copy is the off-VPS leg. If Microsoft loses Sam's account, both legs of the platform-master backup chain (encrypted bundle in OneDrive + passphrase in 1Password) are at risk. Mitigation: a quarterly copy of the encrypted bundle to a separate cloud (Backblaze B2 or similar) is on the post-sprint backlog, not done.
- **Real-time replication.** All backups are point-in-time. A tenant credential added at 14:00 and lost at 14:30 is gone; the daily tarball at 02:00 doesn't have it. Re-running `/activate` for that provider is the only fix.
- **GitHub itself going away.** All repos are public on GitHub. If GitHub disappears, the local clone on Sam's PC is the only code copy. A weekly `git fetch --all` to a second remote (Hostinger Git repo or local NAS) would close this gap. Not done.
- **Hostinger account compromise.** If someone steals Sam's Hostinger login, they can delete VPSes, snapshots, and DNS in one go. 2FA on Hostinger is the only defense; verify it's on. There is no out-of-band copy of the VPS disk image.
- **Airtable account compromise.** Same as Hostinger: 2FA is the only line of defense for the records older than 7 days.
- **AP DNS today.** `americalpatrol.com` DNS is still on GHL per the tabled migration plan. A GHL outage would take AP DNS down with it. WCAS-managed DNS lives on Hostinger; AP does not (yet).
- **The PC dying mid-sync.** If the PC dies during the nightly rsync, OneDrive may have a half-synced backup directory. The VPS-side tarballs are still authoritative for the last 14 days. Don't trust a half-synced OneDrive copy without checking file mod times against the VPS.
- **Cross-tenant blast radius from the dashboard process itself.** A bug that wrote into the wrong tenant's directory would corrupt data and the daily tarball would carry the corruption forward. The slug regex in `heartbeat_store.tenant_root` and `_validate_provider` in `credentials.py` are the defense. There is no per-tenant integrity hash today.

When any of these gaps becomes real, file an entry in `memory/lessons/` and update this section.

---

## Appendix: Quick reference

```
# SSH
ssh garcia-vps                       # 93.127.216.242, the host that matters
ssh ap-vps                           # 31.97.147.220, n8n only, NOT AP

# VPS IDs
garcia-vps virtualMachineId = 1568946

# Containers on garcia-vps
americal-patrol-automations-1
garcia-folklorico-app-1
wcas-dashboard
wcas-caddy

# Backup paths
/opt/wc-solns/<tenant>/{credentials,state_snapshot,state}/
/opt/wc-solns/_platform/{brightlocal,twilio,ghl,airtable,hostinger}/
/opt/backups/tenant-data/<tenant>/<YYYY-MM-DD>.tar.gz
C:\Users\bball\OneDrive\Desktop\Claude\WC Solns\backups\tenant-data\
C:\Users\bball\OneDrive\Desktop\Claude\WC Solns\backups\airtable\
C:\Users\bball\OneDrive\Desktop\Claude\WC Solns\backups\_platform\

# Daily 02:00 UTC = 19:00 Pacific (or 18:00 during DST)
```
