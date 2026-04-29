# Phase 0 audit - /approvals

**Date:** 2026-04-28
**Surface:** `/approvals` (the human-in-the-loop draft queue)
**Audit depth:** function check + UX cleanup only (per parent plan `~/.claude/plans/alright-larry-the-hackathon-kind-swing.md`)
**Scope:** read-only walk; no code edits in this deliverable.

## Summary

13 findings: **3 must-fix-before-tenant-2**, **7 nice-to-have-pre-launch**, **3 defer-to-Phase-2**.

Top 3 priorities:

1. **F1 (CRITICAL)** **Approve doesn't actually send.** The API marks the draft approved + writes a receipt + writes a decision-log entry, but never dispatches the message. The owner sees a green "Approved" toast that's effectively a UX lie. The pipeline that enqueued the draft must poll the archive for status=approved on its next cron tick to actually send - which means up to 24h lag for daily roles, longer for weekly. Tenant-2 launch breaker: a Garcia review reply approved Tuesday at 9 AM doesn't go out until the weekly Reviews pipeline tick - or never, if no dispatcher is wired.
2. **F2** Sidebar/topbar drift (chrome partials F2 from /roles audit) - `/approvals` has the full 7-item nav (good) but is missing the topbar search pill, notifications bell, and account avatar. Also no rail-health summary or pinned-roles list. Same drift class.
3. **F3** No bulk actions. With 10+ drafts pending (which is realistic on day 8 of a tenant), owner clicks through one-by-one. "Approve all," "Approve all from Reviews," "Skip all over 12h" should exist. Tenant-2-blocking once volume hits.

Function check: the queue's plumbing is genuinely well-built. Two-stage guardrails (enqueue + approve), atomic JSONL writes, threading.Lock for single-process serialization, drafts re-queued on guardrail rejection at approve-time so the owner doesn't lose their work, receipts integration, decision-log integration, keyboard shortcuts (J/K/A/E/S) wired and tested per `approvals.js:30-54`, undo toast with 10-second window. The architecture is the strongest of the four surfaces audited so far. **The single biggest gap is the missing "actually send" dispatcher hook**, which is what F1 is about.

## Surface map

- **Route:** `dashboard_app/main.py:607-653` (`GET /approvals`)
- **Template:** `dashboard_app/templates/approvals.html` (114 lines)
- **JS:** `dashboard_app/static/approvals.js` (163 lines) - per-draft handlers + keyboard shortcuts + undo toast
- **Service:** `dashboard_app/services/outgoing_queue.py` (231 lines) - enqueue / approve / skip / list_pending / summary; two-stage guardrails; atomic JSONL writes; threading lock
- **API:** `dashboard_app/api/outgoing.py` (94 lines) - `GET /api/outgoing/pending`, `POST /api/outgoing/{draft_id}/approve`, `POST /api/outgoing/{draft_id}/skip`
- **Receipt store:** `services/receipts` - audit trail of every approved/sent action
- **Activity feed integration:** `outgoing.py:60-68` writes `outgoing.approve` / `outgoing.skip` decision rows
- **Data layout:**
  - `/opt/wc-solns/<tenant>/outgoing/pending.jsonl` - the queue (rewritten on every mutation)
  - `/opt/wc-solns/<tenant>/outgoing/archived.jsonl` - history (append-only)
- **Guardrails:** `services.guardrails.review_outbound` runs on every enqueue AND every approve; rejection on approve re-queues the draft so owner can edit.
- **Demo gate:** `PREVIEW_MODE=true` bypasses session, hardcodes `tenant_id = "americal_patrol"` (`main.py:613-617`)

---

## Findings

### F1. Approve doesn't actually send the message - must-fix-before-tenant-2 (CRITICAL)

**Function:** Owner clicks Approve. Email/SMS/post goes out. That is the entire contract of this surface.

**Today:** The approve flow does NOT dispatch. Reading the code path end-to-end:

1. `approvals.js:80-93` - JS shows a "Approved" toast and POSTs to `/api/outgoing/{draft_id}/approve`
2. `api/outgoing.py:48-70` - calls `outgoing_queue.approve(tenant_id, draft_id, edited_body=...)` and returns `{"ok": True, "status": entry["status"], "draft_id": entry["id"]}`
3. `services/outgoing_queue.py:167-201` - pulls the draft from pending.jsonl, runs guardrails, writes the entry to archived.jsonl with status="approved" or "edited", calls `receipts.append(...)`, returns the entry

The docstring at `outgoing_queue.py:168-170` explicitly says:

> Approve (and optionally edit) a pending draft. Writes a receipt; **caller is responsible for the actual network send.** Returns the final entry so the caller can invoke the pipeline's send(...) with the approved body.

But `api/outgoing.py:55-70` is the caller, and it does NOT invoke any send function. It just returns 200. There's no dispatcher service. There's no send_email() / send_sms() / publish_post() handler. The pipeline that enqueued the draft (e.g., `reviews/run.py` writing a review-reply draft) has no notification path.

The implicit design likely intended pipelines to poll `archived.jsonl` for status=approved entries and dispatch them on their next cron tick. That works for the steady state but introduces latency (up to one cron-cycle of delay) and doesn't survive: a pipeline that errors after enqueuing won't ever come back to send approved drafts.

**Gap:** The toast says "Approved" - the message doesn't go out. Tenant 2 will discover this the first time Itzel approves a review reply that never appears on Google Business Profile.

**Smallest fix:** Two options:

1. **In-band dispatch (~2-3 days)** - Add a `services/dispatch.py` registry mapping `pipeline_id` to a dispatcher callable (`reviews_dispatcher`, `gbp_dispatcher`, etc.). On approve, the API endpoint calls `dispatch.send(entry)` immediately after `outgoing_queue.approve(...)` returns. Dispatcher does the actual SMTP/HTTP send. On send failure, mark entry `status=approved_send_failed` in archived.jsonl and surface to a new "Send failures" section in the approvals UI. The 7 onboarding roles each need a dispatcher; Phase 1 W2-W5 work folds in here.

2. **Out-of-band poll (~half day, but worse)** - Add a `services/dispatch_worker.py` that runs every minute on the VPS, polls all tenants' `archived.jsonl` for entries with status=approved + dispatched_at unset, sends them, marks dispatched_at. Simpler but introduces latency, makes the approval feel laggy, and a worker outage means approvals pile up silently.

**Recommendation: option 1.** The architecture already has the registry pattern (`services/activation_tools.py` does it for activation tools). Reuse the pattern for dispatchers. Folds naturally into the parent plan's §1B "tenant-ize the 4 Google-backed pipelines" work because each generic pipeline ships its dispatcher alongside its run.py.

Estimated effort: **2-3 days** for the dispatch registry + 7 dispatchers (most are 1-2 hours each: Gmail SMTP for email_assistant, GBP API for reviews/gbp, etc.) + send-failure UI + tests including a failure-recovery flow.

---

### F2. Cross-surface chrome drift - must-fix-before-tenant-2

**Function:** `/approvals` should feel identical to every other authed surface in shell, sidebar, and topbar.

**Today:** The sidebar nav is correct (full 7 items including the active `Approvals` with count badge - line 27-29). But:

- Topbar (line 39-45): just the breadcrumb. Missing the rail trigger button (no mobile hamburger), Ask button, search pill, notifications bell.
- Sidebar (line 18-36): has nav, but missing rail-health summary, pinned roles, recent asks, account footer.
- No `id="ap-shell-rail"` on the aside (line 18) or `aria-controls` mobile-trigger machinery either - this means the mobile nav can't toggle.

**Gap:** Same drift class as `/roles` audit F2 and `/roles/{slug}` audit F1. Different shape (this surface keeps the nav but loses the chrome around it), same root cause: every surface inlines its own copy of shell markup.

**Smallest fix:** Bundles into the partials extraction. `templates/_partials/sidebar.html` + `templates/_partials/topbar.html` get included here. Estimated effort: **part of partials extraction (~1 day total across all surfaces)**. Marginal cost on top: 0 minutes.

---

### F3. No bulk actions (Approve all, Approve all from X, Skip all over Nh) - must-fix-before-tenant-2

**Function:** When 10+ drafts are pending (realistic by day 8 of a Pro-tier tenant with all 7 roles producing drafts), owner needs a way to bulk-process.

**Today:** Each draft has its own Approve / Edit / Skip buttons. To approve 10 review replies, owner clicks Approve 10 times.

**Gap:** Tenant-2 will hit this. Itzel approves daily; if she misses 3 days, that's 12+ drafts. Click-through fatigue is a real onboarding-stage churn driver.

**Smallest fix:** Add a sticky bulk-action bar above the list. Three actions: `Approve all (N)`, `Approve from {pipeline} (M)` (dropdown picker), `Skip all over 12h (P)`. Backend endpoint `POST /api/outgoing/bulk` accepting `{action: "approve", filter: {}}` or similar.

Each individual action still goes through the same `outgoing_queue.approve()` so guardrails fire per-item. If any item fails guardrails, it stays in pending and the owner sees a "5 of 7 approved, 2 need your eyes" toast. Bulk action requires a confirm dialog ("Approve 12 drafts?") to prevent accidents.

Estimated effort: **1.5 days** (UI + bulk endpoint + per-item iteration with partial-success handling + confirm dialog + tests). Lands as part of Phase 1D.

---

### F4. Cache-buster `v=20260422d` is stale - nice-to-have-pre-launch

**Function:** Same as `/roles/{slug}` F4. CSS cache-buster should match latest deploy.

**Today:** `approvals.html:11, 110-112` all reference `v=20260422d`. Stale relative to `/dashboard` (`v=20260425g`) and `/roles` (`v=20260426a`).

**Smallest fix:** Bump alongside `/roles/{slug}` F4 + the centralization recommendation. Estimated effort: **part of cross-surface bumper pass (~30 min)**.

---

### F5. Inline `style=` on breadcrumb - nice-to-have-pre-launch

**Function:** Same finding as `/roles/{slug}` F5.

**Today:** `approvals.html:41` - inline `style="color:var(--ink-muted);text-decoration:none;"` on the Home breadcrumb anchor.

**Smallest fix:** Add `.ap-shell__breadcrumb a` rule to styles.css; remove the inline. **Same fix lands once across all surfaces** if done as part of the partials work. Estimated effort: **part of partials extraction**.

---

### F6. Edit-textarea body uses `<pre>` for display - nice-to-have-pre-launch

**Function:** Approval body should render in a way that matches what the channel actually does. For email, `<pre>` is wrong (preserves whitespace exactly, no wrap). For SMS, `<pre>` is right. For a GBP post, neither is quite right.

**Today:** `approvals.html:73` uses `<pre class="ap-approval__body">{{ d.body }}</pre>` for every draft regardless of channel. The CSS likely has `white-space: pre-wrap` on `.ap-approval__body` to soften this; need to verify.

**Gap:** Channel-aware rendering is missing. Email body shows with `<pre>` tag's monospace-by-default. SMS bodies render with weird wrapping. GBP posts (markdown-ish) show their syntax characters as literals.

**Smallest fix:** Branch the rendering on `d.channel`:

```jinja
{% if d.channel == "email" %}
<div class="ap-approval__body ap-approval__body--email">{{ d.body|safe_email_render }}</div>
{% elif d.channel == "sms" %}
<pre class="ap-approval__body ap-approval__body--sms">{{ d.body }}</pre>
{% else %}
<div class="ap-approval__body">{{ d.body }}</div>
{% endif %}
```

Need a custom Jinja filter `safe_email_render` that converts plain-text email bodies to safe HTML (newlines to `<br>`, no innerHTML risk). Or render markdown for blog/GBP/social drafts via the same DOM-construction renderer recommended in `/activate` F10.

Estimated effort: **3-4 hours** including the Jinja filter + per-channel CSS variants + tests.

---

### F7. Skip prompt uses `window.prompt()` (browser native dialog) - nice-to-have-pre-launch

**Function:** Skip flow asks why the owner is skipping. Should be a polished modal, not a native browser prompt.

**Today:** `approvals.js:73` does `var reason = window.prompt('Why are you skipping this?', '');`. Native prompt is jarring, breaks visual continuity, mobile UX is bad, can be styled-blocked by enterprise browser policies.

**Gap:** Native prompts feel like 2008 web. Brand-voiced product should not.

**Smallest fix:** Replace with an inline expanding text area or a modal styled with the dashboard's design tokens. The simplest version: when Skip is clicked, expand a small form between the action buttons and the body, with a "What's the reason?" textarea (max 240 chars) + Confirm + Cancel. On Confirm, fires the skip API and starts the undo toast. Estimated effort: **2-3 hours**.

---

### F8. Edit mode toggles textarea but doesn't save edits without explicit Approve - nice-to-have-pre-launch

**Function:** Edit toggles a textarea visible. Owner edits. Then what? Save? Approve?

**Today:** Per `approvals.js:80-93`, when Approve is clicked, the JS checks if the editor is open and not hidden; if so, it pulls the textarea value and sends it as `edited_body` in the approve payload. The label changes from "Approved" to "Edited and sent." That works.

But there's no "Save without approving" affordance. Owner who wants to draft an edit and come back later either has to remember to leave the editor open (and risk navigating away losing the change) or has to commit to Approve right now.

**Gap:** Real edits often need a moment - "let me check that fact, I'll come back." Today the only persistence is "approve and ship it." Owner has to mental-cache the edits.

**Smallest fix:** Add a "Save edit (don't send yet)" button next to Approve when the editor is open. POST to a new `/api/outgoing/{draft_id}/save_draft` endpoint that updates the body in pending.jsonl in place (re-running guardrails). Owner can come back later and approve. Estimated effort: **3-4 hours**.

---

### F9. Recipient hint truncated to 240 chars but no escape for HTML chars - nice-to-have-pre-launch

**Function:** `recipient_hint` field carries a customer email or name. Renders into the page. Should be safely escaped.

**Today:** `outgoing_queue.py:120` truncates to 240 chars. Template renders `{{ d.recipient_hint }}` (line 67) - Jinja autoescapes by default so this should be safe against HTML injection. Need to verify FastAPI Jinja config has autoescape on.

**Gap:** Most likely fine (FastAPI Jinja2Templates default is autoescape=True). But it's worth a verification line in the audit because the same pattern across multiple surfaces means one config flip could break all of them.

**Smallest fix:** Verify autoescape is on in the Jinja config and add a smoke test that renders a draft with `recipient_hint = "<script>alert(1)</script>"` and asserts the output is escaped. Estimated effort: **15 minutes**.

---

### F10. Empty-state copy is good but mentions a setting that doesn't exist yet - nice-to-have-pre-launch

**Function:** Cold-start empty state says "When a pipeline with 'Approve before send' turned on has a draft ready, it will queue here."

**Today:** `approvals.html:96-101`. The copy is honest, brand-voiced, doesn't fabricate. But it implies a UI control "Approve before send" exists - which is a Phase 1 §1D "Pause/Resume per pipeline" gap (per parent plan). The setting exists as `tenant_prefs.approve_before_send.<pipeline>` in storage but there's no /settings UI to toggle it yet.

**Gap:** Owner reading this lands on /settings and wonders why they can't find the toggle. Not catastrophic but tracks the parent plan's known /settings gaps.

**Smallest fix:** Either ship the toggle on /settings (Phase 1D) or rephrase the empty-state to "When a pipeline produces a draft that needs your eyes, it will queue here. We'll add a per-pipeline approval toggle to /settings soon." Estimated effort: **5 minutes copy edit** + the /settings work tracked separately.

---

### F11. PREVIEW_MODE gate same as before - nice-to-have-pre-launch

**Function:** Same as previous surfaces.

**Today:** `main.py:613-617` same pattern. Bypasses session, hardcodes AP tenant.

**Smallest fix:** Bundles into the demo-gate hygiene pass.

---

### F12. No "send failures" surface yet - defer-to-Phase-2 (gates on F1)

**Function:** Once F1 is fixed (real dispatch wired), there will be cases where dispatch fails (SMTP timeout, Google API 503). Owner needs to see those.

**Today:** N/A (F1 not done yet).

**Recommendation:** Plan a "Send failures" section that lists archived.jsonl entries with `status=approved_send_failed` so the owner can retry. Build alongside F1's fix. Estimated effort: folded into F1's 2-3 day estimate.

---

### F13. No staleness cleanup of pending drafts - defer-to-Phase-2

**Function:** Drafts that sit in pending.jsonl for >7 days should auto-skip with a "stale, please re-queue from the source pipeline" reason.

**Today:** No cleanup. A draft from 2026-04-01 still shows in the queue today (urgency=red). Owner skips it manually. Not catastrophic but feels neglectful.

**Recommendation:** Phase 2 cron-level cleanup. `services/outgoing_queue.expire_stale(tenant_id, days=14)` runs daily, moves stale drafts to archived with `status=expired_stale`. Estimated effort: **2-3 hours when the time comes**.

---

## Function-check verdicts (the things that work and need no change)

- **Two-stage guardrails** (`outgoing_queue.py:111-113`, `:176-184`): enqueue rejects vendor leaks; approve re-checks because edits can introduce new violations. On approve-time rejection, draft goes BACK to pending so owner can edit. **Architecturally excellent.** Pass.
- **Atomic JSONL writes via temp + rename** (`outgoing_queue.py:80-87`): crash-safe. Pass.
- **threading.Lock for single-process serialization** (`outgoing_queue.py:36`): correct for current scale. Annotation at lines 18-20 acknowledges multi-worker scaling needs a real queue. Pass.
- **Receipts integration** (`outgoing_queue.py:191-200`): every approval writes a receipt for the audit trail. Pass.
- **Decision-log integration** (`api/outgoing.py:60-68`): every approve/skip writes to the activity feed so the transparency surface shows owner decisions. Pass.
- **Keyboard shortcuts** (`approvals.js:30-54`): J/K navigate, A/E/S act. Lead text on the page (line 53) advertises them. Hijack-prevention: textarea/input/select don't fire shortcuts (line 33-34). Pass.
- **Undo toast 10-second window** (`approvals.js:107-132`): action commits after 10s, undo cancels. Visual fade on commit. Pass.
- **Urgency-dot age coloring** (`main.py:630-637`): green <2h, amber <12h, red >=12h. Reasonable thresholds. Pass.
- **Edit textarea pre-fills with current body** (`approvals.html:74-76`): owner doesn't lose the AI's draft when starting to edit. Pass.
- **Empty-state copy** (`approvals.html:95-101`): honest, brand-voiced, doesn't fabricate.
- **Pending count badge in sidebar nav** (`approvals.html:28`): correct visibility for "you have N waiting." Pass.

## Effort summary by bucket

| Bucket | Findings | Total estimate |
|---|---|---|
| must-fix-before-tenant-2 | F1 (2-3d), F2 (folds into partials), F3 (1.5d) | **~4 days** (F2 free if partials done) |
| nice-to-have-pre-launch | F4 (15m), F5 (10m), F6 (3-4h), F7 (2-3h), F8 (3-4h), F9 (15m), F10 (5m), F11 (folds into bundle) | **~1.5 days** |
| defer-to-Phase-2 | F12 (folds into F1), F13 (2-3h) | **0** |
| **Phase 1D `/approvals` UX cleanup total** | | **~5.5 days** |

## Cross-surface observations

- **F1 is the most consequential finding in Phase 0 so far.** It changes the parent plan's W3 ship target (Generic `reviews/run.py`) - that pipeline must arrive WITH a dispatcher, not just an enqueue path. Same for W4 (gbp, seo) and W5 (email_assistant). Fold dispatcher work into each pipeline's tenant-ization commit.
- **F3 bulk actions** suggests a similar pattern on `/recommendations` (bulk-apply a set of recs) - worth a single bulk-action UX library that both surfaces use.
- **F8 save-without-approving** parallels `/activate` F1 (voice card persistence) and `/dashboard` F10 (privacy toggle persistence) - the dashboard has a recurring "ephemeral changes lost on reload" theme. Bundle these into a Phase 1D pass on UI-state durability.
- **F10 "Approve before send" toggle** drives Phase 1D `/settings` work. Slot the toggle UI at the same time as the sidebar partials extraction so all the chrome work lands in one PR.

## Cumulative Phase 0 progress

| # | Surface | Status | Findings | Phase 1D effort |
|---|---|---|---|---|
| 1 | `/activate` | done | 10 (3+5+2) | ~4 days |
| 2 | `/dashboard` | done | 12 (3+6+3) | ~2.5-3.5 days |
| 3 | `/roles` | done | 8 (2+5+1) | ~1.5 days |
| 4 | `/roles/{slug}` | done | 11 (3+6+2) | ~2 days |
| 5 | `/approvals` | done | 13 (3+7+3) | ~5.5 days |
| 6 | `/recommendations` | next | - | - |

**Running total: 54 findings, ~15.5 days Phase 1D work mapped.**

The "real dispatch" gap (F1) is the single biggest risk surfaced in Phase 0 so far. Recommend bumping its priority above the partials work in the parent plan's W2-W5 sequence; the plan's existing W3-W5 generic-pipeline work can absorb dispatcher-build cost naturally if scoped correctly.

## Next surface to audit

`/recommendations` - per parent plan: "Recs generation real; 'Apply' sometimes only logs intent. Audit each rec type - does 'Apply' actually do something? Most are placeholders today." Mirrors the F1 dispatch gap on this surface (apply doesn't actually apply for some rec types). Audit before Phase 1 wires real apply handlers.
