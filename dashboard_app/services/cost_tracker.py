"""
Claude API cost-tracking middleware.

Every direct Messages-API or Managed-Agents call wraps through record_call()
which appends a single JSONL line per call:

    {"ts": "...", "tenant_id": "...", "model": "...",
     "input_tokens": 0, "output_tokens": 0, "usd": 0.0, "kind": "message|agent",
     "vendor": "anthropic"}

Two enforcement gates:

  - DAILY_DEV_CAP      ($/day ACROSS all tenants, default $20) - dev-time guardrail
  - DAILY_TENANT_CAP   ($/day PER tenant, default $2)          - per-client kill switch

When a cap is exceeded, should_allow() returns False. Callers are expected to
skip the call and surface a calm "budget reached today" message; nothing crashes.

Pricing table is approximate and intentionally editable; revisit when
Anthropic posts final Opus 4.7 prices.

Vendors other than Anthropic (BrightLocal, etc.) record via
record_call_for_vendor() so cost rollup spans every paid API.

Log rotation: when cost_log.jsonl crosses COST_LOG_MAX_BYTES (default 5 MiB)
it is rotated to cost_log.YYYYMMDD-HHMMSS.jsonl in the same directory and a
fresh empty active file is started. _sum_today() reads both the active file
and any rotated file whose date prefix matches today's UTC date so spend
counts stay accurate across rotations.
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

# Default rotation threshold: 5 MiB. Override with COST_LOG_MAX_BYTES.
_DEFAULT_MAX_BYTES = 5_242_880


def _log_path() -> Path:
    # Allow overriding at call time for tests.
    return Path(os.getenv("COST_LOG_PATH", str(_LOG_PATH)))


def _max_bytes() -> int:
    raw = os.getenv("COST_LOG_MAX_BYTES")
    if not raw:
        return _DEFAULT_MAX_BYTES
    try:
        val = int(raw)
        return val if val > 0 else _DEFAULT_MAX_BYTES
    except ValueError:
        return _DEFAULT_MAX_BYTES


def estimate_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price = _PRICING.get(model)
    if price is None:
        # Unknown model: assume Sonnet-tier so we don't silently under-count.
        price = _PRICING["claude-sonnet-4-6"]
    in_rate, out_rate = price
    return round((input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate, 6)


def _rotated_name_for(now: datetime) -> str:
    return f"cost_log.{now.strftime('%Y%m%d-%H%M%S')}.jsonl"


def _maybe_rotate_locked(path: Path) -> None:
    """Rotate the cost log if it exceeds the configured size threshold.

    Caller MUST hold _LOCK. If the file does not exist or is below the
    threshold, this is a no-op. Atomic via os.replace.
    """
    try:
        if not path.exists():
            return
        size = path.stat().st_size
    except OSError:
        return
    if size < _max_bytes():
        return
    target = path.parent / _rotated_name_for(datetime.now(timezone.utc))
    # If a rotated file with this exact second-stamp already exists (very
    # unlikely; only when called twice in the same second), append a suffix.
    if target.exists():
        suffix = 1
        while True:
            candidate = path.parent / f"{target.stem}-{suffix}.jsonl"
            if not candidate.exists():
                target = candidate
                break
            suffix += 1
    try:
        os.replace(path, target)
    except OSError:
        log.exception("cost log rotation failed path=%s", path)


def list_log_files_for_day(date_iso: str) -> list[Path]:
    """Return all cost log files whose records could include the given UTC date.

    `date_iso` must be YYYY-MM-DD. Includes the active cost_log.jsonl (always,
    since it may hold records for today) plus any rotated
    cost_log.YYYYMMDD-HHMMSS.jsonl whose date prefix matches.
    """
    active = _log_path()
    out: list[Path] = []
    parent = active.parent
    if not parent.exists():
        return [active] if active.exists() else []
    target_compact = date_iso.replace("-", "")
    for child in parent.iterdir():
        if not child.is_file():
            continue
        name = child.name
        if name == active.name:
            continue
        if not name.startswith("cost_log.") or not name.endswith(".jsonl"):
            continue
        # Expected: cost_log.YYYYMMDD-HHMMSS[-N].jsonl
        stamp = name[len("cost_log.") : -len(".jsonl")]
        date_part = stamp.split("-", 1)[0]
        if date_part == target_compact:
            out.append(child)
    if active.exists():
        out.append(active)
    return out


def record_call(
    tenant_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    kind: str = "message",
    note: str | None = None,
    vendor: str = "anthropic",
) -> float:
    """Append to cost log. Returns the USD estimate for the call.

    The `vendor` field is additive and defaults to "anthropic" for
    backwards compatibility with existing callers.
    """
    usd = estimate_usd(model, input_tokens, output_tokens)
    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tenant_id": tenant_id or "_unknown",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "usd": usd,
        "kind": kind,
        "vendor": vendor,
    }
    if note:
        entry["note"] = scrub(note)[:240]

    path = _log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            _maybe_rotate_locked(path)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
    except OSError:
        # Logging must never block a paying customer's call.
        log.exception("cost log write failed path=%s", path)
    return usd


def record_call_for_vendor(
    vendor: str,
    *,
    tenant_id: str,
    kind: str,
    usd: float,
    note: str | None = None,
) -> float:
    """Record a non-Anthropic vendor call (BrightLocal, etc.).

    Token counts are omitted (they have no meaning for these vendors).
    The `model` field is set to the vendor name so downstream rollups stay
    consistent. Returns the recorded USD value.
    """
    try:
        usd_val = float(usd)
    except (TypeError, ValueError):
        usd_val = 0.0
    usd_val = round(usd_val, 6)
    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tenant_id": tenant_id or "_unknown",
        "model": vendor,
        "usd": usd_val,
        "kind": kind,
        "vendor": vendor,
    }
    if note:
        entry["note"] = scrub(note)[:240]

    path = _log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            _maybe_rotate_locked(path)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
    except OSError:
        log.exception("cost log write failed path=%s", path)
    return usd_val


def _sum_today(predicate) -> float:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    paths = list_log_files_for_day(today)
    if not paths:
        return 0.0
    total = 0.0
    for path in paths:
        if not path.exists():
            continue
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
            continue
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
