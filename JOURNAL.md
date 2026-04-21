# WCAS Client Dashboard  -  Build Journal

A daily log of what we built, what we decided, and how the project evolved. Written for two audiences: the hackathon judges who want to see how the idea grew, and future-Sam who needs to remember why choices were made.

**Hackathon:** Built with Opus 4.7 (Cerebral Valley + Anthropic), Apr 21-26, 2026
**Builder:** Sam Alarcon (solo) + Larry (Claude Opus 4.7 as build assistant)
**Target:** the agency-level automation dashboard Sam has wanted since he started running Americal Patrol's pipelines on Claude

**Timestamp format:** all entries carry an ISO-style timestamp  -  `YYYY-MM-DD HH:MM <timezone>`  -  at the top of the entry and again on any mid-day event where ordering matters. All times are America/Los_Angeles (PDT through Apr 26, our build week).

---

## Entry 0  -  Pre-build planning
**2026-04-21 08:30 PDT → 11:27 PDT** (four-hour planning session ending with plan v5 locked)

The hackathon acceptance landed this morning. Sam had just been selected out of 13,000+ applicants down to 500 builders worldwide  -  solo or two-person teams, 5 days, Opus 4.7, $100K prize pool for 6 winners, judged by Boris Cherny and Lydia Hallie and the Claude team.

Sam's prompt was immediate and specific: *"Let's go ahead and build that client dashboard for WestCoast Automation Solutions that streamlines the onboarding process for clients and makes recommendations to their current setup in order to achieve their goals."*

The dashboard wasn't a new idea  -  Sam had designed it in a 2026-04-19 session and parked it as Figma-only for Phase 4 of the 60-day sprint (Jun 8-13), with a real build planned after WCAS had two paying clients. The hackathon pulled the build forward.

### The planning evolution (v1 → v5)

Over four hours on Monday morning, the plan went through five versions. Worth logging because the shape of the final product owes something to each pivot.

**v1**  -  first-pass outline. Three-feature killer loop: 30-min Opus 4.7 conversational onboarding, live pipeline dashboard using real Americal Patrol data, goal-anchored recommendations using Opus 4.7's 1M context window. Deploy to Hostinger VPS (Sam's VPS deploy standard). Public GitHub repo. Five-day schedule with Friday as polish and Saturday as submission day. Hard rule locked in from the start: the dashboard only READS Americal Patrol state and never writes to the AP directory  -  fresh tenant writes go to `/opt/wc-solns/<slug>/`. This was a trust-first constraint: AP is the flagship's production security business, it cannot be risked for a hackathon demo.

**v2**  -  scope correction from Sam. The dashboard is for paying clients only, not a cold-traffic onboarding wizard. Every user has already signed a contract, paid via QuickBooks, and been moved to the Airtable Clients table by the existing n8n Client Onboarding workflow (`VTObkRbwQZ8wiLDf`). The dashboard's job is **activation** (getting purchased pipelines from "bought" to "producing"), not **acquisition**. This narrowed the onboarding agent's tool set from discovery-style ("which pipelines do you need?") to activation-style ("let's get the ones you bought running"). Cleaner product, tighter demo story.

We also added four agency-level features: Day-1 baseline capture (GSC rankings, GBP reviews, follower counts  -  immutable once frozen), a hero-stats strip on the dashboard (Weeks Saved + Revenue Influenced + Goal Progress answering "why am I paying for this?" at a glance), Day-1 goals with bi-weekly Opus check-ins, and a transparency activity feed with 10-second undo on every automated action. Sam's original vision had a "feature request inbox" and a "self-learning loop"  -  both got deferred to Week 2.

**v3**  -  platformization seeds. Sam asked what would take this from "WCAS dashboard" to "platform agencies would license." Answer: five architectural decisions we make this week that cost maybe an hour each but preserve the white-label/multi-tenant option. (1) Every route, file path, and query scoped by `tenant_id` from Day 1  -  no hardcoded "americal_patrol" strings anywhere. (2) Per-tenant `brand.json` override layer so a sub-agency can rebrand in minutes. (3) Per-client knowledge base as the grounding layer for every AI surface (the voice agent, chatbot, email drafts, proposals, QBRs all read the same `kb/*.md` per client  -  single source of truth). (4) Guard-rail review hook interface on every outbound (stub this week, real Opus logic post-hackathon). (5) Goals schema with `tuning_levers` field so post-hackathon we can link goals to automation dials end-to-end  -  no competitor does this.

**v4**  -  honest second-pass audit. Looked at the plan with fresh eyes against the "secure, streamlined, agency-level ready" bar Sam set. Found fifteen gaps. Six must-close security items (XSS escaping review, magic-link token entropy + single-use + hash, production error handler, log/prompt scrubbing, Dependabot on public repo, VPS disk backup). Three streamlined items (activation session resume, timezone per tenant, skeleton loaders). Three agency-level items (one-click "Ask Sam" from every modal, Terms + Privacy stub pages, brand-matched magic-link email template). Five genuinely deferred to Week 2 (CSRF tokens, account data export/delete, credential-revoke re-auth UX, etc.).

Also identified eleven places I had underbudgeted time  -  the demo video and submission writeup got bumped from "record a video" bullet points to most of Day 5 afternoon plus Saturday morning. Baseline capture got simplified to the 3 metrics that don't need new OAuth. The Day 5 stretch "live co-editing with streaming Opus recommendations" got cut entirely to absorb the new load.

**v5**  -  architectural shift to Claude Managed Agents. Sam pointed at https://platform.claude.com/docs/en/managed-agents/overview. Anthropic's managed runtime hosts the agent loop, sandboxed container, file system, tool execution, and event history. The activation agent and recommendations agent become **Managed Agent sessions** instead of hand-rolled loops.

This collapsed an entire category of work. Conversation state, tool execution dispatch, prompt caching, cost tracking, SSE streaming, and  -  critically  -  session resume (a v4 must-close item) all become Anthropic's problem, not ours. The event history persisted server-side IS the session state. Close the tab, open it again, pull events by `session_id`, render.

It also gave us the strongest hackathon story yet: the product uses Anthropic's newest platform surface (Managed Agents, beta header `managed-agents-2026-04-01`), and if we're accepted into the research preview, we can use the multi-agent feature to have an Activation Orchestrator delegate to a Baseline Capturer and Revenue Attributor as genuine sub-agents. *"A multi-agent dashboard built with multi-agent platform tooling."*

Dual-agent strategy locked in at v5: Sam's ten custom Claude Code subagents (in `~/.claude/agents/`) drive the BUILD process  -  security-auditor does the Day 2 auth review, designer drives every visual decision, code-reviewer gates each day's commits, config-guardian validates every config write, site-builder owns VPS deployment. Anthropic Managed Agents drive the PRODUCT runtime  -  what the client actually talks to.

### The five pillars of the final plan

1. **Activation Orchestrator** (Managed Agent, Opus 4.7) drives a 30-minute conversation that gets the client's purchased pipelines from bought to producing. Writes to per-tenant knowledge base as it goes. Session is resumable via Anthropic's event history.
2. **Live tenant-scoped dashboard** (FastAPI + static) with hero stats strip, transparency activity feed, 10-second undo chip on every action, real Americal Patrol data via PC-to-VPS heartbeat.
3. **Goal-anchored Recommendations Generator** (Managed Agent, Opus 4.7, 1M context) reads full tenant state and writes weekly goal-anchored recommendations with one-click Apply. Gated to 30+ days post-activation so recs are grounded in real data.
4. **Baseline Capturer** (Managed Agent) runs once at activation to freeze Day-1 metrics  -  everything compared against that forever.
5. **Guard-rail review pass** (direct Messages API) on every automated outbound before it ships.

Architectural decisions this week preserve: multi-tenant (tenant_id scoping), white-label (brand.json override), knowledge-base grounding (kb/ per tenant), guardrails (review_outbound hook), and goal→tuning linkage (tuning_levers field).

Deploy to `dashboard.westcoastautomationsolutions.com` on Hostinger VPS Docker, public GitHub repo `wcas-client-dashboard` with Dependabot and a data-sanitization flag for public demo mode, UptimeRobot monitoring.

### What we chose NOT to build

This is as important as what we're building. Explicitly out of hackathon scope, most deferred to Week 2:
- Sam voice-note layer on edge-case recommendations (converges with the Larry-as-operator roadmap)
- Sunday 5-bullet brief pipeline
- Referral code tracker in the dashboard
- Admin-only Sam view (onboarding SLA, churn risk, cost-per-client)
- Real Opus-powered guardrails logic (beyond em-dash strip)
- Goal-to-automation auto-tuner (the tuning_levers field becomes real)
- Full brand.json theme-editor UI
- Multi-tenant admin + sub-agency white-label billing
- CSRF token protection beyond SameSite=Strict
- Account data export + delete endpoints (CCPA)
- Credential-revoke detection + guided re-auth flow
- Live co-editing with streaming Opus recommendations (was v4 stretch, cut in v4)
- Second demo tenant (Garcia Folklorico-shaped) for the video

Everything above is documented in the plan file as Week 2 (Apr 27 - May 3) or beyond. Nothing architecturally blocks these.

### Decisions log

- **Repo name:** `wcas-client-dashboard` (hyphenated, GitHub convention), public visibility from Day 1, MIT license on code with a carve-out note for WCAS brand assets
- **Domain:** new subdomain `dashboard.westcoastautomationsolutions.com` via Hostinger DNS; not a subpath on the existing marketing site, so the dashboard can evolve independently
- **Tech stack:** Python 3.12 + FastAPI, static HTML + vanilla JS + the existing `brand-kit/tokens.css`, Docker + Docker Compose on Hostinger VPS, Anthropic Managed Agents for heavy agents, direct Messages API for light synchronous calls
- **Auth:** magic link via Gmail, HttpOnly + SameSite=Strict + Secure cookie, 1-hour token TTL, single-use with SHA-256 hash stored in Airtable `Clients.Magic Link Hash`
- **State:** no new database; JSON files under `/opt/wc-solns/<tenant>/` + the existing Airtable CRM Hub
- **Branding:** DM Serif Display + DM Sans per the WCAS brand brief, zero em dashes anywhere (Sam's non-negotiable), orange `#E97B2E` only on CTAs
- **Deploy:** public GitHub repo → Hostinger Docker API → secrets via environment param (per Sam's standard deploy method, never change)
- **Cost control:** per-tenant $2/day Opus cap; kill-switch SMS to Sam if exceeded; Haiku-first scaffolding during dev, swap to Opus only for demo flow + final passes

### Open questions flagged for Day 1

- Does Sam's API account have `managed-agents-2026-04-01` beta access enabled? (Applied for by default on all API accounts per docs, but worth verifying.)
- Does Sam's API account have 1M context window access for the recommendations agent? (Needs verification  -  typical accounts get 200k.)
- Which of GSC, GBP, Meta, LinkedIn, Google Ads does AP have OAuth set up for? (Determines baseline capture scope.)
- Does the research preview request (multi-agent, memory, outcomes) get approved in time to use in the demo?

---

## Entry 1  -  Day 1 build, morning block
**2026-04-21 11:27 PDT → (in progress)**

**11:27 PDT  -  environment check.** Python 3.14.3 on Windows, `gh` CLI authenticated as `suaveshot` with `repo` scope, Docker not installed locally (expected  -  we build on the Hostinger VPS, not locally). TaskCreate + TaskList tools loaded. Eleven Day-1 tasks queued in the task list.

**11:29 PDT  -  OAuth inventory complete.** Grepped AP env files for configured integrations to scope baseline capture. Findings (credentials stayed in AP's .env; nothing copied into the dashboard repo):
- **GSC (Google Search Console)**  -  `seo_token.json` exists, can pull rankings
- **GBP (Google Business Profile)**  -  `gbp_token.json` exists, can pull reviews
- **Google Ads**  -  refresh token + customer IDs configured, can pull spend/clicks
- **GA4**  -  `ga4_fetcher.py` exists, can pull traffic
- **CRUX API** (Chrome UX Report)  -  configured; gives real-world Core Web Vitals, *better* than running Lighthouse in a container
- **GHL, Twilio, Vapi, Connecteam, DataForSEO**  -  all configured
- **Not connected:** Meta OAuth, LinkedIn OAuth  -  those baseline fields get "connect later"

**Baseline capture scope revised UP.** Original v4 plan was three metrics (GSC rankings, GBP reviews, call count). Revised list: GSC rankings + GBP reviews + Google Ads spend + GA4 traffic + CRUX Core Web Vitals + call count. Six metrics, all backed by existing OAuth.

**11:32 PDT  -  decisions logged.** Added ADR-010 (baseline capture scope) to DECISIONS.md.

*Next: scaffold the repo, create public GitHub repo, smoke-deploy to VPS.*
