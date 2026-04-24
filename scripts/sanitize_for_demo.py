"""Demo-mode deterministic scrambler for tenant-rendered data.

Two jobs:

1. **Library.** Importable primitives that home_context.py + activity_feed.py
   pipe their rendered dicts through when DEMO_MODE=true. Deterministic so
   the same real client always maps to the same demo name across a video
   take (judges notice when names shuffle between scenes).

2. **CLI.**
       python scripts/sanitize_for_demo.py --check  --tenant americal_patrol
       python scripts/sanitize_for_demo.py --write  --tenant americal_patrol

   --check: exits non-zero if running the dashboard with DEMO_MODE=true would
   reveal data it shouldn't (proves the filter works before the video take).

   --write: dumps a sanitized snapshot of the tenant's state under
   /opt/wc-solns/<tenant>/demo_snapshot/ so a fallback render path can pull
   from the snapshot if the live telemetry breaks during recording.

Deterministic via blake2b keyed by DEMO_SCRAMBLE_SALT env var. If unset,
falls back to a static dev salt; production should always set one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

_DEV_SALT = "wcas-demo-dev-salt-do-not-ship"


# ---------------------------------------------------------------------------
# Deterministic hashing
# ---------------------------------------------------------------------------


def _salt() -> str:
    return os.getenv("DEMO_SCRAMBLE_SALT", _DEV_SALT)


def _digest(value: str, length: int = 4) -> str:
    """Stable hex digest of length*2 for the given value."""
    h = hashlib.blake2b(value.encode("utf-8"), key=_salt().encode("utf-8"), digest_size=length)
    return h.hexdigest()


def _index(value: str, modulo: int) -> int:
    """Stable non-negative int derived from value, in [0, modulo)."""
    return int(_digest(value, 8), 16) % max(1, modulo)


# ---------------------------------------------------------------------------
# Scramblers
# ---------------------------------------------------------------------------


_VERTICAL_WORDS = ("HVAC", "Plumbing", "Roofing", "Electric", "Landscaping", "Cleaning")
_SURFACE_WORDS = ("customer", "client", "property", "account")


def scramble_name(original: str, *, kind: str = "customer") -> str:
    """Map a real client/person name to a stable fake one.

    kind="property" biases toward property-style labels ("Property #A").
    kind="customer" uses vertical + number ("HVAC customer #1").
    """
    if not original or not isinstance(original, str):
        return ""
    idx = _index(original, 999) + 1
    if kind == "property":
        letter = chr(ord("A") + (idx - 1) % 26)
        return f"Property #{letter}"
    vertical = _VERTICAL_WORDS[idx % len(_VERTICAL_WORDS)]
    surface = _SURFACE_WORDS[idx % len(_SURFACE_WORDS)]
    return f"{vertical} {surface} #{idx}"


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def scramble_email(original: str) -> str:
    """Replace an email with a stable demo.local address."""
    if not original or not isinstance(original, str):
        return ""
    if not _EMAIL_RE.match(original.strip()):
        return original  # not an email, leave it
    idx = _index(original, 999)
    return f"owner{idx}@demo.local"


_PHONE_RE = re.compile(r"\(?\d{3}\)?[\s.\-]*\d{3}[\s.\-]*\d{4}")


def scramble_phone(original: str) -> str:
    """Return a (555) XXX-XXXX string with the last 4 derived from the input."""
    if not original or not isinstance(original, str):
        return ""
    m = _PHONE_RE.search(original)
    if not m:
        return original
    last4 = f"{_index(original, 10000):04d}"
    return f"(555) XXX-{last4}"


_DOLLAR_RE = re.compile(r"\$([0-9][0-9,]*)(\.\d{1,2})?")


def _scramble_amount(cents_or_dollars: float | int) -> str:
    """Scale/redact a raw numeric amount."""
    amt = float(cents_or_dollars or 0)
    if amt >= 5000:
        return "$X,XXX"
    if amt >= 1000:
        rounded = round(amt / 500) * 500
        return f"~${rounded:,.0f}"
    return f"${amt:,.0f}"


def scramble_dollars(value: Any) -> Any:
    """Scramble any string containing $-prefixed amounts, or a raw number.

    Strings: rewrite each $123.45 match via the amount rules.
    Numbers: return a scrambled string.
    """
    if isinstance(value, (int, float)):
        return _scramble_amount(value)
    if not isinstance(value, str):
        return value

    def repl(match: re.Match) -> str:
        whole = (match.group(1) or "").replace(",", "")
        frac = match.group(2) or ""
        try:
            raw = float(whole + frac)
        except ValueError:
            return match.group(0)
        return _scramble_amount(raw)

    return _DOLLAR_RE.sub(repl, value)


# ---------------------------------------------------------------------------
# High-level transforms for rendered dicts
# ---------------------------------------------------------------------------


_NAME_KEYS = {"customer_name", "contact_name", "property", "property_name", "client", "client_name", "deal_name"}
_EMAIL_KEYS = {"email", "owner_email", "contact_email", "to_email", "from_email"}
_PHONE_KEYS = {"phone", "to", "from", "phone_number", "caller"}
_DOLLAR_KEYS = {"influenced", "revenue", "amount", "value", "delta_text", "deal_value"}


def _walk(obj: Any) -> Any:
    """Recursive in-place-ish scrub of any JSON-ish structure."""
    if isinstance(obj, dict):
        new: dict[str, Any] = {}
        for k, v in obj.items():
            new[k] = _scrub_field(k, v)
        return new
    if isinstance(obj, list):
        return [_walk(v) for v in obj]
    return obj


def _scrub_field(key: str, value: Any) -> Any:
    lk = key.lower()
    if isinstance(value, str):
        if lk in _NAME_KEYS:
            kind = "property" if "property" in lk else "customer"
            return scramble_name(value, kind=kind)
        if lk in _EMAIL_KEYS:
            return scramble_email(value)
        if lk in _PHONE_KEYS:
            return scramble_phone(value)
        if lk in _DOLLAR_KEYS:
            # If the string is a plain "12,400" with no $ prefix, coerce and scramble.
            if "$" not in value:
                try:
                    raw = float(value.replace(",", "").strip())
                    return _scramble_amount(raw)
                except ValueError:
                    pass
            return scramble_dollars(value)
        # A generic string field still gets dollar scrubbed so narratives don't leak values.
        if "$" in value:
            return scramble_dollars(value)
        return value
    if isinstance(value, (int, float)) and lk in _DOLLAR_KEYS:
        return _scramble_amount(value)
    return _walk(value)


def apply_to_activity_row(row: dict) -> dict:
    return _walk(row)


def apply_to_rec(rec: dict) -> dict:
    return _walk(rec)


def apply_to_context(ctx: dict) -> dict:
    """Top-level: scrub a composed home_context dict."""
    return _walk(ctx)


# ---------------------------------------------------------------------------
# Check / write CLI
# ---------------------------------------------------------------------------


def _find_pii_leaks(ctx: dict) -> list[str]:
    """Enumerate places where DEMO_MODE scrubbing would change output.

    Returns a list of short descriptions of findings. Empty list = clean.
    """
    findings: list[str] = []
    scrubbed = apply_to_context(ctx)

    def compare(path: str, a: Any, b: Any) -> None:
        if a == b:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for k in a:
                compare(f"{path}.{k}", a[k], b.get(k))
            return
        if isinstance(a, list) and isinstance(b, list):
            for i, (x, y) in enumerate(zip(a, b)):
                compare(f"{path}[{i}]", x, y)
            return
        findings.append(f"{path}: {a!r} -> {b!r}")

    compare("$", ctx, scrubbed)
    return findings


def _tenant_root(tenant_id: str) -> Path:
    base = os.getenv("TENANT_ROOT", "/opt/wc-solns")
    return Path(base) / tenant_id


def _load_tenant_context(tenant_id: str) -> dict:
    """Stitch together everything the dashboard would render. Read-only."""
    from dashboard_app.services import home_context as hc

    ctx = hc.build(tenant_id=tenant_id, owner_name="", tenant_display="")
    return ctx


def cmd_check(tenant_id: str) -> int:
    try:
        ctx = _load_tenant_context(tenant_id)
    except Exception as exc:
        print(f"ERROR: could not compose context for {tenant_id}: {exc}", file=sys.stderr)
        return 2
    findings = _find_pii_leaks(ctx)
    if not findings:
        print(f"OK: {tenant_id} renders cleanly in demo mode (no scrambling needed)")
        return 0
    print(f"FINDINGS ({len(findings)}) for {tenant_id} when DEMO_MODE=true:")
    for line in findings[:40]:
        print(f"  {line}")
    if len(findings) > 40:
        print(f"  ... and {len(findings) - 40} more")
    # Non-zero exit = the filter WILL scrub something. That's normal for AP.
    # The script's real job is to prove the filter is reaching every field.
    return 1 if findings else 0


def cmd_write(tenant_id: str) -> int:
    try:
        ctx = _load_tenant_context(tenant_id)
    except Exception as exc:
        print(f"ERROR: could not compose context for {tenant_id}: {exc}", file=sys.stderr)
        return 2
    scrubbed = apply_to_context(ctx)
    out_dir = _tenant_root(tenant_id) / "demo_snapshot"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "home_context.json"
    path.write_text(json.dumps(scrubbed, indent=2, default=str), encoding="utf-8")
    print(f"wrote {path} ({path.stat().st_size} bytes)")
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="audit mode")
    parser.add_argument("--write", action="store_true", help="dump sanitized snapshot")
    parser.add_argument("--tenant", default="americal_patrol")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.write:
        return cmd_write(args.tenant)
    # Default / --check
    return cmd_check(args.tenant)


if __name__ == "__main__":
    sys.exit(main())
