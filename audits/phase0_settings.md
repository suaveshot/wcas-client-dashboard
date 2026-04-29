---
surface: /settings
audited: 2026-04-28
auditor: Larry (Claude Opus 4.7)
methodology: Phase 0 framework (function check + UX cleanup, no architecture changes)
---

# Phase 0 audit - /settings

## Summary

`/settings` is the surface where the audit's UX-lie pattern reaches peak density. The page renders six write paths (4 cosmetic toggles + per-pipeline approval matrix + a "Pause every role" danger button) and **all six write to disk while zero are read by any consumer.** Toggling produces a green "Saved" toast and changes nothing about how the dashboard or pipelines actually behave.

This is the same pattern surfaced in `audits/phase0_approvals.md::F1` (approve doesn't dispatch), `audits/phase0_recommendations.md::F1` (apply doesn't execute), and `audits/phase0_goals.md::F1` (bump_current never called). On `/settings` there is no dispatcher to write — the values are read by nobody, full stop. They are decorative until consumers exist.

This is also the right surface to discover that **tenant 2 has no way to see what `/activate` connected** — settings shows email + name but not the credentials matrix (GBP profile, Google account, Airtable base, Stripe key footprint, etc.). Owners onboard, complete activation, and then forget what they wired up.

12 findings: 5 must-fix-before-tenant-2, 5 nice-to-have-pre-launch, 2 defer-to-Phase-2.

### Top 3 priorities (1-line each)

1. **Stop lying.** Either remove the cosmetic toggles entirely, OR (better) wire one of them — `email_digest` is the cheapest end-to-end vertical to prove the consumer pattern works.
2. **Make pause functional.** The kill-switch is the highest-stakes lie on this page. A scared owner clicking "Pause every role" must actually stop heartbeats from acting; today it just writes a flag nothing reads.
3. **Add a credentials read-back card.** "Here's what your dashboard is connected to" — Google account, GBP profile, Airtable base, etc. Tenant 2 will ask "did I finish setup?" within 48 hours.

---

## Findings

### F1. "Pause every role" writes a flag that no pipeline reads - kill switch is fake - must-fix-before-tenant-2

- **Function:** Owner clicks "Pause every role" expecting all pipelines to stop sending until they resume.
- **Today:** `static/settings.js:21-34` POSTs `/api/tenant/pause`. `api/tenant.py:48-60` writes `tenant_config.json:status=paused`. The docstring (`api/tenant.py:7-9`) says "Pipelines read this file before firing; status=paused short-circuits to no-op." Grep across `dashboard_app/` for `status.*paused` returns **zero readers** outside the `api/tenant.py` write path itself.
- **Gap:** Highest-stakes UX lie in the product. An owner who suspects a misfire and hits the panic button will see "All roles paused" toast — and the next heartbeat that lands will still trigger sends. Trust collapse if they ever notice.
- **Smallest fix:** In the same dispatcher pattern that fixes `/approvals`/`/recommendations`/`/goals` (the shared `services/dispatch.py`), add a `_check_tenant_status(tenant_id)` short-circuit at the top of `dispatch.send(...)`. If `tenant_config.json:status == "paused"`, log and no-op. One function, one test.
- **Estimated effort:** 0.5 day if the shared dispatcher exists; else 1 day standalone.

### F2. Four cosmetic toggles save to disk and are never read - must-fix-before-tenant-2

- **Function:** `privacy_default`, `feed_dense_default`, `email_digest`, `errors_only` each promise specific app behavior.
- **Today:** Grep across `dashboard_app/` for each key returns **zero readers** outside `tenant_prefs.py` defaults and the settings template/API.
  - `privacy_default` (line 65) - home page should boot with privacy mode on. `templates/home.html` does not consult the pref.
  - `feed_dense_default` (line 69) - activity feed should default to Dense view. `/activity` template doesn't consult the pref.
  - `email_digest` (line 77) - "Weekly recap email on Sundays". No digest pipeline exists. No cron, no schedule, no sender.
  - `errors_only` (line 81) - "Only email me when something errors". No notification pipeline exists.
- **Gap:** Owner toggles four switches, sees four "Saved." toasts, and nothing changes anywhere. Worse: `email_digest` defaults to `True` (`tenant_prefs.py:28`), so the owner expects a Sunday email that never arrives.
- **Smallest fix:** Two-pronged.
  - Phase 1D first wave: hide `privacy_default`, `feed_dense_default`, `errors_only` behind a "Coming soon" disabled state. These are cheap to ship; flagging is honest until they're wired.
  - Phase 1D second wave: make `email_digest` actually send. The cheapest end-to-end version is a Friday batch of "5 things that happened this week" pulled from `activity_feed` for that tenant. ~2 days of work and it proves the consumer pattern for the other three.
- **Estimated effort:** 0.5 day to add the disabled UI state for the three cosmetic ones; +2 days for the digest (digest can be cut from Phase 1D scope if needed).

### F3. Per-pipeline `Approve before send` toggle is never read by `outgoing_queue` - must-fix-before-tenant-2

- **Function:** Toggle a pipeline ON in this matrix and its outgoing messages should land in `/approvals` instead of dispatching directly.
- **Today:** `api/settings.py:62-81` writes `prefs.require_approval[pipeline_id] = True`. `services/outgoing_queue.py` (entire file) does not import `tenant_prefs` and does not read the pref. The decision of "queue vs. send" lives in the calling pipeline, but no current pipeline checks the pref either.
- **Gap:** Toggling on is decorative — messages will still send autonomously. This is the surface that promises owner control over the most sensitive thing the dashboard does (sending on the owner's behalf), and the promise has zero teeth.
- **Smallest fix:** This is the same `services/dispatch.py` pattern from F1. The dispatcher reads the pref, branches to `outgoing_queue.enqueue()` if on, else dispatches. One unified gate covers F1 (paused) + F3 (approval) + the dispatcher gaps in `/approvals`/`/recommendations`/`/goals`. Combined effort drops dramatically when built once.
- **Estimated effort:** 0 incremental days if shared dispatcher already covers this; 1 day standalone.

### F4. No credentials read-back view - owner forgets what `/activate` connected - must-fix-before-tenant-2

- **Function:** "Here's what your dashboard is wired up to: Google account `sam@x.com`, GBP profile `Americal Patrol`, Airtable base `appXYZ`, etc."
- **Today:** `templates/settings.html` has Profile (just owner name + email) but no Connections / Integrations / Credentials section. The data exists - `services/credentials.py` reads from `/opt/wc-solns/<tenant>/credentials.json` - but it's not surfaced.
- **Gap:** Tenant 2 finishes activation Tuesday, opens the dashboard Wednesday, and asks "did the GBP one go through?" or "which Google account did I use?" There's no answer here. They go back to /activate and re-do steps to check. UX entirely upstream of the audit-driven Phase 1 work.
- **Smallest fix:** Add a `Connections` fieldset between Profile and Privacy & display. For each of the 7 roles, show: provider name, account label (masked email or last-4), connected_at timestamp, "Re-connect" link to /activate. Read-only. Service: `services/credentials.py::summary(tenant_id)` returns a list of redacted entries. Template renders.
- **Estimated effort:** 1 day. Includes redaction tests.

### F5. No "Resume" button when paused - state is invisible - must-fix-before-tenant-2

- **Function:** Owner pauses, then needs to resume.
- **Today:** `templates/settings.html:106` has only `id="ap-pause-all"`. No conditional render based on current `tenant_config.json:status`. The API exposes `/api/tenant/resume` (`api/tenant.py:63-75`) but no UI invokes it.
- **Gap:** Once paused (even though the pause itself doesn't work — F1), the only way to resume is calling the API directly. Owner has no way to recover.
- **Smallest fix:** Pass `tenant_status` to the template from `main.py:540-574`. Render either "Pause every role" (when active) or "Resume all roles" (when paused) plus a one-line "Paused since X" status.
- **Estimated effort:** 0.5 day. Land with F1.

### F6. Timezone in API model but no UI input - nice-to-have-pre-launch

- **Function:** Owner picks their timezone so weekly recap email and activity feed render in their local time.
- **Today:** `api/settings.py:34` accepts `timezone: str | None = Field(default=None, max_length=48)`. `tenant_prefs.py:30` defaults to `America/Los_Angeles`. Template has no timezone selector.
- **Gap:** Hardcoded LA timezone is fine while only Sam tests, fails for tenant 2 in Eastern or Mountain. The field exists; the input just isn't rendered.
- **Smallest fix:** Add a `<select>` in the Profile fieldset with the 5-6 US timezones (LA / Denver / Chicago / NY / AK / HI) pre-populated. Wire to the existing API.
- **Estimated effort:** 0.25 day.

### F7. Owner name pulled from session email split on `@` - nice-to-have-pre-launch

- **Function:** Profile section shows the owner's display name.
- **Today:** `main.py:568` does `(sess.get("em") or "").split("@")[0] if sess else "demo"`. So an owner with email `sam.alarcon+work@gmail.com` shows up as `sam.alarcon+work` in the Profile section.
- **Gap:** Reads as broken or sloppy. Edge cases like aliases, plus-addresses, or gmail dots create ugly display strings.
- **Smallest fix:** Add an editable `display_name` field to `tenant_prefs.py` defaults. Surface as an editable text input. Fall back to email-prefix only if unset.
- **Estimated effort:** 0.5 day.

### F8. "Approve before send" matrix is empty until first heartbeat - chicken-and-egg for tenant 2 - nice-to-have-pre-launch

- **Function:** Owner wants to enable approval gating BEFORE the first message is ever sent — pre-flight safety.
- **Today:** `main.py:550-560` builds the pipelines list from `telemetry.pipelines_for(tenant_id)`. If no heartbeats have landed, `snaps` is empty, template renders "No pipelines detected yet. Toggles appear here as soon as your first heartbeat lands." (`templates/settings.html:99`).
- **Gap:** Owner's first instinct is "let me set up the safety rails before anything sends." Right now they can't — they have to wait for the first send to even see the toggle. Cold-start safety hole.
- **Smallest fix:** Render the canonical 7-role list (the post-hackathon roster) regardless of heartbeat history. When no telemetry exists, show all 7 with default-off toggles. As soon as the first heartbeat for that role lands, the toggle flips into "live" state.
- **Estimated effort:** 0.5 day. Roster source already exists in `services/roster.py`.

### F9. Native browser `confirm()` for pause - inconsistent with rest of app - nice-to-have-pre-launch

- **Function:** Confirm a destructive action.
- **Today:** `static/settings.js:24` uses `if (!confirm('Pause every role? ...'))`. Everywhere else in the app, undo-able actions go through `apToast`'s queued-undo pattern (`/recommendations` dismiss, `/goals` remove, `/approvals` reject).
- **Gap:** Visual inconsistency. The browser confirm dialog also looks unbranded, jarring on mobile, and breaks if the owner has confirms muted.
- **Smallest fix:** Use a 2-step click pattern (first click → button text changes to "Click again to confirm") OR a small modal sheet. The toast undo pattern is wrong here because pausing is meant to take effect immediately.
- **Estimated effort:** 0.5 day.

### F10. No "Delete my data" / cancel-account path - nice-to-have-pre-launch

- **Function:** GDPR/CCPA self-service. Owner can request data export and account deletion.
- **Today:** No path. Owner would have to email Sam.
- **Gap:** Required for any commercial launch. Also a trust signal; absence of a delete path is itself a red flag for security-minded owners.
- **Smallest fix:** Phase 1D minimum: add an "Export my data" button (zips `/opt/wc-solns/<tenant>/`) and a "Delete account" button (2-step confirm + email Sam). Don't auto-delete in code yet; require Sam-in-the-loop until the multi-tenant cleanup story is solid.
- **Estimated effort:** 1 day for export, 0.5 day for the deletion-request UI (action itself stays manual).

### F11. No notification preview - "what does my weekly recap actually look like?" - nice-to-have-pre-launch

- **Function:** Toggle `email_digest` on, see a preview of the recap email.
- **Today:** Toggle, see "Saved.", get nothing (digest doesn't exist anyway - F2). No preview either.
- **Gap:** Trust signal. Owners that opt-in to recurring email expect to know what they're committing to.
- **Smallest fix:** Once F2's digest pipeline exists, surface a "See sample" link next to the toggle that renders a preview from the past 7 days of activity. Defer until F2 ships.
- **Estimated effort:** 0.5 day, dependent on F2.

### F12. prefs.json read on every page render - no caching - defer-to-Phase-2

- **Function:** Performance.
- **Today:** Every page that needs `tenant_prefs` reads JSON from disk per request. Fine at scale=1 (Sam) and scale=2 (tenant 2). Will become noticeable at scale=10.
- **Gap:** Pre-tenant-10 not a problem.
- **Smallest fix:** Add a TTL'd in-memory cache keyed on `(tenant_id, mtime)`. ~0.5 day.
- **Estimated effort:** Defer until tenant 5+.

### F13. No team / multi-user management - defer-to-Phase-2

- **Function:** Tenant has multiple owners (e.g., Itzel + Mariel co-own the studio).
- **Today:** Single owner per session. No invitee flow.
- **Gap:** Phase 2 work, parent plan acknowledges.
- **Smallest fix:** Out of scope for Phase 1.
- **Estimated effort:** N/A.

### F14. No audit-log view of "you turned X on/off when" - defer-to-Phase-2

- **Function:** Owner asks "did I disable Reviews two weeks ago, or did the system?" — sees a setting-change log.
- **Today:** `activity_feed.append_decision` IS being called for every settings change (`api/settings.py:51-56` and `:73-78`), and it does land in `/activity`. So the data is captured; it's just not surfaced on `/settings` itself for in-context reading.
- **Gap:** Minor — owner can still go to `/activity` and filter to `kind=settings.*`.
- **Smallest fix:** Phase 2 - tiny "recent setting changes" expandable section at the bottom of `/settings`.
- **Estimated effort:** Defer.

---

## Methodology checks (per parent plan B1)

| Check | Result |
|---|---|
| Function check | Storage works (read/write/per-pipeline); **consumers are missing across the board.** Six writeable controls, zero readers. |
| UX gap | F1 + F2 + F3 + F4 + F5 are the surface's biggest tenant-2 cliffs. F4 (no credentials view) is the most surprising omission. |
| Smallest fix | All findings sized in fractions of a day. Total: ~5-6 days for must-fix + nice-to-have. |
| Phase 1 priority bucket | Assigned per finding. |
| Composer empty state | Page itself renders fine cold; "no pipelines detected yet" copy is honest. F8 fixes the cold-start gap. |
| Mobile pass | Fieldsets stack via existing `.ap-settings__group` styles. Not browser-tested this audit. |
| Confused-state recovery | Native `confirm()` is the only inconsistency — F9. Otherwise toasts surface errors cleanly. |
| Demo gate | `PREVIEW_MODE` handled at `main.py:544`. `JUDGE_DEMO` not relevant. No regression. |
| Sidebar consistency | PASS - 7-item canonical sidebar present (`templates/settings.html:22-30`). |

---

## Phase 1D effort total

| Bucket | Effort |
|---|---|
| must-fix (F1-F5) | ~3 days (with shared dispatcher); ~5 days standalone |
| nice-to-have (F6-F11) | ~3 days (most can run in parallel) |
| defer (F12-F14) | N/A |
| **Total in scope** | **~6 days** for Phase 1D |

If F1 + F3 (kill switch + per-pipeline approval gating) land as part of the **shared `services/dispatch.py`** flagged in `audits/phase0_approvals.md::F1`, `audits/phase0_recommendations.md::F1`, and `audits/phase0_goals.md::F1`, deduct ~1.5 days. Combined-with-rest total: ~4.5 days for /settings's audit work alone.

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
| 9 | /activity | next | - | - |

**Running totals:** 92 findings, ~32-33.5 days Phase 1D work mapped (with shared-dispatcher dedupe across approvals/recs/goals/settings: ~25-27 days). Half the surfaces audited.

## Cross-cutting themes (cumulative, updated)

1. **Cold-start cliff** - every surface needs a "first 24 hours" empty state pass. /settings's F8 (canonical pipelines pre-heartbeat) is the latest entry.
2. **Sidebar/topbar partials** - drift not present here; global Phase 1D fix still tracked for /role_detail.
3. **Demo-gate hygiene** - 9 PREVIEW_MODE-gated routes need a single test. /settings correctly gated.
4. **Slug normalization** - not relevant.
5. **Shared dispatcher** - now spans /approvals + /recommendations + /goals + **/settings (pause + per-pipeline approval).** Four surfaces collapse into one fix. **The single biggest Phase 1D unlock.** Estimated combined effort: 6-7 days for the shared dispatcher vs. 11-13 days if built four times.
6. **Hero coupling** - /goals F2 + /dashboard hero card #3 — same bug, fix once.
7. **NEW: UX lies** - settings has 6 controls that all save successfully and do nothing. Recommendations apply, approvals approve, goals progress are the same shape. **Treat "save without consumer" as a Phase 1D anti-pattern to find and either remove or wire.** This is now the most cited audit theme.

---

## Next surface to audit

**`/activity`** - chronological decision log + heartbeats. Per parent plan: "Live · streaming label always renders even when feed is empty". Need to check:
- Cold-start (no heartbeats)
- Privacy mode behavior (does `/activity` honor `privacy_default`? → no, per F2)
- Filter UI for `kind=settings.*` etc. (relevant to settings F14)
- Dense vs. relaxed toggle (relevant to settings F2 `feed_dense_default`)
- Demo gate
- Sidebar consistency
- Pagination / load-more for tenants with months of history
