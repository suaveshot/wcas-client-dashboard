# Judge quickstart

One-page tour for a hackathon judge. The flows below all run against real
tenant data unless you pick the demo-mode path.

## The live demo

**Live URL:** https://dashboard.westcoastautomationsolutions.com

**Try as a judge:** click *Try as a judge* on the landing page. The button
POSTs to `/auth/judge`, which mints a session as Garcia Folklorico and
drops you straight into the live `/activate` wizard. No email, no magic
link, no inbox detour.

If you want different data behind the cards:
  * `PREVIEW_MODE=true` on the server renders a static 14-role mock against
    the hand-crafted Americal Patrol data for demo-video recording.
  * Otherwise you're looking at live telemetry pushed from Sam's PC over
    the `/api/heartbeat` receiver.

## What to try (in this order)

1. **Activate a new tenant on camera (v0.6.0 hero).** Hit `/activate?intro=1`
   as the judge user. A 4-slide intro explains the flow. Then the **Voice
   & Personalization specialist** (Managed Agents beta, 19 custom tools)
   runs a 4-turn conversation:

   - **Turn 1 - voice card.** Type *"let's get started - my site is
     garciafolklorico.com"*. The agent fetches the site, extracts 3-5
     voice traits, and renders the **voice card panel**: a hardcoded
     generic AI message on the left, the same message rewritten in the
     owner's voice on the right. The right side is editable. Click
     *This is us* to accept (the voice profile saves to `tenant_kb/voice.md`
     and every downstream automation reads from it forever).
   - **Turn 2 - confirm + ask about CRM.** Agent saves the company facts
     and asks what booking/CRM system you use.
   - **Turn 3 - CRM mapping.** Tell it Airtable. The agent reads Garcia's
     bookings base, identifies active / lapsed / brand-new student
     segments, and renders the **CRM mapping panel** showing counts +
     proposed automations per segment. Click *Looks right* to accept.
   - **Turn 4 - Connect Google + activate.** One OAuth click connects
     four roles. The remaining three light up as the agent activates
     them, marks complete, and triggers sample generation.

   **Demo finale.** A hero card appears at the top of the samples grid:
   *"Want to see what your re-engagement email would say to Maria Sanchez
   (37 days inactive)?"* Click. Opus drafts a real personalized email in
   the owner's voice, addressed to a real (synthetic-but-realistic)
   inactive customer, live, in 3 seconds. Citation badges under every
   sample show *voice: about page*, *data: last_engagement*,
   *playbook: re_engagement* so the provenance is visible. This is the
   moment the entire pitch ("AI learns your voice and your data")
   becomes a single provable artifact on screen.

2. **Ask your business a question.** Hit `Cmd-K` (or `Ctrl-K`), type `?`
   followed by any question - *"which role saved me the most time this
   week?"* or *"is anything overdue?"*. You get a 2-4 sentence plain-English
   answer with source chips citing the pipelines that informed it. That's
   one Opus 4.7 call with the entire tenant workspace in context (1M window,
   no RAG). Cache-flagged, so a follow-up within 5 minutes costs pennies.

3. **See the receipts.** Click any role card on Home, then scroll down to
   *Show the last 25 receipts*. That drawer is the actual text of every
   outbound message we sent on the owner's behalf - subject, body,
   timestamp. Privacy mode blurs the recipient; content stays legible.

4. **Approve before send.** Go to Settings. Turn on *Approve before send*
   for any pipeline. From then on, instead of firing, that pipeline queues
   its drafts to `/approvals` where you can Approve, Edit, or Skip with
   keyboard shortcuts (`A` / `E` / `S` / `J` / `K`). Every approval has a
   10-second undo chip. Seeded demo drafts are available via
   `python scripts/seed_drafts.py <tenant_id>`.

5. **Watch the sidebar.** Each pinned role shows a colored status dot; the
   one that ran in the last minute pulses green. The rail-top strip
   summarizes counts; the rail-bottom remembers your last three questions
   so a one-click repeat works from any page.

## What to press

| Shortcut | Does |
|---|---|
| `Cmd-K` / `Ctrl-K` | Open the palette (jump to a role OR ask a global question) |
| `Ctrl-Shift-P` | Privacy mode on/off |
| `Ctrl-Shift-F` | Focus mode (hide the chrome) |
| `A` / `E` / `S` | Approve / Edit / Skip (on `/approvals`) |
| `J` / `K` | Next / previous draft |
| `Esc` | Close palette or drawer |

## Judging signals we're aiming for

* **Managed Agents running a real activation flow.** The `/activate` chat
  is a real Managed Agent with 14 custom tools that provision accounts,
  advance pipeline rings, and write per-tenant knowledge files. It is not
  a scripted demo.
* **Opus 4.7 1M-context, user-facing.** Global Ask composes everything the
  tenant has produced into one prompt. No RAG, no chunking.
* **Agency-level trust primitives.** Receipts drawer + Approve-before-send
  together solve "what did you send in my name?" - the question that kills
  agency automation conversions.
* **Every dollar visible.** The ask-answer footer shows per-call cost;
  `/cost` is a structured jsonl that's greppable. Daily dev + per-tenant
  caps kill switch are env-driven.
* **10-second undo everywhere.** Apply / Dismiss / Approve / Edit / Skip
  all route through the same toast primitive.
* **No vendor leaks in client output.** The guardrail seam rejects
  Claude / Opus / Anthropic / GPT mentions in every outbound pass.

## Troubleshooting

* **Dashboard blank / 401 on anything.** You need a session cookie. Click
  *Try as a judge* on the landing or sign in with a real WCAS email.
* **Global Ask returns "assistant is offline."** Anthropic key missing on
  server or daily cap hit - check `cost_log.jsonl`.
* **No receipts or drafts showing.** Seed them:
  ```bash
  python scripts/seed_receipts.py <tenant_id>
  python scripts/seed_drafts.py  <tenant_id>
  ```
