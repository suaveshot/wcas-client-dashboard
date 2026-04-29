---
surface: /activity
audited: 2026-04-28
auditor: Larry (Claude Opus 4.7)
methodology: Phase 0 framework (function check + UX cleanup, no architecture changes)
---

# Phase 0 audit - /activity

## Summary

`/activity` is the transparency feed at full length. The mechanics work: heartbeats and decisions merge into one chronological stream, newest first, with a clean role-pill + action-text + relative-time layout. Sam's Apr 27 edit added an `{% if feed %}` empty-state branch to the template - good instinct, but the service `services/activity_feed.py:232-241` always returns at least one synthetic row, so the template's `{% else %}` is dead code. (Functionally equivalent end state - the synthetic row IS the empty state - but worth knowing.)

The bigger story: **`/activity` is the surface where the `/settings` UX-lie pattern (audit `phase0_settings.md::F2`) becomes concrete and demonstrable.** Two of the four "cosmetic" prefs from `/settings` are supposed to land here:

- `privacy_default` - "Start every session with privacy mode on" - but privacy mode lives in `localStorage('wcas_privacy')` (`shell.js:16`); shell.js boot never reads the server pref, so the toggle is purely decorative.
- `feed_dense_default` - "Default the activity feed to Dense view" - the dense state is wired in `shell.js:825` (reads `LS_FEED_DENSE`), but `/activity` has **no Dense toggle UI** to flip it. So the feature exists as code but cannot be reached except by manual `localStorage.setItem('wcas_feed_dense', '1')` in DevTools.

There is also no filter, no search, no pagination, no per-role view. With an 80-row hardcoded cap (`main.py:485`), tenant 2 hits the wall in week 3. This is the "quiet phase 1D surface" - small individually, but the cumulative findings make it a real liability.

13 findings: 5 must-fix-before-tenant-2, 6 nice-to-have-pre-launch, 2 defer-to-Phase-2.

### Top 3 priorities (1-line each)

1. **Pagination / load-more.** 80 rows is a hard wall. Tenant 2 in week 3 will ask "what happened last Tuesday?" and have nowhere to go.
2. **Filter chips for kind + role.** Even before pagination, owner needs to grep "show me only owner decisions, not heartbeats." Cheap to add; high signal.
3. **Surface the Dense toggle that already exists.** `feed_dense_default` is wired in shell.js but has no visible button on `/activity`. Either expose it or delete the dead code.

---

## Findings

### F1. No filter UI by `kind` or `role` - must-fix-before-tenant-2

- **Function:** Owner filters the stream to "only owner decisions" or "only Reviews actions" or "only errors".
- **Today:** `templates/activity.html:50-70` renders all 80 rows in one stream. No filter chips, no role pills (other than the inline pill on each row), no kind toggle.
- **Gap:** With both heartbeats and decisions interleaved, the owner has no easy way to find a specific class of event. "Did I dismiss that GBP rec last Tuesday?" requires scrolling 80 rows. "Did anything error this week?" requires scrolling 80 rows.
- **Smallest fix:** Add a filter chip strip above the feed: All / Decisions / Heartbeats / Errors. Plus a role select (the 7 canonical roles + "All"). Filters apply client-side over the rendered DOM (no API changes needed for v1).
- **Estimated effort:** 0.75 day. Reuses chip pattern from /recommendations.

### F2. Hardcoded 80-row cap with no pagination or "load more" - must-fix-before-tenant-2

- **Function:** Owner views older history.
- **Today:** `main.py:485` calls `activity_feed.build(tenant_id, max_rows=80)`. No surface anywhere for older rows.
- **Gap:** Tenant 2 in week 3 hits the 80-row ceiling on a busy day (heartbeats + automated decisions easily fill 80 in 5-6 days). The audit log promise of `/activity` becomes false retroactively.
- **Smallest fix:** Add `?before=<iso_ts>&limit=80` query params to a new `GET /api/activity` endpoint. Add a "Load older" button at the bottom of the feed; click fetches the next 80 and appends client-side. Service: `activity_feed.build(tenant_id, max_rows=80, before=ts)`.
- **Estimated effort:** 1 day. Includes one test for the windowing.

### F3. `privacy_default` server pref never read by shell.js boot - must-fix-before-tenant-2

- **Function:** Owner toggles `privacy_default=true` in `/settings`. Next time they open the dashboard in a new tab, privacy mode should be on.
- **Today:** `shell.js:16` defines `LS_PRIVACY = 'wcas_privacy'`. Boot reads localStorage only. Server-side `tenant_prefs.privacy_default` is never fetched.
- **Gap:** The toggle on `/settings` saves successfully and changes nothing in any tab the owner ever opens. This is the most direct example of the UX-lie pattern flagged in `phase0_settings.md::F2`.
- **Smallest fix:** Add a small `<script>` tag at the top of every page template (or in the `<head>`) that injects `window.WCAS_PREFS = {{ prefs_json }}`. shell.js boot at line 800ish reads `WCAS_PREFS.privacy_default` as the default-ON signal if localStorage is empty. ~30 lines of JS.
- **Estimated effort:** 0.5 day. Requires touching every template's `<head>`; do it as a `_prefs.html` partial include to avoid drift.

### F4. `feed_dense_default` consumer exists but no UI to toggle it on `/activity` - must-fix-before-tenant-2

- **Function:** Switch the activity feed between Dense (compact rows) and Relaxed (current default) view.
- **Today:** `shell.js:825` reads `LS_FEED_DENSE` on boot and applies the class. `shell.js:79` writes the pref. But `/activity` template has no toggle button to fire that write. Setting it requires DevTools.
- **Gap:** Feature is half-built. The setting exists, the consumer exists, the UI doesn't.
- **Smallest fix:** Add a small "Dense / Relaxed" segmented control in the topbar of `/activity`, wired to the existing `applyFeedDensity()` function. While here, also wire `feed_dense_default` server pref into the boot fallback (same partial as F3).
- **Estimated effort:** 0.5 day.

### F5. `decisions.jsonl` is unbounded; no rotation or compaction - must-fix-before-tenant-2

- **Function:** Long-running tenant doesn't accumulate a 100MB activity log.
- **Today:** `services/activity_feed.py:152-166` appends one JSON line per decision. Every settings toggle, every rec dismiss, every approval. No truncation, no rotation, no archival.
- **Gap:** At 1 decision/min for a busy tenant, that's ~1.5MB/day of disk. The bigger concern is `_decision_rows()` reads the entire file into memory on every page render (`activity_feed.py:175-186`). Tenant on day 200 with 300k lines = noticeable page latency.
- **Smallest fix:** Tail-only read for the last N rows + monthly file rollover (`decisions-2026-04.jsonl`, etc.). Read aggregates the current month + the prior month for the page render; older months load on-demand for "Load older" pagination.
- **Estimated effort:** 1 day. Includes migration of existing single-file tenants.

### F6. Time format uses server local time (LA), not tenant timezone - nice-to-have-pre-launch

- **Function:** Tenant 2 in NY sees 9 AM events at 9 AM, not 6 AM (LA-shifted).
- **Today:** `activity_feed.py:84` does `local = dt.astimezone()`. With no tz argument, `astimezone()` uses the system's local tz - the dashboard server's, which is whatever the VPS Docker container is set to (likely UTC or LA, not the tenant's preference).
- **Gap:** Same issue as `phase0_settings.md::F6`. Tenant in different timezone sees confusing times. Until tenant 2, hidden.
- **Smallest fix:** Read `tenant_prefs.timezone` (already exists) and pass to `_humanize` for `astimezone(ZoneInfo(tz))`. Land alongside settings F6 (timezone UI input).
- **Estimated effort:** 0.25 day on top of settings F6.

### F7. Heartbeat rows and decision rows visually identical - nice-to-have-pre-launch

- **Function:** Owner glances at a row and immediately knows if it's an automated action or their own decision.
- **Today:** Both row types use the same shape: time + icon + role-pill + text. Heartbeats use the role-specific icon; decisions use a checkmark icon (`activity_feed.py:195`). The icon is the only visual differentiator and it's small.
- **Gap:** "Did I dismiss that, or did the system?" is hard to tell at a glance.
- **Smallest fix:** Add a left-edge color stripe per row: blue for heartbeats, green for owner decisions, amber for errors. ~5 lines of CSS.
- **Estimated effort:** 0.25 day.

### F8. Synthetic empty-state row reads as a real feed entry - nice-to-have-pre-launch

- **Function:** Cold-start owner sees clear "this is empty" messaging.
- **Today:** `activity_feed.py:232-241` returns a fake row with role="Dashboard", icon=fallback shield, action="Your activity feed wakes up...", relative="waiting". Renders inside the same `.ap-feed__row` container as a real event.
- **Gap:** New owner might think the dashboard is making this up. Or might not realize there's literally nothing else there - the row "looks like" a real event.
- **Smallest fix:** Distinguish the cold-start state at the template layer (Sam's Apr 27 `{% if feed %}` was the right shape) and make the service return an actual empty list. Move the friendly copy to the empty-state branch where it visually reads as guidance, not a feed entry. Net: one template change, one service-contract change. (Sam's existing template branch already exists at `templates/activity.html:71-78`; just need to flip the service contract.)
- **Estimated effort:** 0.25 day.

### F9. 137-char summary truncation with no expand affordance - nice-to-have-pre-launch

- **Function:** Owner reads truncated text, wants the full version.
- **Today:** `activity_feed.py:111-112` cuts at 137 chars + "...". No expand button, no tooltip with full text, no detail view.
- **Gap:** Truncated rows are dead-end information. Owner has to dig through `/opt/wc-solns/<tenant>/heartbeats/` (which they can't access) to see the full message.
- **Smallest fix:** Click a row to expand it inline (CSS-only `details/summary` or a toggle class). Show full summary, plus link to the role detail page if applicable.
- **Estimated effort:** 0.5 day.

### F10. `aria-live="polite"` on the feed but no auto-refresh - nice-to-have-pre-launch

- **Function:** Owner leaves `/activity` open, new heartbeat lands, feed updates without a refresh.
- **Today:** `templates/activity.html:51` has `<div class="ap-feed" role="log" aria-live="polite">`. No JS poll, no SSE, no websocket. Page is static after initial render.
- **Gap:** The `aria-live` attribute promises live updates to assistive tech. They never come. Mild a11y dishonesty plus owner expectation of liveness that doesn't materialize.
- **Smallest fix:** Either remove the `aria-live` (most honest, ~0 effort) OR add a 30-second poll that calls a new `GET /api/activity?since=<ts>` and prepends new rows (~1 day of work).
- **Estimated effort:** 0.1 day to remove the lie; 1 day to make it true. **Recommend removing for Phase 1D, deferring auto-refresh to Phase 2.**

### F11. Decision-log writes are best-effort and silently swallowed on OSError - nice-to-have-pre-launch

- **Function:** When a decision fails to write to disk, the user-visible action still claims success. (Same pattern across multiple API routes that call `activity_feed.append_decision`.)
- **Today:** Pattern in `api/settings.py:57-58`, `api/goals.py:57-58`, `api/recs.py` - try/except OSError around the append. The action returns 200 OK regardless.
- **Gap:** A full disk or permission flip would let the owner believe they pinned a goal / approved a draft / dismissed a rec, but `/activity` would have no record. The audit trail is silently lossy.
- **Smallest fix:** Log the OSError to a Sentry-like sink, or surface a small "audit log degraded" banner on `/activity`. At minimum, increment a tenant-level counter that surfaces to admin.
- **Estimated effort:** 0.25 day for the logging; 0.5 day for the banner.

### F12. No CSV / JSON export of the activity log - defer-to-Phase-2

- **Function:** Compliance / accountant / lawyer asks "send me a 90-day audit trail."
- **Today:** No export path. Owner could SSH and grab the JSONL but tenant 2 doesn't have shell access.
- **Gap:** Tenant 5+ concern; not urgent.
- **Smallest fix:** Add a "Download CSV" button at the bottom of `/activity`. ~0.5 day.
- **Estimated effort:** Defer until tenant 5+ or first real audit request.

### F13. No keyword search across rows - defer-to-Phase-2

- **Function:** Owner searches "Itzel" or "review" or "GBP" across the activity log.
- **Today:** No search box. Filter chips (F1) cover the common dimensions; freetext search is the next step.
- **Gap:** Power-user feature; not blocker.
- **Smallest fix:** A search input above the feed, applies a substring filter client-side over the rendered rows. ~0.5 day.
- **Estimated effort:** Defer to Phase 2.

---

## Methodology checks (per parent plan B1)

| Check | Result |
|---|---|
| Function check | Storage works (heartbeat snapshots + JSONL decisions). Truncation works. Time formatting uses server-local TZ - F6. |
| UX gap | F1 + F2 + F3 + F4 + F5 are the surface's tenant-2 hard cliffs. F4 (Dense toggle has consumer + no UI) is the most surprising. |
| Smallest fix | All findings sized in fractions of a day. Total: ~5 days for must-fix + nice-to-have. |
| Phase 1 priority bucket | Assigned per finding. |
| Composer empty state | Synthetic row pattern (F8). Sam's Apr 27 template edit is the right answer; just flip the service contract. |
| Mobile pass | Feed rows use flex layout; should stack OK. Not browser-tested this audit. |
| Confused-state recovery | F11 - silent OSError swallowing in the audit-log writers. |
| Demo gate | `PREVIEW_MODE` handled at `main.py:482`. `JUDGE_DEMO` not relevant. No regression. |
| Sidebar consistency | PASS - 7-item canonical sidebar present (`templates/activity.html:22-30`). |

---

## Phase 1D effort total

| Bucket | Effort |
|---|---|
| must-fix (F1-F5) | ~3.75 days |
| nice-to-have (F6-F11) | ~1.5 days (most are small) |
| defer (F12-F13) | N/A |
| **Total in scope** | **~5.25 days** for Phase 1D |

If F3 + F4 (server-pref bridge for privacy/dense) lands as a **shared `_prefs.html` partial** on every template, the same fix covers `phase0_settings.md::F2` for those two prefs. Saves ~0.5 day net across both audits.

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
| 10 | /auth/login + magic-link | next | - | - |

**Running totals:** 105 findings, ~37-39 days Phase 1D work mapped. With shared-dispatcher dedupe (approvals/recs/goals/settings) + shared prefs-partial (settings/activity): ~28-30 days.

## Cross-cutting themes (cumulative, updated)

1. **Cold-start cliff** - every audited surface needs a "first 24 hours" empty state pass. /activity F8 is the latest entry: synthetic row that reads as a real entry.
2. **Sidebar/topbar partials** - drift not present here; global Phase 1D fix still tracked for /role_detail.
3. **Demo-gate hygiene** - 9 PREVIEW_MODE-gated routes need a single test. /activity correctly gated.
4. **Slug normalization** - not relevant.
5. **Shared dispatcher** - /approvals + /recommendations + /goals + /settings (pause + per-pipeline approval). Four surfaces collapse into one. Combined effort: 6-7 days vs. 11-13 days.
6. **Hero coupling** - /goals F2 + /dashboard hero card #3, fix once.
7. **UX lies** - settings has 6, recommendations has 1 (apply), approvals has 1 (approve), goals has 1 (progress), and now activity has 1 (aria-live without auto-refresh). **Total: 10 unconsumed promises across 5 surfaces.** Treat "save without consumer" as the #1 Phase 1D anti-pattern.
8. **NEW: Server prefs vs. localStorage drift** - /settings writes to `tenant_prefs.json`; shell.js reads from localStorage only. Three prefs (`privacy_default`, `feed_dense_default`, `email_digest`) span this gap. Fix: a shared `_prefs.html` template partial that injects `window.WCAS_PREFS` from server data, consumed by shell.js boot.

---

## Next surface to audit

**`/auth/login`** + magic-link email + `/auth/verify` flow. Per parent plan, the login UX is the gate every tenant 2 will hit first. Need to check:
- Magic-link email copy + branding
- Token TTL and abuse protection
- Error states (expired link, wrong email, replay attempts)
- Bot/throttle handling
- Mobile experience (the link will be opened on mobile in many cases)
- Demo gate (PREVIEW_MODE bypass + the `JUDGE_DEMO` /auth/judge interplay)
- Session cookie flags + persistence
- Cold-start: a brand-new email that has never authenticated before
