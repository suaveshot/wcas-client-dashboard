"""
Airtable Clients-table adapter.

The dashboard is a READER of the CRM. The n8n Client Onboarding
workflow is the WRITER (creates the row, provisions the tenant dir,
sends the welcome email). Once we're verifying magic links we also
write-back three auth fields to the same row:

    Magic Link Hash       SHA-256 of the outstanding token
    Magic Link Expires    ISO-8601 expiry (UTC)
    Magic Link Consumed   checkbox; set True on redeem

All reads filter by exact email match. Field names are intentionally
human-readable so Sam can audit them in the Airtable UI.
"""

import os
from typing import Any

try:
    from pyairtable import Api
except ImportError:  # pragma: no cover - missing dep is caught at startup
    Api = None  # type: ignore


_FLD_EMAIL = "Email"
_FLD_TENANT_ID = "Tenant ID"
_FLD_STATUS = "Status"
_FLD_MAGIC_HASH = "Magic Link Hash"
_FLD_MAGIC_EXPIRES = "Magic Link Expires"
_FLD_MAGIC_CONSUMED = "Magic Link Consumed"
_FLD_ROLE = "Role"  # optional; admin clients are also marked via ADMIN_EMAILS env


def _table():
    if Api is None:
        raise RuntimeError("pyairtable not installed")
    pat = os.getenv("AIRTABLE_PAT", "")
    base = os.getenv("AIRTABLE_BASE_ID", "")
    table_id = os.getenv("AIRTABLE_CLIENTS_TABLE_ID", "")
    if not (pat and base and table_id):
        raise RuntimeError("Airtable env vars missing (AIRTABLE_PAT / AIRTABLE_BASE_ID / AIRTABLE_CLIENTS_TABLE_ID)")
    return Api(pat).table(base, table_id)


def find_by_email(email: str) -> dict[str, Any] | None:
    """Return the first Client record whose Email matches, or None."""
    safe = email.replace("'", "\\'")
    formula = f"LOWER({{{_FLD_EMAIL}}}) = LOWER('{safe}')"
    records = _table().all(formula=formula, max_records=1)
    return records[0] if records else None


def find_by_hash(hashed_token: str) -> dict[str, Any] | None:
    """Return the first Client record whose Magic Link Hash matches."""
    safe = hashed_token.replace("'", "\\'")
    formula = f"{{{_FLD_MAGIC_HASH}}} = '{safe}'"
    records = _table().all(formula=formula, max_records=1)
    return records[0] if records else None


def stash_magic_link(record_id: str, hashed_token: str, expiry_iso: str) -> None:
    """Overwrite any outstanding magic link fields on this row."""
    _table().update(
        record_id,
        {
            _FLD_MAGIC_HASH: hashed_token,
            _FLD_MAGIC_EXPIRES: expiry_iso,
            _FLD_MAGIC_CONSUMED: False,
        },
    )


def mark_consumed(record_id: str) -> None:
    """Single-use: once redeemed, the hash is effectively dead."""
    _table().update(
        record_id,
        {
            _FLD_MAGIC_CONSUMED: True,
            _FLD_MAGIC_HASH: "",
            _FLD_MAGIC_EXPIRES: "",
        },
    )


def extract_tenant_id(record: dict[str, Any]) -> str:
    fields = record.get("fields", {})
    return fields.get(_FLD_TENANT_ID, "") or ""


def extract_email(record: dict[str, Any]) -> str:
    fields = record.get("fields", {})
    return fields.get(_FLD_EMAIL, "") or ""


def extract_role(record: dict[str, Any], email: str) -> str:
    """
    Role resolution:
    1. ADMIN_EMAILS env var allowlist takes precedence (Sam can self-promote
       without editing Airtable).
    2. Clients.Role field (if present) overrides for per-record escalation.
    3. Default: client.
    """
    admins = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
    if email.lower() in admins:
        return "admin"
    fields = record.get("fields", {})
    role = (fields.get(_FLD_ROLE) or "").lower()
    if role in ("admin", "client"):
        return role
    return "client"


def is_active(record: dict[str, Any]) -> bool:
    """Paused tenants still get a session, but the dashboard renders
    a branded paused page instead of live telemetry. Middleware reads
    this flag; auth does not block issuance."""
    status = (record.get("fields", {}).get(_FLD_STATUS) or "").lower()
    return status in ("", "active", "current")


def extract_magic_link(record: dict[str, Any]) -> tuple[str, str, bool]:
    f = record.get("fields", {})
    return (
        f.get(_FLD_MAGIC_HASH, "") or "",
        f.get(_FLD_MAGIC_EXPIRES, "") or "",
        bool(f.get(_FLD_MAGIC_CONSUMED, False)),
    )
