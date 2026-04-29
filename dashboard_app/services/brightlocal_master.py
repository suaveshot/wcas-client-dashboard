"""BrightLocal Pattern C wiring - adds tenant locations to Sam's master
account, fetches local-pack rankings on their behalf.

This is the W5 kickoff piece. The recommender that consumes these
rankings (combined with GA4 + GSC + fetch_site_facts) ships in W5.5
as services/seo_recommender.py. See ADR-030 + plan section 1B.5.

Pattern C invariant: the master API key lives ONLY at
/opt/wc-solns/_platform/brightlocal/master.json (chmod 600,
root-owned). Tenant code paths are forbidden from reading this file
- they go through this module's read functions, which never expose
the key. Tests pin that boundary.

Endpoint URLs follow BrightLocal Local SEO Tools API v4. They are
documented but vendor-specific; verify against the live console
when first wiring (Sam confirmed API access on 2026-04-26 per
project memory). The HTTP layer is fully injectable so tests run
without network.

Per-tenant config side-effects:
  - On successful add_tenant_location, this module stamps
    `brightlocal_location_id` into the tenant's tenant_config.json
    so fetch_rankings can find it next run.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import cost_tracker, heartbeat_store, platform_master

log = logging.getLogger(__name__)

API_BASE = "https://tools.brightlocal.com/seo-tools/api"
DEFAULT_TIMEOUT = 30.0
SIG_TTL_SECONDS = 1800  # BrightLocal sig expiry window

# Estimate, not authoritative; Sam will tune from real BrightLocal invoices.
# BrightLocal Local SEO Tools API bills per credit and credit-per-call varies
# by endpoint, so this default is a safe upper bound for dev-cap purposes.
BRIGHTLOCAL_COST_PER_CALL_USD = 0.10

# Tenant id used for master-account-only calls (no specific tenant attached).
_PLATFORM_TENANT_ID = "_platform"


class BrightLocalError(RuntimeError):
    """Base class for BrightLocal-specific failures."""


class BrightLocalNotProvisioned(BrightLocalError):
    """Sam hasn't put a master.json at /opt/wc-solns/_platform/brightlocal/."""


class BrightLocalBudgetExceeded(BrightLocalError):
    """Raised when a BrightLocal call would exceed the daily dev or per-tenant cap."""


# ---------------------------------------------------------------------------
# auth helper
# ---------------------------------------------------------------------------


def _master_key() -> str:
    """Return the master BrightLocal API key, or raise BrightLocalNotProvisioned.

    Tenant code paths must NEVER call this directly - it bypasses the
    Pattern C invariant. Only this module + the recommender (W5.5)
    should hit it.
    """
    record = platform_master.load_master("brightlocal")
    if not record:
        raise BrightLocalNotProvisioned(
            "BrightLocal master.json not found - provision "
            "/opt/wc-solns/_platform/brightlocal/master.json first."
        )
    api_key = (record.get("api_key") or record.get("key") or "").strip()
    if not api_key:
        raise BrightLocalNotProvisioned(
            "BrightLocal master.json present but missing 'api_key' field."
        )
    return api_key


def _master_secret(record: dict[str, Any]) -> str:
    """Some BrightLocal accounts have a separate signing secret. Defaults
    to api_key when no secret is configured (matches the v4 sig contract
    where api_key + sig is sufficient on most endpoints)."""
    return (record.get("api_secret") or record.get("api_key") or "").strip()


def _sign(api_key: str, api_secret: str, expires: int | None = None) -> dict[str, str]:
    """Build the (api-key, sig, expires) auth params for a BrightLocal v4 call."""
    if expires is None:
        expires = int(time.time()) + SIG_TTL_SECONDS
    msg = f"{api_key}{expires}".encode("utf-8")
    sig = hmac.new(
        api_secret.encode("utf-8"),
        msg,
        hashlib.sha1,
    ).hexdigest()
    return {"api-key": api_key, "sig": sig, "expires": str(expires)}


# ---------------------------------------------------------------------------
# HTTP layer (injectable)
# ---------------------------------------------------------------------------


def _default_post_form(url: str, fields: dict[str, str], timeout: float) -> dict[str, Any]:
    """POST application/x-www-form-urlencoded; return parsed JSON. The
    BrightLocal v4 endpoints are form-encoded (not JSON). Tests inject
    a fake to avoid the network."""
    from urllib.parse import urlencode

    data = urlencode(fields).encode("utf-8")
    req = Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body) if body else {}


def _default_get(url: str, params: dict[str, str], timeout: float) -> dict[str, Any]:
    from urllib.parse import urlencode

    full = f"{url}?{urlencode(params)}"
    req = Request(full, method="GET", headers={"Accept": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body) if body else {}


# ---------------------------------------------------------------------------
# tenant config side-effects
# ---------------------------------------------------------------------------


def _tenant_config_path(tenant_id: str):
    return heartbeat_store.tenant_root(tenant_id) / "tenant_config.json"


def _read_tenant_config(tenant_id: str) -> dict[str, Any]:
    path = _tenant_config_path(tenant_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_tenant_config(tenant_id: str, data: dict[str, Any]) -> None:
    path = _tenant_config_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    import os as _os
    _os.replace(tmp, path)


def get_tenant_location_id(tenant_id: str) -> str | None:
    """Read the BrightLocal location id we previously assigned to this tenant.
    Returns None if not provisioned yet."""
    cfg = _read_tenant_config(tenant_id)
    val = cfg.get("brightlocal_location_id")
    return val.strip() if isinstance(val, str) and val.strip() else None


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def add_tenant_location(
    tenant_id: str,
    *,
    biz_name: str,
    address: str,
    city: str,
    state: str,
    postcode: str,
    country: str = "US",
    lat: float | None = None,
    lng: float | None = None,
    keywords: list[str] | None = None,
    search_engines: list[str] | None = None,
    post_fn: Callable[[str, dict[str, str], float], dict[str, Any]] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Create a Local Search Rank Checker campaign on Sam's master account
    for this tenant and persist the resulting location_id into the tenant's
    tenant_config.json.

    Returns the location_id (str). Raises BrightLocalError on failure.
    Idempotent at the tenant level: if a location_id is already stamped
    on tenant_config.json, returns it without calling the API again.
    """
    existing = get_tenant_location_id(tenant_id)
    if existing:
        return existing

    record = platform_master.load_master("brightlocal")
    if not record:
        raise BrightLocalNotProvisioned(
            "BrightLocal master.json not found at "
            "/opt/wc-solns/_platform/brightlocal/master.json"
        )
    api_key = (record.get("api_key") or record.get("key") or "").strip()
    if not api_key:
        raise BrightLocalNotProvisioned("BrightLocal master.json missing api_key")
    api_secret = _master_secret(record)

    allowed, reason = cost_tracker.should_allow(tenant_id or _PLATFORM_TENANT_ID)
    if not allowed:
        raise BrightLocalBudgetExceeded(reason or "budget exceeded")

    fields: dict[str, str] = _sign(api_key, api_secret)
    fields.update({
        "business-name": biz_name,
        "address1": address,
        "city": city,
        "region": state,
        "postcode": postcode,
        "country": country,
    })
    if lat is not None:
        fields["lat"] = str(lat)
    if lng is not None:
        fields["lng"] = str(lng)
    if keywords:
        # BrightLocal accepts repeated keyword params; flatten with []. The
        # exact param key may need adjusting once Sam's account is live.
        fields["keywords"] = "|".join(keywords)
    if search_engines:
        fields["search-engines"] = "|".join(search_engines)
    else:
        fields["search-engines"] = "google"

    poster = post_fn or _default_post_form
    url = f"{API_BASE}/v4/lsr/create-campaign"
    try:
        resp = poster(url, fields, timeout)
    except (HTTPError, URLError, OSError, json.JSONDecodeError) as exc:
        raise BrightLocalError(f"create-campaign HTTP failed: {exc}") from exc

    if not isinstance(resp, dict):
        raise BrightLocalError(f"unexpected create-campaign response shape: {type(resp).__name__}")
    if resp.get("success") is False or "errors" in resp:
        raise BrightLocalError(f"create-campaign returned errors: {resp.get('errors') or resp}")

    location_id = (
        resp.get("location-id")
        or resp.get("campaign-id")
        or resp.get("location_id")
        or resp.get("campaign_id")
        or ""
    )
    if not location_id:
        raise BrightLocalError(f"create-campaign returned no id: {resp}")

    cfg = _read_tenant_config(tenant_id)
    cfg["brightlocal_location_id"] = str(location_id)
    cfg["brightlocal_provisioned_at"] = int(time.time())
    _write_tenant_config(tenant_id, cfg)

    cost_tracker.record_call_for_vendor(
        "brightlocal",
        tenant_id=tenant_id or _PLATFORM_TENANT_ID,
        kind="add_tenant_location",
        usd=BRIGHTLOCAL_COST_PER_CALL_USD,
    )

    return str(location_id)


def fetch_rankings(
    tenant_id: str,
    *,
    get_fn: Callable[[str, dict[str, str], float], dict[str, Any]] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Fetch the latest local-search-rank-checker results for this tenant.

    Returns a dict with the raw rankings payload. The shape passes through
    to W5.5's seo_recommender which knows how to read it. Raises:
      - BrightLocalNotProvisioned if no master.json or tenant has no location_id
      - BrightLocalError on HTTP / parse failures
    """
    location_id = get_tenant_location_id(tenant_id)
    if not location_id:
        raise BrightLocalNotProvisioned(
            f"Tenant {tenant_id} has no brightlocal_location_id - "
            f"run add_tenant_location first."
        )

    record = platform_master.load_master("brightlocal")
    if not record:
        raise BrightLocalNotProvisioned("BrightLocal master.json not found")
    api_key = (record.get("api_key") or record.get("key") or "").strip()
    api_secret = _master_secret(record)

    allowed, reason = cost_tracker.should_allow(tenant_id or _PLATFORM_TENANT_ID)
    if not allowed:
        raise BrightLocalBudgetExceeded(reason or "budget exceeded")

    params: dict[str, str] = _sign(api_key, api_secret)
    params["location-id"] = location_id

    getter = get_fn or _default_get
    url = f"{API_BASE}/v4/lsr/get-search-results"
    try:
        resp = getter(url, params, timeout)
    except (HTTPError, URLError, OSError, json.JSONDecodeError) as exc:
        raise BrightLocalError(f"get-search-results HTTP failed: {exc}") from exc

    if not isinstance(resp, dict):
        raise BrightLocalError(f"unexpected get-search-results shape: {type(resp).__name__}")
    if resp.get("success") is False or "errors" in resp:
        raise BrightLocalError(f"get-search-results returned errors: {resp.get('errors') or resp}")

    cost_tracker.record_call_for_vendor(
        "brightlocal",
        tenant_id=tenant_id or _PLATFORM_TENANT_ID,
        kind="fetch_rankings",
        usd=BRIGHTLOCAL_COST_PER_CALL_USD,
    )

    return resp


def is_provisioned() -> bool:
    """Quick check used by /admin to decide whether to surface the
    'Provision BrightLocal location' button. Doesn't expose the key."""
    return platform_master.is_provisioned("brightlocal")


__all__ = [
    "API_BASE",
    "BRIGHTLOCAL_COST_PER_CALL_USD",
    "BrightLocalBudgetExceeded",
    "BrightLocalError",
    "BrightLocalNotProvisioned",
    "add_tenant_location",
    "fetch_rankings",
    "get_tenant_location_id",
    "is_provisioned",
]
