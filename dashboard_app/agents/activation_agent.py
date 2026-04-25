"""
Activation Orchestrator - Managed Agent glue.

One shared agent + one shared cloud environment for the deployment.
Per-tenant session stored at <tenant_root>/agent_session.json so an
activation conversation survives across POSTs.

Public surface:
    run_turn(tenant_id, user_message) -> dict
    reset_session(tenant_id) -> bool
    get_agent_id()           -> str   (lazy, cached)
    get_environment_id()     -> str   (lazy, cached)
    get_or_create_session(tenant_id) -> str

Event loop (verified against anthropic SDK 0.96.0 beta types):
    user.message ->
      [agent.custom_tool_use]* ->
        session.status_idle(stop_reason.requires_action) ->
          user.custom_tool_result (one per tool use) ->
            agent.message / agent.custom_tool_use ->
              session.status_idle(end_turn)  # done
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import anthropic

from ..services import activation_tools, cost_tracker, heartbeat_store
from ..services.scrubber import scrub

log = logging.getLogger("dashboard.activation_agent")


MODEL = os.getenv("ACTIVATION_AGENT_MODEL", "claude-opus-4-7")
BETA_HEADER = "managed-agents-2026-04-01"

# Turn budget: set generously because tier-2 tool sequences (capture_baseline
# hits 5 Google APIs, create_ga4_property makes 3 sequential calls,
# verify_gsc_domain adds the site) can stack into a multi-minute turn when the
# agent chains them. 120s gives real room; the system prompt also instructs
# the agent to pause between logical chunks so turns stay user-interactive.
DEFAULT_TURN_BUDGET_S = 120


_AGENT_ID: str | None = None
_ENVIRONMENT_ID: str | None = None
_META_LOCK = threading.Lock()
_TENANT_LOCKS: dict[str, threading.Lock] = {}


def _shared_dir() -> Path:
    """Directory that holds process-wide agent + environment IDs."""
    base = Path(os.getenv("TENANT_ROOT", "/opt/wc-solns"))
    p = base / "_platform" / "agents"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _tenant_lock(tenant_id: str) -> threading.Lock:
    with _META_LOCK:
        lock = _TENANT_LOCKS.get(tenant_id)
        if lock is None:
            lock = threading.Lock()
            _TENANT_LOCKS[tenant_id] = lock
        return lock


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = """You are the WCAS Voice & Personalization specialist.

Your one-line job: "I learn your voice and your data so the rest of
your AI team sounds like you, not like a chatbot."

You are not here to help the owner sign in. OAuth handles that with
one click. You are here to do the work OAuth cannot: read their
website to learn how they sound, read their CRM to learn who they
serve, then translate both into the proven WCAS automation playbooks
so every message the platform sends in the months ahead reads like
the owner wrote it themselves.

You sound like a competent operator, not a chatbot. First person. Terse.
No em dashes, ever. Use commas, periods, or parens. Never "I'm an AI"
or "as an assistant." Never name the model or vendor running you.

THE 7 WCAS AUTOMATIONS YOU PERSONALIZE
======================================
gbp (Google Business Profile posts), seo (Search Console + Analytics
health), reviews (review-reply drafting), email_assistant (Gmail
inbound reply drafts), chat_widget (site chatbot in the owner's voice),
blog (blog-post generator), social (Facebook + Instagram post drafter).

THREE-LAYER ARCHITECTURE (this is the philosophy)
=================================================
1. MECHANICS = Sam's pre-designed playbooks. Deterministic. You do
   not invent orchestration logic at runtime.
2. ADAPTATION = You read the site + CRM ONCE during this conversation
   and write structured artifacts (voice card, CRM mapping, KB sections).
3. PERSONALIZATION = Downstream automations read those artifacts on
   every run and produce voice-matched output.

Your job is layer 2. Get it right and every automation in layer 3
sounds like the owner. Skip it and they sound generic.

CRITICAL PACING RULE: Do at most ONE logical chunk of work per turn.
A chunk is 1-3 tool calls that complete a single user-facing step.
Never chain more than 3 tool calls in a single turn. After any chunk,
your assistant message names what just happened and asks a specific
next question. Stop, wait for the owner.

FOUR-TURN HAPPY PATH
====================

Turn 1 (owner says hi / gives a URL):
  Call fetch_site_facts(url) AND detect_website_platform(url). One chunk.
  Read the raw HTML. Extract: business name, what they sell, hours, AND
  3-5 voice traits (warm? formal? bilingual? pun-heavy? brand-name-heavy?
  family-oriented?). Write a generic AI sample message ("Hi! Don't
  forget your appointment tomorrow.") in YOUR head. Then write the
  same message in THEIR voice using the traits you just identified.
  Call propose_voice_card(traits, generic_sample, voice_sample,
  sample_context, source_pages). The UI will render the side-by-side
  panel. STOP. In your assistant message say something like "Read
  your site. Here's how I hear you, take a look on the right."
  Wait for the owner to accept or edit the card.

Turn 2 (owner accepted/edited the voice card):
  Call confirm_company_facts(...) with the final business fields you
  pulled. Call write_kb_entry(section="services", ...). Optionally
  write_kb_entry for policies/pricing/faq if the site had them.
  Three calls max. End by asking what CRM or booking system they use
  to track customers (Airtable, Pipedrive, GHL, a Google Sheet).

Turn 3 (owner named their CRM):
  If they named Airtable AND a base is whitelisted for them:
    Call fetch_airtable_schema(base_id="") to read their actual data
    using the tenant default. Examine the tables, fields, and sample
    rows it returns. Identify segments worth acting on:
      - active customers (engaged in the last 30 days)
      - inactive_30d (lapsed 30+ days, ripe for re-engagement)
      - brand_new (created in the last 30 days, ripe for welcome)
    Map their column names to WCAS canonical fields (first_name,
    last_engagement, contact_email).
    Call propose_crm_mapping(base_id, table_name, field_mapping,
    segments, proposed_actions). Each segment needs slug, label,
    count, and up to 5 sample_names from the actual data. Each
    proposed_action ties a segment to a playbook + automation.
    The UI will render the segment-preview panel.
  If they named a non-Airtable CRM (or the schema fetch returns
  no_base_configured):
    Skip the schema read. Note in conversation that the CRM
    connection is post-hackathon for that vendor, and move on.
  Either way: end the turn by telling them to click the orange
  "Connect Google" button above the composer.

Turn 4 (owner returned from Google OAuth + accepted the CRM mapping):
  Quote ONE specific number from the probe summary in context (review
  count, GSC sites, GA4 properties). Then activate the rings the data
  unlocks. For Garcia and similar tenants:
    - activate_pipeline("gbp", "connected")
    - activate_pipeline("seo", "connected")
    - activate_pipeline("reviews", "connected")
  Three calls max. In a follow-up turn (Turn 4b if the owner says
  "keep going"), call activate_pipeline("email_assistant", "connected")
  + activate_pipeline("chat_widget", "connected") +
  activate_pipeline("blog", "connected") + capture_baseline().
  Then record_provisioning_plan(items=[...]) with exactly 7 items
  ONCE per session. Then mark_activation_complete(note="..."). The
  UI will draft 7 first-week samples + the live customer simulation.
  End with a warm two-sentence closing.

TOOL SURFACE
============
- fetch_site_facts(url), detect_website_platform(url) - turn 1
- propose_voice_card(traits, generic_sample, voice_sample, ...) - turn 1
- confirm_company_facts(...), write_kb_entry(section, content) - turn 2
- fetch_airtable_schema(base_id) - turn 3, BEFORE propose_crm_mapping
- propose_crm_mapping(base_id, table_name, field_mapping, segments,
  proposed_actions) - turn 3
- activate_pipeline(role_slug, step) - turn 4
- capture_baseline() - turn 4
- record_provisioning_plan(items) - ONCE per session, turn 4
- mark_activation_complete(note) - finish the wizard
- create_ga4_property / verify_gsc_domain - optional, only if needed
- request_credential(service, method) - only for non-Google providers;
  the orange Connect Google button is already in the UI

SCREENSHOTS AS A FALLBACK
=========================
If the owner's message has a "[Attached screenshot context: ...]" block
prepended, examine it carefully before answering. Describe what you see
in the current screen - the specific buttons, menus, current state -
and THEN suggest the next concrete action. Do not rely on what you
remember the UI used to look like.

VOICE RULES
===========
- Owner-to-owner. The reader is a dance-studio owner, HVAC operator,
  plumber. Warm, direct, zero corporate.
- Max 3 sentences per turn unless summarizing a probe or panel.
- Proof beats promises. After any tool that returns real data, cite
  one specific number in your reply.
- No emoji.
- No flattery. No "great question."
- If a tool returns is_error=true, narrate the failure in one sentence
  and suggest the next concrete step. Never retry a failing tool more
  than twice in a row.

THE DROPPED PIPELINES
=====================
sales_pipeline, ads, qbr are not in the 7-pipeline roster. Do not call
activate_pipeline on those slugs. If the owner asks, tell them they're
on the week-2 roadmap once we confirm their CRM (sales) or have a
month of baseline data (ads).

CONCIERGE FOR THE REST
======================
Owners occasionally ask about QuickBooks sync, Twilio SMS, GoHighLevel
provisioning. Those are "owner_signup with Sam walking you through"
(record it in the provisioning plan), not something you wire here.
"""


# ---------------------------------------------------------------------------
# Client + agent + environment lifecycle
# ---------------------------------------------------------------------------


def get_client() -> anthropic.Anthropic:
    """Singleton-ish Anthropic client. Re-created per process, not per call."""
    # anthropic.Anthropic reads ANTHROPIC_API_KEY from env by default.
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is required for the activation agent")
    return anthropic.Anthropic(default_headers={"anthropic-beta": BETA_HEADER})


def _agent_id_file() -> Path:
    return _shared_dir() / f"agent_id_{MODEL}.txt"


def _environment_id_file() -> Path:
    return _shared_dir() / "environment_id.txt"


def get_agent_id(*, client: anthropic.Anthropic | None = None) -> str:
    """Create (or re-use cached) agent with the 14 activation tools + system prompt."""
    global _AGENT_ID
    with _META_LOCK:
        if _AGENT_ID:
            return _AGENT_ID
        f = _agent_id_file()
        if f.exists():
            cached = f.read_text(encoding="utf-8").strip()
            if cached:
                _AGENT_ID = cached
                return cached
        client = client or get_client()
        agent = client.beta.agents.create(
            name="wcas-activation-orchestrator",
            model=MODEL,
            system=SYSTEM_PROMPT,
            tools=list(activation_tools.TOOL_SCHEMAS),
        )
        _AGENT_ID = agent.id
        f.write_text(_AGENT_ID, encoding="utf-8")
        log.info("created activation agent id=%s model=%s", _AGENT_ID, MODEL)
        return _AGENT_ID


def get_environment_id(*, client: anthropic.Anthropic | None = None) -> str:
    """Create (or re-use cached) shared cloud environment."""
    global _ENVIRONMENT_ID
    with _META_LOCK:
        if _ENVIRONMENT_ID:
            return _ENVIRONMENT_ID
        f = _environment_id_file()
        if f.exists():
            cached = f.read_text(encoding="utf-8").strip()
            if cached:
                _ENVIRONMENT_ID = cached
                return cached
        client = client or get_client()
        env = client.beta.environments.create(
            name="wcas-activation-env",
            config={"type": "cloud", "networking": {"type": "unrestricted"}},
        )
        _ENVIRONMENT_ID = env.id
        f.write_text(_ENVIRONMENT_ID, encoding="utf-8")
        log.info("created activation environment id=%s", _ENVIRONMENT_ID)
        return _ENVIRONMENT_ID


def _session_file(tenant_id: str) -> Path:
    return heartbeat_store.tenant_root(tenant_id) / "agent_session.json"


def get_or_create_session(
    tenant_id: str, *, client: anthropic.Anthropic | None = None
) -> str:
    """Return session id for this tenant. Creates one on first call per tenant."""
    with _tenant_lock(tenant_id):
        f = _session_file(tenant_id)
        if f.exists():
            try:
                cached = json.loads(f.read_text(encoding="utf-8")).get("session_id")
                if cached:
                    return cached
            except (OSError, json.JSONDecodeError):
                pass

        client = client or get_client()
        agent_id = get_agent_id(client=client)
        env_id = get_environment_id(client=client)
        sess = client.beta.sessions.create(
            agent=agent_id,
            environment_id=env_id,
            title=f"activation-{tenant_id}",
        )
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"session_id": sess.id}, indent=2), encoding="utf-8")
        log.info("created activation session tenant=%s session=%s", tenant_id, sess.id)
        return sess.id


def reset_session(
    tenant_id: str, *, client: anthropic.Anthropic | None = None
) -> bool:
    """Drop the stored session id. Best-effort delete on the remote session too."""
    with _tenant_lock(tenant_id):
        f = _session_file(tenant_id)
        if not f.exists():
            return False
        try:
            old = json.loads(f.read_text(encoding="utf-8")).get("session_id")
        except (OSError, json.JSONDecodeError):
            old = None
        try:
            f.unlink()
        except OSError:
            pass
        if old:
            try:
                (client or get_client()).beta.sessions.delete(old)
            except Exception:  # best-effort; remote may already be gone
                log.info("remote session delete failed for tenant=%s", tenant_id)
        return True


# ---------------------------------------------------------------------------
# Turn driver
# ---------------------------------------------------------------------------


def _tool_summary(name: str, ok: bool, payload: dict[str, Any]) -> str:
    """60-ish char human summary for the UI event pill."""
    if not ok:
        return f"{name}: {str(payload.get('error', 'failed'))[:60]}"
    status = payload.get("status", "ok")
    if name == "fetch_site_facts":
        return f"fetched {payload.get('url','site')} ({payload.get('pages',[{}])[0].get('status','?')})"
    if name == "confirm_company_facts":
        return f"saved {len(payload.get('fields_recorded', []))} fields to company.md"
    if name == "request_credential":
        return f"render_button {payload.get('service','google')}/{payload.get('method','oauth')}"
    if name == "activate_pipeline":
        return f"{payload.get('role_slug','?')} -> {payload.get('step','?')}"
    if name == "capture_baseline":
        errs = payload.get("errors") or []
        return "baseline captured" + (f" ({len(errs)} errors)" if errs else "")
    if name == "create_ga4_property":
        mid = payload.get("measurement_id", "")
        return f"GA4 {status}" + (f" {mid}" if mid else "")
    if name == "verify_gsc_domain":
        return f"GSC {status}"
    if name == "mark_activation_complete":
        return "activation marked complete"
    if name == "propose_voice_card":
        return f"voice card rendered ({payload.get('trait_count', 0)} traits)"
    if name == "fetch_airtable_schema":
        tcount = payload.get("table_count", 0)
        return f"read CRM schema ({tcount} table{'s' if tcount != 1 else ''})"
    if name == "propose_crm_mapping":
        scount = payload.get("segment_count", 0)
        return f"CRM mapping rendered ({scount} segment{'s' if scount != 1 else ''})"
    return f"{name}: {status}"


# Keep these literals out of the source (brand rule: no em dashes in *.py files).
_EM_DASH = chr(0x2014)
_EN_DASH = chr(0x2013)


def _post_filter_text(text: str) -> str:
    """Strip em/en dashes from agent output before returning to the UI."""
    return text.replace(_EM_DASH, ", ").replace(_EN_DASH, ", ")


def run_turn(
    tenant_id: str,
    user_message: str,
    *,
    turn_budget_s: int = DEFAULT_TURN_BUDGET_S,
    client: anthropic.Anthropic | None = None,
) -> dict[str, Any]:
    """Send one user message, pump events until end_turn, return collected result.

    Returns:
        {
            "events": [{"role": "tool"|"assistant"|"system", ...}, ...],
            "usage": {"input_tokens": N, "output_tokens": N, "usd": ...},
            "reached_idle": bool,   # True = end_turn, False = timed out / exhausted
        }
    """
    if not (user_message or "").strip():
        raise ValueError("user_message must be non-empty")

    # Cap check BEFORE spinning the event loop.
    allowed, reason = cost_tracker.should_allow(tenant_id)
    if not allowed:
        return {
            "events": [{"role": "system", "text": reason or "daily cap reached"}],
            "usage": {"input_tokens": 0, "output_tokens": 0, "usd": 0.0},
            "reached_idle": False,
        }

    client = client or get_client()
    session_id = get_or_create_session(tenant_id, client=client)

    deadline = time.monotonic() + turn_budget_s
    events_out: list[dict[str, Any]] = []
    pending_tool_calls: list[Any] = []
    total_input = 0
    total_output = 0
    reached_end = False

    try:
        with client.beta.sessions.events.stream(session_id) as stream:
            client.beta.sessions.events.send(
                session_id,
                events=[
                    {
                        "type": "user.message",
                        "content": [{"type": "text", "text": user_message.strip()}],
                    }
                ],
            )

            for event in stream:
                if time.monotonic() > deadline:
                    log.warning("activation turn exceeded budget tenant=%s", tenant_id)
                    events_out.append({
                        "role": "system",
                        "text": "Still thinking. Send again if you want me to keep going.",
                    })
                    break

                t = getattr(event, "type", "") or ""

                if t == "agent.message":
                    for block in getattr(event, "content", []) or []:
                        if getattr(block, "type", "") == "text":
                            text = _post_filter_text(block.text or "")
                            if text:
                                events_out.append({"role": "assistant", "text": text})

                elif t == "agent.custom_tool_use":
                    pending_tool_calls.append(event)

                elif t == "span.model_request_end":
                    usage = getattr(event, "model_usage", None)
                    if usage is not None:
                        total_input += int(getattr(usage, "input_tokens", 0) or 0)
                        total_output += int(getattr(usage, "output_tokens", 0) or 0)

                elif t == "session.status_idle":
                    stop = getattr(event, "stop_reason", None)
                    stop_type = getattr(stop, "type", "")

                    if stop_type == "end_turn":
                        reached_end = True
                        break
                    if stop_type == "retries_exhausted":
                        events_out.append({
                            "role": "system",
                            "text": "I hit the retry limit on that turn. Try sending your message again.",
                        })
                        break
                    if stop_type == "requires_action":
                        # Dispatch every pending tool call and send results back.
                        result_events: list[dict[str, Any]] = []
                        for call in pending_tool_calls:
                            name = getattr(call, "name", "") or ""
                            raw_args = getattr(call, "input", {}) or {}
                            args = raw_args if isinstance(raw_args, dict) else {}
                            ok, payload = activation_tools.dispatch(
                                tenant_id, name, args
                            )
                            events_out.append({
                                "role": "tool",
                                "name": name,
                                "ok": ok,
                                "summary": _tool_summary(name, ok, payload),
                            })
                            result_events.append({
                                "type": "user.custom_tool_result",
                                "custom_tool_use_id": call.id,
                                "content": [
                                    {"type": "text", "text": json.dumps(payload)}
                                ],
                                "is_error": not ok,
                            })
                            log.info(
                                "tool %s tenant=%s ok=%s",
                                name, tenant_id, ok,
                            )
                        pending_tool_calls = []
                        if result_events:
                            client.beta.sessions.events.send(
                                session_id, events=result_events
                            )
                        # Loop continues; stream yields more events after
                        # we send the tool results.

                elif t in ("session.status_running", "span.model_request_start"):
                    pass  # progress ticks, nothing to surface

                elif t == "session.error":
                    events_out.append({
                        "role": "system",
                        "text": "Something went sideways on my end. Try again, I'll pick up where we left off.",
                    })
                    log.error(
                        "session.error tenant=%s payload=%s",
                        tenant_id,
                        scrub(str(event))[:200],
                    )
                    break

                elif t == "session.status_terminated":
                    # Remote closed the session. Clear local cache so next turn recovers.
                    reset_session(tenant_id, client=client)
                    events_out.append({
                        "role": "system",
                        "text": "Let's start fresh. Send your message again.",
                    })
                    break

    except anthropic.APIError as exc:
        log.exception("activation agent APIError tenant=%s", tenant_id)
        events_out.append({
            "role": "system",
            "text": "I'm having trouble reaching my brain right now. Try again in a few seconds.",
        })
        # Don't reset session on transient API errors; it's recoverable.
        return {
            "events": events_out,
            "usage": {"input_tokens": total_input, "output_tokens": total_output, "usd": 0.0},
            "reached_idle": False,
        }

    usd = cost_tracker.record_call(
        tenant_id=tenant_id,
        model=MODEL,
        input_tokens=total_input,
        output_tokens=total_output,
        kind="activation_turn",
    )

    return {
        "events": events_out,
        "usage": {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "usd": usd,
        },
        "reached_idle": reached_end,
    }


# ---------------------------------------------------------------------------
# Test hooks
# ---------------------------------------------------------------------------


def _reset_module_cache_for_tests() -> None:
    """Clear process-level agent/environment caches so tests can re-create."""
    global _AGENT_ID, _ENVIRONMENT_ID
    with _META_LOCK:
        _AGENT_ID = None
        _ENVIRONMENT_ID = None
        _TENANT_LOCKS.clear()
