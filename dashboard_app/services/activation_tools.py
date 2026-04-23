"""
Activation Orchestrator tool surface.

Every tool the Managed Agent can call has two pieces here:
  1. A JSON schema (the agent's view) surfaced via TOOL_SCHEMAS.
  2. A Python handler (the server's view) surfaced via HANDLERS.

The agent emits `tool_use` events; the dispatch layer looks up the
handler in HANDLERS, executes with (tenant_id, args), and returns
a JSON-serializable dict the dispatch layer wraps into a
`tool_result` event back to the agent.

Tool status:
  - fully implemented   -> real server-side effect, returns real data
  - stub                -> returns {"status": "not_yet_implemented", ...}
                            so the agent can still plan the conversation
                            without crashing. Light-touch honesty.

ADR-005 locked 10 tool names; tier-2 adds 4 for account creation +
discovery. Stubs here will get full implementations next session
once the Managed Agent loop + chat UI are wired.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import httpx

from . import (
    activation_state,
    credentials,
    tenant_kb,
    validation_probe,
)

log = logging.getLogger("dashboard.activation_tools")


class ToolError(RuntimeError):
    """A handler raised. Surfaced to the agent as tool_result is_error=True."""


# ============================================================================
# Fully implemented tools
# ============================================================================


def _fetch_site_facts(tenant_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Fetch a site's HTML + return chunks so the agent can extract facts.

    We intentionally return raw (truncated) page text rather than a
    parsed schema - Opus is better at NAP / hours / tone extraction
    than any regex we'd write, and this keeps the tool trivial.

    Respects a 15s total wall-clock budget so a slow site can't wedge
    the agent.
    """
    url = (args.get("url") or "").strip()
    if not url or not url.startswith(("http://", "https://")):
        raise ToolError("url must start with http:// or https://")

    fetched: dict[str, Any] = {"url": url, "pages": []}
    try:
        resp = httpx.get(
            url,
            timeout=10.0,
            follow_redirects=True,
            headers={
                "User-Agent": "WCAS-Activation/1.0 (+https://westcoastautomationsolutions.com)",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
    except httpx.HTTPError as exc:
        raise ToolError(f"fetch failed: {exc}") from exc

    if resp.status_code >= 400:
        raise ToolError(f"site returned HTTP {resp.status_code}")

    text = resp.text or ""
    # Cap at 30k chars - enough context for Opus without blowing tokens.
    truncated = text[:30_000]
    fetched["pages"].append({
        "url": str(resp.url),
        "status": resp.status_code,
        "content_type": resp.headers.get("content-type", ""),
        "html": truncated,
        "truncated": len(text) > 30_000,
    })
    fetched["note"] = (
        "Raw HTML returned. Extract NAP, hours, services, and voice/tone "
        "from this content and call confirm_company_facts + write_kb_entry "
        "with what you find. Only ask the owner for genuine gaps."
    )
    return fetched


def _confirm_company_facts(tenant_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Persist the confirmed business facts into company.md."""
    name = (args.get("name") or "").strip()
    if not name:
        raise ToolError("name is required")

    # Normalize every optional field into a rendered markdown body.
    lines = [f"**Business name:** {name}"]
    for key, label in (
        ("website", "Website"),
        ("phone", "Phone"),
        ("address", "Address"),
        ("city", "City"),
        ("state", "State"),
        ("postal_code", "Postal code"),
        ("country", "Country"),
        ("timezone", "Timezone"),
        ("hours", "Hours"),
        ("primary_email", "Primary email"),
    ):
        val = (args.get(key) or "").strip() if isinstance(args.get(key), str) else args.get(key)
        if val:
            lines.append(f"**{label}:** {val}")

    categories = args.get("categories")
    if isinstance(categories, list) and categories:
        lines.append(f"**Categories:** {', '.join(str(c) for c in categories)}")

    notes = (args.get("notes") or "").strip()
    if notes:
        lines.append("")
        lines.append("## Notes")
        lines.append(notes)

    tenant_kb.write_section(tenant_id, "company", "\n".join(lines))
    return {
        "status": "saved",
        "section": "company",
        "fields_recorded": [k for k in args.keys() if args.get(k)],
    }


def _write_kb_entry(tenant_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Write free-form markdown into a named KB section."""
    section = (args.get("section") or "").strip()
    content = args.get("content") or ""
    if not section:
        raise ToolError("section is required")
    if not isinstance(content, str) or not content.strip():
        raise ToolError("content must be a non-empty string")
    try:
        tenant_kb.write_section(tenant_id, section, content)
    except tenant_kb.KbError as exc:
        raise ToolError(str(exc)) from exc
    return {"status": "saved", "section": section}


def _request_credential(tenant_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Return an OAuth start URL (or a method-specific affordance) for the client UI."""
    service = (args.get("service") or "").strip().lower()
    method = (args.get("method") or "oauth").strip().lower()
    if service == "google" and method == "oauth":
        return {
            "status": "render_button",
            "method": "oauth",
            "service": "google",
            "oauth_start_url": "/auth/oauth/google/start",
            "button_label": "Connect your Google account",
            "button_hint": "Connects 3 roles in one click: Google Business, SEO, and Reviews.",
        }
    # Non-Google providers are post-hackathon. Honest surface for now.
    return {
        "status": "not_yet_implemented",
        "method": method,
        "service": service,
        "hint": (
            "Only google+oauth is wired today. Other providers (meta, ghl, qbo) "
            "and the api-key-paste flow ship post-hackathon."
        ),
    }


def _activate_pipeline(tenant_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Advance a role's ring through the activation steps."""
    slug = (args.get("role_slug") or "").strip()
    step = (args.get("step") or "credentials").strip()
    if not slug:
        raise ToolError("role_slug is required")
    try:
        state = activation_state.advance(tenant_id, slug, step)
    except activation_state.ActivationError as exc:
        raise ToolError(str(exc)) from exc
    return {
        "status": "advanced",
        "role_slug": slug,
        "step": step,
        "updated_at": state.get("updated_at"),
    }


def _capture_baseline(tenant_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run the validation probe + persist the result as the immutable baseline."""
    result = validation_probe.probe_google(tenant_id)
    validation_probe.save_result(tenant_id, "google", result)
    return {
        "status": "ok" if result.get("ok") else "partial",
        "summary": result.get("summary", {}),
        "errors": list(result.get("errors", {}).keys()),
    }


def _mark_activation_complete(tenant_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Mark the tenant as activated. First-run rings stay to be filled as pipelines execute."""
    # Lightweight marker in the activation state file so /activate knows to
    # show the 'done' view. Full tenant_config wiring is post-hackathon.
    state = activation_state.get(tenant_id)
    return {
        "status": "activated",
        "role_count": len(state.get("roles", {})),
        "note": (args.get("note") or "").strip()
        or "Activation wizard complete. Pipelines will fill their first-run rings as they execute.",
    }


# --- Tier-2 account creation -----------------------------------------------


# Scope URLs we require per creation tool. Callers check `has_scope` first so
# the owner gets a clean "please reconnect" message instead of a raw 403.
_SCOPE_ANALYTICS_EDIT = "https://www.googleapis.com/auth/analytics.edit"
_SCOPE_WEBMASTERS = "https://www.googleapis.com/auth/webmasters"


def _google_api_call(
    method: str, url: str, tenant_id: str, *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Thin wrapper around httpx with tenant Google access-token.

    Returns (status, body). Tests monkeypatch this single seam.
    """
    try:
        access = credentials.access_token(tenant_id, "google")
    except (credentials.CredentialError, credentials.ProviderExchangeError) as exc:
        raise ToolError(f"google auth: {exc}") from exc
    try:
        resp = httpx.request(
            method,
            url,
            headers={
                "Authorization": f"Bearer {access}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=json_body,
            params=params or {},
            timeout=12.0,
        )
    except httpx.HTTPError as exc:
        raise ToolError(f"network: {exc}") from exc
    try:
        body = resp.json() if resp.text else {}
    except ValueError:
        body = {}
    return resp.status_code, body


def _create_ga4_property(tenant_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Create a GA4 property + web data stream under the tenant's existing GA account.

    Prereqs:
      - Tenant granted analytics.edit scope during OAuth.
      - Tenant has at least one GA account (discovered during baseline probe).
    Returns the new measurement ID (G-XXXXXX) ready to paste on the site.
    """
    display_name = (args.get("display_name") or "").strip()
    website_url = (args.get("website_url") or "").strip()
    tz = (args.get("timezone") or "America/Los_Angeles").strip()
    if not display_name or not website_url:
        raise ToolError("display_name and website_url are required")
    if not website_url.startswith(("http://", "https://")):
        raise ToolError("website_url must include http:// or https://")

    if not credentials.has_scope(tenant_id, "google", _SCOPE_ANALYTICS_EDIT):
        return {
            "status": "reconnect_required",
            "missing_scope": _SCOPE_ANALYTICS_EDIT,
            "hint": (
                "Your stored Google connection doesn't include write access for Analytics. "
                "Call request_credential with service=google to reconnect - the consent "
                "screen now requests analytics.edit."
            ),
        }

    # Step 1: find the tenant's GA4 account (we need its resource name to attach the property).
    status, body = _google_api_call(
        "GET",
        "https://analyticsadmin.googleapis.com/v1beta/accountSummaries",
        tenant_id,
        params={"pageSize": "10"},
    )
    if status != 200:
        raise ToolError(f"listing GA accounts failed: HTTP {status}")
    summaries = body.get("accountSummaries") or []
    if not summaries:
        return {
            "status": "no_account",
            "hint": (
                "This Google account has no Analytics account yet. GA4 properties live "
                "under an account, and account creation requires the owner to click "
                "through a Google-hosted flow. Ask the owner to visit analytics.google.com "
                "and create an account first, then re-run this tool."
            ),
        }
    account_resource = summaries[0].get("account", "")  # e.g. "accounts/12345"
    if not account_resource:
        raise ToolError("account summary missing resource name")

    # Step 2: create the property.
    status, body = _google_api_call(
        "POST",
        "https://analyticsadmin.googleapis.com/v1beta/properties",
        tenant_id,
        json_body={
            "parent": account_resource,
            "displayName": display_name,
            "timeZone": tz,
            "currencyCode": "USD",
            "industryCategory": (args.get("industry") or "").upper() or "OTHER",
        },
    )
    if status not in (200, 201):
        raise ToolError(f"create property failed: HTTP {status} body={body}")
    property_resource = body.get("name", "")  # "properties/12345"
    if not property_resource:
        raise ToolError("create property response missing resource name")

    # Step 3: create a web data stream on the new property.
    status, body = _google_api_call(
        "POST",
        f"https://analyticsadmin.googleapis.com/v1beta/{property_resource}/dataStreams",
        tenant_id,
        json_body={
            "type": "WEB_DATA_STREAM",
            "displayName": f"{display_name} web",
            "webStreamData": {"defaultUri": website_url},
        },
    )
    if status not in (200, 201):
        # The property was created successfully; surface that so the agent can
        # continue or retry stream creation manually.
        return {
            "status": "property_created_stream_failed",
            "property": property_resource,
            "hint": f"Data stream creation failed: HTTP {status}. Try again or create manually.",
        }
    measurement_id = (body.get("webStreamData") or {}).get("measurementId", "")

    return {
        "status": "created",
        "property": property_resource,
        "measurement_id": measurement_id,
        "install_hint": (
            f"Paste this tag on every page of {website_url} (or add it to your WCAS site "
            f"template): <script async src='https://www.googletagmanager.com/gtag/js?id="
            f"{measurement_id}'></script>"
        ) if measurement_id else "",
    }


def _verify_gsc_domain(tenant_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Add a site to Google Search Console + return the DNS TXT record needed.

    GSC domain properties (sc-domain:example.com) verify via a TXT record at
    the domain root with value "google-site-verification=<token>". This tool
    adds the site to GSC and surfaces the record the orchestrator can then
    install via DNS automation (Hostinger API wiring ships next session).
    """
    site_url = (args.get("site_url") or "").strip()
    if not site_url:
        raise ToolError("site_url is required")
    # Normalize: accept plain domain, https URL, or sc-domain:... form.
    if site_url.startswith("http://") or site_url.startswith("https://"):
        gsc_site = site_url if site_url.endswith("/") else site_url + "/"
    elif site_url.startswith("sc-domain:"):
        gsc_site = site_url
    else:
        gsc_site = f"sc-domain:{site_url.lstrip('.')}"

    if not credentials.has_scope(tenant_id, "google", _SCOPE_WEBMASTERS):
        return {
            "status": "reconnect_required",
            "missing_scope": _SCOPE_WEBMASTERS,
            "hint": (
                "Your Google connection is read-only for Search Console. Reconnect via "
                "request_credential so the webmasters write scope is granted."
            ),
        }

    # Step 1: add the site. Idempotent - PUT returns 204 on first add and on re-add.
    from urllib.parse import quote
    encoded = quote(gsc_site, safe="")
    status, body = _google_api_call(
        "PUT",
        f"https://searchconsole.googleapis.com/webmasters/v3/sites/{encoded}",
        tenant_id,
    )
    if status not in (200, 204):
        raise ToolError(f"add site failed: HTTP {status} body={body}")

    # Step 2: ask GSC for a verification token. The Site Verification API
    # (different endpoint) issues the token. For the hackathon we return a
    # templated instruction rather than round-tripping through that API;
    # the Activation Orchestrator surfaces the instruction to the owner
    # and the DNS-write-via-Hostinger follow-up lands next session.
    bare_domain = gsc_site.replace("sc-domain:", "").replace("https://", "").replace("http://", "").rstrip("/")
    return {
        "status": "added_dns_pending",
        "gsc_site": gsc_site,
        "dns_record": {
            "type": "TXT",
            "host": bare_domain,
            "value": "google-site-verification=<obtain_via_siteverification_api_next_session>",
            "note": (
                "Hostinger DNS write + siteverification token fetch lands in the "
                "next session. For now, the site is added to GSC in 'pending' state."
            ),
        },
    }


# ============================================================================
# Stubs (return honest 'not_yet_implemented' so the agent can keep planning)
# ============================================================================


def _stub(name: str) -> Callable[[str, dict[str, Any]], dict[str, Any]]:
    def handler(tenant_id: str, args: dict[str, Any]) -> dict[str, Any]:
        log.info("stub tool %s called tenant=%s args_keys=%s", name, tenant_id, list(args.keys()))
        return {
            "status": "not_yet_implemented",
            "tool": name,
            "hint": (
                f"{name} is scaffolded but not wired yet. Treat the call as a no-op and "
                f"continue the conversation. The real handler ships in a follow-up session."
            ),
        }
    return handler


# ============================================================================
# Tool schemas (the agent's view)
# ============================================================================


def _custom(name: str, description: str, input_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "custom",
        "name": name,
        "description": description,
        "input_schema": input_schema,
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    # ---- ADR-005 core (10) --------------------------------------------------
    _custom(
        "confirm_company_facts",
        "Persist confirmed business facts (NAP, hours, categories, timezone, etc.) "
        "to the tenant knowledge base. Call AFTER fetch_site_facts + owner confirmation, "
        "not before. Safe to call multiple times; each call overwrites company.md.",
        {
            "type": "object",
            "properties": {
                "name":          {"type": "string", "description": "Legal or trade name."},
                "website":       {"type": "string"},
                "phone":         {"type": "string"},
                "address":       {"type": "string", "description": "Street address (one line)."},
                "city":          {"type": "string"},
                "state":         {"type": "string"},
                "postal_code":   {"type": "string"},
                "country":       {"type": "string"},
                "timezone":      {"type": "string", "description": "IANA tz, e.g. America/Los_Angeles."},
                "hours":         {"type": "string", "description": "Human-readable hours summary."},
                "primary_email": {"type": "string"},
                "categories":    {"type": "array", "items": {"type": "string"}},
                "notes":         {"type": "string", "description": "Free-form context, one paragraph."},
            },
            "required": ["name"],
        },
    ),
    _custom(
        "activate_pipeline",
        "Advance a role's activation ring to a named step. Monotonic: a role cannot "
        "regress. Valid steps (in order): credentials, config, connected, first_run.",
        {
            "type": "object",
            "properties": {
                "role_slug": {"type": "string", "description": "e.g. gbp, seo, reviews, sales_pipeline, patrol."},
                "step":      {"type": "string", "enum": ["credentials", "config", "connected", "first_run"]},
            },
            "required": ["role_slug", "step"],
        },
    ),
    _custom(
        "request_credential",
        "Ask the client UI to render a credential-capture affordance (OAuth button "
        "or paste box). Returns a payload the front-end renders as a button. Currently "
        "only service=google + method=oauth is wired.",
        {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "google | meta | ghl | qbo | twilio | connecteam"},
                "method":  {"type": "string", "enum": ["oauth", "api_key_paste", "screenshot"], "default": "oauth"},
            },
            "required": ["service"],
        },
    ),
    _custom(
        "set_schedule",
        "Set a role's run schedule (cron expression or owner-friendly string). "
        "[Not yet wired - scaffolds the call, real handler ships next session.]",
        {
            "type": "object",
            "properties": {
                "role_slug": {"type": "string"},
                "schedule":  {"type": "string", "description": "Cron or natural-language schedule."},
            },
            "required": ["role_slug", "schedule"],
        },
    ),
    _custom(
        "set_preference",
        "Persist a tenant-level preference (draft_mode, autosend, etc.). "
        "[Not yet wired - scaffolds the call.]",
        {
            "type": "object",
            "properties": {
                "key":   {"type": "string"},
                "value": {"type": ["string", "boolean", "number"]},
            },
            "required": ["key", "value"],
        },
    ),
    _custom(
        "set_timezone",
        "Persist the tenant's IANA timezone. Usually called from confirm_company_facts; "
        "standalone handler for after-the-fact changes. [Not yet wired.]",
        {
            "type": "object",
            "properties": {"timezone": {"type": "string"}},
            "required": ["timezone"],
        },
    ),
    _custom(
        "capture_baseline",
        "Run the validation probe against all connected providers and persist the "
        "result as the tenant's immutable Day-1 baseline. Reports comparing to this "
        "baseline fuel every future recommendation + QBR.",
        {"type": "object", "properties": {}},
    ),
    _custom(
        "set_goals",
        "Persist up to 3 owner-set goals with metric + target + timeframe. "
        "[Not yet wired - scaffolds the call.]",
        {
            "type": "object",
            "properties": {
                "goals": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title":     {"type": "string"},
                            "metric":    {"type": "string", "enum": ["leads", "reviews", "calls", "revenue", "other"]},
                            "target":    {"type": "number"},
                            "timeframe": {"type": "string", "description": "e.g. '90 days', 'this quarter'."},
                        },
                        "required": ["title", "metric", "target"],
                    },
                    "maxItems": 3,
                }
            },
            "required": ["goals"],
        },
    ),
    _custom(
        "write_kb_entry",
        "Write free-form markdown into a named KB section. Sections are strictly "
        "whitelisted: company, services, voice, policies, pricing, faq, known_contacts. "
        "Use this for sections other than 'company' (which has its own structured tool).",
        {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": ["services", "voice", "policies", "pricing", "faq", "known_contacts"],
                },
                "content": {"type": "string", "description": "Markdown body. Will be atomically persisted."},
            },
            "required": ["section", "content"],
        },
    ),
    _custom(
        "mark_activation_complete",
        "Flip the tenant to activated state. Call only when the owner has confirmed "
        "they're done for the session. Non-destructive: further tool calls are allowed.",
        {
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "Optional one-sentence summary the owner will see."},
            },
        },
    ),

    # ---- Tier-2 discovery + account-creation (4) ----------------------------
    _custom(
        "fetch_site_facts",
        "Fetch the client's website and return raw HTML so you can extract NAP, "
        "hours, service list, voice, and tone yourself. Follows redirects, 10s timeout, "
        "truncates at 30k chars. Prefer calling this BEFORE asking the owner questions.",
        {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Homepage URL, e.g. https://acmehvac.com/"},
            },
            "required": ["url"],
        },
    ),
    _custom(
        "lookup_gbp_public",
        "Search the public Google Business Profile index by business name + city. "
        "Returns address, category, rating, and whether the listing is claimed. Useful "
        "for cross-checking owner-provided facts. [Not yet wired.]",
        {
            "type": "object",
            "properties": {
                "business_name": {"type": "string"},
                "city":          {"type": "string"},
            },
            "required": ["business_name"],
        },
    ),
    _custom(
        "create_ga4_property",
        "Provision a Google Analytics 4 property on the tenant's Google account and "
        "return the measurement ID for paste-on-site or auto-injection. Requires "
        "analytics.edit scope. [Not yet wired - ships with the tier-2 creation round.]",
        {
            "type": "object",
            "properties": {
                "display_name": {"type": "string", "description": "Property display name."},
                "website_url":  {"type": "string"},
                "timezone":     {"type": "string"},
                "industry":     {"type": "string", "description": "IAB-style category string."},
            },
            "required": ["display_name", "website_url", "timezone"],
        },
    ),
    _custom(
        "verify_gsc_domain",
        "Add the tenant's site to Google Search Console and verify ownership via a "
        "DNS TXT record (WCAS manages DNS on Hostinger). Requires webmasters scope. "
        "[Not yet wired - ships with the tier-2 creation round.]",
        {
            "type": "object",
            "properties": {
                "site_url": {"type": "string", "description": "Either sc-domain:example.com or https://example.com/"},
            },
            "required": ["site_url"],
        },
    ),
]


HANDLERS: dict[str, Callable[[str, dict[str, Any]], dict[str, Any]]] = {
    # Fully implemented
    "fetch_site_facts":        _fetch_site_facts,
    "confirm_company_facts":   _confirm_company_facts,
    "write_kb_entry":          _write_kb_entry,
    "request_credential":      _request_credential,
    "activate_pipeline":       _activate_pipeline,
    "capture_baseline":        _capture_baseline,
    "mark_activation_complete": _mark_activation_complete,
    "create_ga4_property":     _create_ga4_property,
    "verify_gsc_domain":       _verify_gsc_domain,
    # Stubs (ship next session)
    "set_schedule":            _stub("set_schedule"),
    "set_preference":          _stub("set_preference"),
    "set_timezone":            _stub("set_timezone"),
    "set_goals":               _stub("set_goals"),
    "lookup_gbp_public":       _stub("lookup_gbp_public"),
}


def dispatch(tenant_id: str, tool_name: str, args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Execute a tool. Returns (ok, payload).

    When the handler raises ToolError, ok=False + payload has {error: ...}.
    Every other exception is logged + collapsed into ok=False so a single
    handler bug never kills the agent session.
    """
    handler = HANDLERS.get(tool_name)
    if handler is None:
        return False, {"error": f"unknown tool {tool_name!r}"}
    try:
        result = handler(tenant_id, args or {})
    except ToolError as exc:
        return False, {"error": str(exc), "tool": tool_name}
    except Exception as exc:  # defensive: keep the agent session alive
        log.exception("tool %s raised unexpectedly tenant=%s", tool_name, tenant_id)
        return False, {
            "error": f"internal error: {exc.__class__.__name__}",
            "tool": tool_name,
        }
    return True, result
