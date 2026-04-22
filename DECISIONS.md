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

---

## ADR-011  -  VPS selection: srv1568946 (Ubuntu) over srv892948 (n8n)
**Date:** 2026-04-21 12:40 PDT
**Status:** Accepted

**Decision:** Deploy the dashboard to Hostinger VPS srv1568946.hstgr.cloud (IP 93.127.216.242), not srv892948.hstgr.cloud (IP 31.97.147.220).

**Why:**
- srv892948 runs n8n + the existing WCAS workflow stack (9+ production workflows). A dashboard bug there could disrupt every active WCAS automation.
- srv1568946 runs lower-criticality services (Garcia Folklorico site + AP automations support). Isolation protects the n8n stack.
- SSH key is already configured for srv1568946 as the `garcia-vps` alias in ~/.ssh/config; no new access provisioning needed.

**Trade-off:** the dashboard needs to call n8n webhooks (hosted on srv892948). That's cross-VPS over the public internet, adds ~20ms latency, but n8n webhooks are already designed for public access.

---

## ADR-012  -  Shared Caddy proxy plan (pending authorization)
**Date:** 2026-04-21 13:15 PDT
**Status:** Proposed, pending Sam authorization

**Situation:** srv1568946 has ports 80/443 held by `garcia-folklorico-caddy-1`, a Caddy instance running in command mode (`caddy reverse-proxy --from api.garciafolklorico.com --to app:8000`). Dedicated to one domain.

**Proposed decision:** Replace Garcia's dedicated Caddy with a shared Caddy container that serves both `api.garciafolklorico.com` and `dashboard.westcoastautomationsolutions.com` from a Caddyfile. New files at `/docker/wcas-dashboard/` (Caddyfile + docker-compose.yml). New Caddy joins `garcia-folklorico_default` network to reach Garcia's app container. Garcia's app container is never touched.

**Why not skip and use a different port?**
- Non-standard port breaks HTTPS-via-Let's Encrypt.
- Judges hitting a dashboard at `:8443` see a broken-looking URL. Bad first impression.
- The shared-proxy pattern is where the VPS needs to go anyway once a second WCAS paying client signs. Doing it now is 30 min; doing it when we have 3 clients is a weekend.

**Trade-off:** 30 to 60 seconds of downtime on `api.garciafolklorico.com` during the swap. Garcia is a dance-studio booking site, not time-critical. Sam can pick a low-traffic moment.

**Alternative paths already considered and rejected:**
- Modify Garcia's compose in place: same risk, less clean state.
- Run dashboard on a separate port: bad URL, breaks judging first impression.
- Deploy to the other VPS (srv892948): no SSH access from this session.
- Deploy via Cloudflare / Vercel: forbidden per `feedback_no_cloudflare.md`.
- Defer VPS deploy entirely: costs us the live-URL judging criterion on Day 2.

**Sam's decision required before Day 2 build starts.**

---

---

## ADR-013  -  Heartbeat pattern: PC-side fire-and-forget
**Date:** 2026-04-21 22:20 PDT
**Status:** Accepted and shipping

**Decision:** Americal Patrol pipelines running on Sam's PC push their post-run state to the dashboard via a small Python script (`Americal Patrol/shared/push_heartbeat.py`) that each pipeline's `.bat` wrapper calls at end-of-run. The script:
- Never crashes the calling pipeline (always exits 0 even on network error).
- Has a 5-second HTTP timeout (never blocks pipeline beyond that).
- Redirects stdout/stderr to `nul` in the .bat so pipeline output stays clean.
- Logs locally to `shared/heartbeat.log` for debugging without SSH to the VPS.
- Caps `state_summary` payload to scalar fields + `*_count` rollups, keeping total under ~3 KB. Full tenant state is read server-side when needed.

**Why fire-and-forget vs tight coupling:** the pipelines are production workloads that must not break because of the dashboard. Heartbeat failure should be a dashboard-side problem, not a pipeline-side one. The dashboard treats missing heartbeats as "unknown" status, which is the correct degraded-mode UX.

**Auth:** shared secret header `X-Heartbeat-Secret`. Rotatable via `.env` on both ends.

**Wired pipelines this week:** patrol (Morning Reports), seo (Weekly SEO), sales_pipeline (Daily Run). Remaining pipelines wired Day 2 morning.

---

## ADR-014  -  Managed Agent resource lifecycle: archive vs delete
**Date:** 2026-04-21 22:40 PDT
**Status:** Accepted

**Decision (discovered via smoke test):** The Anthropic Managed Agents Python SDK uses different cleanup semantics across resource types:
- `client.beta.agents.archive(id)` (NOT `delete`)  -  agents are versioned resources; archiving retains version history but marks them inactive.
- `client.beta.environments.delete(id)`  -  environment templates are deleted outright.
- `client.beta.sessions.delete(id)`  -  session records are deleted outright.

**Why this matters:** Day 3 activation flow creates resources per tenant activation. If we use the wrong cleanup method, either cleanup fails silently (agents) or resources pile up costing money. Document the distinction in `scripts/smoke_managed_agent.py` as a canonical reference.

---

## ADR-015  -  External uptime monitor via GitHub Actions cron
**Date:** 2026-04-21 22:50 PDT
**Status:** Proposed (template ready, pending workflow-scope auth)

**Decision:** Rather than sign up for a third-party uptime service, run the uptime check as a GitHub Actions cron job every 10 minutes. Job runs on GitHub's infrastructure (fully external from our Hostinger VPS), pings `/healthz`, fails the workflow on non-200, which sends the repo owner an email notification. Zero cost, zero extra account, included in GitHub free tier minutes.

**Template:** `docs/ci-templates/uptime.yml.template`. Activates once Sam runs `gh auth refresh -s workflow -h github.com` and moves the file to `.github/workflows/uptime.yml`.

---

---

## ADR-016  -  Sam-only `/admin` operator view
**Date:** 2026-04-21 evening
**Status:** Accepted, ships Day 4 afternoon

**Decision:** Add an admin-scoped route tree at `/admin/*` that renders an operator command center: all-clients grid, per-tenant invoice status, kill switches, cost-per-client, onboarding SLA clock, cross-client intel, platform health. Gated by an `ADMIN_EMAILS` env var allowlist (default: `salarcon@americalpatrol.com`). Session cookie carries `role="admin"` claim; wrong role yields 403 with branded error page.

**Why:** the product's whole multi-tenant architecture (tenant-id scoping, per-tenant configs, isolated KBs) is invisible from the single-client view. The admin view exercises that architecture end-to-end AND gives Sam the one view he actually needs to profitably run an agency: whose pipelines are healthy, whose invoices are paid, whose costs exceed their revenue.

**Alternative considered:** build a separate admin app. Rejected because it would require a second auth system, separate deploy, separate CI. Adding `/admin/*` to the existing FastAPI app with one allowlist check is 1/10th the work.

**Layout (six rows):**
1. Operator hero: total MRR · platform cost · gross margin % · client count by status
2. Needs-you-today inbox: escalations, voice notes owed, overdue invoices, churn alerts, stuck activations
3. Client grid: one card per tenant with pipeline health, goal progress, invoice badge, MRR, cost-month, kill switch
4. Cross-client intelligence: pipeline leaderboard, Opus-generated anonymized patterns (Week 2)
5. Platform health: Managed Agents spend, error rate, deploy SHA, uptime (Week 2)
6. Quick actions: broadcast, export, refresh-all-recs (Week 2)

**Hackathon scope cut:** rows 1-3 ship Day 4; rows 4-6 are Week 2.

---

## ADR-017  -  Kill switch design: alert-first, manual-trigger, reversible
**Date:** 2026-04-21 evening
**Status:** Accepted, ships Day 4

**Decision:** The kill switch flips `tenant.status` between `"active"` and `"paused"` via `POST /admin/api/clients/<tenant_id>/status`. Paused tenants have every pipeline run guarded by a status check (no-op if not active). Paused client's `/dashboard` renders a branded "account paused, contact Sam" page. Every flip logs to `dashboard_decisions.jsonl` with timestamp + operator email + from/to status + optional reason.

**Why not auto-pause at 30 days overdue?** Every payment situation has context: wire delays, disputes, new cards, bank holidays. Auto-pause at 30 days risks damaging client relationships over bookkeeping friction. Alert-first is the agency-level choice. An optional `AUTO_PAUSE_AT_DAYS=45` env var exists for when Sam trusts the automation enough to enable it later.

**Why all-or-nothing per tenant vs per-pipeline granularity?** Per-pipeline creates a combinatorial explosion of partial states that's hard to reason about, hard to test, and hard to recover from. One toggle, one state transition. Simpler = safer.

**Why reversible with state preservation?** If flipping the switch required a re-activation ceremony, operators would avoid using it even when appropriate. Reversible = low-friction = actually used.

**Paused page UX:** plain-English owner-to-owner voice, no error state, no technical details. *"Your account is paused. Reach out to Sam at info@westcoastautomationsolutions.com to reactivate."* Dignified, not punishing.

**Invoice integration:** reads `Clients.Payment Status` from Airtable (populated by existing n8n Payment Sync workflow `6C7ngCdtIPzdTSE0`). No new QBO work required. If QBO OAuth not yet configured, invoice badge shows neutral "QBO sync pending" instead of empty UI.

---

## ADR-018  -  Cost tracking by tenant for profitability visibility
**Date:** 2026-04-21 evening
**Status:** Accepted, ships Day 5 morning

**Decision:** Every Anthropic API call (Messages + Managed Agents) gets tagged with the originating `tenant_id` via the cost-tracker middleware. Costs roll up to a per-client total visible on the admin client card and in the operator hero margin calculation.

**Why:** the single metric agencies never track is cost-per-client. Some clients cost 3x what they pay because of heavy agent usage or edge-case resolution. Without visibility, WCAS silently subsidizes unprofitable tenants. With visibility, Sam can make pricing + tier decisions grounded in data.

**Implementation:** extends the existing cost tracker from Day 2 security block. Adds a `tenant_id` column to the tracker's JSONL log. Admin view aggregates by tenant over current month.

**Post-hackathon extension:** flag any tenant whose trailing-30-day cost exceeds 30% of MRR as "margin at risk." Opus proposes tier upgrade or pipeline trimming.

---

## ADR-019  -  Magic-link auth with hashed-token Airtable storage
**Date:** 2026-04-22
**Status:** Accepted, shipped Day 2 AM

**Decision:** Auth uses a one-hour single-use magic link. The plaintext token is only ever held by the client's email client and the one HTTP request that redeems it; Airtable stores only the SHA-256 hash of the outstanding token plus its expiry and a consumed flag. Redeeming it performs a constant-time compare (`hmac.compare_digest`), writes consumed=True, clears the hash, and issues a signed session cookie.

**Why not password auth?** Passwords are a liability for a small-shop product: credential stuffing, password resets, breached-hash rotation, and "forgot my password" support tickets. Owner-operators have one job and it's not remembering another password.

**Why hash the token in Airtable?** If an Airtable row export leaks (screen-shared support call, accidental copy, compromised PAT), the attacker still can't log in  -  they only see the hash, and we compare against it with a constant-time check. The plaintext token is high-entropy enough that brute-forcing the hash is impractical within the 1-hour window.

**Why SHA-256 and not bcrypt/argon2?** The tokens are random with 256 bits of entropy and single-use within 60 minutes; the attack is not "break the hash" but "exhaust the hash space during the window." Offline cracking of a hashed high-entropy single-use token isn't meaningfully slowed by a slow hash. SHA-256 gets us verifiable fixed-length hex storage without adding a dependency.

**Neutral-response privacy:** `/auth/request` always redirects to the "check your inbox" page, even for unknown emails and even when Airtable is misconfigured or down. An attacker can't enumerate which emails exist in the Clients table.

---

## ADR-020  -  Session cookie: itsdangerous, HttpOnly+SameSite=Strict+Secure, 24h rolling
**Date:** 2026-04-22
**Status:** Accepted, shipped Day 2 AM

**Decision:** Successful magic-link redemption issues an `itsdangerous.URLSafeTimedSerializer` signed cookie with salt `wcas-session-v1`, max_age=86400, HttpOnly, SameSite=Strict, Secure (when `PRODUCTION=true`). The cookie payload is `{"tid": tenant_id, "em": email, "rl": role}`  -  the minimum needed for middleware to resolve a request to a tenant.

**Why a signed cookie and not a server-side session store?** One container, no Redis. We don't have a session-write bottleneck problem; we have a "make sure the signature holds" problem. itsdangerous is a 50KB dep that's already in requirements for FastAPI utilities.

**Why SameSite=Strict over Lax?** The dashboard is a single-origin SPA with no cross-site POSTs that need to carry the session (the only inbound write from another origin is `/api/heartbeat`, which uses a shared secret header, not the cookie). Strict eliminates the whole CSRF class for this week; Lax would leave us debating edge cases.

**Why no session-side invalidation list yet?** Hackathon scope: one container, small number of sessions. If a token is leaked, we can invalidate all sessions by rotating `SESSION_SECRET`. Post-hackathon adds a per-session id + server-side revocation list.

---

## ADR-021  -  Rate limiting: in-process sliding window with two buckets
**Date:** 2026-04-22
**Status:** Accepted, shipped Day 2 AM

**Decision:** A tiny threading-lock + deque sliding-window limiter protects two endpoints: `/auth/request` at 5 events per 15 minutes per email (stops email-bombing a known address), and `/api/heartbeat` at 120 events per minute per tenant (stops a stolen shared secret from flooding snapshot storage). Single-process; when we scale past one container we'll swap to Redis or Cloudflare rate-limit rules.

**Why in-process and not Nginx?** Our deploy is Caddy in front of one container; Caddy rate-limiting is available but per-IP, not per-email or per-tenant. The attacker dimension that matters here is "spray one known email from many IPs" for the login flow, and "one stolen secret floods storage" for the heartbeat. IP-level limiting is the wrong axis.

**Why not a library like slowapi?** We avoided the dependency weight for 40 lines of code. When we need multi-container sync, a library with a Redis backend earns its inclusion.

---

## ADR-022  -  PII scrubber: redact by pattern before any log write
**Date:** 2026-04-22
**Status:** Accepted, shipped Day 2 AM

**Decision:** Every string that gets written to `dashboard_decisions.jsonl`, to the cost tracker log, or to any future observability sink passes through `services.scrubber.scrub()` first. Patterns redact emails, phones, dollar amounts, and common secret prefixes (`sk-ant-api03-`, `pat...`, `ghp_`, `ghs_`). Prod ALWAYS scrubs; `DEBUG_LOG_PROMPTS=true` disables scrubbing for dev only.

**Why conservative patterns (false-positive-prone)?** Over-redaction is correct behavior here. A phone number that looks like a timestamp is fine to redact; an email hiding inside prose that isn't caught and ends up in a log dump is a data-protection failure. We optimize for zero PII leaks at the cost of some `[phone]` noise in logs when a pattern matches an unrelated 10-digit sequence.

**Why not structured logging?** Long-term, yes. For the hackathon, the scrubber is a single seam that covers the existing JSONL-append log paths without rewriting every call site.

---

## ADR-023  -  Cost tracker: JSONL per call, dev + tenant caps, unknown-model fallback to Sonnet-tier
**Date:** 2026-04-22
**Status:** Accepted, shipped Day 2 AM

**Decision:** Every Anthropic Messages-API or Managed-Agents call passes through `cost_tracker.record_call(tenant_id, model, in_tok, out_tok, kind)` which appends one JSON line to `/opt/wc-solns/_platform/cost_log.jsonl`. Two gates: `DAILY_DEV_CAP` across all tenants (default $20) and `DAILY_TENANT_CAP` per tenant (default $2). `should_allow(tenant_id)` returns (False, reason) when either cap is hit, and callers surface a calm "budget reached today" message instead of crashing.

**Why JSONL and not SQLite?** JSONL is append-only with no lock contention across threads, easy to grep, easy to re-ingest into whatever analytics tool ships post-hackathon. One container, one process; SQLite's transactional story isn't needed yet.

**Why Sonnet-tier pricing as the unknown-model fallback?** If a new model name lands between my knowledge and the caller's runtime, under-counting its cost would mask the spend. Over-counting by pricing it as Sonnet is the conservative error  -  we'll stop sooner on a budget breach, not later.

**Why cap at $20 dev / $2 tenant?** The $500 hackathon credit budget split across 6 days is ~$83/day. $20 is a quarter of that  -  tight enough to fail loud on a runaway loop during dev, loose enough not to kick in during normal activity. $2 per tenant is set so that 10 active tenants at cap is still below the daily dev cap (10 * $2 = $20), keeping the math coherent.

---

*More ADRs added as decisions are made during the build.*
