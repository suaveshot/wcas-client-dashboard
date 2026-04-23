# Judge quickstart

One-page tour for a hackathon judge. The flows below all run against real
tenant data unless you pick the demo-mode path.

## The live demo

**Live URL:** https://dashboard.westcoastautomationsolutions.com

**Try as a judge:** click *Try as a judge* on the landing page. That POSTs
your email to the magic-link endpoint (we pre-seeded `demo@claudejudge.com`
in Airtable, so the link flows to you).

If you want to skip email:
  * `PREVIEW_MODE=true` on the server renders a static 14-role mock against
    the hand-crafted Americal Patrol data for demo-video recording.
  * Otherwise you're looking at live telemetry pushed from Sam's PC over
    the `/api/heartbeat` receiver.

## What to try (in this order)

1. **Ask your business a question.** Hit `Cmd-K` (or `Ctrl-K`), type `?`
   followed by any question - *"which role saved me the most time this
   week?"* or *"is anything overdue?"*. You get a 2-4 sentence plain-English
   answer with source chips citing the pipelines that informed it. That's
   one Opus 4.7 call with the entire tenant workspace in context (1M window,
   no RAG). Cache-flagged, so a follow-up within 5 minutes costs pennies.

2. **See the receipts.** Click any role card on Home, then scroll down to
   *Show the last 25 receipts*. That drawer is the actual text of every
   outbound message we sent on the owner's behalf - subject, body,
   timestamp. Privacy mode blurs the recipient; content stays legible.

3. **Approve before send.** Go to Settings. Turn on *Approve before send*
   for any pipeline. From then on, instead of firing, that pipeline queues
   its drafts to `/approvals` where you can Approve, Edit, or Skip with
   keyboard shortcuts (`A` / `E` / `S` / `J` / `K`). Every approval has a
   10-second undo chip. Seeded demo drafts are available via
   `python scripts/seed_drafts.py <tenant_id>`.

4. **Watch the sidebar.** Each pinned role shows a colored status dot; the
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
