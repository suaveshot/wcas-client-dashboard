# Phase 0 audit - /roles/{slug}

**Date:** 2026-04-28
**Surface:** `/roles/{role_slug}` (per-role detail page)
**Audit depth:** function check + UX cleanup only (per parent plan `~/.claude/plans/alright-larry-the-hackathon-kind-swing.md`)
**Scope:** read-only walk; no code edits in this deliverable.

## Summary

11 findings: **3 must-fix-before-tenant-2**, **6 nice-to-have-pre-launch**, **2 defer-to-Phase-2**.

Top 3 priorities:

1. **F1** Sidebar is missing `Approvals` AND `Goals` nav items. The post-hackathon sidebar-fix commit `67572c1` updated `/dashboard` but didn't propagate to `/roles/{slug}`. Tenant on a role page can't navigate to the pending-approvals queue without going home first.
2. **F2** No "Run now" button per parent plan §`/roles/{slug}` (Phase 1.5 line 53). When a role errors, owner has no manual-trigger path - must wait for next cron tick. Tenant-2-blocking for any role that runs daily or weekly.
3. **F3** No "what this role does" header copy. The page shows `{{ role_name }}` + status + telemetry, but never explains in plain English what the automation is FOR. Owner can't differentiate a confusing "Configured but no run yet" state from "this is the wrong role."

Function check: when there IS a heartbeat, the page does its job - status, summary, state grid, log timeline, raw log details, receipts trigger, role-grounded Ask form. Empty-state handling is honest. The biggest gaps are missing chrome (F1) and missing affordances (F2, F3) the parent plan already flagged.

## Surface map

- **Route:** `dashboard_app/main.py:460-475` (`GET /roles/{role_slug}`)
- **Slug regex guard:** `main.py:463` - `^[a-z0-9][a-z0-9_-]{0,63}$` (defensive; blocks path traversal)
- **Composer:** `dashboard_app/services/role_detail.py` (72 lines) - `build()` reads heartbeat, runs `log_timeline.parse()` on `log_tail`, returns 11-key context
- **Template:** `dashboard_app/templates/role_detail.html` (162 lines)
- **Reused services:** `home_context._role_display`, `home_context._humanize_ago`, `home_context._state_from_status`, `services.log_timeline.parse`
- **JS:** `static/undo.js`, `static/shell.js`, `static/ask.js` (per-role grounded ask), receipts modal triggered by class `.ap-receipts-trigger` (handler probably in shell.js or a separate receipts.js)
- **Stylesheet cache-buster:** `v=20260422d` (line 11) - older than `/dashboard`'s `v=20260425g` and `/roles`'s `v=20260426a`

---

## Findings

### F1. Sidebar missing `Approvals` + `Goals` nav items - must-fix-before-tenant-2

**Function:** Every authed surface should expose all 7 nav items so the tenant can move between any two surfaces in one click.

**Today:** `role_detail.html:24-47` shows 5 nav items: Home, Roles, Activity, Recommendations, Settings. Missing: Approvals, Goals. The post-hackathon commit `67572c1` ("fix(home): add Approvals + Goals to sidebar nav") only touched `/dashboard` (`home.html`). It propagated to `/roles` (`roles.html:36-46` has Approvals at line 40, but is also missing Goals - so /roles is also incomplete; this audit catches it as a re-finding of /roles audit F2's "drift" issue).

Comparison:

| Nav item | `/dashboard` | `/roles` | `/roles/{slug}` |
|---|---|---|---|
| Home | ✓ | ✓ | ✓ |
| Roles | ✓ | ✓ | ✓ |
| Approvals | ✓ | ✓ | **missing** |
| Activity | ✓ | ✓ | ✓ |
| Recommendations | ✓ | ✓ | ✓ |
| Goals | ✓ | **missing** | **missing** |
| Settings | ✓ | ✓ | ✓ |

**Gap:** Tenant on `/roles/{slug}` who realizes a draft is queued can't get to `/approvals` without going Home -> Approvals (two clicks instead of one). Same for Goals.

**Smallest fix:** Bundles into `/roles` audit F2 (the partials extraction). Build `templates/_partials/sidebar.html` with all 7 items + active-state logic, replace the inline nav in `home.html`, `roles.html`, `role_detail.html`, `activity.html`, `goals.html`, `settings.html`, `approvals.html`, `recommendations.html`. Active-state via `{% set active_nav = "roles" %}` set by each template, partial reads it.

Estimated effort: **part of /roles F2 partials extraction (~1 day total)**. Marginal cost on top of /roles F2: 0 minutes - this is automatically fixed when the partial lands.

---

### F2. No "Run now" / manual trigger button - must-fix-before-tenant-2

**Function:** When a role errors or stalls, the owner should have a one-click "try again" path that fires the pipeline manually outside its scheduled window.

**Today:** Nothing. The page has the receipts trigger (`role_detail.html:116-122`), the Ask form (`:134-149`), and the raw log `<details>` block (`:108-113`). No run-now button.

**Gap:** Parent plan flags this explicitly: "Add manual 'run now' button (Phase 1.5)" (`alright-larry-the-hackathon-kind-swing.md` Phase 0 row for `/roles/{slug}`). Tenant 2 is a real test case - if Garcia's Reviews pipeline errors at 2 AM, Itzel's only recourse is to message Sam and wait. Self-service retry is the correct behavior.

**Smallest fix:** Add a button next to the receipts trigger:

```html
<button class="ap-btn ap-btn--secondary ap-run-now"
        data-pipeline-id="{{ role_slug }}"
        type="button">Run now</button>
```

Click POSTs to a new `/api/role/{slug}/run` endpoint. Endpoint validates the slug, looks up the pipeline's manual-trigger handler via a registry (`services/pipeline_runners.py` - new file), invokes it as a subprocess, returns `{"ok": true, "run_id": "..."}`. Page polls `/api/activation/state` (or a new role-status endpoint) to see when the heartbeat updates.

Important guard: rate-limit the run-now action (one per role per 60 seconds) so an impatient owner doesn't fan out 10 manual runs. Also guard against the role not having a manual handler registered yet (some pipelines may not be safely re-runnable; show "manual run not supported for this role" and a Sam-escalation hint).

Estimated effort: **1.5 days** including the registry + per-pipeline manual handlers for the 7 onboarding roles + rate-limit + error-state UI + tests. Real tenant-2 unblocker.

---

### F3. No "what this role does" header copy - must-fix-before-tenant-2

**Function:** A tenant new to a role's page should immediately know what the automation does, in their own voice. Not "Reviews" - but "Drafts review replies for your Google Business Profile in your voice. You approve before they go live."

**Today:** `role_detail.html:69` is `<h1 class="ap-role-detail__title">{{ role_name }}</h1>`. The next visible line (line 70-73) is the meta line "Status: ... Last run ...". No prose about what the role does, what its outputs look like, what cadence it runs at, or where the work lands.

**Gap:** Role names alone aren't self-documenting. "Email Assistant" - what does it do, exactly? "GBP" - draft posts, reply to reviews, both? "Blog" - publishes where, in what voice, how often? Owner has to ask Sam.

**Smallest fix:** Add a per-role description constant in `services/role_descriptions.py` (or extend `services/roster.py` with a `description` field). Each entry has a 1-2 sentence "what this does" string written in tenant-voice from the playbook KB. Render between the meta line and the summary on `role_detail.html`.

```python
ROLE_DESCRIPTIONS = {
    "reviews": "Drafts review replies for your Google Business Profile in your voice. You approve each one before it goes live.",
    "gbp": "Posts updates and offers to your Google Business Profile every Tuesday morning. Honest about pricing, no fluff.",
    "email_assistant": "Drafts replies to inbound emails in your voice using your knowledge base. Drafts only - you review and send.",
    # ...one per role
}
```

Estimated effort: **3 hours** including writing the 7 descriptions + threading through the route handler + template render + a test that every roster role has a description.

---

### F4. CSS cache-buster is stale (`v=20260422d`) - nice-to-have-pre-launch

**Function:** Static assets cache for 7 days on Hostinger per `lessons/nearmiss_static_css_cached_7_days.md`. Cache-buster query string forces revalidation when CSS changes ship.

**Today:** `role_detail.html:11` references `styles.css?v=20260422d` and `:158-160` references `undo.js`, `shell.js`, `ask.js` all at `?v=20260422d`. Other surfaces are at `v=20260425g` (`/activate`, `/dashboard`) or `v=20260426a` (`/dashboard`, `/roles`). The 0.7.1 deploy today shipped CSS edits to `static/index.html` and `templates/activity.html` + `goals.html`, but `role_detail.html` wasn't bumped.

**Gap:** Tenant who browses `/dashboard` (loads `styles.css?v=20260425g` - fresh CSS) and then clicks into a role detail page (which references `styles.css?v=20260422d`) will hit the OLDER cached copy if the browser cached `v=20260422d` previously. Cross-page CSS inconsistency until the cache rolls.

**Smallest fix:** Bump every cache-buster in `role_detail.html` to match the latest (whatever the next deploy ships). Better: centralize cache-buster in a Jinja global so all templates share one version. Estimated effort: **15 minutes** for the bump; **30 minutes** for the centralization.

---

### F5. Inline `style=` attribute in breadcrumb - nice-to-have-pre-launch

**Function:** Style declarations belong in the cache-busted stylesheet, not inline.

**Today:** `role_detail.html:58` has `<a href="/dashboard" style="color:var(--ink-muted);text-decoration:none;">Home</a>`. Same pattern in `/roles` audit F3 (inline `<style>` block) - this is one inline `style=` instead of a block.

**Gap:** Style fragmentation. CSS class `.ap-shell__breadcrumb a` should handle this.

**Smallest fix:** Add `.ap-shell__breadcrumb a { color: var(--ink-muted); text-decoration: none; }` to `styles.css`. Remove the inline. Estimated effort: **10 minutes**.

---

### F6. Status CSS class set unverified for all states - nice-to-have-pre-launch

**Function:** `role_detail.html:71` renders `ap-role-detail__status--{{ status }}` where status comes from payload `(payload.get("status") or "unknown").lower()` (`role_detail.py:47`). Every status value the system can produce should have a corresponding CSS class so the color renders correctly.

**Today:** Status values flowing through: `ok`, `error`, `paused`, `unknown`, anything-else (which falls into the `unknown` branch in `home_context._state_from_status`). Plus `waiting` from the no-snapshot path (`role_detail.py:35`). Need to verify `styles.css` has `.ap-role-detail__status--ok`, `.ap-role-detail__status--error`, `.ap-role-detail__status--paused`, `.ap-role-detail__status--unknown`, `.ap-role-detail__status--waiting`. If any is missing, the status pill renders unstyled.

**Gap:** Audit can't fully verify without grepping styles.css. Likely fine because the page has been running on AP, but worth a verification pass before tenant 2.

**Smallest fix:** Grep `styles.css` for `ap-role-detail__status--` and confirm all 5+ variants exist. If any missing, add. Estimated effort: **15 minutes**.

---

### F7. State rows show raw payload keys with title-case formatting - nice-to-have-pre-launch

**Function:** State summary rows should show human-friendly labels.

**Today:** `role_detail.py:55` does `"label": k.replace("_", " ").title()`. So `pending_review_count` becomes "Pending Review Count". Better than raw, but cosmetic. "Pending" is fine; "Review Count" is borderline; "Sla Hours Remaining" is bad (SLA should be uppercase).

**Gap:** Per-pipeline payloads have field names tuned for the pipeline author, not the owner. Generic title-case won't catch acronyms (SLA, GBP, NAP) or compound terms.

**Smallest fix:** Add a per-key label dictionary in `role_detail.py`:

```python
STATE_LABELS = {
    "pending_review_count": "Reviews waiting",
    "sla_hours_remaining": "SLA hours left",
    "last_action": "Last action",
    "next_run": "Next run",
    # ...common ones, fall back to title-case
}
```

Apply in `_state_rows` builder. Estimated effort: **1 hour** for an initial label dictionary covering the 20-30 most common state-summary keys across the 7+ roles.

---

### F8. Receipts trigger uses dash-form slug - nice-to-have-pre-launch

**Function:** The receipts modal trigger at `role_detail.html:116-119` carries `data-pipeline-id="{{ role_slug }}"` where `role_slug` is the dash form (e.g. `email-assistant`). The downstream receipts API likely keys on the underscore form (e.g. `email_assistant`).

**Today:** `role_detail.py:17` handles both forms via `_find_snapshot()` (matches `pid == role_slug or pid == underlying`). But the receipts modal handler in JS may not. Worth verifying.

**Gap:** If the receipts API expects underscore form, the dash-form data attribute breaks the modal silently for any pipeline whose name contains an underscore (`email_assistant`, `chat_widget`, `sales_pipeline`, `morning_reports`).

**Smallest fix:** Either (a) thread the underscore form through the template as a separate `data-pipeline-id-underscore` attribute, or (b) have the JS normalize dash-to-underscore before calling the receipts API. Verify what the API expects via `/api/receipts/{pipeline_id}` route handler. Estimated effort: **30 minutes** to verify + fix.

---

### F9. PREVIEW_MODE gate same as before - nice-to-have-pre-launch

**Function:** Same as `/dashboard` F3 and `/roles` F7. `PREVIEW_MODE=true` bypasses session, hardcodes `tenant_id = "americal_patrol"` (`main.py:472`).

**Today:** Identical pattern.

**Smallest fix:** Bundles into the Demo-gate hygiene pass. Marginal cost: 5 minutes for one extra smoke test pair.

---

### F10. No "history" / past runs view - defer-to-Phase-2

**Function:** Show last N runs of this role (not just last one) so the owner can see trends, error rate, recent recovery.

**Today:** Only the most-recent heartbeat snapshot is rendered. `heartbeat_store.read_all()` returns one snapshot per pipeline. There's no per-pipeline run history beyond what receipts captures (which is per-message, not per-run).

**Gap:** Real but defer-able. Receipts modal addresses the "what did this send" question; run history addresses "how reliable is this." Phase 2 work.

**Recommendation:** Phase 2. Probably ships alongside the per-tenant cost dashboard (parent plan §3C) which also needs historical telemetry.

---

### F11. No retry-on-failure UX when receipts modal API fails - defer-to-Phase-2

**Function:** Receipts trigger should handle the modal-API failure gracefully (network error, 500, 401).

**Today:** Audit can't verify without reading `shell.js` / `receipts.js` (didn't load it for this audit). Worth a separate JS-bundle audit pass.

**Recommendation:** Phase 2 along with the broader JS error-handling pass.

---

## Function-check verdicts (the things that work and need no change)

- **Slug validation regex** (`main.py:463`): defensive, blocks path traversal. Pass.
- **Composer dual-form snapshot match** (`role_detail.py:18`): handles both `email-assistant` and `email_assistant`. Pass.
- **state_rows cap at 16** (`role_detail.py:69`): defensive against payloads with 100+ state keys. Pass.
- **log_tail cap at 4000 chars** (`role_detail.py:57`): defensive against runaway logs. Pass.
- **`<details>` for raw log** (`role_detail.html:109-113`): collapsed by default; doesn't dominate the page. Pass.
- **Privacy class on state values** (`role_detail.html:85`): consistent with other surfaces. Pass.
- **Per-role grounded Ask form** (`role_detail.html:134-149`): the right pattern - "ask about this role's recent telemetry" - and the textarea placeholder is brand-voiced. Pass.
- **Empty-state copy** (`role_detail.html:128-130`): honest, doesn't fabricate. Pass.
- **Cold-start `has_snapshot=False` branch** (`role_detail.py:30-43`): returns a clean stable shape so the template never null-derefs. Pass.

## Effort summary by bucket

| Bucket | Findings | Total estimate |
|---|---|---|
| must-fix-before-tenant-2 | F1 (folds into partials extraction), F2 (1.5d), F3 (3h) | **~2 days standalone** (F1 free if partials done) |
| nice-to-have-pre-launch | F4 (15m), F5 (10m), F6 (15m), F7 (1h), F8 (30m), F9 (folds into bundle) | **~2 hours** |
| defer-to-Phase-2 | F10, F11 | **0** |
| **Phase 1D `/roles/{slug}` UX cleanup total** | | **~2 days** |

## Cross-surface observations

- **F1 folds into /roles F2 partials extraction.** Building the sidebar partial automatically fixes the missing-Approvals + missing-Goals drift here AND on `/roles`.
- **F4 cache-buster** suggests other surfaces may also be on outdated busters. Worth a one-shot grep across all templates and align everything to a single date string per deploy.
- **F8 dash-vs-underscore slug normalization** likely affects any other surface that builds API URLs from slugs (Approvals queue per-role drill-in, Recommendations Apply handlers, Activity feed role-pill links). Worth bundling into a "slug normalization pass" task.
- **F2 manual-run-now and F3 role-description** both reach beyond this surface - the description copy lives in the role registry, the run-now machinery is the foundation for Phase 2's per-tenant scheduler. Treat these as Phase 1 platform work that surfaces here, not as `/roles/{slug}`-only edits.

## Cumulative Phase 0 progress

| # | Surface | Status | Findings | Phase 1D effort |
|---|---|---|---|---|
| 1 | `/activate` | done | 10 (3+5+2) | ~4 days |
| 2 | `/dashboard` | done | 12 (3+6+3) | ~2.5-3.5 days |
| 3 | `/roles` | done | 8 (2+5+1) | ~1.5 days |
| 4 | `/roles/{slug}` | done | 11 (3+6+2) | ~2 days |
| 5 | `/approvals` | next | - | - |

## Next surface to audit

`/approvals` - the queue that has to actually work for tenant 2. Per the parent plan: "Plumbing works; only fills if a tenant pipeline writes drafts. Verify 'approve' actually triggers a real send through the new generic pipelines; needs Phase 1 wiring." High-stakes surface because it's where the human-in-the-loop contract lives. Audit before Phase 1 wires real pipeline drafts into it.
