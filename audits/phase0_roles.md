# Phase 0 audit - /roles

**Date:** 2026-04-28
**Surface:** `/roles` (the all-roles index)
**Audit depth:** function check + UX cleanup only (per parent plan `~/.claude/plans/alright-larry-the-hackathon-kind-swing.md`)
**Scope:** read-only walk; no code edits in this deliverable.

## Summary

8 findings: **2 must-fix-before-tenant-2**, **5 nice-to-have-pre-launch**, **1 defer-to-Phase-2**.

Top 2 priorities:

1. **F1** Cold-start `/roles` shows a single empty-state paragraph instead of the 7 expected roles in `pending` state. Same gap as `/dashboard` F1; same fix - read `roster.ACTIVATION_ROSTER` and emit all 7.
2. **F2** `/roles` is missing every cross-surface chrome element that `/dashboard` has: rail health summary, pinned roles, recent asks, search pill, notifications bell, account avatar. Two routes that should feel like the same product feel like two different ones.

This is the smallest surface in Phase 0 (template is 96 lines, route handler is 40 lines, just shipped post-hackathon in commit `235c05f`). The audit is correspondingly short. Most findings are consistency drift from `/dashboard`, not bugs in the new code itself.

## Surface map

- **Route:** `dashboard_app/main.py:418-457` (`GET /roles`)
- **Template:** `dashboard_app/templates/roles.html` (96 lines, includes a 14-line inline `<style>` block at lines 12-26)
- **Data source:** `services.heartbeat_store.read_all(tenant_id)` directly (no intermediate composer service unlike `home_context.build` for `/dashboard`)
- **Cross-references reused from `home_context`:** `_humanize_ago`, `_state_from_status`, `_role_display`, `_display_from_slug` (all imported / called inline at `main.py:435-454`)
- **JS:** `static/undo.js`, `static/shell.js` (no roles-specific bundle)
- **Demo gate:** `PREVIEW_MODE=true` env bypasses session, hardcodes `tenant_id = "americal_patrol"` (`main.py:421-424`). Same gate flagged in `/dashboard` audit F3.

---

## Findings

### F1. Cold-start shows single empty-state paragraph instead of 7 expected roles - must-fix-before-tenant-2

**Function:** A fresh tenant who finished `/activate` should see all 7 roles on `/roles` in a `pending` state, mirroring `/dashboard` F1.

**Today:** `roles.html:84-86` Jinja `{% else %}` block:

```html
{% else %}
<p class="ap-rec-empty__body">Roles will appear here once their first heartbeat arrives.</p>
{% endfor %}
```

One paragraph. No grid, no role names, no schedule.

**Gap:** Identical to `/dashboard` F1. The role index that should anchor the tenant's mental model of "here are my 7 automations" anchors on a vague placeholder. Worse than `/dashboard` because at least the home grid had ONE card; this is just text.

**Smallest fix:** Move the cold-start logic out of the route handler. Build a shared helper - `services/roles_index.py:build_index(tenant_id)` - that returns the same 7-role pending list when heartbeats are empty. Both `/roles` and the `_fallback_roles_when_empty()` fix from `/dashboard` F1 call into it. Single source of truth.

```python
def build_index(tenant_id: str) -> list[dict[str, Any]]:
    snaps = heartbeat_store.read_all(tenant_id)
    if not snaps:
        return [
            {"slug": r["slug"], "name": r["name"], "state": "pending",
             "last_action": "Starts on next scheduled run.",
             "last_run": "queued", "run_count": 0}
            for r in roster.ACTIVATION_ROSTER
        ]
    # ...existing per-snapshot row building moved here from main.py:428-447
```

CSS reuses the `.ap-roles-row__dot--pending` class added during `/dashboard` F1's fix. Estimated effort: **2 hours** (extract helper + cold-start branch + tests). Lands as part of the same commit that fixes `/dashboard` F1.

---

### F2. Cross-surface chrome drift - must-fix-before-tenant-2

**Function:** `/roles` is one of the 7 sidebar nav items. It should feel identical to every other surface in shell, sidebar, and topbar.

**Today:** Reading `roles.html` against `home.html`, the following elements are present on `/dashboard` and missing on `/roles`:

| Element | `/dashboard` | `/roles` | Why it matters |
|---|---|---|---|
| Sidebar rail-health strip ("14 roles · 11 running...") | `home.html:34-42` | absent | The page is literally about roles - it should show role health prominently |
| Sidebar pinned-roles section | `home.html:77-88` | absent | Sam's pins should follow him across surfaces |
| Sidebar recent-asks section | `home.html:90-102` | absent | Same |
| Sidebar account footer (avatar + name + plan) | `home.html:104-112` | absent | Owner orientation |
| Topbar search pill (Cmd-K) | `home.html:129-133` | absent | Cross-surface keyboard shortcut |
| Topbar notifications bell | `home.html:140-143` | absent | The notification count belongs everywhere |

**Gap:** `/roles` reads as a stub that someone built after `/dashboard` and never circled back to harmonize. The post-hackathon commit `235c05f` was specifically labeled "replace /roles placeholder" - the wiring shipped, the chrome catch-up didn't.

**Smallest fix:** Extract the `/dashboard` sidebar + topbar markup into Jinja partials (`templates/_partials/sidebar.html`, `templates/_partials/topbar.html`) that `/roles`, `/dashboard`, `/approvals`, `/activity`, `/goals`, `/settings`, `/recommendations`, and `/roles/{slug}` all `{% include %}`. Build the includes once, swap the inline markup on every surface, never have this drift again.

Estimated effort: **1 day**. The biggest item is auditing every surface to confirm none has a reason to deviate from the shared chrome. Most of them shouldn't. Worth it because it makes every future Phase 0 audit smaller and Phase 1D's UX cleanup pass cleaner. **Recommendation: prioritize this AHEAD of `/dashboard` F1 + F2 because the partials are also where the cold-start fixes from `/dashboard` will live.**

---

### F3. Inline `<style>` block instead of styles.css - nice-to-have-pre-launch

**Function:** Style declarations should live in `static/styles.css` (the cache-busted single file) so changes don't require template edits and the browser can cache the bundle.

**Today:** `roles.html:12-26` has 14 lines of inline `<style>` defining `.ap-roles-grid`, `.ap-roles-row`, `.ap-roles-row__dot`, etc. These look reusable; the parent plan calls for `/roles/{slug}` and other surfaces to share the same cards.

**Gap:** Style fragmentation. Each new surface that wants role cards has to re-define them.

**Smallest fix:** Move the inline block into `static/styles.css` under an `Activity / Roles index` section header. Bump the cache-buster on the styles.css link to `v=YYYYMMDD<letter>` per the durable preference about Hostinger's 7-day static cache. Estimated effort: **30 minutes**.

---

### F4. Sort order ignores state priority - nice-to-have-pre-launch

**Function:** Errored or attention-needed roles should rise to the top of the index. Sorted purely alphabetically, an errored Reviews role buries below Sales Pipeline and Social.

**Today:** `main.py:447` does `role_rows.sort(key=lambda r: r["name"])`. Alphabetic, no state weighting.

**Gap:** Tenant landing on `/roles` to triage a problem has to scan the whole list. The state dot exists (line 75 in template), but eye-scan order is name-driven.

**Smallest fix:** Two-key sort: state priority (error > attention > paused > active > pending) then name. Estimated effort: **15 minutes**.

```python
STATE_PRIORITY = {"error": 0, "attention": 1, "paused": 2, "active": 3, "pending": 4}
role_rows.sort(key=lambda r: (STATE_PRIORITY.get(r["state"], 99), r["name"]))
```

---

### F5. No grade pill, no spark line, no influenced-revenue meta - nice-to-have-pre-launch

**Function:** `/dashboard`'s role cards (`home.html:232-249`) carry a grade letter (A/B/C/F), a sparkline, and an "influenced" dollar metric. The same data is in `home_context.py:121-131`. `/roles` strips all three.

**Today:** `/roles` row shows: state dot, name, last action text, run count, last run timestamp. That's it.

**Gap:** Inconsistent density. `/dashboard` is the abridged view (3-rec home cap, top-of-the-week summary); `/roles` should be the FULL view. It's currently the SAME or LOWER density than `/dashboard`. Reversed.

**Smallest fix:** Reuse the same `home_context._state_from_status()` returns the grade and spark path; the route handler at `main.py:436-447` already calls it but discards `_grade` and `_spark` (note the underscore prefix - they're explicitly thrown away). Just rename the locals and pass them through to the template. Add the cells to `roles.html`. Estimated effort: **1 hour** including CSS for the per-row sparkline.

---

### F6. Last-action fallback "Running on schedule." applies to errored roles too - nice-to-have-pre-launch

**Function:** When a role's payload has no `last_action` or `summary`, the template falls back to a generic line. That fallback shouldn't override real status text on an errored role.

**Today:** `roles.html:78` reads `{{ role.last_action or 'Running on schedule.' }}`. If a role is in `state="error"` AND its payload has no `last_action`, the row shows a green "running on schedule" message under a red error dot. Conflicting signal.

**Gap:** Will hit the moment a real pipeline errors with a payload that doesn't include a summary line. Visible UI lie.

**Smallest fix:** Branch the fallback per state in the route handler:

```python
fallback = {
    "error": "Errored on last run; check the role page.",
    "attention": "Hasn't reported in a while.",
    "paused": "Paused.",
    "pending": "Starts on next scheduled run.",
}.get(state, "Running on schedule.")
last_action = payload.get("last_action") or payload.get("summary") or fallback
```

Estimated effort: **15 minutes**.

---

### F7. PREVIEW_MODE gate same as /dashboard F3 - nice-to-have-pre-launch

**Function:** Same as `/dashboard` F3. `/roles` reads `PREVIEW_MODE` at `main.py:421` and bypasses session auth when true.

**Today:** Identical pattern. When the env flag is on and there's no session, the route hardcodes `tenant_id = "americal_patrol"` (line 424) and serves AP heartbeat data publicly.

**Gap:** Same threat profile as `/dashboard` F3.

**Smallest fix:** Bundles into the "Demo-gate hygiene pass" Phase 1 W1 task identified in `/dashboard` audit's cross-surface observations. One docstring, one smoke test pair, one runbook entry, applied to all 9 routes.

Estimated effort: **part of the bundled Demo-gate hygiene pass**. Marginal cost on top of `/dashboard` F3: 5 minutes for one extra smoke test pair.

---

### F8. No filter or grouping on the role grid - defer-to-Phase-2

**Function:** When there are 14+ roles (AP today, Ultra-tier tenants future), the user should be able to filter to a subset.

**Today:** Flat grid. No filter chips, no search-within-roles, no grouping by state.

**Gap:** Not urgent at 7 roles (Garcia / typical Pro tenant). At 14 roles (AP), the grid takes 2-3 scrolls on mobile. Worth doing eventually; not before tenant 2.

**Smallest fix:** Add 5 filter chips above the grid: `All · Running · Attention · Errored · Paused`. Click sets a `?state=` query param and re-renders filtered. Optionally a search input. Defer until Sam onboards Ultra-tier tenant or AP comes back into the platform fully (Phase 3B).

Estimated effort: **3-4 hours** when the time comes.

---

## Function-check verdicts (the things that work and need no change)

- **Route handler validates role_slug regex on the detail page** (`main.py:463`): `^[a-z0-9][a-z0-9_-]{0,63}$` - good defensive pattern, blocks path traversal.
- **Roles grid uses CSS auto-fill responsive grid** (`roles.html:13`): `repeat(auto-fill, minmax(280px, 1fr))`. Truly responsive without breakpoints. Pass.
- **Skip-to-main-content anchor** (`roles.html:29`): a11y baseline holds.
- **Eyebrow + title + lead pattern** matches `/approvals` and other content surfaces. Pass.
- **Run count + last-run combo** is the right at-a-glance density per row. Pass.

## Effort summary by bucket

| Bucket | Findings | Total estimate |
|---|---|---|
| must-fix-before-tenant-2 | F1 (2h, shares fix with `/dashboard` F1), F2 (1d) | **~1 day + 2 hours** |
| nice-to-have-pre-launch | F3 (30m), F4 (15m), F5 (1h), F6 (15m), F7 (part of bundle) | **~2 hours** |
| defer-to-Phase-2 | F8 (3-4h, defer) | **0** |
| **Phase 1D `/roles` UX cleanup total** | | **~1.5 days** |

Of which: **F2 (sidebar/topbar partials extraction) is the highest-leverage item across the whole Phase 0 audit so far.** Building the partials once removes drift risk on every remaining surface. Recommend prioritizing F2 ahead of `/dashboard` F1+F2 in the Phase 1D queue, then folding F1 fixes into the partials' cold-start branch.

## Cross-surface observations

- **F1 share fix with `/dashboard` F1.** `services/roles_index.py:build_index` becomes the single source of truth for "what does a fresh-tenant role list look like." Both routes call it. The 7-role roster lives in `roster.ACTIVATION_ROSTER` (already exists per memory).
- **F2 partials extraction is THE prerequisite** for clean `/roles/{slug}`, `/approvals`, `/activity`, `/goals`, `/settings`, `/recommendations` audits in Phase 0. Each of those surfaces likely has its own minor drift from `/dashboard`. Doing the partials first means every subsequent audit's chrome findings collapse to "yep, includes the partial, pass."
- **F3 (inline style block)** suggests `/roles/{slug}` and other recently-shipped surfaces may have similar fragments. Worth a one-shot scrub as part of F2's partials work.
- **F5 (drop fields on `/roles`)** is a symptom of build-incrementally; the 30-minute fix is also a 30-minute parity exercise.

## Cumulative Phase 0 progress

| # | Surface | Status | Findings | Phase 1D effort |
|---|---|---|---|---|
| 1 | `/activate` | done | 10 (3+5+2) | ~4 days |
| 2 | `/dashboard` | done | 12 (3+6+3) | ~2.5-3.5 days |
| 3 | `/roles` | done | 8 (2+5+1) | ~1.5 days |
| 4 | `/roles/{slug}` | next | - | - |

## Next surface to audit

`/roles/{slug}` - the per-role detail page. Just shipped post-hackathon (commit `b0d4a63` fixed the tenant name on it; the underlying renderer existed earlier). Audit before any role-detail Phase 1 work. Lives at `main.py:460+`.
