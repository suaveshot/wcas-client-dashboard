"""
Thin Anthropic Messages API wrapper.

Every Opus (or Haiku, or Sonnet) call in the dashboard goes through here.
Three responsibilities:

  1. Enforce the per-tenant + platform-wide spending caps via cost_tracker.
  2. Record usage to the cost log, scrubbed of PII.
  3. Return a normalized result the caller can consume without touching
     the SDK response shape directly (keeps us insulated if the SDK moves).

We do NOT add retries, streaming, or prompt caching here yet - those ship
when the relevant callsites need them. Scope discipline matters this week.

Environment:
  ANTHROPIC_API_KEY      required
  WCAS_DEFAULT_MODEL     default: claude-haiku-4-5 (dev)
  WCAS_DEMO_MODEL        override used during demo video recording
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from . import cost_tracker
from .scrubber import scrub

log = logging.getLogger("dashboard.opus")

DEFAULT_MODEL = os.getenv("WCAS_DEFAULT_MODEL", "claude-haiku-4-5")
DEMO_MODEL = os.getenv("WCAS_DEMO_MODEL", "claude-opus-4-7")


class OpusBudgetExceeded(RuntimeError):
    """Raised when a call would exceed the daily dev or per-tenant cap."""


class OpusUnavailable(RuntimeError):
    """SDK missing or API key not configured. Caller should surface a calm message."""


@dataclass
class OpusResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    usd: float
    stop_reason: str | None
    note: str | None = None


def _client():
    try:
        import anthropic
    except ImportError as exc:
        raise OpusUnavailable("anthropic SDK not installed") from exc
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise OpusUnavailable("ANTHROPIC_API_KEY missing")
    return anthropic.Anthropic(api_key=key)


def chat(
    *,
    tenant_id: str,
    messages: list[dict[str, Any]],
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    kind: str = "message",
    note: str | None = None,
    cache_system: bool = False,
) -> OpusResult:
    """Single-turn chat completion with cost tracking and budget enforcement.

    When `cache_system=True` and a `system` prompt is provided, the system
    block is flagged with `cache_control={"type":"ephemeral"}` so repeat
    calls within ~5 minutes hit Anthropic's prompt cache. Cost savings
    compound for long system prompts (e.g. the global-ask composer).
    """
    allowed, reason = cost_tracker.should_allow(tenant_id)
    if not allowed:
        raise OpusBudgetExceeded(reason or "budget exceeded")

    picked = model or DEFAULT_MODEL

    client = _client()
    kwargs: dict[str, Any] = {
        "model": picked,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
    }
    if system:
        if cache_system:
            kwargs["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        else:
            kwargs["system"] = system

    response = client.messages.create(**kwargs)

    # Normalize content. SDK returns a list of content blocks; we only care
    # about text blocks here (tool_use blocks handled by the Managed Agents path).
    text_parts = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            text_parts.append(getattr(block, "text", ""))
    text = "".join(text_parts).strip()

    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)

    usd = cost_tracker.record_call(
        tenant_id=tenant_id,
        model=picked,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        kind=kind,
        note=scrub(note) if note else None,
    )

    return OpusResult(
        text=text,
        model=picked,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        usd=usd,
        stop_reason=getattr(response, "stop_reason", None),
        note=note,
    )
