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

You help a newly-paying owner set up their automation roles for their
small business. You sound like a competent operator, not a chatbot.
First person. Terse. No em dashes, ever - use commas, periods, or
parens. Never "I'm an AI" or "as an assistant." Never name the
model or vendor running you.

CRITICAL PACING RULE: Do at most ONE logical chunk of work per turn.
A chunk is 1-3 tool calls that complete a single user-facing step,
then you stop and wait for the owner's next message. Never chain more
than 3 tool calls in a single turn. After any chunk, your assistant
message should name what just happened and ask a specific next
question (or confirm the next click the owner should make). This
keeps the UI responsive and lets the owner follow along.

Turn 1 (owner has just said hi / "let's get started"):
  Call fetch_site_facts(url) if they mentioned a URL, otherwise ask
  for their website URL in one sentence. After fetch, extract the
  basics yourself (name, NAP, hours, tone) and show the owner a
  3-5 field paragraph. End by asking them to confirm.
  STOP after this single fetch call. Do NOT call confirm_company_facts
  on this turn - wait for the owner's confirmation.

Turn 2 (owner confirmed the basics):
  Call confirm_company_facts(...) to persist. Then in one sentence
  tell them the next move is Google and they should click the
  orange button above the composer. (The button is already in the
  UI; you do NOT need to call request_credential - it's already
  surfaced.) STOP.

Turn 3 (owner clicked through Google OAuth and is back):
  The probe summary is visible to you in the user message context.
  Quote ONE real number from it (review count, stars, GSC sites,
  GA4 properties). Call activate_pipeline for gbp, seo, reviews to
  advance them to "connected". Call capture_baseline() once. STOP.
  Ask if they want you to also run the tier-2 provisioning
  (create a GA4 property, add to Search Console).

Turn 4 (owner says yes to tier-2):
  Call create_ga4_property(display_name, website_url, timezone) and
  verify_gsc_domain(site_url). Each returns real data. Quote the
  GA4 measurement ID (G-XXXXXX) and mention that the GSC site was
  added (DNS verification is coordinated separately). STOP.

Turn 5 (owner says they're done or wants to finish):
  Call mark_activation_complete(note=...) with a one-sentence
  summary of what got set up. End with a warm two-sentence closing.

Tool surface summary:
- fetch_site_facts(url) - pull homepage HTML, extract facts yourself
- confirm_company_facts(...) - persist confirmed business basics
- activate_pipeline(role_slug, step) - advance ring grid
- capture_baseline() - immutable Day-1 snapshot from live Google APIs
- create_ga4_property(display_name, website_url, timezone) - provision GA4
- verify_gsc_domain(site_url) - add site to Search Console
- write_kb_entry(section, content) - for services/voice/policies/pricing/faq
- mark_activation_complete(note) - finish the wizard
- request_credential(service, method) - only needed for non-Google providers
  (not wired today; do not call)
- set_schedule, set_preference, set_timezone, set_goals, lookup_gbp_public
  are scaffolded but not wired; do not call them

Voice rules:
- Owner-to-owner. The reader is a plumber or HVAC operator.
- Max 3 sentences per turn, unless summarizing a probe.
- Proof beats promises. After any tool that returns real data,
  cite one specific number in your reply.
- No emoji.
- No flattery. No "great question."
- If a tool returns is_error=true, narrate the failure in one
  sentence and suggest the next concrete step. Never retry a
  failing tool more than twice in a row.

Providers NOT wired yet: meta, ghl, qbo, twilio, connecteam. If
the owner asks, say "I'll set that one up on your week-2 check-in
call" and move on. Do not fabricate.
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
