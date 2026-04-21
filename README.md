# WCAS Client Dashboard

> A multi-agent dashboard for agency-level client activation, built with multi-agent platform tooling.

**Hackathon submission:** [Built with Opus 4.7](https://cerebralvalley.ai/e/built-with-4-7-hackathon)  -  Cerebral Valley + Anthropic, Apr 21-26, 2026.

**Live demo:** [dashboard.westcoastautomationsolutions.com](https://dashboard.westcoastautomationsolutions.com) *(deployment pending Day 1)*
**Judge demo-mode link:** *(seeded Day 5, token published here before submission)*

## What this is

WestCoast Automation Solutions (WCAS) sells a ten-automation platform to owner-operator service businesses  -  SEO, blogs, Google Ads, sales pipeline, email assistant, GBP, reviews, voice agent, social media, and QBRs. The problem isn't selling it. The problem is the moment between *"they signed the contract"* and *"the pipelines are actually producing."* Agencies fumble this window; clients feel abandoned, pipelines stay dark, and by the time anyone runs a report nobody remembers what the client actually wanted.

This dashboard is that window, done right.

1. **Activation**  -  a 30-minute conversation, driven by an Opus 4.7 Managed Agent, that takes a freshly-signed client from zero to producing. The model walks them through their purchased pipelines, captures credentials, writes their personalized config, seeds a per-client knowledge base, freezes Day-1 baseline metrics, and captures 1-3 concrete goals for the next 90 days.
2. **Live monitoring**  -  the dashboard shows real pipeline telemetry with a hero strip that answers the only question clients actually have: *"why am I paying for this?"* Weeks Saved + Revenue Influenced + Goal Progress, updated live.
3. **Goal-anchored recommendations**  -  after 30 days of real telemetry, a second Opus 4.7 Managed Agent reads the full tenant state in a single 1M-context call and produces recommendations tied to the client's pinned goals. One-click Apply, with a 10-second undo on every automated action.

The demo runs against real Americal Patrol data  -  a 40-year-old security patrol company that's been Sam's flagship client for Claude-driven automation since day one. The founder is the customer. Every system shipped here was first run on Sam's own business.

## How Opus 4.7 is used

Three **Claude Managed Agent** sessions, plus supporting direct Messages API calls:

| Agent | Platform | Tools | Why Opus 4.7 specifically |
|---|---|---|---|
| Activation Orchestrator | Managed Agents (`managed-agents-2026-04-01`) | 10 activation tools, file ops, bash | 30-min tool-heavy conversation with server-side resume via event history |
| Recommendations Generator | Managed Agents, weekly cron | File ops, config writes, **1M context window** for full tenant history | Goal-anchored recs from entire log + state + baseline + goals in a single prompt  -  no RAG, no chunking |
| Baseline Capturer | Managed Agents, one-shot | Web fetch (CRUX, PageSpeed), MCP (GSC, GBP, GA4, Google Ads) | Parallel fan-out across six OAuth APIs |

Remaining Opus calls stay on direct Messages API: the guard-rail review pass on every automated outbound, the hero-stats revenue attribution narrative, and the "Ask Claude about this pipeline" contextual shortcut.

## Architecture

*Architecture diagram inserted Day 5.*

## Platformization seeds

Five architectural decisions made in hackathon week that preserve the "license this to sub-agencies" option without a rewrite later:

1. **Tenant-scoped everything**  -  every route, file path, query takes a `tenant_id`. No hardcoded strings.
2. **Per-tenant brand override**  -  `/opt/wc-solns/<tenant>/brand.json` swaps logo + colors + fonts via CSS custom properties.
3. **Per-client knowledge base**  -  `kb/*.md` per tenant grounds every future Opus surface (voice agent, chatbot, email drafts, QBRs).
4. **Guard-rail review hook**  -  every automated outbound passes through `review_outbound()` before sending; em-dash strip this week, full Opus sanity pass post-hackathon.
5. **Goals → tuning levers schema**  -  goals.json captures `tuning_levers`; post-hackathon an auto-tuner will read goals and write config deltas to bias automations toward each goal.

## Getting started locally

```bash
git clone https://github.com/suaveshot/wcas-client-dashboard.git
cd wcas-client-dashboard
cp .env.example .env
# Fill in ANTHROPIC_API_KEY, AIRTABLE_PAT, SESSION_SECRET at minimum
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
uvicorn dashboard_app.main:app --reload
```

Open http://localhost:8000. Without a magic-link token the landing page renders; activation and dashboard routes require authentication.

## Deploying to Hostinger VPS

```bash
# On the VPS
git clone https://github.com/suaveshot/wcas-client-dashboard.git /opt/wc-solns/dashboard_app
cd /opt/wc-solns/dashboard_app
cp .env.example .env
# Fill in real values
docker compose up -d
```

See `docs/deploy.md` for the full production setup (Let's Encrypt, nginx proxy, backup cron).

## Build journal + decisions

Every major decision and daily progress is logged openly:

- [`JOURNAL.md`](./JOURNAL.md)  -  chronological build log with timestamps
- [`DECISIONS.md`](./DECISIONS.md)  -  architecture decision records

## License

Code: [MIT](./LICENSE).
WCAS brand assets (logo, tokens, marketing copy): proprietary, see LICENSE brand notice.

---

*Built solo during Apr 21-26, 2026 for the Claude Opus 4.7 hackathon. See [JOURNAL.md](./JOURNAL.md) for the full story.*
