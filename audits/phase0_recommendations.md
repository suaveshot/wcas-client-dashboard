# Phase 0 audit - /recommendations

**Date:** 2026-04-28
**Surface:** `/recommendations` (Opus-generated and seeded recs with Apply / Dismiss / Ask)
**Audit depth:** function check + UX cleanup only (per parent plan `~/.claude/plans/alright-larry-the-hackathon-kind-swing.md`)
**Scope:** read-only walk; no code edits in this deliverable.

## Summary

13 findings: **2 must-fix-before-tenant-2**, **8 nice-to-have-pre-launch**, **3 defer-to-Phase-2**.

Top 2 priorities:

1. **F1 (CRITICAL)** **Apply doesn't apply.** The `/api/recommendations/{rec_id}/act` endpoint just writes a JSONL row to `rec_actions.jsonl` and an audit-log entry. There is **no per-rec-type Apply handler** that actually executes the recommendation. The "Apply" button effectively means "Dismiss as 'I would do this if I could'." The parent plan flagged this exactly: "Apply sometimes only logs intent." Confirmed: it ALWAYS only logs intent.
2. **F2** Cross-surface chrome drift - has the full 7-item nav (good) but missing topbar search pill, notifications bell, account avatar; missing sidebar rail-health, pinned roles, recent asks. Same partials-extraction gap as every other surface audited.

The recommendations refresh side is **excellent.** Real Opus call, full tenant context, two-stage guardrails (rejected recs become drafts visible only to admins), 5/day rate limit per tenant, cost cap, audit log, structured error mapping with friendly toasts (429 / 502 / 503 / 500). The cards themselves carry evidence + confidence + reversibility + impact metadata in the model output. The refresh-and-reload UX works. **The single gap is the missing Apply dispatcher** (which, like /approvals F1, is the same architectural pattern needed: a per-type handler registry).

## Surface map

- **Route:** `dashboard_app/main.py:497-537` (`GET /recommendations`)
- **Template:** `dashboard_app/templates/recommendations.html` (144 lines)
- **JS bundles:** `static/recommendations.js` (80 lines, refresh + tabs), `static/rec_actions.js` (140 lines, per-card Apply/Dismiss/Ask handlers)
- **API:** `dashboard_app/api/recs.py` (132 lines)
  - `POST /api/recommendations/{rec_id}/act` - records action (apply/dismiss) to JSONL only
  - `POST /api/recommendations/refresh` - triggers real Opus regeneration with rate limit + cost cap
- **Service:** `services/rec_actions.py` (69 lines, read-back only), `services/recs_generator.py`, `services/recs_store.py`, `services/seeded_recs.py` (deterministic fallback when no Opus pass exists)
- **Data layout:**
  - `/opt/wc-solns/<tenant>/rec_actions.jsonl` - apply/dismiss history
  - `/opt/wc-solns/<tenant>/recommendations/YYYY-MM-DD.json` - latest Opus pass
- **Demo gate:** `PREVIEW_MODE=true` bypasses session, hardcodes `tenant_id = "americal_patrol"` (`main.py:500-503`)

---

## Findings

### F1. Apply doesn't actually apply - must-fix-before-tenant-2 (CRITICAL)

**Function:** Owner clicks Apply on a rec like "Add 3 keyword variants to your GBP post template." Something happens. The keyword variants are added. Or a draft is queued. Or the linked tool is launched. *Something.*

**Today:** Reading `api/recs.py:45-85` end-to-end:

```python
@router.post("/api/recommendations/{rec_id}/act")
async def api_recs_act(rec_id, body, tenant_id):
    # validate rec_id + action
    entry = {"ts": ..., "rec_id": ..., "action": ...}
    # append to /opt/wc-solns/<tenant>/rec_actions.jsonl
    audit_log.record(...)
    return JSONResponse({"ok": True, ...})
```

That's the whole handler. No dispatch, no per-rec-type executor, no integration with the relevant pipeline. The page lead says "Nothing applies without your click" (`recommendations.html:48`). Technically true - nothing applies *with* your click either.

The recs themselves carry rich metadata that a dispatcher COULD use: `proposed_tool`, `confidence`, `reversibility`, `impact.estimate` (`recommendations.html:78-85`). The `proposed_tool` field already exists in the model output - that's the dispatcher's lookup key.

**Gap:** Same severity as /approvals F1. The whole product is built on "AI proposes, human approves, system acts." Apply not acting breaks the value proposition. Tenant 2 will discover this the first time Itzel clicks Apply on "Reply to the 4 reviews waiting on you" and nothing happens.

**Smallest fix:** Mirror /approvals F1's approach: build a `services/rec_dispatch.py` registry mapping `proposed_tool` to a Python callable. Each callable takes `(tenant_id, rec)` and returns either a new draft (which goes into the approvals queue) or an immediate-effect result (e.g., changing a setting). On `POST /act`, after recording the intent, dispatch:

```python
@router.post("/api/recommendations/{rec_id}/act")
async def api_recs_act(...):
    # ...record intent (existing code)
    if action == "apply":
        try:
            outcome = rec_dispatch.execute(tenant_id, rec_id)
        except rec_dispatch.NotImplementedForType as e:
            outcome = {"queued_for_review": True, "reason": str(e)}
        except rec_dispatch.DispatchError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=502)
        return JSONResponse({"ok": True, "rec_id": rec_id, "outcome": outcome})
    return JSONResponse({"ok": True, ...})
```

Initial dispatcher coverage:

- `gbp_post_draft` - creates a draft GBP post in the outgoing queue
- `review_reply_draft` - creates a draft review reply in the outgoing queue
- `update_role_setting` - flips a tenant pref (e.g., enable approve-before-send)
- `schedule_change` - updates `tenant_scheduler.json` (Phase 2 ships)
- `kb_update` - appends to a KB section (rare; admin-gated)

Recs whose `proposed_tool` doesn't have a dispatcher yet return `{queued_for_review: true}` and surface in admin Drafts so Sam can hand-execute them. Honest fallback.

Estimated effort: **3-4 days** for the dispatch registry + 5 initial dispatchers + tests + UI feedback (apply success vs queued-for-review). Folds naturally into the `/approvals` F1 dispatcher work since both surfaces need the same pattern; the `services/rec_dispatch.py` and `services/dispatch.py` from /approvals F1 might even merge into one shared registry.

---

### F2. Cross-surface chrome drift - must-fix-before-tenant-2

**Function:** Same as previous surfaces.

**Today:** Sidebar nav has full 7 items (`recommendations.html:23-29`) including the active `Recommendations` (line 27). But:

- Topbar (line 34-43) has only the rail trigger and breadcrumb. Missing search pill, Ask button, notifications bell.
- Sidebar (line 16-32) has nav, but missing rail-health summary, pinned roles, recent asks, account footer.

**Gap:** Same drift class as every other surface. `/recommendations` actually has slightly more chrome than `/approvals` (it has the rail-trigger button - `/approvals` doesn't), suggesting partial-fix history.

**Smallest fix:** Bundles into the partials extraction. Marginal cost: 0 minutes once partials land.

---

### F3. Cache-buster mismatch within the same template - nice-to-have-pre-launch

**Function:** Stylesheet and JS cache-busters should match per deploy.

**Today:** `recommendations.html` references styles.css at `?v=20260423a` (line 11) but the four JS bundles at `?v=20260426a` (lines 139-142). The CSS buster is OLDER than the JS buster - so a tenant who loaded styles.css at v=20260423a yesterday and visits this page today gets the old CSS but new JS, which can produce inconsistent rendering.

Comparison across surfaces:

| Surface | styles.css buster | JS busters |
|---|---|---|
| `/dashboard` | `v=20260425g` | `v=20260426a` |
| `/roles` | `v=20260426a` | `v=20260426a` |
| `/roles/{slug}` | `v=20260422d` | `v=20260422d` |
| `/approvals` | `v=20260422d` | `v=20260422d` |
| `/recommendations` | `v=20260423a` | `v=20260426a` (mismatch within template) |

`recommendations.html` is the most-broken on this dimension because it has internal inconsistency. Others are cross-page-stale but at least internally consistent.

**Smallest fix:** Centralize the cache-buster in a Jinja global (recommended in `/roles/{slug}` F4) so all templates reference one constant. Bump on deploy. Estimated effort: **30 minutes for the centralization** + carries through all surfaces forever.

---

### F4. Inline `style=` on breadcrumb and rec footer - nice-to-have-pre-launch

**Function:** Same as `/roles/{slug}` F5.

**Today:** Two inline `style=` attributes on this surface:

- `recommendations.html:39` - breadcrumb anchor (cross-cutting; same pattern everywhere)
- `recommendations.html:96` - `<div class="ap-rec__footer" style="margin-top: 16px;">` - one-off layout fix that should be in CSS
- `recommendations.html:107` - inline color/font on the spark glyph (`style="color:var(--accent);font-size:14px;"`)

**Smallest fix:** Move all three into `.ap-rec__footer`, `.ap-btn--spark` rules in styles.css. Estimated effort: **15 minutes**.

---

### F5. Refresh rate-limit (5/day) is invisible to the owner - nice-to-have-pre-launch

**Function:** When refresh is rate-limited (429 from `api/recs.py:90-94`), the only feedback is the toast "Daily refresh limit reached." Owner can't see "you have 3 left today" before they click.

**Today:** No counter, no quota indicator. Backend tracks via `rate_limit.recs_refresh_limiter`. Need to expose the remaining-quota in the page render context so the button can show "Refresh (3 left today)" and disable when 0.

**Gap:** Owner who clicks Refresh and gets the limit toast doesn't know if they were close to or far from the limit. Trial-and-error UX.

**Smallest fix:** Add `recs_refresh_remaining = rate_limit.recs_refresh_limiter.remaining(tenant_id)` to the route handler context and render in the button label: `<span>Refresh ({{ recs_refresh_remaining }} left)</span>` when remaining < 5. Estimated effort: **1 hour** (limiter `remaining()` method may need adding, then template + JS update).

---

### F6. Refresh reload is jarring - nice-to-have-pre-launch

**Function:** Successful refresh swaps in the new recs. Should feel smooth.

**Today:** `recommendations.js:58` does `setTimeout(function () { window.location.reload(); }, 900);`. 900ms after the success toast, the whole page reloads. Toast disappears mid-fade. Sometimes the new recs flash in awkwardly.

**Gap:** Modern web UX should swap the cards in via DOM update without a hard reload. Especially since the API response already returns the count of fresh recs - the page could fetch `/api/recommendations/list` (or extend the refresh response to include the actual recs) and re-render the list inline.

**Smallest fix:** Extend `/api/recommendations/refresh` response to include the full live + draft rec arrays. Have the JS swap them into `.ap-recs-list` via DOM construction (no innerHTML). Estimated effort: **3-4 hours** (template render extracted to a partial that JS can also use, or DOM-construction renderer in JS that mirrors the Jinja template).

---

### F7. "Ask" button on recs has unclear handler - nice-to-have-pre-launch

**Function:** Each rec card has an "Ask" button (`recommendations.html:106-109`). What does it do?

**Today:** `data-rec-action="ask"` - the handler lives in `static/rec_actions.js` (140 lines, not read in this audit but worth verifying). If the handler routes to the global Ask drawer with a pre-filled question about the rec ("Why this recommendation?"), good. If it does nothing or duplicates Apply, problematic.

**Gap:** Same pattern as `/dashboard` F6 (quick-action chips with unclear handlers). Audit can't fully verify without reading the JS bundle.

**Smallest fix:** Read `rec_actions.js` and verify the Ask handler does what it should. If it pre-fills the global Ask drawer with a contextual question, document that. If it doesn't, wire it up. Estimated effort: **1 hour** verification + 2-3 hours implementation if missing.

---

### F8. Generated_at timestamp formatting is fragile - nice-to-have-pre-launch

**Function:** Show when the latest Opus pass ran.

**Today:** `recommendations.html:54` does `{{ recs_generated_at[:16].replace('T', ' ') }} UTC`. String slicing on a Jinja value. Works for ISO 8601 timestamps; breaks for any other format (e.g., if the generator ever switches to epoch seconds or a different ISO variant).

**Gap:** Cosmetic but fragile. Better to format server-side.

**Smallest fix:** Pre-format in the route handler:

```python
ctx["recs_generated_at_human"] = (
    datetime.fromisoformat(generated_at.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M UTC")
    if generated_at else None
)
```

Template renders `{{ recs_generated_at_human }}`. Estimated effort: **20 minutes**.

---

### F9. No bulk apply / bulk dismiss - nice-to-have-pre-launch

**Function:** Same gap as `/approvals` F3. With 12+ recs visible (possible after a fresh Opus pass), owner clicks one at a time.

**Today:** No bulk actions. Each rec has its own footer.

**Gap:** Same UX cliff as approvals once volume hits.

**Smallest fix:** Same shared bulk-action library proposed in `/approvals` F3. Both surfaces use it. Estimated effort: **part of /approvals F3 (1.5 days)** + ~3 hours marginal cost to wire it on this surface.

---

### F10. No filter by goal - nice-to-have-pre-launch

**Function:** Recs can carry a `goal` field linking them to one of the tenant's pinned goals. With 5 goals + 12 recs, owner should be able to filter "show me only recs tied to my Reviews goal."

**Today:** No filter chips. All recs render in a flat list.

**Smallest fix:** Add filter chips above the list: `All · Goal: Reviews · Goal: Sales · Untagged`. Click sets a `?goal=` query param. Estimated effort: **2 hours**.

---

### F11. PREVIEW_MODE gate same as before - nice-to-have-pre-launch

**Function:** Same as previous surfaces.

**Today:** `main.py:500-503` same pattern.

**Smallest fix:** Bundles into the demo-gate hygiene pass. Marginal cost: 5 minutes for one extra smoke test pair.

---

### F12. Drafts panel shows guardrail-rejected recs without resolution path - defer-to-Phase-2

**Function:** Admin tab shows recs the model proposed but the guardrail rejected. Useful for tuning. Admin should see WHY the guardrail rejected and have a resolution path: edit + re-submit, dismiss, or escalate.

**Today:** `recommendations.html:122-132` renders the draft headline + reason + a `draft_reason or 'blocked by guardrail'` chip. No resolution buttons. Admin can only read; can't act.

**Gap:** Tuning the guardrail is a real ongoing job. Admin needs a workflow.

**Recommendation:** Phase 2 admin work alongside the broader `/admin` surface (parent plan §3D). Estimated effort: **half day when the time comes**.

---

### F13. Generated-at timestamp doesn't show local time - defer-to-Phase-2

**Function:** "2026-04-28 14:32 UTC" is technically accurate but UTC math is owner-unfriendly. Should show local time (or both).

**Today:** UTC only.

**Recommendation:** Phase 2. Use the tenant's timezone from the KB or browser-side local-format conversion. Estimated effort: **1 hour when the time comes**.

---

## Function-check verdicts (the things that work and need no change)

- **Refresh rate limit + cost cap** (`api/recs.py:88-110`): 5/day per tenant, daily budget enforcement via `cost_tracker` inside the Opus call. Pass.
- **Two-source rec model** (`main.py:506-518`): if a fresh Opus pass exists (<48h old), use it; else fall back to `seeded_recs.build_with_drafts()`. Surface is never empty. Pass.
- **Guardrail-as-drafts pattern** (`main.py:521-522`): rejected recs become admin-visible drafts so the tuning loop has signal. Pass.
- **filter_unacted reads rec_actions.jsonl** (`services/rec_actions.py:62-69`): applied/dismissed recs hide across page loads. The post-hackathon `597acc4` commit fix works. Pass.
- **Audit log on every act** (`api/recs.py:78-83`): every Apply/Dismiss writes to audit_log. Pass.
- **Cards carry full provenance** (`recommendations.html:78-95`): proposed_tool, confidence, reversibility, impact, evidence list. Excellent transparency. Pass.
- **Refresh button error mapping** (`recommendations.js:48-72`): explicit handling for 200, 429, 502, 503, plus catch-all + network error. User sees a friendly toast, not a generic error. Pass.
- **Refresh button busy state** (`recommendations.js:32-37`): button disables, label changes to "Refreshing", restored on error. Pass.
- **Cost surfaced in success toast** (`recommendations.js:52-56`): "$0.04 spent" - transparent about Opus spend per refresh. Pass.

## Effort summary by bucket

| Bucket | Findings | Total estimate |
|---|---|---|
| must-fix-before-tenant-2 | F1 (3-4d, can share registry with /approvals F1), F2 (folds into partials) | **~3-4 days standalone** (or shared with /approvals F1 = ~5-6 days combined) |
| nice-to-have-pre-launch | F3 (30m), F4 (15m), F5 (1h), F6 (3-4h), F7 (1h verify, 2-3h fix), F8 (20m), F9 (part of /approvals bundle), F10 (2h), F11 (folds into bundle) | **~1 day** |
| defer-to-Phase-2 | F12 (half day), F13 (1h) | **0** |
| **Phase 1D `/recommendations` UX cleanup total** | | **~4-5 days standalone, less if shared with /approvals** |

## Cross-surface observations

- **F1 and /approvals F1 are the same architectural pattern.** Both need a per-type dispatcher registry. Strongly recommend designing them as ONE registry (`services/dispatch.py`) with two callers (`api/outgoing.py` for approval-on-send, `api/recs.py` for rec-Apply). Each pipeline registers handlers for the rec types AND draft types it owns. **Combined effort drops from 5-6 days separate to 4-5 days unified.**
- **F3 cache-buster mismatch** suggests the JS-bumper script that updated other surfaces missed the styles.css line on this template. Worth a sed pass across all templates to align everything before Phase 1 starts.
- **F6 reload-after-refresh** is one of three places the dashboard does heavy reloads instead of in-place updates (the other two: `/activate` post-OAuth though that one has the scripted reveal, and `/approvals` after approve which uses fade + remove). Consistency win to convert all three to in-place DOM updates.
- **F9 bulk actions + /approvals F3** justify a shared bulk-action UX library that both surfaces use. Roughly 1.5 days for the library, 3 hours marginal per surface using it.

## Cumulative Phase 0 progress

| # | Surface | Status | Findings | Phase 1D effort |
|---|---|---|---|---|
| 1 | `/activate` | done | 10 (3+5+2) | ~4 days |
| 2 | `/dashboard` | done | 12 (3+6+3) | ~2.5-3.5 days |
| 3 | `/roles` | done | 8 (2+5+1) | ~1.5 days |
| 4 | `/roles/{slug}` | done | 11 (3+6+2) | ~2 days |
| 5 | `/approvals` | done | 13 (3+7+3) | ~5.5 days |
| 6 | `/recommendations` | done | 13 (2+8+3) | ~4-5 days |
| 7 | `/goals` | next | - | - |

**Running total: 67 findings, ~19-21 days Phase 1D work mapped (with sharing optimizations: ~16-18 days).**

## Next surface to audit

`/goals` - per parent plan: "Storage works; progress math placeholder." Worth checking what "progress math placeholder" actually means in code, and whether the goal-pinning UX is ready for tenant 2 (the first goal an owner sets is what wakes up the third hero stat on the home page).
