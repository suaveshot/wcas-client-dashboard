---
surface: /healthz (also: /auth/dev-login dev-only sibling)
audited: 2026-04-28
auditor: Larry (Claude Opus 4.7)
methodology: Phase 0 framework (function check + UX cleanup, no architecture changes)
---

# Phase 0 audit - /healthz

## Summary

`/healthz` is a 4-line endpoint at `main.py:162-165` returning `{"status":"ok","version":app.version}`. Used by Docker container probe + UptimeRobot. Currently returns 0.7.1 (verified during Apr 28 Part A deploy).

This is a tiny surface but worth a deliberate pass because it is the **single externally-monitored signal of dashboard liveness**. Today's implementation says "ok" no matter what; if Airtable is down, the data directory is unwritable, or `SESSION_SECRET` is unset, the endpoint still returns 200 OK with `status=ok`. That makes the uptime monitor a placebo.

The other discovery: `/auth/dev-login` (`main.py:168-187`) is a dev-only shortcut that 404s when `PRODUCTION=true`. Verify on prod that `PRODUCTION` is in fact set; otherwise the shortcut would be a tenant-bypass vector. (Smoke check item, not a finding.)

7 findings: 4 must-fix-before-tenant-2, 2 nice-to-have-pre-launch, 1 defer-to-Phase-2.

### Top 3 priorities (1-line each)

1. **Make `status:"ok"` actually mean something.** Add a deep-health check (data dir writable + SESSION_SECRET set + Airtable reachable, the last being optional/timeboxed). Return 503 if any required subsystem is unreachable.
2. **Add `Cache-Control: no-cache, no-store`.** Today's response can be cached by intermediaries; UptimeRobot's polling could see stale "ok" if a CDN caches it.
3. **Verify `PRODUCTION=true` is set on prod VPS.** If unset, `/auth/dev-login?tenant=americal_patrol` mints a session. Smoke check.

---

## Findings

### F1. `status:"ok"` is hardcoded; doesn't reflect subsystem health - must-fix-before-tenant-2

- **Function:** Health endpoint should return 503 when the dashboard is unable to serve traffic correctly.
- **Today:** `main.py:162-165` returns 200 + `status=ok` unconditionally. No checks on data directory, secrets, Airtable, heartbeat store, or any other dependency.
- **Gap:** A misconfigured deploy (e.g., `SESSION_SECRET` missing, `/opt/wc-solns` not mounted) would still return 200 OK from the health probe. Docker thinks the container is healthy and routes traffic to it; UptimeRobot reports green; reality is the dashboard returns 500 on every login. The single externally-monitored signal is a placebo.
- **Smallest fix:** Inline a fast health check:
  ```python
  def _deep_check() -> tuple[str, dict]:
      issues = {}
      if not os.getenv("SESSION_SECRET"):
          issues["session_secret"] = "missing"
      if not Path("/opt/wc-solns").is_dir() or not os.access("/opt/wc-solns", os.W_OK):
          issues["data_dir"] = "unwritable"
      # Airtable: skip if AIRTABLE_API_KEY is intentionally absent (dev),
      # but if set, ping with a 1-second timeout.
      ...
      return ("ok" if not issues else "degraded", issues)
  ```
  Return 503 if `issues` is non-empty. Allow `?shallow=1` for the original behavior so Docker probe doesn't churn on a slow Airtable.
- **Estimated effort:** 0.5 day. Includes one test asserting 503 + issue list when SESSION_SECRET is missing.

### F2. No `Cache-Control` header - intermediaries can cache "ok" - must-fix-before-tenant-2

- **Function:** Health endpoint always returns fresh data.
- **Today:** No headers set. FastAPI default is no `Cache-Control`, which means upstream proxies (CDN, reverse proxy) decide. Hostinger's edge could in theory cache.
- **Gap:** A staged outage would not be reflected if a CDN cached the last-known 200 for 5 minutes. UptimeRobot would report green after the dashboard is already unhealthy.
- **Smallest fix:** Set `Cache-Control: no-cache, no-store, must-revalidate` and `Pragma: no-cache` on the response.
- **Estimated effort:** 0.1 day.

### F3. Version string leaked publicly - mild fingerprint - must-fix-before-tenant-2

- **Function:** Public health response shouldn't help an attacker target known-vulnerable versions.
- **Today:** Returns `version: "0.7.1"` to anyone. Once a CVE drops against a future version, attackers can grep `/healthz` to find vulnerable instances.
- **Gap:** Mild risk. FastAPI also exposes `/docs` and `/openapi.json` by default unless disabled, so version-style fingerprinting may already be possible — though grep across `main.py` shows OpenAPI is gated behind `dev_mode` (verify at deploy).
- **Smallest fix:** Two-pronged.
  - Move version to an authenticated `GET /api/admin/version` endpoint that returns full deploy metadata (sha, deploy_time, etc.).
  - Keep `/healthz` returning only `{"status":"ok"}` or a coarse hash of the version.
- **Estimated effort:** 0.5 day. Includes verifying that `/docs` is also gated in prod.

### F4. No `/readyz` (or equivalent deep readiness probe) - must-fix-before-tenant-2

- **Function:** Docker / orchestrator distinguishes "container is up" (`/healthz`) from "container is ready to serve traffic" (`/readyz`).
- **Today:** Only `/healthz` exists. Used as both the liveness AND readiness probe in `docker-compose.yml`.
- **Gap:** During boot, the container runs `/healthz` immediately and gets 200 — but Airtable client hasn't loaded credentials yet, the templates haven't compiled, etc. Traffic gets routed to a half-initialized worker for the first few seconds of every redeploy.
- **Smallest fix:** Add `/readyz` that asserts:
  - Settings loaded (env validation passed)
  - Templates compiled (Jinja2 environment ready)
  - Heartbeat store accessible
  - Optional: Airtable reachable in the last 60 seconds (cached)
  Returns 503 + JSON payload until all green. Update `docker-compose.yml` to use `/readyz` for `healthcheck:` + Hostinger Auto-Heal probe. Keep `/healthz` shallow for fast Docker liveness.
- **Estimated effort:** 0.75 day. Includes `docker-compose.yml` edit and one smoke test.

### F5. Endpoint is async but does no I/O - pointless overhead - nice-to-have-pre-launch

- **Function:** Match async-vs-sync to actual I/O profile.
- **Today:** `async def healthz()` with no awaits. FastAPI runs it in the event loop, no benefit.
- **Gap:** Cosmetic. Once F1's deep check lands, the function will have real I/O and async will become meaningful.
- **Smallest fix:** Either leave as-is (fixes itself when F1 lands) or change to `def healthz()` for now. Recommend: leave alone, fix as part of F1.
- **Estimated effort:** 0.

### F6. No structured response for monitoring (build_sha, deploy_time, etc.) - nice-to-have-pre-launch

- **Function:** Richer telemetry for UptimeRobot / future Grafana / Sam's monitoring.
- **Today:** `{"status":"ok","version":"0.7.1"}` only.
- **Gap:** When something goes wrong, knowing the precise commit SHA and deploy timestamp from the health endpoint accelerates diagnosis.
- **Smallest fix:** Combine with F3's authenticated `/api/admin/version` endpoint. Add `BUILD_SHA` and `DEPLOY_TIME` env vars set during the Hostinger Docker rebuild. Surface via the authenticated endpoint, not `/healthz`.
- **Estimated effort:** 0.25 day. Lands as part of F3.

### F7. No `/metrics` Prometheus endpoint - defer-to-Phase-2

- **Function:** Future-proof for Grafana / Prometheus scrape.
- **Today:** None.
- **Gap:** Phase 2 work; no urgent need pre-tenant-5.
- **Smallest fix:** `prometheus_fastapi_instrumentator` package, ~2 hours of integration once decided.
- **Estimated effort:** Defer.

---

## Smoke check (not a finding)

**Verify `PRODUCTION=true` is set on the VPS.** `main.py:178` 404s `/auth/dev-login` only when `os.getenv("PRODUCTION", "false").lower() == "true"`. If prod is missing this env var, an attacker can `GET /auth/dev-login?tenant=americal_patrol` and mint a Garcia session. The dev-login route otherwise has all the right guards (slug regex, dev-only flag).

```bash
ssh garcia-vps 'docker exec wcas-dashboard env | grep PRODUCTION'
```

Expected: `PRODUCTION=true`. If missing, add to VPS `.env` and `docker compose up -d --build`.

---

## Methodology checks (per parent plan B1)

| Check | Result |
|---|---|
| Function check | Endpoint returns 200 + JSON. Version interpolation works. F1 (no real subsystem check) means the body is partially fictional. |
| UX gap | F1 (placebo health) and F4 (no readyz) are the main externally-visible gaps. F3 (version leak) is the security-flavored one. |
| Smallest fix | All findings sized in fractions of a day. Total: ~2 days for must-fix + nice-to-have. |
| Phase 1 priority bucket | Assigned per finding. |
| Composer empty state | N/A - JSON endpoint. |
| Mobile pass | N/A. |
| Confused-state recovery | F1 is exactly this gap: today the endpoint always says "ok" even when the dashboard is broken. |
| Demo gate | Public, no PREVIEW_MODE / JUDGE_DEMO. Correct (probe needs to work without auth). |
| Sidebar consistency | N/A. |

---

## Phase 1D effort total

| Bucket | Effort |
|---|---|
| must-fix (F1-F4) | ~1.85 days |
| nice-to-have (F5-F6) | ~0.25 day (most of F5+F6 absorbed by F1+F3) |
| defer (F7) | N/A |
| **Total in scope** | **~2.1 days** for Phase 1D |

Smallest audit on the punch list. Land in week 1 of Phase 1D alongside the auth-flow + legal-pages mechanical fixes; same flavor of work (housekeeping with security-adjacent stakes).

## Cumulative Phase 0 progress

| # | Surface | Status | Findings | Phase 1D effort |
|---|---|---|---|---|
| 1 | /activate | done | 10 (3+5+2) | ~4 days |
| 2 | /dashboard | done | 12 (3+6+3) | ~2.5-3.5 days |
| 3 | /roles | done | 8 (2+5+1) | ~1.5 days |
| 4 | /roles/{slug} | done | 11 (3+6+2) | ~2 days |
| 5 | /approvals | done | 13 (3+7+3) | ~5.5 days |
| 6 | /recommendations | done | 13 (2+8+3) | ~4-5 days |
| 7 | /goals | done | 13 (4+7+2) | ~6.5 days |
| 8 | /settings | done | 12 (5+5+2) | ~6 days |
| 9 | /activity | done | 13 (5+6+2) | ~5.25 days |
| 10 | /auth/login + magic-link | done | 13 (5+6+2) | ~3.15 days |
| 11 | /legal/terms + /legal/privacy | done | 14 (6+6+2) | ~3.7 days |
| 12 | /healthz | done | 7 (4+2+1) | ~2.1 days |
| 13 | /demo/* (regression) | next | - | - |

**Running totals:** 139 findings, ~46-48 days Phase 1D work mapped. With shared-dispatcher dedupe + shared prefs-partial: ~37-39 days.

## Cross-cutting themes (cumulative, updated)

1-9. (See prior audits.)
10. **HTML-entity encoding bypasses brand-rule pre-commit hook** - persists; legal pages F1.
11. **Compliance hygiene** - persists; legal pages F2-F4.
12. **NEW: Status-endpoint truth budget** - every status/health/uptime signal in the product (auth_log, healthz, /api/* `ok=true` returns) needs a test that asserts what "ok" actually means. Add to Phase 1D punch list.
13. **NEW: Smoke checks accumulate** - we now have a list of 5+ deploy-time smoke checks (PRODUCTION env var, /docs gate, JUDGE_DEMO default off, etc.). Worth turning into a single `scripts/post_deploy_smoke.sh` for the VPS pull/build/run cycle.

---

## Next surface to audit

**`/demo/*`** - the cinematic activation + dashboard demo routes from Day 6 hackathon work. Per parent plan, regression check that:
- They still 404 by default (JUDGE_DEMO=false on prod, verified)
- The route definitions haven't bit-rotted since Apr 25 (5-scene activation + 6-scene dashboard with embedded speaker notes)
- No tenant data leaks via the seeded "Riverbend Barbershop" demo
- Mobile play works (canvas-style scenes)
- Sound autoplay isn't an issue
- Browser-back during a scene transition doesn't break state
