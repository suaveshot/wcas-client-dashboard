# Phase 0 audit - /activate

**Date:** 2026-04-28
**Surface:** `/activate` (the activation wizard)
**Audit depth:** function check + UX cleanup only (per parent plan `~/.claude/plans/alright-larry-the-hackathon-kind-swing.md`)
**Scope:** read-only walk; no code edits in this deliverable.

## Summary

10 findings: **3 must-fix-before-tenant-2**, **5 nice-to-have-pre-launch**, **2 defer-to-Phase-2**.

Top 3 priorities (in order):

1. **F1** Voice card + CRM mapping artifacts disappear on page reload - owner can't re-read what we wrote about their business after activation completes. (Day-1 essential per parent plan.)
2. **F2** Ring legend uses internal-jargon labels ("Credentials / Config / Connected / First run") that don't match the plain-English in-ring labels right beside them ("Logged in / Configured / Ready, awaiting first run / Running"). Same surface, two languages.
3. **F3** No "start over" affordance. Tenant with a stuck or wrong activation has no clean reset path; their only escape is browser-side wizard amnesia (clear sessionStorage, hope).

Function check overall: the surface DOES what it's designed to do. The 4-turn flow runs end-to-end; OAuth post-flow scripted reveal is genuinely well-executed; error recovery in the chat composer is handled with system bubbles and re-enabled inputs; mobile breakpoints exist down to 480px. The findings below are UX gaps and copy mismatches, not broken function. Sam's instinct that "the dashboard is an excellent onboarding artifact" holds up to file:line scrutiny.

## Surface map (for reference)

- **Route:** `dashboard_app/main.py:235` (`GET /activate`), `dashboard_app/main.py:386` (`GET /activate/terms`), `dashboard_app/main.py:305` (`GET /api/activation/state`)
- **Mounted routers:** `main.py:73-78` (chat / panel / simulate / terms / samples / screenshot)
- **Template:** `dashboard_app/templates/activate.html` (404 lines)
- **JS:** `dashboard_app/static/activate.js` (1106 lines), `dashboard_app/static/intro.js` (carousel), `dashboard_app/static/agent_viz.js` (demo-mode AI animations)
- **CSS blocks:** `dashboard_app/static/styles.css` lines 3540-3995 (main layout, chat, ring grid), 3998-4003 (mobile <=860px), 4137-4142 (mobile <=480px), 5233+ (celebration choreography)
- **Backend services:** `agents/activation_agent.py`, `services/activation_state.py`, `services/activation_tools.py`, `services/voice_card.py`, `services/crm_mapping.py`, `services/tenant_kb.py`, `services/credentials.py`
- **API:** `api/activation_chat.py`, `api/activation_panel.py`, `api/activation_simulate.py`, `api/activation_terms.py`, `api/activation_samples.py`, `api/activation_screenshot.py`, `api/oauth.py`

---

## Findings

### F1. Voice card + CRM mapping not re-readable post-activation - must-fix-before-tenant-2

**Function:** Owner should be able to re-read everything the agent wrote about their business (voice card, CRM segment mapping) at any time after the wizard completes. The activation produced *artifacts about them*; those artifacts are the deliverable.

**Today:** Voice card + CRM mapping panels are appended as chat bubbles inline in the chat stream (`activate.js:441-521` for voice card, `:523-596` for CRM mapping). The artifacts themselves are persisted to disk via `POST /api/activation/panel-accept` (`activate.js:598-612`), so the data survives. But on page reload, the boot sequence (`activate.js:1053-1075`) re-renders only:

- Initial ring states from server-rendered HTML
- Provisioning plan strategy chips via `fetchProvisioningPlan()` + `applyStrategyChips()`
- Existing samples via `fetchSamples()`

It does NOT re-fetch the persisted voice card or CRM mapping. There's no `fetchPanels()` call. Owner who comes back to /activate the next day sees the rings and the samples grid but loses access to the voice-card side-by-side and the CRM segment breakdown the agent showed them yesterday.

**Gap:** The voice card is the deliverable that proves "we hear you." Losing it on reload means the most differentiated UI moment in the wizard is ephemeral. Same for CRM mapping.

**Smallest fix:** Add `fetchVoiceCard()` + `fetchCrmMapping()` to the boot sequence; render via existing `appendVoiceCardBubble` + `appendCrmMappingBubble`. Backend endpoints likely already exist (`api/activation_panel.py` has the accept route; check for read routes - if absent, add a thin `GET /api/activation/panel/{type}` returning the latest accepted artifact). Estimated effort: **3-4 hours** (wire fetch + render; verify the bubbles render-in-history don't re-fire side effects like the "Saving..." button-state flip).

Better-but-bigger version: dedicate a `/settings/voice` panel where the voice card + CRM mapping are first-class citizens, editable post-activation. That's the Phase 1 §1D direction. The 4-hour fix unblocks tenant 2; the bigger fix lands in Phase 1D.

---

### F2. Ring legend jargon mismatches in-ring labels - must-fix-before-tenant-2

**Function:** The 4-step legend at the top of the ring grid should teach the owner what each ring stage means, in the same language the rings themselves use.

**Today:** The legend at `activate.html:283-300` reads:

> Credentials | Config | Connected | First run

Each in-ring sub-state at `activate.html:329-333` (and `activate.js:58-62`) reads:

> Logged in | Configured | Ready, awaiting first run | Running

Same UI, same surface, same wizard, two different languages for the four states.

**Gap:** Owner reads the legend ("Config"), then looks at their first-completed ring ("Configured"), reads the next ring ("Ready, awaiting first run"), and has to translate three times to match the legend. The in-ring labels are noticeably better English; the legend reads like product-internal taxonomy.

**Smallest fix:** Update the legend strings at `activate.html:286, 290, 294, 298` to match the in-ring labels:

| Step | Current legend | Recommended legend |
|---|---|---|
| 1 | Credentials | Logged in |
| 2 | Config | Set up |
| 3 | Connected | Verified |
| 4 | First run | Running |

(The "Set up" / "Verified" recommendation is from the parent plan's Phase 0 table. "Configured" is also fine if it's preferred, but 8 chars is borderline tight; "Set up" reads cleaner.)

Estimated effort: **15 minutes**. Two-line copy edit in one template.

---

### F3. No "start over" affordance - must-fix-before-tenant-2

**Function:** A wizard built around an LLM agent must have an obvious "I want to start over" button. Real owners hit confusion mid-flow (wrong website URL, wrong CRM, agent went off-rails on a screenshot). Without a reset, they're stuck with whatever artifacts the agent already wrote.

**Today:** `activate.html:114-128` shows the bar header. Only affordance is "Back to dashboard" (line 115-117). No reset, no "delete what you wrote and start over." The chat stream has no clear button. The composer textarea has no clear-history action. Searching the full template for "start over" / "restart" / "reset" returns no matches.

A determined owner could clear sessionStorage in DevTools and hard-reload, but the agent's persisted artifacts (voice card, CRM mapping, KB sections, provisioning plan) all survive that. The only true reset is `scripts/seed_garcia_onboarding.py --reset-only` which is an SSH-into-VPS-and-run-script operation only Sam can do.

**Gap:** Tenant 2 (Garcia) we can hand-reset via SSH. Tenant 5 self-service we cannot. Even concierge-onboarded tenants benefit from a "let me try again" button mid-call.

**Smallest fix:** Add a "Start over" button to the bar header (`activate.html:114-128`) that opens a confirm dialog ("This deletes the voice card, CRM mapping, KB notes, and provisioning plan you've written so far. Continue?") and on confirm POSTs to a new `/api/activation/reset` endpoint that calls into existing reset machinery in `scripts/seed_garcia_onboarding.py` (the `--reset-only` flag already does the right thing - extract its core into a service function `services/activation_reset.py` and call from both the CLI script and the new API route).

Estimated effort: **1 day**. Most of the work is the safe-reset service function; the button + dialog + API route is straightforward.

---

### F4. No persistent "what you'll get" preview above composer - nice-to-have-pre-launch

**Function:** Owner lands on /activate and needs to immediately see what the wizard is going to deliver - rings, voice card, CRM mapping, sample drafts - in 5 seconds, without reading the agent's opening message.

**Today:** The 4-slide intro carousel at `activate.html:28-112` covers this beautifully (read website -> read data -> one-click connect -> draft your first week). But it's a **one-time modal** - `intro.js` shows it on first visit only, hides after Esc / Skip / completion. After that, the composer shows the assistant's opening message and the right-side ring grid. There's no persistent above-the-fold "here's what's about to happen" preview that survives page reload.

**Gap:** Returning owners (came back day 2 to finish, agent stalled mid-turn) lose the orientation. The parent plan called this out as a fix; it's still real.

**Smallest fix:** Convert the carousel content into a 3-card horizontal preview that lives ABOVE the chat composer at all times. Cards: "Read your site" / "Read your data" / "Wire up the rest". Below 860px stack vertically. Cards have small SVG glyphs from the existing intro slides. Carousel modal can stay as the first-visit overlay, but the persistent strip is the new contract.

Estimated effort: **3-4 hours**. Pure template + CSS work; no JS state changes.

---

### F5. Voice/CRM panels can drift out of chat-scroll view - nice-to-have-pre-launch

**Function:** The voice card + CRM mapping are the wizard's most differentiated outputs. They need to be obviously visible while the owner is reviewing them - and findable later when the owner wants to re-read.

**Today:** Both render as inline chat bubbles (`activate.js:441-521`, `:523-596`). They append to the chat stream and `scrollChatToBottom()` fires on append. But subsequent agent messages, tool events, and system bubbles push them up out of view. By the time the owner accepts the voice card and 3 more turns happen, the voice-card bubble is 2 scroll-screens up.

The parent plan's recommendation was "promote voice/CRM panels to a sticky right-drawer." That's correct. The current chat-bubble pattern means the voice card competes for space with every other event in the stream.

**Gap:** Differentiated UX moments are in a fragile position. Combined with F1 (lost on reload), this is the single biggest UX cliff.

**Smallest fix:** Two options, ranked:

1. **Sticky drawer (parent plan's recommendation, ~1.5 days):** When a voice card or CRM mapping is rendered, also clone its DOM into a sticky right-side drawer that stays visible regardless of chat scroll. After accept, the drawer entry stays, marked "saved." Drawer also acts as the F1 fix - re-loaded voice cards repopulate the drawer on boot.
2. **In-stream pin button (~3 hours):** Add a small "Pin to top" affordance on the voice card and CRM mapping bubbles. Pinned cards render in a fixed slot above the chat stream. Fewer DOM mechanics, less ambitious, but still solves the "where did it go" problem.

Recommendation: option 1. Combines naturally with F1's fix - one drawer holds both the post-reload re-render and the live render.

Estimated effort: **1-1.5 days** for the sticky drawer.

---

### F6. Mobile <=480px keeps ring grid at 2 columns - nice-to-have-pre-launch

**Function:** On a phone, 7 rings should be readable, tappable (44px target per durable preference), and not cramped.

**Today:** `styles.css:4137-4142` keeps the ring grid at `grid-template-columns: 1fr 1fr` (2 cols) at <=480px. The default layout is 4-col, the <=860px breakpoint at line 4002 drops to 2-col, the <=480px breakpoint *holds* at 2-col rather than collapsing to 1-col.

7 rings in 2 cols = 4 rows of mostly-2 rings (last row has 1). Each ring has a 68px bezel chip, label, sub-state text, and a hover/focus tooltip. At 360px viewport width minus padding, each ring gets ~150px of horizontal space - workable but tight, and tooltip overflow is likely.

**Gap:** Not catastrophic, but real-device test on a 360px-width phone (a iPhone 12 mini in landscape, a low-end Android in portrait) is needed before tenant 2. The parent plan flags mobile as a durable preference.

**Smallest fix:** Add a third breakpoint `@media (max-width: 380px) { .ap-activate-rings__grid { grid-template-columns: 1fr; } }` so very small viewports get a single-column scroll list. Also verify the tooltip overflow at 480px (the `:nth-child(-n+4)` rule at `styles.css:3980` flips the tooltip down for the first 4 - check that the same logic applies in 2-col mobile layout where the "first 4" happens to be the first 2 rows).

Estimated effort: **1-2 hours** (CSS edit + DevTools mobile-emulation pass + screenshot for the audit followup; real-device test is the durable preference but can be a Phase 1D pre-tenant-2 gate).

---

### F7. "Saved" badge in header is static, never actually toggles - nice-to-have-pre-launch

**Function:** The header bar's "Saved" indicator at `activate.html:124-127` should communicate live save state - flip when an edit is in flight, confirm when it lands.

**Today:** The badge is static markup. There's no JS that toggles it between "Saving..." / "Saved" / error states. It always shows "Saved" regardless of actual state.

**Gap:** Decorative-only UI element occupying real estate that should be informative. If anything goes wrong with a save (panel-accept fails, KB write fails), the badge still says "Saved" while the chat shows an error system bubble. Confusing.

**Smallest fix:** Either wire it up - flip to "Saving..." on every fetch-with-side-effects (`postChat`, `postPanelAccept`, `uploadScreenshot`), back to "Saved" on success, "Save failed" on error - or remove it. Wiring is ~2 hours; removal is 5 minutes. Removal is honest given the mostly-async-fire-and-forget pattern. Wire-up is friendlier.

Estimated effort: **2 hours wire-up**, **5 minutes remove**.

---

### F8. ETA "About 12 min left" is hardcoded on first load - nice-to-have-pre-launch

**Function:** The header shows estimated time remaining. Should be roughly accurate.

**Today:** `activate.html:122` hardcodes "About 12 min left" in the server-rendered HTML. `activate.js:158-161` does compute a real ETA on JS hydration based on `(total - completed) * 2` minutes per remaining ring. So the hardcoded value gets corrected within milliseconds.

**Gap:** Owners who hard-reload and watch carefully see "12 min" briefly before it updates. Owners on slow networks (the JS bundle is large) see "12 min" for longer. Minor flicker.

**Smallest fix:** Pass `roster|length` and `completed_count` into the template render at `main.py:235` and compute the ETA server-side: something like `{{ (roster|length - completed_count) * 2 }} min left`. Or just remove the placeholder and let JS fill it on hydrate (slight loading-flash trade-off).

Estimated effort: **30 minutes**.

---

### F9. `?demo=1` query param enables AI-thinking visualizations on real /activate, not gated - defer-to-Phase-2

**Function:** The `?demo=1` flag at `activate.html:19` toggles `window.DEMO_VIZ` which `agent_viz.js` reads to play streaming overlays during voice-extraction and CRM-mapping panels. It's distinct from the JUDGE_DEMO env gate (which controls /demo/* cinematic routes).

**Today:** Anyone can append `?demo=1` to the live `/activate` URL and trigger the AI-thinking visualizations. It doesn't expose synthetic data - the underlying chat/agent flow is real. The viz is just nice-to-watch.

**Gap:** Not a real gap. The flag is harmless and visually-only. But it IS a second demo system on the same surface; future-Larry should know they exist independently, and someone confusing the two could over-gate or under-gate something.

**Recommendation:** Document in the post-hackathon runbook (Phase 1G) that two demo gates exist:

- `JUDGE_DEMO=true` env -> opens `/demo/*` cinematic routes + `/auth/judge`
- `?demo=1` URL param -> AI-thinking visualizations on the real `/activate` flow

No code change. Note for the runbook.

---

### F10. Sample body markdown rendered as plain text - defer-to-Phase-2

**Function:** Generated sample drafts (GBP post, review reply, blog draft, etc.) should render their markdown formatting - bold, lists, line breaks - so the owner sees what the published version will actually look like.

**Today:** `activate.js:698` does `body.textContent = sample.body_markdown || ""` - markdown is set as plain text. The code's own comment acknowledges this:

> // Body is plain text rendering of markdown (no innerHTML). Good enough
> // for the demo; a post-hackathon pass could render real markdown.

**Gap:** Cosmetic. The body shows asterisks and pound signs as literal characters. Sample feels less polished than it actually is.

**Smallest fix:** Add a tiny markdown-to-DOM renderer (no innerHTML - DOM-construction-only, list of supported tokens: bold/italic/lists/headings/paragraphs). ~3-4 hours including a test that exercises the supported tokens against a known fixture. Or vendor a known-safe library like `marked` + DOMPurify if Sam wants something battle-tested (~1 day plus dep audit).

Estimated effort: **3-4 hours hand-rolled** or **1 day vendored**. Defer until tenant 3 - tenant 2's screen-share concierge call will gloss over the rendering nit.

---

## Function-check verdicts (the things that work and need no change)

For completeness, the items the parent plan wondered about that turned out fine:

- **Composer empty state**: fresh tenant sees the assistant opening message at `activate.html:137-147` ("I learn your voice and your data..."). Composer placeholder is "Keep going, or tell me what you want to tackle..." (line 231). Adequate.
- **Confused-state recovery**: chat send wraps in try/catch with a friendly system bubble (`activate.js:1038-1040`). 429 has its own message ("Slow down for a moment, I'm still catching up", line 626). Panel-accept failure shows "Couldn't save. Try again." with re-enabled button (`:506-509`, `:581-584`). reached_idle=false shows "Still thinking on my end. Send 'keep going' when you want me to pick up." (`:1033-1037`). Good.
- **OAuth post-flow scripted reveal**: `playOAuthReveal` at `activate.js:199-286` is genuinely well-executed - 4-stage chat narration, ring fills, composer locked during the sequence, fall-back to server state if anything throws. Don't redo this.
- **Celebration choreography**: `checkActivationComplete` at `activate.js:119-137` - sessionStorage-keyed once-per-session gate, halo + 28-piece confetti + elapsed time badge. Works.
- **Demo gate regression**: `/activate` unauthed still 303s to `/auth/login` post-deploy of 0.7.1 (verified in JOURNAL Entry 18 prod smoke). The new `JUDGE_DEMO=false` default doesn't break the live wizard.
- **Mobile breakpoints exist**: 860px (stack chat above rings), 480px (hide some bar elements). The grid is responsive. F6 is the only mobile finding, and it's about behavior at the very smallest viewports.

## Effort summary by bucket

| Bucket | Findings | Total estimate |
|---|---|---|
| must-fix-before-tenant-2 | F1 (4h), F2 (15m), F3 (1d) | **~1.5 days** |
| nice-to-have-pre-launch | F4 (4h), F5 (1.5d), F6 (2h), F7 (2h), F8 (30m) | **~2.5 days** |
| defer-to-Phase-2 | F9 (runbook note), F10 (4h or 1d) | **0-1 day** |
| **Phase 1D `/activate` UX cleanup total** | | **~4 days** |

This estimate folds into the parent plan's Phase 1D ("UX cleanup pass per Phase 0 findings"). The full Phase 0 audit covers 16 surfaces; aggregating all 16 deliverables produces the Phase 1D scope.

## Next surface to audit

Per the parent plan's Phase 0 table (line 45), the 15 remaining surfaces in priority-of-scrutiny order are:

1. `/dashboard` (home) - cold-start UX is the next biggest gap per parent plan
2. `/roles` - ditto cold-start
3. `/roles/{slug}` - same family
4. `/approvals` - the one that has to actually work for tenant 2
5. `/recommendations` - audit each rec type's "Apply" handler
6. `/goals` - progress-math placeholder per parent plan
7. `/settings` - Pause/Resume gap
8. `/activity` - quick scan
9. `/auth/login` + magic-link - verify email template avoids Claude/Opus naming
10. Magic-link email body - same
11. `/legal/terms` + `/legal/privacy` - lawyer review trigger (Phase 1F)
12. `/healthz` - confirm nothing
13. `/demo/activation` + `/demo/dashboard` - confirm gate works
14. `/` (public homepage) - confirm post-0.7.1 cleanup
15. 401/404/500 templates - copy polish

Sam picks the next surface; same methodology applies.
