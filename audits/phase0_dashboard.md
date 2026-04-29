# Phase 0 audit - /dashboard (home)

**Date:** 2026-04-28
**Surface:** `/dashboard` (the home / week-summary view)
**Audit depth:** function check + UX cleanup only (per parent plan `~/.claude/plans/alright-larry-the-hackathon-kind-swing.md`)
**Scope:** read-only walk; no code edits in this deliverable.

## Summary

12 findings: **3 must-fix-before-tenant-2**, **6 nice-to-have-pre-launch**, **3 defer-to-Phase-2**.

Top 3 priorities:

1. **F1** Cold-start roles grid shows ONE placeholder card ("First run pending") instead of all 7 expected roles in a `pending` state. Fresh tenant has no idea what's about to come online.
2. **F2** Cold-start has no "first 7 days" timeline explaining when each role starts producing output. The narrative copy says "first heartbeat will arrive after the next scheduled execution" - that's accurate but vague; the owner can't tell if "next" is in 1 hour or 1 week.
3. **F3** A third demo gate `PREVIEW_MODE` (separate from `JUDGE_DEMO` shipped in 0.7.1 and `?demo=1`) exposes `/dashboard`, `/roles`, `/approvals`, `/goals`, `/settings`, `/activity`, `/recommendations`, and 2 others to anyone unauthed when set true. Currently OFF on the VPS (verified live), but needs runbook documentation alongside the other two gates and a regression test.

Function check overall: the home surface DOES the right work for tenants with heartbeats. The narrative + hero stats + roles grid + activity feed + recommendations all render with real telemetry, with thoughtful empty-state copy on each component. Hero stats handle cold-start correctly with "--" placeholders and verified-tip messages. Recommendations fall back to seeded recs (12) so the surface is never blank. The bleak-cold-start risk the parent plan called out IS real - but it's smaller than the parent plan implied, and concentrated in the roles grid + missing timeline. The other components are honest and graceful.

## Surface map

- **Route:** `dashboard_app/main.py:785-807` (`GET /dashboard`)
- **Template:** `dashboard_app/templates/home.html` (357 lines) - whole-page layout: sidebar rail, topbar, narrative, hero stats, quick-actions, roles grid, feed/recs split
- **Context composer:** `dashboard_app/services/home_context.py` (307 lines) - top-level `build()` at line 102; cold-start fallbacks at lines 271 (`_hero_stats_placeholder`, defined but unused - see F8), 294 (`_fallback_roles_when_empty`)
- **Hero stats:** `dashboard_app/services/hero_stats.py` (173 lines) - real math from heartbeats; honest "--" placeholders for cold-start
- **Activity feed:** `dashboard_app/services/activity_feed.py` (241 lines) - heartbeat snapshots + decisions.jsonl; cold-start renders one row "wakes up on the first heartbeat"
- **Recommendations:** `services/recs_store.py` + `services/seeded_recs.py` + `services/rec_actions.py` filter chain
- **Static JS:** `static/shell.js`, `static/undo.js`, `static/rec_actions.js` (no home-specific bundle)
- **Demo mock:** `dashboard_app/main.py:659-783` (`_demo_home_context()`) - Americal Patrol hand-crafted mock used only when `PREVIEW_MODE=true`

---

## Findings

### F1. Cold-start roles grid shows 1 placeholder card, not 7 expected roles - must-fix-before-tenant-2

**Function:** A fresh tenant who just finished `/activate` should see all 7 roles on the home page in a `pending` state, with a clear "this one starts on its next scheduled tick" affordance per role. The roles grid is the home page's primary anchor.

**Today:** `home_context.py:294-307` defines `_fallback_roles_when_empty()` returning a single card:

```python
return [{
    "slug": "first-run",
    "name": "First run pending",
    "state": "active",
    "state_text": "queued",
    ...
}]
```

That's it. One card. The `roles or _fallback_roles_when_empty()` selector at `home_context.py:187` swaps the 7 expected roles for that one card whenever the heartbeat snapshot list is empty - which is exactly the case for every fresh tenant for the first 24 hours.

**Gap:** The home page that should anchor on "look at all your roles" anchors on a vague single placeholder. Combined with F2 (no timeline), the fresh tenant lands here, sees one card, and has no model of what's about to happen.

**Smallest fix:** Replace `_fallback_roles_when_empty()` with logic that reads the activated roster from `services/roster.py` (the same source-of-truth `/activate` uses) and returns 7 cards in a `pending` state, each with the role's display name and a `state_text` like "Starts on next scheduled run." The grid becomes self-documenting.

```python
def _fallback_roles_when_empty() -> list[dict[str, Any]]:
    from . import roster
    return [
        {
            "slug": r["slug"],
            "name": r["name"],
            "state": "pending",
            "state_text": "starts on next scheduled run",
            "actions": 0,
            "influenced": "0",
            "last_run": "queued",
            "grade": None,
            "spark_path": _SPARK_FLAT,
        }
        for r in roster.ACTIVATION_ROSTER
    ]
```

CSS likely needs a new `.ap-role-card--pending` variant (muted, no spark animation, no grade pill). Estimated effort: **2-3 hours** (Python + CSS + test).

---

### F2. No "first 7 days" timeline for cold-start tenants - must-fix-before-tenant-2

**Function:** A fresh tenant should see a timeline showing when each role's first scheduled run will fire. "Reviews runs daily at 9 AM, first run tomorrow morning." "Blog runs weekly on Mondays, first post Apr 29." This is the contract the home page makes about what's about to happen.

**Today:** Nothing. The narrative for cold-start tenants reads:

> Your roles are connected and queued for their first run. The first heartbeat will arrive after the next scheduled execution; this page will wake up as soon as data flows.

(`home_context.py:158-162`). It's honest but vague. There's no rendered list of "Reviews: tomorrow 9 AM. Blog: Monday 7 AM. GBP: Tuesday 8 AM." anywhere on the surface.

**Gap:** Owner can't tell if the platform is broken or just patient. Combined with F1, the cold-start home page communicates "something is queued" without saying when, what, or in what order.

**Smallest fix:** Add a "First 7 days" timeline section between the narrative (Row 1) and the hero stats (Row 2). Renders only when `not has_live` (zero heartbeats yet). Each row shows a date, a role, and the expected first-run time. Schedule data lives in cron / `tenant_scheduler.py` (Phase 2 ships) - for Phase 1 hardcode the schedule per role from the WCAS playbook defaults (Reviews daily 9 AM, GBP daily 8 AM, Blog weekly Mon 7 AM, etc.) in a new `services/role_schedules.py` constant. Phase 2 makes it tenant-configurable.

Estimated effort: **1 day** (template section + CSS + the schedules constant + a `_first_runs_for_cold_start()` helper in `home_context.py` + test).

---

### F3. PREVIEW_MODE is a third demo gate, undocumented - must-fix-before-tenant-2

**Function:** Production `/dashboard` and 8 other internal surfaces should be auth-gated. No public path to seeing Sam's AP mock data.

**Today:** Setting `PREVIEW_MODE=true` on the VPS `.env` bypasses session auth on these routes (`main.py:785-807` for /dashboard plus 8 more route handlers grepped at lines 409, 421, 467, 481, 500, 544, 580, 613, 794). When that flag is true and the request has no session, the route serves the hand-crafted Americal Patrol mock at `_demo_home_context()` (`main.py:659-783`).

VPS is currently correctly OFF (verified live: `/dashboard` returns 303 to login on 2026-04-28 evening). But:

1. There's no docstring or runbook entry warning that flipping `PREVIEW_MODE=true` exposes Sam's tenant mock data publicly to anyone without a session.
2. There's no smoke test asserting the route returns 303 when `PREVIEW_MODE=false` AND there's no session (analogous to the JUDGE_DEMO 404 test we shipped in 0.7.1).
3. JUDGE_DEMO + PREVIEW_MODE + `?demo=1` is now THREE demo gates, none documented together. Future Sam (or future Larry) flipping one without remembering the others is a real risk.

**Gap:** Same security profile as JUDGE_DEMO before 0.7.1 - a stale env flag could expose synthetic tenant data to a search-engine-indexing path. Not on fire, but unmonitored.

**Smallest fix:** Three pieces:

1. Add a docstring at `main.py:659` (`_demo_home_context`) and at every route handler that reads `PREVIEW_MODE` saying: "Default off. When true, this route bypasses session auth and serves the AP mock context. Only enable on the VPS when recording marketing footage; flip OFF immediately after."
2. Add smoke tests `test_dashboard_303_when_preview_off`, `test_dashboard_serves_mock_when_preview_on`. Mirror the pattern from `tests/test_smoke.py:test_judge_demo_404_when_gate_closed`.
3. Add a "Demo gates" section to JOURNAL Entry 18 (or a dedicated `docs/runbook/demo_gates.md`) listing the three: `JUDGE_DEMO`, `PREVIEW_MODE`, `?demo=1` - what each does, default state, when to flip, when to flip back.

Estimated effort: **2 hours** (docstring + tests + runbook entry).

---

### F4. "Live · streaming" label always renders even when feed is the empty-state row - nice-to-have-pre-launch

**Function:** The "Live · streaming" eyebrow at `home.html:271` should communicate that the feed is connected and updating. When there's nothing flowing, that label is misleading.

**Today:** `<div class="ap-feed__live">Live · streaming</div>` is hardcoded into the template, rendered unconditionally inside `.ap-feed`. When `feed` contains only the cold-start empty-state row from `activity_feed.py:232-241` ("Your activity feed wakes up on the first heartbeat..."), the page still says "Live · streaming" right above it.

**Gap:** Cold-start tenant reads "Live · streaming" + "Your activity feed wakes up on the first heartbeat." Cognitive dissonance.

**Smallest fix:** Wrap the live label in `{% if feed and feed[0].time %}` (the empty-state row has `time=""`). Or better: add a `feed_is_cold_start` boolean to the context and gate on that. Estimated effort: **15 minutes**.

---

### F5. Roles grid header has dead "Pinned only" link - nice-to-have-pre-launch

**Function:** `home.html:225-228` has a section-head control reading `View: All · Pinned only` with both as anchor tags pointing to `#`. Should toggle the role grid filter.

**Today:** Both `<a href="#">` tags are inert - no JS handler, no query-param scaffold, no filter on the server side. Click does nothing except scroll to top.

**Gap:** Looks interactive, isn't. UX rule: every visible affordance should do something or be removed.

**Smallest fix:** Either wire it (1 day - filter `roles` server-side via `?view=pinned` query param using `pinned_roles` slugs as the allow-list) or remove the toggle entirely (5 minutes). The pinned roles are already surfaced in the sidebar rail (`home.html:78-88`); the duplication on the home grid is low-value. **Recommendation: remove.**

Estimated effort: **5 minutes remove**, **1 day wire**.

---

### F6. Quick-action chips ("Set a goal", "Pause a role", "Request something", "Ask") have no JS handlers - nice-to-have-pre-launch

**Function:** `home.html:199-216` renders 4 prominent quick-action chips. Should each fire its action or open a flow.

**Today:** All four are bare `<button type="button">` with no event listeners. No handlers in `shell.js`, `undo.js`, or `rec_actions.js` (the three JS bundles loaded on home). They're decorative.

**Gap:** Same problem as F5 - prominent affordances that look clickable, do nothing. The "Ask" chip is a particular UX trap because the topbar's "Ask" button (`home.html:136-139`) does work (opens the global Ask drawer); the home-canvas "Ask" chip is a duplicate that doesn't.

**Smallest fix:** Three of them have natural handlers:

- "Set a goal" → `window.location = "/goals/new"` (or open a goal-create modal)
- "Pause a role" → open a role-picker modal that calls `/api/settings/role/<slug>/pause`
- "Ask" → trigger the same handler the topbar Ask button uses (`shell.js` likely has it - reuse)

The "Request something" chip is the orphan - what does it do? If it's "request a feature from Sam", it's an email drafter; if it's "request the team build me a one-off automation", it's a concierge intake form. Worth confirming intent before wiring.

Estimated effort: **3-4 hours** for the 3 with clear handlers; **defer Request-something** until intent is locked. Or **5 minutes remove all 4** if quick-actions aren't load-bearing for the demo and Phase 1D revisits them.

---

### F7. Notifications bell badge renders count but no popover - nice-to-have-pre-launch

**Function:** `home.html:140-143` shows a bell icon with a count badge when `notifications_count > 0`. Clicking should open a notifications popover/list.

**Today:** Bare `<button class="ap-shell__bell">` with no click handler. The badge correctly reflects `notifications.count(tenant_id)` but clicking does nothing.

**Gap:** Day-1 essential #1 from the parent plan: "Notifications - SMS or email when something needs approval, daily digest 7 AM, weekly recap Friday. Otherwise `/approvals` rots." The bell is the in-app surface for the same need. Not wiring it leaves the count badge as a tease.

**Smallest fix:** Two-stage approach.

1. **Phase 1 (~3 hours):** click opens a popover listing the items - reuse the `/approvals` queue. Each item links to its detail. Mark-all-read button. No real-time push, just on-render fetch.
2. **Phase 2 (~1-2 days):** wire to a daily-digest email + per-event SMS path (depends on Twilio A2P approval per `blocker_twilio_a2p_pending.md`).

Estimated effort: **3 hours for the popover** in Phase 1.

---

### F8. `_hero_stats_placeholder` is dead code - nice-to-have-pre-launch

**Function:** Either use the function or delete it.

**Today:** `home_context.py:271-291` defines `_hero_stats_placeholder(n_roles)` returning 3 honest-placeholder cards. The function is never called - `build()` uses `hero_stats.build(tenant_id)` from the dedicated `hero_stats.py` module instead, which has its own (slightly different) placeholders. The local function is leftover from before `hero_stats.py` was extracted.

**Gap:** Dead code reads as a fork - someone editing one set of placeholders won't know there's a parallel set in the unused function.

**Smallest fix:** Delete `_hero_stats_placeholder` (lines 271-291). Add a regression test that `home_context.build(tenant_id)` returns the same hero-stats shape as `hero_stats.build(tenant_id)` directly to lock in the single source of truth.

Estimated effort: **15 minutes delete**, **30 minutes if also adding the regression test**.

---

### F9. Mobile rail trigger exists but no test of mobile flow - nice-to-have-pre-launch

**Function:** Mobile owner taps the hamburger trigger (`home.html:120-122`) and the sidebar rail slides in. Mobile-first is in durable preferences.

**Today:** The trigger button exists with `aria-controls="ap-shell-rail"` and `aria-expanded="false"`. `shell.js` (per the script tag at line 353) presumably wires it. Need to verify the slide-in works at <860px (the breakpoint identified in /activate audit), and that the rail is dismissible via tap-outside.

**Gap:** Audit can't fully verify mobile flow without DevTools. The auditor (me, today) didn't run a real-device pass. Same gap flagged in `/activate` audit F6.

**Smallest fix:** Real-device test on a 360px-width phone. Actions to verify: tap trigger → rail in; tap any nav item → rail out + navigate; tap outside rail → rail out; tap topbar search pill → keyboard pops; quick-action chips reachable without sidebar in the way; role cards stack 1-col below 767px (existing CSS handles this per `styles.css:963`).

Estimated effort: **30 minutes real-device test**. Findings feed Phase 1D.

---

### F10. Privacy-mode `.ap-priv` blur class is wired but the toggle is missing - nice-to-have-pre-launch

**Function:** Sensitive numbers (revenue, owner name, hero values) wear `.ap-priv` so a privacy mode can blur them when Sam is screen-sharing. Toggle should live in the topbar or settings.

**Today:** `.ap-priv` is consistently applied at `home.html:108, 184, 243` and across the codebase. But there's no visible toggle anywhere on the home surface. `shell.js` may have the toggle keybinding (probably `?p` or similar), but no UI affordance surfaces it.

**Gap:** Designed-for-privacy product where the privacy toggle is hidden. Sam will hit a moment screen-sharing with a prospect where he wants to blur revenue and won't remember the keybind.

**Smallest fix:** Add a small "eye" icon button in the topbar between the search pill and the Ask button. Click toggles a `body[data-privacy="on"]` attribute; CSS selectors `body[data-privacy="on"] .ap-priv { filter: blur(8px); }` apply the blur. Persist the state to localStorage so it survives reloads.

Estimated effort: **2 hours** (icon + JS toggle + CSS + localStorage).

---

### F11. `?demo=1` runs recommendation-scan animation on first paint - defer-to-Phase-2

**Function:** Same gate as `/activate`'s `?demo=1` - enables `agent_viz.js` overlays. On `/dashboard` it triggers a "scan recommendations" overlay on first paint per the comment at `home.html:17-19`.

**Today:** `window.DEMO_VIZ` set from URL query (line 19). Public, anyone can trigger.

**Gap:** Same not-really-a-gap as `/activate` F9. No synthetic data exposure, just visual flourishes.

**Recommendation:** Document in the runbook entry covered by F3 alongside `JUDGE_DEMO` and `PREVIEW_MODE`. No code change.

---

### F12. Hero stat "Revenue influenced" direction is hardcoded "up" even when value is "--" - defer-to-Phase-2

**Function:** When a stat is in cold-start with value "--", the direction arrow should not imply trend.

**Today:** `hero_stats.py:151` hardcodes `"direction": "up"` for the Revenue card unconditionally. Same on `home_context.py:271-291` (the dead-code placeholder). Template renders `↗` next to "--" which reads as "trending up to dash."

**Gap:** Cosmetic. Misreads as "we're not measuring this yet, but it's going up." Honest framing should drop the arrow when the value is a placeholder.

**Smallest fix:** Add `"direction": "neutral"` (or simply drop the field) when value is "--". Template already handles unknown direction with `→` (the right-arrow flat indicator at line 188). Estimated effort: **15 minutes**.

---

## Function-check verdicts (the things that work and need no change)

- **Narrative cold-start copy** (`home_context.py:158-162`): honest, brand-voiced, doesn't fabricate. Pass.
- **Hero stats cold-start "--" + verified-tip** (`hero_stats.py:128-173`): correct pattern; only F12 is a nit.
- **Recommendations seeded fallback** (`home_context.py:166-171`): cold-start tenants see 12 seeded recs filtered through `rec_actions.filter_unacted` capped at 3 + footer "See all (12)". Excellent. The applied/dismissed-recs-stick fix from commit `597acc4` works.
- **Activity feed cold-start row** (`activity_feed.py:232-241`): one honest empty-state row. Better than blank. F4's "Live · streaming" label issue is the only nit.
- **Attention banner singular discipline** (`home_context.py:133-147`): one error or one overdue, never multiple. Smart.
- **Rail health summary** (`home.html:34-42`): inline counts at the top of the sidebar - "14 roles · 11 running · 2 attention · 1 error" - exact right density.
- **Pinned roles + recent asks in sidebar** (`home.html:77-102`): both render only when populated. No empty headers.
- **Sidebar nav has 7 items** (Home, Roles, Approvals, Activity, Recommendations, Goals, Settings - per commit `67572c1`). Matches every other surface. Pass.
- **Demo gates: VPS PREVIEW_MODE is OFF** (verified live, `/dashboard` 303 to login). The gate's wiring works correctly when default-closed.

## Effort summary by bucket

| Bucket | Findings | Total estimate |
|---|---|---|
| must-fix-before-tenant-2 | F1 (3h), F2 (1d), F3 (2h) | **~1.5 days** |
| nice-to-have-pre-launch | F4 (15m), F5 (5m or 1d), F6 (3-4h or 5m), F7 (3h), F8 (30m), F9 (30m), F10 (2h) | **~1 day** if removing dead chips, **~2 days** if wiring everything |
| defer-to-Phase-2 | F11 (runbook note), F12 (15m) | **15 minutes** |
| **Phase 1D `/dashboard` UX cleanup total** | | **~2.5-3.5 days** |

## Cross-surface observations

Items that compound with `/activate` audit findings or appear elsewhere in Phase 0:

- **Demo-gate audit needed across all 9 routes** that read `PREVIEW_MODE`. F3 covers `/dashboard`; the same gate also affects `/roles`, `/approvals`, `/goals`, `/settings`, `/activity`, `/recommendations` plus 2 others. Each route needs the same docstring + smoke test treatment. Bundle into a single Phase 1 W1 task: "Demo-gate hygiene pass."
- **Cold-start expected-roles list** (F1 fix) reuses `services/roster.py` - the same source `/activate` uses. Centralizing into `roster.ACTIVATION_ROSTER` benefits both surfaces and any future `/roles` cold-start work.
- **Empty state + "first 7 days" timeline** (F2) is the same pattern that should land on `/roles` and `/roles/{slug}` - all three surfaces have a "before first heartbeat" cliff. Build the timeline component once, render in all three places.
- **Quick-action and bell wiring** (F6, F7) intersects with the parent plan's notifications + day-1-essentials list. Phase 1 W1 work.

## Next surface to audit

Per the parent plan's Phase 0 table, in priority-of-scrutiny order:

1. ~~`/activate`~~ done (`audits/phase0_activate.md`)
2. ~~`/dashboard`~~ done (this file)
3. **`/roles`** - the all-roles index. Per memory, just shipped post-hackathon (commit `235c05f`); audit it before Phase 1 builds on it.
4. `/roles/{slug}` - per-role detail
5. `/approvals` - the queue that has to actually work for tenant 2
6. `/recommendations` - audit each rec type's "Apply" handler (whether each actually does something)
7. `/goals` - progress-math placeholder
8. `/settings` - Pause/Resume gap
9. `/activity` - quick scan (mostly verified via the activity_feed module read here)
10. Magic-link email + `/auth/login` flow - copy review for Claude/Opus naming
11. `/legal/terms` + `/legal/privacy` - lawyer review trigger
12. `/healthz` - confirm nothing
13. `/demo/activation` + `/demo/dashboard` - confirm gate (already verified post-0.7.1)
14. `/` (public homepage) - confirm post-0.7.1 cleanup
15. 401/404/500 templates - copy polish

`/roles` is the natural next pick: just shipped, has known cold-start parallels with `/dashboard`, and the parent plan's Phase 0 table calls out the same gap ("Empty for new tenants").
