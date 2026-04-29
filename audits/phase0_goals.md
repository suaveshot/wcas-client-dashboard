---
surface: /goals
audited: 2026-04-28
auditor: Larry (Claude Opus 4.7)
methodology: Phase 0 framework (function check + UX cleanup, no architecture changes)
---

# Phase 0 audit - /goals

## Summary

`/goals` is a small, well-scoped surface (113-line template, 67-line JS, 113-line service, 77-line API router). Storage works correctly: pinning, removing, listing, and the per-tenant JSON file at `/opt/wc-solns/<tenant>/goals.json`. The page chrome is consistent with the rest of the post-hackathon shell (sidebar has all 7 items, breadcrumb wired, topbar trigger present).

The dominant finding is what the parent plan flagged: **the "progress math" is a placeholder.** `services/goals.py:102` defines `bump_current(tenant_id, goal_id, delta)` to nudge a goal's `current` value, but **no other code in `dashboard_app/` ever calls it.** Every pinned goal lives at `current: 0` forever. This has a downstream UX consequence on `/dashboard`: hero card #3 ("Goal progress") changes from "--" / "set a goal" / "learning" to "0%" / "0 of 20" / "behind" the moment an owner pins their first goal. **Pinning a goal makes the home page look worse, not better.** That is the most important thing to fix before tenant 2.

13 findings: 4 must-fix-before-tenant-2, 7 nice-to-have-pre-launch, 2 defer-to-Phase-2.

### Top 3 priorities (1-line each)

1. **Wire goal progress to real signals** - pick a minimum dispatcher (reviews via heartbeat metadata, leads via Airtable, calls via dialer feed) so `current` advances. Without this, goals are decorative.
2. **Stop the "behind / 0%" regression on the home hero** - once a goal is pinned but `current=0`, hero card #3 should still say "tracking" not "behind". Adjust `_goal_progress` to gate "behind" on time-elapsed, not raw percentage.
3. **Add edit-in-place** - title or target typos currently require remove + re-add, losing `created_at` and audit trail. Cheap fix; high tenant-2 dignity.

---

## Findings

### F1. `bump_current` is defined but never called - goal progress never advances - must-fix-before-tenant-2

- **Function:** Pinned goals should advance toward target as automation activity (reviews collected, leads booked, calls completed) accumulates.
- **Today:** `services/goals.py:102-112` defines `bump_current(tenant_id, goal_id, delta)`. Grep across `dashboard_app/` returns zero call sites outside the function definition itself. Heartbeat receivers, role pipelines, and the recommendations dispatcher (which doesn't exist yet either - see `audits/phase0_recommendations.md` F1) all skip the goals layer.
- **Gap:** The hero stat #3 promise ("Every recommendation anchors to them, and the Home hero tracks progress" - `templates/goals.html:48`) is unbacked. After 30 days, a goal that should read "12 of 20 reviews, on track" still reads "0 of 20, behind".
- **Smallest fix:** Stand up a minimum 3-metric dispatcher (one demo path is enough for tenant 2):
  - `leads`: increment on heartbeat where `kind=lead.created` lands
  - `reviews`: increment on heartbeat where `kind=review.posted` (5-star only) lands
  - `revenue`: defer to Phase 1B - depends on Airtable Deals first-touch attribution that the Revenue card itself is waiting on
  - `calls`: defer to Phase 1B - blocked on Twilio voice agent SMS loop
  - `other`: never auto-bump; expose a manual "+1" button on the goal card
- **Estimated effort:** 1.5 days for the leads + reviews wiring (where to call `bump_current` from `api/heartbeats.py`); 0.5 day for the manual "+1" UI on `other`-metric goals.

### F2. Pinning a goal regresses the home hero from "learning" to "behind" - must-fix-before-tenant-2

- **Function:** Owner pins their first goal and the home page should feel rewarding, not punishing. Hero card #3 ("Goal progress") should move from "--" / "set a goal" / "learning" to a proxy that says "we're watching, results coming."
- **Today:** `services/hero_stats.py:96-125` (`_goal_progress`) - if `target>0` and `current=0`, returns `("0%", "0 of 20", "behind")` because `pct=0 < 40 → "behind"`.
- **Gap:** A brand-new pinned goal at minute zero shows status text "behind" with a "0 of 20" subtitle. That is the worst UX trigger we have anywhere in the product: the action of engaging makes the dashboard look worse.
- **Smallest fix:** In `_goal_progress`, gate "behind" on time-elapsed. If `(now - created_at) < 25% of timeframe` (e.g., < ~22 days for a 90d goal), force `status = "tracking"`. Alternative: cap "behind" at any goal where `current=0 AND elapsed_pct < 50%` - just don't shame a brand-new commitment.
- **Estimated effort:** 0.5 day - a single function and a status-mapping test.

### F3. No edit-in-place: typo in title or wrong target requires remove + re-add - must-fix-before-tenant-2

- **Function:** An owner who pins "Get 20 new 5-star reivews" (typo) should be able to fix it in two clicks.
- **Today:** API exposes only `POST /api/goals` (add) and `DELETE /api/goals/{id}` (remove) - `api/goals.py:38-76`. Template offers no edit affordance. Service has no `update()` method.
- **Gap:** The owner has to remove and re-pin, which: (a) loses the original `created_at`, breaking the time-elapsed logic in F2, (b) writes a `goals.remove` then `goals.add` decision into the activity feed, polluting the audit trail with phantom "intent change" rows.
- **Smallest fix:** Add `PATCH /api/goals/{id}` that accepts `{title?, target?}` (metric and timeframe stay immutable to keep the math stable). Render an inline edit pencil icon on each card; clicking swaps the title to a small input + save/cancel.
- **Estimated effort:** 1 day - service `update()` method + API route + 1 test + small JS swap-to-input pattern (the chat composer in /activate is similar).

### F4. No "set initial progress" path on pin - must-fix-before-tenant-2

- **Function:** Tenant 2 onboards Tuesday. They've already collected 3 of their 20 target reviews this quarter and want the goal to reflect that. The pin form should accept a starting value.
- **Today:** `templates/goals.html:77-102` form has Title / Metric / Target / Timeframe only. Service `add()` at `services/goals.py:57-88` hard-codes `current: 0` (line 81).
- **Gap:** Even after F1 wires automation-driven bumps, the owner cannot account for pre-existing progress. The first 30 days of every newly-pinned goal will show 0 because the bump dispatcher only fires forward.
- **Smallest fix:** Add an optional `current` field to `GoalBody` (default 0) in `api/goals.py:26-30`. Add a "Already at" number input in the form with placeholder "0 (optional)". Service `add()` reads it and stores.
- **Estimated effort:** 0.5 day.

### F5. Goals page sidebar drift check - PASS - skip

- **Verified:** `templates/goals.html:22-30` has the canonical 7-item sidebar (Home, Roles, Approvals, Activity, Recommendations, Goals, Settings). Active class on the right item. No drift.
- **Action:** None. Mention here only because the same drift bit /role_detail and /approvals (see prior audits).

### F6. Page hard-reloads on add instead of optimistic prepend - nice-to-have-pre-launch

- **Function:** Pinning a goal should feel instant.
- **Today:** `static/goals.js:27` does `window.location.reload()` 500ms after the toast.
- **Gap:** Owner sees a toast, then a 200-300ms blank flash, then the page redraws. Compared to the optimistic-disappear pattern used on rec dismiss / approval approve, this feels janky.
- **Smallest fix:** On 200, render the new card by cloning a `<template>` block at the top of the list, no reload. Reuse the existing `.ap-goal-card` markup.
- **Estimated effort:** 0.5 day.

### F7. Remove-undo timing is confusing - card stays visible during 10s undo window - nice-to-have-pre-launch

- **Function:** Click ✕, see a "Removing... [Undo]" toast, decide.
- **Today:** `static/goals.js:34-56` shows a 10-second undo toast but the goal card stays fully visible and clickable in the meantime. Only on `onCommit` does the card actually `.remove()`.
- **Gap:** Owner clicks ✕, expects the card to fade; nothing visually changes for 10 seconds. They click ✕ again. Now they have two pending undo toasts for the same card. Click ✕ a third time and the card finally disappears (undo committed).
- **Smallest fix:** Immediately set `card.style.opacity = 0.4` and disable the ✕ button on first click. On `onCommit`, `card.remove()`. On undo, restore.
- **Estimated effort:** 0.25 day.

### F8. No validation that target makes sense for metric - nice-to-have-pre-launch

- **Function:** "Get 100,000 new 5-star reviews in 30d" should at least warn.
- **Today:** `api/goals.py:29` validates `target: float = Field(..., gt=0)` only. Service `goals.py:69` checks `target > 0`. No upper bound, no metric-aware sanity range.
- **Gap:** A typo (8 → 800) creates a goal that will never look anything other than "behind", undermining trust in the dashboard's signal quality.
- **Smallest fix:** Soft caps per metric: leads ≤ 200, reviews ≤ 500, calls ≤ 1000, revenue ≤ 1,000,000. Above the cap, return 400 with a friendly message ("That looks high for a 90-day target. Sure?"). Owner can confirm by sending `?force=1` (or a JS confirm prompt).
- **Estimated effort:** 0.5 day.

### F9. No preset goal templates - tenant 2 has to invent the wording - nice-to-have-pre-launch

- **Function:** Onboarding flow shows three example goals so the first pin is fast.
- **Today:** `templates/goals.html:77-102` form is blank. The placeholder "Get 20 new 5-star reviews" hints at one shape but disappears as soon as the user types.
- **Gap:** A non-marketing owner doesn't know the difference between a leads goal and a calls goal. They abandon the form or pin something that doesn't match how they think about success.
- **Smallest fix:** Add 3-4 starter chips above the form: "+5 reviews / 30d", "+10 leads / 60d", "+15% revenue / 90d", "Faster replies (other)". Click prefills the four inputs.
- **Estimated effort:** 0.5 day.

### F10. Timeframe is text-relative but never compared to `created_at` to show time remaining - nice-to-have-pre-launch

- **Function:** "20 of 20 reviews, on track" is good. "20 of 20 reviews, **with 14 days to go**" is strictly better.
- **Today:** `templates/goals.html:62-66` renders `({{ g.timeframe }})` as a static label like `(90d)`. No "X days remaining" calculation.
- **Gap:** Owner can't tell from a glance whether they have a week left or two months. Reduces the actionability of the goal page.
- **Smallest fix:** In `main.py:585-593`, compute `days_remaining = max(0, days_in_timeframe - (now - created_at).days)` and pass to the template. Render as `(14 days left)` next to the bar.
- **Estimated effort:** 0.5 day.

### F11. Hero stat #3 uses `goals[0]` (insertion order) - no notion of "primary goal" - nice-to-have-pre-launch

- **Function:** When the owner pins three goals, the home hero should show whichever goal they consider most important, not whichever they pinned first.
- **Today:** `services/hero_stats.py:113` literally `first = goals[0]`. No `is_primary` flag, no rank, no UI to designate.
- **Gap:** The first goal an owner pins might be "Other - email replies under 4h" (low-stakes operational), and the second might be "Reviews - 20 new 5-star" (the actual stake). Hero card #3 will display the operational one forever.
- **Smallest fix:** Add `"is_primary": bool` to the goal entry shape; default the first pinned to primary. UI: small star icon on each card, click to promote. Service `_goal_progress` filters for `is_primary=True` first, else fallback to `goals[0]`.
- **Estimated effort:** 0.5 day.

### F12. Status thresholds (75/40) are hard-coded with no time-elapsed adjustment - nice-to-have-pre-launch

- **Function:** "On track" for a 90-day goal at day 80 with 70% complete means very different things than at day 10 with 70% complete.
- **Today:** `services/hero_stats.py:118-124`:
  ```
  if pct >= 75: "on track"
  elif pct >= 40: "trending up"
  else: "behind"
  ```
  No reference to `created_at` or `timeframe`.
- **Gap:** A goal at day 5 of 90 with 30% complete is *crushing it* but the home hero says "trending up" or worse "behind". Same goal at day 85 with 30% complete is genuinely "behind" but ours treats it identically.
- **Smallest fix:** Compute `expected_pct = elapsed_days / total_days * 100`. Status:
  - `pct >= expected_pct + 10` → "on track"
  - `pct >= expected_pct - 10` → "trending up"
  - else → "behind"
  This is the natural extension of F2. Land them together.
- **Estimated effort:** 0.5 day on top of F2 (combined 0.75 day).

### F13. No "what changed since I pinned this" trail on the goal card - defer-to-Phase-2

- **Function:** Owner clicks a goal and sees the chronological list of bumps that got it from 0 to 12 ("+1 review from Itzel - Apr 24", etc.).
- **Today:** No detail view. Goal cards are read-only display.
- **Gap:** Trust signal missing. Owner has to dig through `/activity` or the heartbeat log to see what counted toward a goal.
- **Smallest fix:** Add a `services/goals.py::history(tenant_id, goal_id)` that joins activity_feed rows with `kind=goals.bump` and a `goal_id` field on each. New route `GET /goals/{id}` for detail, or expand-on-click drawer.
- **Estimated effort:** 1.5 days. Defer until F1 dispatcher exists - history is meaningless without bumps.

### F14. No per-recommendation hookup ("does this rec move my reviews goal?") - defer-to-Phase-2

- **Function:** Recommendations card shows "Pin 5 reviews to GBP - this moves your Reviews goal +5 toward 20."
- **Today:** Recs are decoupled from goals. No `goal_id` field on rec entries. No badge on rec cards.
- **Gap:** The promise in `templates/goals.html:48` ("Every recommendation anchors to them") is half-true: goals exist, recs exist, but they don't know about each other.
- **Smallest fix:** Add optional `target_goal_id` field to `recs_store` entries. Phase 1D when both F1 here and the dispatcher in `audits/phase0_recommendations.md::F1` ship together.
- **Estimated effort:** 1 day, but only after the recs dispatcher exists. Defer.

---

## Methodology checks (per parent plan B1)

| Check | Result |
|---|---|
| Function check | Storage works (read/add/remove tested in `tests/test_goals*.py` if present - verify); progress math is **placeholder, confirmed**. F1 is the single biggest claim from the parent plan, validated. |
| UX gap | F2 + F3 + F4 are the three things tenant 2 will hit on day 1. |
| Smallest fix | All findings sized in fractions of a day. Total: ~6.5 days for must-fix + nice-to-have, ~2.5 days deferred. |
| Phase 1 priority bucket | Assigned per finding. |
| Composer empty state | Empty list = no `<li>` rendered, form appears immediately. Reasonable cold-start. F9 (preset chips) elevates from "reasonable" to "good". |
| Mobile pass | Form uses semantic `<label>` wrappers - should stack. Not tested in browser this audit; flag for Phase 1D pre-deploy mobile check. |
| Confused-state recovery | API errors surface via toast (`goals.js:23-29`). Network failure on remove drops the card, undo path covers it. Good. |
| Demo gate | `PREVIEW_MODE` handled at `main.py:580`. `JUDGE_DEMO` not relevant here. No regression. |
| Sidebar consistency | PASS - 7-item canonical sidebar present. |

---

## Phase 1D effort total

| Bucket | Effort |
|---|---|
| must-fix (F1-F4) | ~3.5 days |
| nice-to-have (F6-F12) | ~3 days |
| defer (F13-F14) | ~2.5 days, blocked on F1 + recs dispatcher |
| **Total in scope** | **~6.5 days** for Phase 1D |

If F1 (goal bump dispatcher) lands as part of the **shared dispatcher** flagged in `audits/phase0_recommendations.md::F1` and `audits/phase0_approvals.md::F1`, deduct ~0.5 day from F1 here. Combined-with-recs-and-approvals total: ~6 days.

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
| 8 | /settings | next | - | - |

**Running totals:** 80 findings, ~26-27.5 days Phase 1D work mapped (with shared-dispatcher dedupe across approvals/recs/goals: ~22-24 days).

## Cross-cutting themes (cumulative, updated)

1. **Cold-start cliff** - every surface needs a "first 24 hours" empty state pass. /goals is one of the few that handles it well.
2. **Sidebar/topbar partials** - drift not present here, but the global fix (Phase 1D) still needed for /role_detail.
3. **Demo-gate hygiene** - 9 PREVIEW_MODE-gated routes need a single test to confirm tenant-only access. /goals correctly gated.
4. **Slug normalization** - not relevant to /goals.
5. **Shared dispatcher** - now spans /approvals (queued send), /recommendations (per-rec-type apply), AND /goals (current bump). All three should land as one `services/dispatch.py` registry. **This is the biggest single Phase 1D unlock.** Estimated combined effort: 5-6 days for the shared dispatcher vs. 8-9 days if built three times.
6. **Hero coupling** - /goals F2 and /dashboard's hero card #3 are the same bug seen from two surfaces. Fix on the hero side once.

---

## Next surface to audit

**`/settings`** - per parent plan, the 16th surface. Will check:
- Tenant prefs (notifications, business hours, on-call escalation)
- Credentials view (read-only re-display of what `/activate` collected)
- Tenant info (name, timezone, contact)
- Demo gate (PREVIEW_MODE)
- Sidebar consistency
- Role-based gating (does Settings show fields the owner shouldn't see?)
- Cold-start (fresh tenant with no prefs set)
