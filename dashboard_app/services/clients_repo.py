"""
Airtable Clients-table adapter.

The dashboard is a READER of the CRM. The n8n Client Onboarding
workflow is the WRITER (creates the row, provisions the tenant dir,
sends the welcome email). Once we're verifying magic links we also
write-back fields to the same row:

    Magic Link Hash SHA-256 of the outstanding token
    Magic Link Expires ISO-8601 expiry (UTC)
    Magic Link Consumed checkbox; set True on redeem
    Onboarding Approved gate: Sam flips this on before the client
                          can run the activation wizard
    Onboarding Completed At timestamp set when mark_activation_complete
                          fires. Blocks re-entry into the wizard.
    TOS Version Accepted "1.0", "1.1", etc. - the version of the terms
                          the client actually clicked through
    TOS Accepted At ISO-8601 timestamp of that click
    TOS Accepted IP request IP at the time of the click (audit trail)
    TOS Accepted UA truncated user-agent string

All reads filter by exact email match. Field names are intentionally
human-readable so Sam can audit them in the Airtable UI.
"""

import os
from datetime import datetime, timezone
from typing import Any

try:
    from pyairtable import Api
except ImportError: # pragma: no cover - missing dep is caught at startup
    Api = None # type: ignore


_FLD_EMAIL = "Email"
_FLD_TENANT_ID = "Tenant ID"
_FLD_STATUS = "Status"
_FLD_MAGIC_HASH = "Magic Link Hash"
_FLD_MAGIC_EXPIRES = "Magic Link Expires"
_FLD_MAGIC_CONSUMED = "Magic Link Consumed"
_FLD_ROLE = "Role" # optional; admin clients are also marked via ADMIN_EMAILS env

# §0 onboarding gate
_FLD_ONBOARDING_APPROVED = "Onboarding Approved"
_FLD_ONBOARDING_COMPLETED_AT = "Onboarding Completed At"

# §0.5 legal basics
_FLD_TOS_VERSION = "TOS Version Accepted"
_FLD_TOS_ACCEPTED_AT = "TOS Accepted At"
_FLD_TOS_ACCEPTED_IP = "TOS Accepted IP"
_FLD_TOS_ACCEPTED_UA = "TOS Accepted UA"

# Current TOS version. Bumping this invalidates old acceptances; the /activate
# route will redirect any tenant whose recorded version != CURRENT_TOS_VERSION
# back to the terms page.
CURRENT_TOS_VERSION = "1.0"


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


# ---------------------------------------------------------------------------
# §0 Onboarding approval gate
# ---------------------------------------------------------------------------


def is_onboarding_approved(record: dict[str, Any]) -> bool:
    """Sam flips this on once the client is vetted + paid. Default False."""
    return bool(record.get("fields", {}).get(_FLD_ONBOARDING_APPROVED, False))


def onboarding_completed_at(record: dict[str, Any]) -> str | None:
    """Non-empty when the wizard has already finished for this tenant."""
    val = record.get("fields", {}).get(_FLD_ONBOARDING_COMPLETED_AT)
    return val if isinstance(val, str) and val.strip() else None


def find_by_tenant_id(tenant_id: str) -> dict[str, Any] | None:
    """Look up a Clients row by Tenant ID (the stable slug)."""
    safe = str(tenant_id).replace("'", "\\'")
    formula = f"{{{_FLD_TENANT_ID}}} = '{safe}'"
    records = _table().all(formula=formula, max_records=1)
    return records[0] if records else None


def is_onboarding_approved_by_tenant(tenant_id: str) -> bool:
    """Tenant-id variant used by the activation dispatch gate. Admin-role
    tenants bypass (Sam can always self-test). Fails closed: any error
    looking up the row returns False so provisioning is blocked.

    DISABLE_ONBOARDING_APPROVAL_GATE=1 bypasses the gate for tests +
    local dev where Airtable isn't wired. MUST NOT be set in production
    (the dashboard refuses to bypass when COOKIE_DOMAIN indicates prod).
    """
    if os.getenv("DISABLE_ONBOARDING_APPROVAL_GATE") == "1":
        # Production safety: only respect the bypass if we're not on the
        # public prod domain. If someone sets this in prod by accident,
        # the gate stays closed.
        domain = (os.getenv("COOKIE_DOMAIN") or "").lower()
        if "westcoastautomationsolutions.com" not in domain:
            return True
    try:
        record = find_by_tenant_id(tenant_id)
    except RuntimeError:
        return False
    if record is None:
        return False
    # Admin bypass: a tenant whose email is in ADMIN_EMAILS can always run tools.
    email = extract_email(record)
    admins = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
    if email and email.lower() in admins:
        return True
    return is_onboarding_approved(record)


def mark_onboarding_completed(record_id: str, at: str | None = None) -> None:
    """Stamp the row as complete. Called by mark_activation_complete."""
    when = at or datetime.now(timezone.utc).isoformat()
    _table().update(record_id, {_FLD_ONBOARDING_COMPLETED_AT: when})


# ---------------------------------------------------------------------------
# §0.5 Terms of service + privacy acceptance
# ---------------------------------------------------------------------------


def has_accepted_tos_version(record: dict[str, Any], version: str | None = None) -> bool:
    """True when the client has clicked through the named TOS version.
    Default: the current version as defined at module top."""
    target = version or CURRENT_TOS_VERSION
    accepted = record.get("fields", {}).get(_FLD_TOS_VERSION)
    return isinstance(accepted, str) and accepted.strip() == target


def record_tos_acceptance(
    record_id: str,
    *,
    version: str | None = None,
    ip: str = "",
    user_agent: str = "",
) -> None:
    """Persist a TOS acceptance click. Overwrites prior acceptance (bumping
    version counts as a new consent round). user_agent is truncated to 400
    chars to stay well under Airtable's single-line-text limit."""
    now = datetime.now(timezone.utc).isoformat()
    _table().update(
        record_id,
        {
            _FLD_TOS_VERSION: version or CURRENT_TOS_VERSION,
            _FLD_TOS_ACCEPTED_AT: now,
            _FLD_TOS_ACCEPTED_IP: (ip or "")[:64],
            _FLD_TOS_ACCEPTED_UA: (user_agent or "")[:400],
        },
    )
