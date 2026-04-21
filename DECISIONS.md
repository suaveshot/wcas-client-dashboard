# Architecture Decision Records

Short crisp entries for each major technical decision. Format: **what, why, alternatives considered, date**. Order is chronological.

---

## ADR-001  -  Use Claude Managed Agents for heavy agent workloads
**Date:** 2026-04-21
**Status:** Accepted

**Decision:** The Activation Orchestrator, Recommendations Generator, and Baseline Capturer run on Anthropic's Managed Agents platform (beta `managed-agents-2026-04-01`), not hand-rolled loops on the Messages API.

**Why:**
- Long-running sessions (30-min activation chat) are exactly the use case Managed Agents is designed for.
- Server-side event history solves our session-resume requirement (U1) for free.
- Built-in file tool writes directly to the agent's sandbox workspace; we copy out to the tenant directory on completion  -  no custom file-writing endpoint.
- Hackathon judging rewards creative use of Claude's capabilities; this is Anthropic's newest platform surface.
- Reduces about half a day of harness code (agent loop, tool dispatch, caching, SSE streaming, cost tracking).

**Alternatives considered:**
- Hand-rolled loop on direct Messages API (what v1-v4 of the plan assumed). Rejected because session resume, cost tracking, and prompt caching all become our problem.
- Claude Agent SDK run inside our container. Rejected because we'd still operate the runtime.

**Trade-offs:**
- Beta feature  -  behaviors may shift between releases.
- Rate limit: 60 create-requests/min per org (acceptable for single-tenant demo).
- Research preview features (multi-agent, memory, outcomes) require separate application.

---

## ADR-002  -  Keep light synchronous calls on the direct Messages API
**Date:** 2026-04-21
**Status:** Accepted

**Decision:** Guard-rail review pass, hero-stats revenue attribution narrative, "Ask Claude about this pipeline" shortcut, and activity-feed item summarization all use direct Messages API calls  -  not Managed Agents.

**Why:**
- These calls are short, synchronous, low-complexity.
- Managed Agent session overhead (create session + environment + events) would be wasteful for one-shot calls measured in seconds.
- Lower-latency path to user.

---

## ADR-003  -  Hard separation: dashboard only READS Americal Patrol state
**Date:** 2026-04-21
**Status:** Accepted (non-negotiable)

**Decision:** The hackathon dashboard application never writes to the Americal Patrol directory, never modifies AP's config, and never triggers AP pipelines to run. It only reads state that the AP pipelines push to the VPS via `push_heartbeat.py`.

**Why:**
- AP is a production security business operating since 1986. It cannot be risked for a hackathon demo.
- If the dashboard has a bug, it cannot affect AP pipelines that clients depend on.
- Every dashboard write goes to `/opt/wc-solns/<tenant_id>/`  -  new tenants only. AP's tenant directory is read-only to dashboard code.

**Enforcement:** code-reviewer agent checks every file write path during end-of-day review; grep check on Day 1, Day 3, Day 5.

---

## ADR-004  -  Dual-agent strategy: build-time vs runtime
**Date:** 2026-04-21
**Status:** Accepted

**Decision:** Two categories of Claude agents serve two distinct purposes in this build.

**Build-time agents** (drive the development process):
- Sam's ten custom subagents in `~/.claude/agents/`  -  security-auditor, code-reviewer, config-guardian, designer, site-builder, pipeline-ops, deep-research, content-creator, email-drafter, claude-code-guide
- Dispatched via the Agent tool in Claude Code during the 5-day build

**Product-runtime agents** (what the client's dashboard actually runs):
- Three Anthropic Managed Agents  -  Activation Orchestrator, Recommendations Generator, Baseline Capturer
- Plus direct Messages API calls for light synchronous work

**Why:** Both categories are "Claude agents" in different senses. Using both is a coherent hackathon story  -  *"a multi-agent dashboard built with multi-agent tooling"*  -  and leverages Sam's existing agent suite investment.

---

## ADR-005  -  Dashboard for paying clients only (activation, not acquisition)
**Date:** 2026-04-21
**Status:** Accepted

**Decision:** The dashboard surfaces only to clients who have already signed, paid, and been moved to the Airtable Clients table by the existing n8n Client Onboarding workflow (`VTObkRbwQZ8wiLDf`). The onboarding conversation is an **activation flow** for pipelines they've already purchased  -  not a discovery or sales tool.

**Why:**
- Sam's business model is subscription, not self-serve.
- Narrows the activation agent's tool set from "recommend which pipelines" to "configure the ones you bought"  -  cleaner, tighter demo.
- Matches the existing CRM flow (Deal Won → Client created → dashboard access granted).

**Consequence:** the onboarding agent's tool set is 10 tools scoped to activation (confirm_company_facts, activate_pipeline, request_credential, set_schedule, set_preference, set_timezone, capture_baseline, set_goals, write_kb_entry, mark_activation_complete)  -  no `recommend_pipeline` or `remove_pipeline`.

---

## ADR-006  -  Per-client knowledge base as the grounding layer for every AI surface
**Date:** 2026-04-21
**Status:** Accepted (seed this week, full wiring post-hackathon)

**Decision:** Each tenant gets `/opt/wc-solns/<tenant>/kb/` with markdown files (`company.md`, `services.md`, `pricing.md`, `voice.md`, `policies.md`, `faq.md`, `known_contacts.md`). The Activation Orchestrator writes to these as it collects facts. Every future Opus-powered surface (voice agent, chatbot, email drafts, proposals, QBRs) reads from the same KB.

**Why:**
- Single source of truth per client. No AI surface drifts from any other.
- Aligns with Sam's `project_larry_as_operator.md` vision for multi-surface brain.
- Hidden competitive moat  -  no one else does per-client KB grounding cleanly.

**Scope this week:** KB directory created, activation agent writes to it, recommendations agent reads from it.
**Scope post-hackathon:** wire voice agent, chatbot, email assistant, and QBR generator to read from the same KB.

---

## ADR-007  -  Repo is public from Day 1 with strict hygiene
**Date:** 2026-04-21
**Status:** Accepted

**Decision:** `github.com/<sam-user>/wcas-client-dashboard` is public visibility from the first commit, MIT license on code, with a separate note that brand assets (logo, fonts, tokens) are WCAS property and not MIT-licensed.

**Why:**
- Hackathon submission requires a discoverable repo.
- Building in the open is a trust signal to judges and future clients.

**Enforcement:**
- `.gitignore` blocks all `.env*` except `.env.example`, all state files, all tenant data, all KB files.
- Pre-commit hook runs gitleaks or equivalent secret scanner.
- Dependabot + `pip-audit` GitHub Action enabled.
- No hardcoded strings: "Americal Patrol", "AP", client names, deal values  -  grep-checked on Day 1, Day 3, Day 5.
- `demo_mode=true` env flag scrambles tenant names + redacts dollar values for the public demo video.

---

## ADR-008  -  Recommendations engine gated until 30 days post-activation
**Date:** 2026-04-21
**Status:** Accepted

**Decision:** The Recommendations Generator is disabled for the first 30 days after a tenant is activated. During that window the dashboard shows a "we're learning your patterns  -  first recommendations in X days" placeholder card.

**Why:**
- Recommendations need real telemetry to be grounded. Generic recs produced from thin data would damage trust.
- The demo video gets a better arc: fresh demo tenant shows the placeholder, mature Americal Patrol tenant shows live goal-anchored recommendations.

---

## ADR-009  -  Goal-anchored recommendations (not just metric-dump)
**Date:** 2026-04-21
**Status:** Accepted

**Decision:** Every recommendation must be tied to one of the client's Day-1 goals, with quantified impact against that goal.

**Why:**
- Transforms the dashboard from "a thing you check" into "a coach you hear from."
- Goals are collected during activation and pinned to the dashboard.
- Opus 4.7's reasoning quality shines when anchoring to a specific target.

**Example shape:** *"You're at 47/80 leads toward your 90-day goal. Your sales pipeline sent 42 follow-ups this month; 39 landed before 9am. Shifting to 10-11am should lift open rates 18%, roughly 12 extra leads per month."*

---

---

## ADR-010  -  Baseline capture scope: six metrics from existing OAuth
**Date:** 2026-04-21 11:32 PDT
**Status:** Accepted

**Decision:** Day-1 baseline captures six metrics by reusing Americal Patrol's already-connected OAuth integrations:
1. GSC rankings (top 10 keywords) via existing `seo_token.json` + `gsc_fetcher.py`
2. GBP review count + star average via existing `gbp_token.json` + GBP Business Information API
3. Google Ads 30-day spend + clicks + impressions via existing refresh token + customer IDs
4. GA4 sessions + top-landing-page traffic via existing `ga4_fetcher.py`
5. Core Web Vitals (LCP, INP, CLS) via the CRUX API  -  real-world field data, no headless browser needed
6. Call volume snapshot via GHL API (existing `GHL_LOCATION_ID`)

**Why:**
- Every metric is backed by OAuth that AP already has working. Zero new OAuth setup on the hackathon critical path.
- CRUX API is strictly better than running Lighthouse headlessly in the VPS container: real field data from real Chrome users, no 1 GB browser image overhead, no flaky synthetic measurement.
- Six concrete metrics are enough to produce a rich "before" story for every future recommendation, QBR, and ROI calculation.

**Alternatives considered:**
- Minimum 3-metric baseline (GSC, GBP reviews, call count)  -  rejected as too thin.
- Add Meta + LinkedIn follower counts  -  rejected for hackathon scope (OAuth not set up); deferred to post-hackathon.
- Run Lighthouse headlessly in Docker  -  rejected in favor of CRUX API (simpler, smaller image, real-world data).

**Scope note:** The baseline metrics ship frozen into `/opt/wc-solns/<tenant>/baseline.json` at activation completion. The file is immutable after first write; all future reports compare against it.

---

*More ADRs added as decisions are made during the build.*
