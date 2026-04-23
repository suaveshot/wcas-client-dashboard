"""
Live-data probe fired after a tenant finishes Google OAuth.

The probe proves the new refresh token works by pulling a small,
discovery-mode payload from each Google API we intend to use. It
answers the question 'Connected. What does this user have?' using
only the access token, without requiring the tenant to have told us
a GBP location ID, GA4 property, or GSC site URL yet.

Shape returned by probe_google():
    {
      "ok": True,        # True if at least one sub-probe succeeded
      "errors": {...},   # per-sub-probe error string when it failed
      "summary": {
        "gmail":    {"email": "owner@acme.com", "messages_total": 12345},
        "calendar": {"calendar_count": 4, "primary": "owner@acme.com"},
        "gsc":      {"site_count": 3, "first_site": "https://acme.com/"},
        "ga4":      {"account_count": 1, "property_count": 2},
        "gbp":      {"account_count": 1, "location_count": 2, "total_review_count": 312, "average_rating": 4.6}
      }
    }

Every sub-probe is isolated in a try/except so one slow or dead
vendor API cannot blank the whole summary. Timeout is hard-capped
per request.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from . import credentials, heartbeat_store

log = logging.getLogger("dashboard.probe")


_HTTP_TIMEOUT_SECONDS = 8.0
_SAFE_PROVIDER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def save_result(tenant_id: str, provider: str, result: dict[str, Any]) -> Path | None:
    """Persist a probe result so /api/activation/state can render it later."""
    if not _SAFE_PROVIDER.match(provider or ""):
        return None
    try:
        root = heartbeat_store.tenant_root(tenant_id) / "probe_results"
    except heartbeat_store.HeartbeatError:
        return None
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{provider}.json"
    payload = dict(result)
    payload["saved_at"] = datetime.now(timezone.utc).isoformat()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_result(tenant_id: str, provider: str) -> dict[str, Any] | None:
    """Return the most recent probe result for this tenant+provider, or None."""
    if not _SAFE_PROVIDER.match(provider or ""):
        return None
    try:
        root = heartbeat_store.tenant_root(tenant_id) / "probe_results"
    except heartbeat_store.HeartbeatError:
        return None
    path = root / f"{provider}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def probe_google(tenant_id: str) -> dict[str, Any]:
    """Run every Google sub-probe for this tenant. Returns aggregate summary."""
    try:
        access = credentials.access_token(tenant_id, "google")
    except (credentials.CredentialError, credentials.ProviderExchangeError) as exc:
        log.info("probe_google: cannot get access_token tenant=%s err=%s", tenant_id, exc)
        return {"ok": False, "errors": {"access_token": str(exc)}, "summary": {}}

    summary: dict[str, Any] = {}
    errors: dict[str, str] = {}

    for name, probe in (
        ("gmail", _probe_gmail),
        ("calendar", _probe_calendar),
        ("gsc", _probe_gsc),
        ("ga4", _probe_ga4),
        ("gbp", _probe_gbp),
    ):
        try:
            summary[name] = probe(access)
        except _ProbeError as exc:
            errors[name] = str(exc)
        except Exception as exc:  # belt-and-suspenders - a probe bug never blanks the rest
            log.exception("probe_google: unexpected error in %s tenant=%s", name, tenant_id)
            errors[name] = f"unexpected: {exc.__class__.__name__}"

    return {
        "ok": bool(summary),
        "errors": errors,
        "summary": summary,
    }


# --- Sub-probes -------------------------------------------------------------


def _probe_gmail(access_token: str) -> dict[str, Any]:
    body = _get_json(
        "https://gmail.googleapis.com/gmail/v1/users/me/profile",
        access_token,
    )
    return {
        "email": body.get("emailAddress", ""),
        "messages_total": int(body.get("messagesTotal") or 0),
    }


def _probe_calendar(access_token: str) -> dict[str, Any]:
    body = _get_json(
        "https://www.googleapis.com/calendar/v3/users/me/calendarList",
        access_token,
        params={"maxResults": "250", "minAccessRole": "reader"},
    )
    items = body.get("items") or []
    primary = ""
    for item in items:
        if item.get("primary"):
            primary = item.get("id", "")
            break
    return {
        "calendar_count": len(items),
        "primary": primary,
    }


def _probe_gsc(access_token: str) -> dict[str, Any]:
    body = _get_json(
        "https://searchconsole.googleapis.com/webmasters/v3/sites",
        access_token,
    )
    sites = body.get("siteEntry") or []
    first_site = sites[0].get("siteUrl", "") if sites else ""
    return {
        "site_count": len(sites),
        "first_site": first_site,
    }


def _probe_ga4(access_token: str) -> dict[str, Any]:
    body = _get_json(
        "https://analyticsadmin.googleapis.com/v1beta/accountSummaries",
        access_token,
        params={"pageSize": "50"},
    )
    summaries = body.get("accountSummaries") or []
    property_count = sum(len(s.get("propertySummaries") or []) for s in summaries)
    return {
        "account_count": len(summaries),
        "property_count": property_count,
    }


def _probe_gbp(access_token: str) -> dict[str, Any]:
    # GBP account listing is on the legacy v4 endpoint (plus
    # mybusinessaccountmanagement.googleapis.com/v1 for newer reads). Use v1
    # since the new consent screen grants business.manage which maps there.
    acc_body = _get_json(
        "https://mybusinessaccountmanagement.googleapis.com/v1/accounts",
        access_token,
    )
    accounts = acc_body.get("accounts") or []
    result: dict[str, Any] = {
        "account_count": len(accounts),
        "location_count": 0,
        "total_review_count": 0,
        "average_rating": 0.0,
    }
    if not accounts:
        return result

    # First-account discovery only: listing every location across many
    # accounts is a separate engagement and not needed for the activation
    # summary. For the demo tenant (single-account AP) this is exactly right.
    first_account_name = accounts[0].get("name", "")  # e.g. "accounts/1234567890"
    if not first_account_name:
        return result

    loc_body = _get_json(
        f"https://mybusinessbusinessinformation.googleapis.com/v1/{first_account_name}/locations",
        access_token,
        params={"readMask": "name,title", "pageSize": "100"},
    )
    locations = loc_body.get("locations") or []
    result["location_count"] = len(locations)

    # Reviews live on the v4 legacy endpoint. We ask for 0 rows but get the
    # aggregate counts in the response.
    if locations:
        first_loc_name = locations[0].get("name", "")  # e.g. "locations/5555"
        review_body = _get_json(
            f"https://mybusiness.googleapis.com/v4/{first_account_name}/{first_loc_name}/reviews",
            access_token,
            params={"pageSize": "1"},
        )
        result["total_review_count"] = int(review_body.get("totalReviewCount") or 0)
        avg = review_body.get("averageRating")
        try:
            result["average_rating"] = round(float(avg), 2) if avg is not None else 0.0
        except (TypeError, ValueError):
            result["average_rating"] = 0.0

    return result


# --- helpers ----------------------------------------------------------------


class _ProbeError(RuntimeError):
    """A single sub-probe failed. Never escapes probe_google()."""


def _get_json(url: str, access_token: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    """Single seam for HTTP GET; tests monkeypatch this."""
    try:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            params=params or {},
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise _ProbeError(f"network: {exc}") from exc
    if resp.status_code >= 400:
        raise _ProbeError(f"http {resp.status_code}: {resp.text[:200]}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise _ProbeError(f"non-json body: {exc}") from exc
    if not isinstance(body, dict):
        raise _ProbeError("response body is not an object")
    return body
