"""
Claude API cost-tracking middleware.

Every direct Messages-API or Managed-Agents call wraps through record_call()
which appends a single JSONL line per call:

    {"ts": "...", "tenant_id": "...", "model": "...",
     "input_tokens": 0, "output_tokens": 0, "usd": 0.0, "kind": "message|agent"}

Two enforcement gates:

  - DAILY_DEV_CAP      ($/day ACROSS all tenants, default $20) - dev-time guardrail
  - DAILY_TENANT_CAP   ($/day PER tenant, default $2)          - per-client kill switch

When a cap is exceeded, should_allow() returns False. Callers are expected to
skip the call and surface a calm "budget reached today" message; nothing crashes.

Pricing table is approximate and intentionally editable; revisit when
Anthropic posts final Opus 4.7 prices.
"""

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .scrubber import scrub

log = logging.getLogger("dashboard.cost")

# Per-million-token USD. Update from Anthropic pricing page.
_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-7": (15.0, 75.0),
    "claude-opus-4-6": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}

_LOG_PATH = Path(os.getenv("COST_LOG_PATH", "/opt/wc-solns/_platform/cost_log.jsonl"))
_LOCK = threading.Lock()


def _log_path() -> Path:
    # Allow overriding at call time for tests.
    return Path(os.getenv("COST_LOG_PATH", str(_LOG_PATH)))


def estimate_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price = _PRICING.get(model)
    if price is None:
        # Unknown model: assume Sonnet-tier so we don't silently under-count.
        price = _PRICING["claude-sonnet-4-6"]
    in_rate, out_rate = price
    return round((input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate, 6)


def record_call(
    tenant_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    kind: str = "message",
    note: str | None = None,
) -> float:
    """Append to cost log. Returns the USD estimate for the call."""
    usd = estimate_usd(model, input_tokens, output_tokens)
    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tenant_id": tenant_id or "_unknown",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "usd": usd,
        "kind": kind,
    }
    if note:
        entry["note"] = scrub(note)[:240]

    path = _log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
    except OSError:
        # Logging must never block a paying customer's call.
        log.exception("cost log write failed path=%s", path)
    return usd


def _sum_today(predicate) -> float:
    path = _log_path()
    if not path.exists():
        return 0.0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total = 0.0
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = row.get("ts", "")
                if not ts.startswith(today):
                    continue
                if predicate(row):
                    total += float(row.get("usd") or 0)
    except OSError:
        return total
    return total


def dev_spend_today() -> float:
    return _sum_today(lambda _row: True)


def tenant_spend_today(tenant_id: str) -> float:
    return _sum_today(lambda row: row.get("tenant_id") == tenant_id)


def should_allow(tenant_id: str) -> tuple[bool, str | None]:
    """Return (allowed, reason_if_blocked)."""
    try:
        dev_cap = float(os.getenv("DAILY_DEV_CAP", "20"))
    except ValueError:
        dev_cap = 20.0
    try:
        tenant_cap = float(os.getenv("DAILY_TENANT_CAP", "2.00"))
    except ValueError:
        tenant_cap = 2.0

    if dev_spend_today() >= dev_cap:
        return False, f"Daily platform cap reached (${dev_cap:.2f})"
    if tenant_id and tenant_spend_today(tenant_id) >= tenant_cap:
        return False, f"Daily tenant cap reached (${tenant_cap:.2f})"
    return True, None
