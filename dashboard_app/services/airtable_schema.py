"""
Per-tenant Airtable schema reader.

The Activation Orchestrator calls this through the fetch_airtable_schema
tool to introspect a tenant's CRM/booking base. It returns table names,
field types, row counts, and a small set of recent sample rows that
have been PII-scrubbed before they reach the agent.

Per-tenant base whitelist:
    /opt/wc-solns/<tenant_id>/tenant_config.json
    {
      "airtable_bookings": {
        "base_id": "appXXX",
        "table_name": "Bookings"   # optional; if missing we list all tables
      }
    }

A base_id NOT in the tenant's whitelist is rejected. Agents cannot
enumerate arbitrary bases; they can only read what the operator
pre-registered for that tenant.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import heartbeat_store, scrubber

try:
    from pyairtable import Api
except ImportError:  # pragma: no cover
    Api = None  # type: ignore

log = logging.getLogger("dashboard.airtable_schema")


class AirtableSchemaError(RuntimeError):
    """Raised on whitelist miss, missing PAT, or pyairtable failure."""


def _config_path(tenant_id: str) -> Path:
    return heartbeat_store.tenant_root(tenant_id) / "tenant_config.json"


def load_tenant_config(tenant_id: str) -> dict[str, Any]:
    """Return the tenant_config.json contents (empty dict on miss)."""
    try:
        path = _config_path(tenant_id)
    except heartbeat_store.HeartbeatError:
        return {}
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def whitelisted_base_id(tenant_id: str, kind: str = "airtable_bookings") -> str | None:
    """The base_id this tenant is allowed to read for the given kind, or None."""
    cfg = load_tenant_config(tenant_id)
    entry = cfg.get(kind) or {}
    base_id = entry.get("base_id")
    return base_id if isinstance(base_id, str) and base_id.startswith("app") else None


def _api() -> "Api":
    if Api is None:
        raise AirtableSchemaError("pyairtable not installed")
    pat = os.getenv("AIRTABLE_PAT", "")
    if not pat:
        raise AirtableSchemaError("AIRTABLE_PAT not set")
    return Api(pat)


def _scrub_record_value(value: Any) -> Any:
    """Run the PII scrubber over string-shaped values. Numbers + bools pass through.
    Lists of strings get scrubbed elementwise."""
    if isinstance(value, str):
        return scrubber.scrub(value)
    if isinstance(value, list):
        return [_scrub_record_value(v) for v in value]
    return value


def fetch_schema(tenant_id: str, base_id: str) -> dict[str, Any]:
    """Read the schema + sample rows of every table in a whitelisted base.

    Returns:
        {
          "base_id": "appXXX",
          "fetched_at": "...",
          "tables": [
            {
              "name": "Bookings",
              "fields": [{"name": "Student Name", "type": "singleLineText"}, ...],
              "row_count": 47,
              "sample_recent_records": [{"id": "rec...", "fields": {...}}, ...]   # max 5, scrubbed
            },
            ...
          ]
        }

    Raises AirtableSchemaError on whitelist miss or API failure.
    """
    allowed = whitelisted_base_id(tenant_id, "airtable_bookings")
    if allowed is None:
        raise AirtableSchemaError(
            f"no whitelisted Airtable base configured for tenant {tenant_id!r}"
        )
    if base_id != allowed:
        raise AirtableSchemaError(
            f"base_id {base_id!r} not in whitelist for tenant {tenant_id!r}"
        )

    api = _api()
    try:
        base = api.base(base_id)
        schema = base.schema()
    except Exception as exc:  # pyairtable raises a few different shapes
        log.exception("airtable schema fetch failed tenant=%s base=%s", tenant_id, base_id)
        raise AirtableSchemaError(f"schema fetch failed: {exc}") from exc

    tables_out: list[dict[str, Any]] = []
    for tbl in getattr(schema, "tables", []) or []:
        name = getattr(tbl, "name", "") or ""
        if not name:
            continue
        fields = []
        for fld in getattr(tbl, "fields", []) or []:
            fields.append({
                "name": getattr(fld, "name", "") or "",
                "type": getattr(fld, "type", "") or "",
            })

        # Sample records (cap 30) so the agent has enough visibility to
        # identify date-based segments (active vs lapsed). Without this,
        # 5 random rows is not enough to reason about the population.
        sample_records: list[dict[str, Any]] = []
        try:
            t = api.table(base_id, name)
            raw = t.all(max_records=30)
            for rec in raw:
                fields_map = rec.get("fields") or {}
                scrubbed = {k: _scrub_record_value(v) for k, v in fields_map.items()}
                sample_records.append({
                    "id": rec.get("id", ""),
                    "fields": scrubbed,
                })
        except Exception as exc:  # individual table failure shouldn't kill the whole call
            log.warning("sample fetch failed tenant=%s table=%s: %s", tenant_id, name, exc)

        # row_count: pyairtable doesn't expose this directly without listing, so
        # we approximate by paging once with a small page_size. For Garcia's
        # bookings (target ~30-50 rows post-seed) this is cheap.
        row_count = 0
        try:
            t = api.table(base_id, name)
            row_count = sum(1 for _ in t.iterate(page_size=100))
        except Exception as exc:
            log.warning("row count failed tenant=%s table=%s: %s", tenant_id, name, exc)

        tables_out.append({
            "name": name,
            "fields": fields,
            "row_count": row_count,
            "sample_recent_records": sample_records,
        })

    return {
        "base_id": base_id,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "tables": tables_out,
    }
