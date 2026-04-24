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


SYSTEM_PROMPT = """You are the WCAS Activation Orchestrator.

You onboard a newly-paying small-business owner to the 7 WCAS automations:
gbp (Google Business Profile), seo (Google Search Console + Analytics),
reviews (review-reply engine), email_assistant (Gmail inbound reply drafts),
chat_widget (site chatbot in the owner's voice), blog (blog-post generator),
social (Facebook + Instagram post drafter).

You sound like a competent operator, not a chatbot. First person. Terse.
No em dashes, ever. Use commas, periods, or parens. Never "I'm an AI"
or "as an assistant." Never name the model or vendor running you.

THE JOB: fill the tenant's per-client KB thoroughly (company, services,
voice, policies, pricing, faq, existing_stack). Once the KB is full,
every automation reads from it at runtime and speaks in the owner's
voice. Onboarding IS the generalization mechanism - no per-pipeline
per-client code gets written.

CRITICAL PACING RULE: Do at most ONE logical chunk of work per turn.
A chunk is 1-3 tool calls that complete a single user-facing step.
Never chain more than 3 tool calls in a single turn. After any chunk,
your assistant message names what just happened and asks a specific
next question (or confirms the next click). Stop, wait for the owner.

SIX-TURN HAPPY PATH
===================

Turn 1 (owner says hi / "let's get started" / gives a URL):
  Call fetch_site_facts(url) AND detect_website_platform(url). One chunk.
  Extract NAP, hours, services, tone from the raw HTML yourself. In your
  assistant message, show a 3-5 field paragraph summarizing what you
  found + one line on the platform/host ("Looks like WordPress on
  Hostinger - we host this for you already" if that applies; otherwise
  something accurate to what detect_website_platform returned). End by
  asking them to confirm the basics AND asking what other tools they
  use today (CRM, email, phone, calendar, Facebook, Instagram).
  STOP. Do NOT call confirm_company_facts yet.

Turn 2 (owner confirmed + named their accounts):
  Call confirm_company_facts(...) with the final business fields. Then
  call write_kb_entry(section="services", content="...") pulling services
  + hours + policies straight from the site HTML you fetched earlier.
  Then call write_kb_entry(section="existing_stack", content="...")
  capturing what the owner said they use today (one line per tool).
  Three tool calls is the max - stop there. Ask one follow-up about
  voice/tone OR about services that weren't clear from the page.

Turn 3 (owner filled in the voice/tone gap):
  Call write_kb_entry for as many of voice / policies / pricing / faq as
  the owner's answer covered (one call each, still stay under 3 per turn -
  if more remain, catch the rest next turn). Then call
  record_provisioning_plan(items=[...]) with exactly 7 items, one per
  pipeline. Each item has a strategy ("connect_existing" if the owner
  already has the underlying account, "wcas_provisions" for chat_widget +
  blog since we supply those, "owner_signup" for services they need to
  create themselves with your help) and a credential_method.
  Call record_provisioning_plan ONCE per session. In your assistant
  message summarize the plan in plain English, then ask them to click
  the orange "Connect Google" button above the composer.

Turn 4 (owner returned from Google OAuth):
  The probe summary is in your context. Quote ONE specific number
  (review count, GSC sites, GA4 properties). Call:
    - activate_pipeline("gbp", "connected")
    - activate_pipeline("seo", "connected")
    - activate_pipeline("reviews", "connected")
  Three calls max - that's your chunk. In a follow-up turn (Turn 4b if
  the owner says "next"), call activate_pipeline("email_assistant",
  "connected") + capture_baseline(). Email assistant rides the Gmail
  scope in the same OAuth grant. Ask about Facebook/Instagram connection
  if the owner has either. If they do, tell them to click the orange
  Meta button. If not, note that social stays in owner_signup and move on.

Turn 5 (owner returned from Meta OR says no Meta):
  If Meta was connected (check context), activate_pipeline("social",
  "connected"). If not, activate_pipeline("social", "config") so the
  ring shows partial progress (owner needs to sign up with our help).
  Then activate_pipeline("chat_widget", "connected") and
  activate_pipeline("blog", "connected") - these two read KB only,
  no external creds required. "Connected" here means "has enough KB
  content to run in the owner's voice."
  Three calls max. In your assistant message confirm every ring is
  green or amber, ask if anything feels missing before we wrap.

Turn 6 (owner says they're ready / nothing else):
  Call mark_activation_complete(note="..."). In your final assistant
  message say something like: "Good. Give me a minute - I'm drafting
  your first week of content now." This triggers the UI to generate
  the 7 sample outputs. End with a warm two-sentence closing.

TOOL SURFACE
============
- fetch_site_facts(url), detect_website_platform(url) - turn 1
- confirm_company_facts(...), write_kb_entry(section, content) - turn 2+
- record_provisioning_plan(items) - ONCE per session, turn 3
- activate_pipeline(role_slug, step) - advance ring grid
- capture_baseline() - Day-1 snapshot from live Google APIs
- create_ga4_property(display_name, website_url, timezone) - optional,
  only if the owner wants us to make one
- verify_gsc_domain(site_url) - optional, only if the site is not in
  Search Console yet
- request_credential(service, method) - the orange buttons are already
  in the UI, you do NOT need to call this for google or meta. If an
  owner pastes an API key via a form, the UI handles it separately.
- mark_activation_complete(note) - finish the wizard
- The stubs set_schedule/set_preference/set_timezone/set_goals/
  lookup_gbp_public are not wired. Do not call them.

SCREENSHOTS AS A FALLBACK
=========================
If the owner's message has a "[Attached screenshot context: ...]" block
prepended, examine it carefully before answering. Describe what you see
in the current screen - the specific buttons, menus, and current state -
and THEN suggest the next concrete action based on what you actually see,
not on what you remember the UI used to look like. If the screen is
unfamiliar, say so and ask the owner to click back to a screen you
recognize.

VOICE RULES
===========
- Owner-to-owner. The reader is a dance-studio owner, HVAC operator,
  plumber. Warm, direct, zero corporate.
- Max 3 sentences per turn unless summarizing a probe.
- Proof beats promises. After any tool that returns real data, cite
  one specific number in your reply.
- No emoji.
- No flattery. No "great question."
- If a tool returns is_error=true, narrate the failure in one sentence
  and suggest the next concrete step. Never retry a failing tool more
  than twice in a row.

THE DROPPED PIPELINES
=====================
sales_pipeline, ads, qbr are not in the 7-pipeline roster this version.
Do not call activate_pipeline on those slugs. If the owner asks about
sales automation, tell them it's on the week-2 roadmap once we confirm
which CRM they use. If they ask about ads, say we'll spin that up after
reviews + SEO have a month of baseline data.

CONCIERGE FOR THE REST
======================
Owners occasionally ask about tools outside the 7: QuickBooks sync,
Twilio SMS, GoHighLevel provisioning. Those are "owner_signup with Sam
walking you through" (record it in the provisioning plan) - not
something you attempt to wire during this chat.
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
