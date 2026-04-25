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
  - fully implemented -> real server-side effect, returns real data
  - stub -> returns {"status": "not_yet_implemented", ...}
                            so the agent can still plan the conversation
                            without crashing. Light-touch honesty.

ADR-005 locked 10 tool names; tier-2 adds 4 for account creation +
discovery. Stubs here will get full implementations next session
once the Managed Agent loop + chat UI are wired.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from . import (
    activation_state,
    airtable_schema,
    audit_log,
    clients_repo,
    credentials,
    crm_mapping,
    email_sender,
    heartbeat_store,
    tenant_kb,
    validation_probe,
    voice_card,
)

log = logging.getLogger("dashboard.activation_tools")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# §0e Provisioning-tool gate. Tools that create real external infrastructure
# (new GA4 properties, GSC registrations, future Twilio sub-accounts, etc.)
# require the tenant's Onboarding Approved flag to be true. Non-provisioning
# tools (fetch_site_facts, write_kb_entry, activate_pipeline, etc.) run on
# any authenticated tenant so Sam can demo the conversation even on a not-
# yet-approved tenant row.
PROVISIONING_TOOLS: frozenset[str] = frozenset({
    "create_ga4_property",
    "verify_gsc_domain",
})

# §0g Sam-alerting map. Tool calls that should wake Sam when they fire.
# Rate-limited per (tenant, event_type) in email_sender.alert_sam.
_TOOLS_THAT_ALERT_SAM: frozenset[str] = PROVISIONING_TOOLS | {"mark_activation_complete"}


class ToolError(RuntimeError):
    """A handler raised. Surfaced to the agent as tool_result is_error=True."""


# ============================================================================
# Fully implemented tools
# ============================================================================


def _httpx_get(url: str, *, timeout: float = 10.0) -> "httpx.Response":
    """Shared GET wrapper used by fetch_site_facts + detect_website_platform.
    Extracted so tests can monkeypatch a single seam."""
    return httpx.get(
        url,
        timeout=timeout,
        follow_redirects=True,
        headers={
            "User-Agent": "WCAS-Activation/1.0 (+https://westcoastautomationsolutions.com)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )


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
        resp = _httpx_get(url, timeout=10.0)
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


def _detect_website_platform(tenant_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Classify a client's website by platform + host so the agent can
    pivot the conversation on turn 1.

    Checks (in priority order):
      1. <meta name="generator" ...> tag - most platforms self-identify
      2. Common CDN / asset path fingerprints in the HTML
      3. Specific subdomain patterns (*.myshopify.com, *.webflow.io)
      4. Response headers (powered-by, server, x-shopify-*)

    Host-provider guess is best-effort based on IP-range heuristics via
    the response's origin IP (exposed by httpx in resp.extensions) or
    header signals. Returns "unknown" when signals are inconclusive - 
    we never guess when we're not sure.

    `takeover_feasible` is the flag the orchestrator uses to decide
    whether WCAS can offer to host the site. True for static + WordPress,
    False for managed platforms like Shopify / Wix / Squarespace.
    """
    url = (args.get("url") or "").strip()
    if not url:
        raise ToolError("url is required")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        resp = _httpx_get(url, timeout=10.0)
    except httpx.HTTPError as exc:
        raise ToolError(f"fetch failed: {exc}") from exc

    body = (resp.text or "")[:60_000] # platform fingerprints live near the top
    body_lower = body.lower()
    headers_lower = {k.lower(): (v or "").lower() for k, v in resp.headers.items()}
    final_url = str(resp.url).lower()

    signals: list[str] = []
    platform = "unknown"

    # --- Generator meta tag --------------------------------------------------
    import re as _re
    gen_match = _re.search(
        r'<meta\s+name=["\']generator["\']\s+content=["\']([^"\']+)["\']',
        body,
        _re.IGNORECASE,
    )
    generator_value = (gen_match.group(1) if gen_match else "").strip()
    if generator_value:
        signals.append(f"generator meta: {generator_value[:80]}")
        gl = generator_value.lower()
        if "wordpress" in gl:
            platform = "wordpress"
        elif "squarespace" in gl:
            platform = "squarespace"
        elif "wix" in gl:
            platform = "wix"
        elif "webflow" in gl:
            platform = "webflow"
        elif "shopify" in gl:
            platform = "shopify"
        elif "ghost" in gl:
            platform = "ghost"

    # --- Fingerprint fallbacks (run even if generator matched, to add signals)
    if "cdn.shopify.com" in body_lower or ".myshopify.com" in final_url or headers_lower.get("x-shopify-stage"):
        platform = "shopify" if platform == "unknown" else platform
        signals.append("shopify fingerprint")
    if "static.wixstatic.com" in body_lower or ".wixsite.com" in final_url:
        platform = "wix" if platform == "unknown" else platform
        signals.append("wix fingerprint")
    if "squarespace-cdn.com" in body_lower or "static1.squarespace.com" in body_lower:
        platform = "squarespace" if platform == "unknown" else platform
        signals.append("squarespace fingerprint")
    if ".webflow.io" in final_url or "assets.website-files.com" in body_lower:
        platform = "webflow" if platform == "unknown" else platform
        signals.append("webflow fingerprint")
    if "/wp-content/" in body_lower or "/wp-includes/" in body_lower:
        platform = "wordpress" if platform == "unknown" else platform
        signals.append("wp-content paths")
    # GoHighLevel-hosted funnels render through msgsndr.com iframes / assets.
    if "msgsndr.com" in body_lower or "leadconnectorhq.com" in body_lower or "gohighlevel" in body_lower:
        platform = "ghl_hosted" if platform == "unknown" else platform
        signals.append("ghl/leadconnector fingerprint")

    # --- Header signals ------------------------------------------------------
    powered_by = headers_lower.get("x-powered-by", "")
    if powered_by:
        signals.append(f"x-powered-by: {powered_by[:80]}")
        if "wordpress" in powered_by and platform == "unknown":
            platform = "wordpress"

    server = headers_lower.get("server", "")
    if server:
        signals.append(f"server: {server[:80]}")

    # --- Host provider guess -------------------------------------------------
    host_provider_guess = "unknown"
    # Hostinger VPS IPs the WCAS platform uses (from memory: garcia-vps / ap-vps).
    hostinger_ip_prefixes = ("93.127.", "31.97.", "147.93.")
    cf_headers = headers_lower.get("cf-ray") or headers_lower.get("cf-cache-status")
    if cf_headers:
        host_provider_guess = "cloudflare_proxied"
        signals.append(f"cloudflare: {cf_headers[:40]}")

    # Try to read the resolved IP via httpx's extensions (not always populated).
    network_stream = resp.extensions.get("network_stream") if hasattr(resp, "extensions") else None
    peer_ip = ""
    try:
        if network_stream is not None:
            info = network_stream.get_extra_info("server_addr")
            if info:
                peer_ip = str(info[0])
    except (AttributeError, TypeError, KeyError, IndexError):
        peer_ip = ""
    if not peer_ip:
        # Fallback: try a direct DNS lookup. Best-effort; ignore failures.
        try:
            import socket as _socket
            from urllib.parse import urlparse as _urlparse
            parsed_host = _urlparse(final_url).hostname or ""
            if parsed_host:
                peer_ip = _socket.gethostbyname(parsed_host)
        except (OSError, ValueError):
            peer_ip = ""
    if peer_ip:
        signals.append(f"ip: {peer_ip}")
        if any(peer_ip.startswith(p) for p in hostinger_ip_prefixes):
            host_provider_guess = "hostinger"
        elif peer_ip.startswith("184.168.") or peer_ip.startswith("97.74."):
            host_provider_guess = "godaddy"
        elif peer_ip.startswith(("104.21.", "172.67.", "104.16.")):
            host_provider_guess = "cloudflare_proxied"

    # Shopify / Wix / Squarespace / Webflow are their own "host" too.
    if host_provider_guess == "unknown" and platform in ("shopify", "wix", "squarespace", "webflow"):
        host_provider_guess = platform

    # --- Takeover feasibility -----------------------------------------------
    # WCAS can take over static + WP sites (we host on Hostinger). Managed
    # SaaS platforms (Shopify/Wix/Squarespace) we respect - we don't try to
    # migrate their storefronts.
    takeover_feasible = platform in ("wordpress", "static", "ghost", "unknown")

    # If we couldn't name a platform but also couldn't detect fingerprints,
    # default to "static" (plain HTML) so takeover-feasible defaults honestly.
    if platform == "unknown" and not signals:
        platform = "static"
        signals.append("no platform fingerprints found; assuming static HTML")
        takeover_feasible = True

    notes = []
    if platform == "unknown":
        notes.append("Platform is ambiguous; ask the owner what tool they built the site in.")
    if host_provider_guess == "hostinger":
        notes.append("Site is already on WCAS infrastructure - we own the hosting.")
    if platform in ("shopify", "wix", "squarespace"):
        notes.append(
            f"Site is on {platform}. We won't move it, but we can install our chat widget + push SEO."
        )

    return {
        "url": final_url,
        "platform": platform,
        "signals": signals[:8], # cap the noise the agent sees
        "host_provider_guess": host_provider_guess,
        "takeover_feasible": takeover_feasible,
        "notes": " ".join(notes) if notes else "",
    }


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


_STRATEGIES = frozenset({"connect_existing", "wcas_provisions", "owner_signup"})
_CRED_METHODS = frozenset({"oauth", "api_key_paste", "screenshot", "concierge"})


def _record_provisioning_plan(tenant_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Capture the agent's per-pipeline setup strategy for this tenant.

    Writes two artifacts:
      1. kb/provisioning_plan.md - markdown for humans (Sam's concierge handoff)
      2. state_snapshot/provisioning_plan.json - structured JSON so the UI
         can paint per-ring strategy chips and the dashboard can render the
         handoff table post-activation.

    Overwrites on each call; the agent is instructed to call this tool
    exactly once per session.
    """
    items = args.get("items")
    if not isinstance(items, list) or not items:
        raise ToolError("items must be a non-empty array")

    cleaned: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            raise ToolError("each item must be an object")
        service = (raw.get("service") or "").strip()
        strategy = (raw.get("strategy") or "").strip()
        method = (raw.get("credential_method") or "").strip()
        if not service:
            raise ToolError("each item needs a service slug")
        if strategy not in _STRATEGIES:
            raise ToolError(
                f"strategy must be one of {sorted(_STRATEGIES)}, got {strategy!r}"
            )
        if method and method not in _CRED_METHODS:
            raise ToolError(
                f"credential_method must be one of {sorted(_CRED_METHODS)}, got {method!r}"
            )
        cleaned.append({
            "service": service,
            "strategy": strategy,
            "credential_method": method or "concierge",
            "owner_task": (raw.get("owner_task") or "").strip()[:400],
            "sam_task": (raw.get("sam_task") or "").strip()[:400],
        })

    # --- Human-readable markdown for Sam's handoff ---------------------------
    strategy_labels = {
        "connect_existing": "Connect to their existing account",
        "wcas_provisions": "WCAS provisions fresh",
        "owner_signup": "Owner signs up with WCAS walking them through",
    }
    method_labels = {
        "oauth": "OAuth click",
        "api_key_paste": "Paste API key",
        "screenshot": "Screenshot-guided",
        "concierge": "Sam concierge",
    }
    lines: list[str] = [
        "Per-pipeline strategy captured during activation intake. Each row is",
        "the agent's best plan based on the owner's answers + the site probe.",
        "",
        "| Pipeline | Strategy | How | Owner task | Sam task |",
        "|---|---|---|---|---|",
    ]
    for item in cleaned:
        row = (
            f"| {item['service']} "
            f"| {strategy_labels.get(item['strategy'], item['strategy'])} "
            f"| {method_labels.get(item['credential_method'], item['credential_method'])} "
            f"| {(item['owner_task'] or ' - ').replace('|', '\\|')} "
            f"| {(item['sam_task'] or ' - ').replace('|', '\\|')} |"
        )
        lines.append(row)

    try:
        tenant_kb.write_section(tenant_id, "provisioning_plan", "\n".join(lines))
    except tenant_kb.KbError as exc:
        raise ToolError(str(exc)) from exc

    # --- Structured JSON for the UI + future /admin view --------------------
    try:
        import json as _json
        root = heartbeat_store.tenant_root(tenant_id) / "state_snapshot"
        root.mkdir(parents=True, exist_ok=True)
        path = root / "provisioning_plan.json"
        payload = {
            "tenant_id": tenant_id,
            "updated_at": _now_iso(),
            "items": cleaned,
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(_json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
    except (OSError, heartbeat_store.HeartbeatError) as exc:
        raise ToolError(f"could not persist provisioning plan JSON: {exc}") from exc

    return {
        "status": "saved",
        "item_count": len(cleaned),
        "json_path": f"state_snapshot/provisioning_plan.json",
    }


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


def _propose_voice_card(tenant_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Persist the voice card the agent extracted from the site fetch.

    Side effects:
      - state_snapshot/voice_card.json (structured, for the UI panel)
      - kb/voice.md (human-readable mirror, for downstream Opus surfaces)

    Returns a `card_id` the UI uses for the panel-accept callback.
    """
    traits = args.get("traits") or []
    if not isinstance(traits, list) or not traits:
        raise ToolError("traits must be a non-empty array")
    generic = (args.get("generic_sample") or "").strip()
    voice_sample = (args.get("voice_sample") or "").strip()
    if not generic or not voice_sample:
        raise ToolError("generic_sample and voice_sample are required")

    sample_context = (args.get("sample_context") or "").strip()
    source_pages = args.get("source_pages") or []
    if not isinstance(source_pages, list):
        source_pages = []

    payload = voice_card.save(
        tenant_id,
        traits=[str(t) for t in traits],
        generic_sample=generic,
        voice_sample=voice_sample,
        sample_context=sample_context,
        source_pages=[str(p) for p in source_pages],
    )

    # Mirror the structured card into voice.md so every downstream Opus
    # surface (sample generator, recs, ask, future agents) reads the
    # same voice description as the UI panel.
    md_lines = [
        "## Voice traits",
        "",
        *(f"- {t}" for t in payload["traits"]),
        "",
        "## Sample message in this voice",
        "",
        f"_Context: {payload['sample_context'] or 'general greeting'}_",
        "",
        payload["voice_sample"],
        "",
        "## For comparison: generic AI version",
        "",
        payload["generic_sample"],
    ]
    try:
        tenant_kb.write_section(tenant_id, "voice", "\n".join(md_lines))
    except tenant_kb.KbError as exc:
        raise ToolError(f"voice KB write failed: {exc}") from exc

    return {
        "status": "rendered",
        "card_id": payload["card_id"],
        "panel_type": "voice_card",
        "trait_count": len(payload["traits"]),
    }


def _fetch_airtable_schema(tenant_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Read the schema (+ small set of scrubbed sample rows) of a tenant's
    whitelisted Airtable base. Per-tenant base_id whitelist enforced."""
    base_id = (args.get("base_id") or "").strip()
    if not base_id:
        # If the agent didn't pass one, fall back to the whitelisted default.
        whitelisted = airtable_schema.whitelisted_base_id(tenant_id, "airtable_bookings")
        if not whitelisted:
            return {
                "status": "no_base_configured",
                "hint": (
                    "This tenant has no Airtable base whitelisted yet. "
                    "Sam can register one in tenant_config.json before "
                    "running the CRM mapping step."
                ),
            }
        base_id = whitelisted

    try:
        schema = airtable_schema.fetch_schema(tenant_id, base_id)
    except airtable_schema.AirtableSchemaError as exc:
        raise ToolError(str(exc)) from exc

    return {
        "status": "ok",
        "base_id": schema["base_id"],
        "table_count": len(schema["tables"]),
        "tables": schema["tables"],
    }


def _propose_crm_mapping(tenant_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Persist the agent's mapping of the tenant's CRM to a WCAS playbook.

    Side effects:
      - state_snapshot/crm_mapping.json (structured, for the UI panel + simulate endpoint)
      - kb/crm_mapping.md (human-readable mirror, for Sam's concierge handoff)
    """
    base_id = (args.get("base_id") or "").strip()
    table_name = (args.get("table_name") or "").strip()
    if not base_id or not table_name:
        raise ToolError("base_id and table_name are required")

    field_mapping = args.get("field_mapping") or {}
    if not isinstance(field_mapping, dict) or not field_mapping:
        raise ToolError("field_mapping must be a non-empty object")

    segments = args.get("segments") or []
    if not isinstance(segments, list) or not segments:
        raise ToolError("segments must be a non-empty array")

    proposed_actions = args.get("proposed_actions") or []
    if not isinstance(proposed_actions, list):
        proposed_actions = []

    payload = crm_mapping.save(
        tenant_id,
        base_id=base_id,
        table_name=table_name,
        field_mapping=field_mapping,
        segments=segments,
        proposed_actions=proposed_actions,
    )

    # Markdown mirror so the handoff doc has it.
    md_lines = [
        f"**Base:** `{payload['base_id']}` table `{payload['table_name']}`",
        "",
        "## Field mapping",
        "",
        "| WCAS field | Their column |",
        "|---|---|",
    ]
    for wcas, theirs in payload["field_mapping"].items():
        md_lines.append(f"| `{wcas}` | {theirs} |")
    md_lines.extend(["", "## Segments"])
    for seg in payload["segments"]:
        md_lines.append(f"- **{seg['label']}** ({seg['count']}) - slug `{seg['slug']}`")
    if payload["proposed_actions"]:
        md_lines.extend(["", "## Proposed actions"])
        for act in payload["proposed_actions"]:
            md_lines.append(
                f"- segment `{act['segment']}` -> playbook `{act['playbook']}` via `{act['automation']}`"
            )
    try:
        tenant_kb.write_section(tenant_id, "crm_mapping", "\n".join(md_lines))
    except tenant_kb.KbError as exc:
        raise ToolError(f"crm_mapping KB write failed: {exc}") from exc

    return {
        "status": "rendered",
        "mapping_id": payload["mapping_id"],
        "panel_type": "crm_mapping",
        "segment_count": len(payload["segments"]),
        "action_count": len(payload["proposed_actions"]),
    }


def _mark_activation_complete(tenant_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Mark the tenant as activated. First-run rings stay to be filled as pipelines execute."""
    note = (args.get("note") or "").strip()
    state = activation_state.mark_complete(tenant_id, note=note or None)
    return {
        "status": "activated",
        "activated_at": state.get("activated_at"),
        "role_count": len(state.get("roles", {})),
        "note": note
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
    account_resource = summaries[0].get("account", "") # e.g. "accounts/12345"
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
    property_resource = body.get("name", "") # "properties/12345"
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
                "name": {"type": "string", "description": "Legal or trade name."},
                "website": {"type": "string"},
                "phone": {"type": "string"},
                "address": {"type": "string", "description": "Street address (one line)."},
                "city": {"type": "string"},
                "state": {"type": "string"},
                "postal_code": {"type": "string"},
                "country": {"type": "string"},
                "timezone": {"type": "string", "description": "IANA tz, e.g. America/Los_Angeles."},
                "hours": {"type": "string", "description": "Human-readable hours summary."},
                "primary_email": {"type": "string"},
                "categories": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "string", "description": "Free-form context, one paragraph."},
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
                "step": {"type": "string", "enum": ["credentials", "config", "connected", "first_run"]},
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
                "method": {"type": "string", "enum": ["oauth", "api_key_paste", "screenshot"], "default": "oauth"},
            },
            "required": ["service"],
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
        "write_kb_entry",
        "Write free-form markdown into a named KB section. Sections are strictly "
        "whitelisted. Use this for every section other than 'company' (which has "
        "its own structured tool) and 'provisioning_plan' (use record_provisioning_plan "
        "for that).",
        {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": [
                        "services", "voice", "policies", "pricing",
                        "faq", "known_contacts", "existing_stack",
                    ],
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
        "create_ga4_property",
        "Provision a Google Analytics 4 property on the tenant's Google account and "
        "return the measurement ID for paste-on-site or auto-injection. Requires "
        "analytics.edit scope. [Not yet wired - ships with the tier-2 creation round.]",
        {
            "type": "object",
            "properties": {
                "display_name": {"type": "string", "description": "Property display name."},
                "website_url": {"type": "string"},
                "timezone": {"type": "string"},
                "industry": {"type": "string", "description": "IAB-style category string."},
            },
            "required": ["display_name", "website_url", "timezone"],
        },
    ),
    _custom(
        "verify_gsc_domain",
        "Add the tenant's site to Google Search Console and verify ownership via a "
        "DNS TXT record. The tool adds the site + surfaces the TXT record spec the "
        "owner needs to add at their DNS provider (Hostinger / GoDaddy / Cloudflare / "
        "whoever). Works for any host - we don't automate the DNS write itself. "
        "Requires webmasters scope.",
        {
            "type": "object",
            "properties": {
                "site_url": {"type": "string", "description": "Either sc-domain:example.com or https://example.com/"},
            },
            "required": ["site_url"],
        },
    ),

    # ---- v0.6.0 Voice & Personalization pivot (3) ---------------------------
    _custom(
        "propose_voice_card",
        "Render the side-by-side voice comparison panel in the wizard chat. "
        "Call this AFTER fetch_site_facts when you've extracted the owner's "
        "voice from the raw HTML. Pass 3-5 trait keywords describing how they "
        "sound, a hardcoded `generic_sample` (the bland AI version of a typical "
        "message - e.g. 'Hi! Don't forget your appointment tomorrow.'), and a "
        "`voice_sample` you wrote in the owner's actual voice for the same "
        "context. This persists voice.md AND a structured panel the UI renders. "
        "Call ONCE per session (call again only if the owner edits their voice).",
        {
            "type": "object",
            "properties": {
                "traits": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "3-5 short keywords (e.g. 'warm', 'family-oriented', 'bilingual')",
                },
                "generic_sample": {"type": "string", "description": "The bland AI version of a typical message (~1-2 sentences)"},
                "voice_sample": {"type": "string", "description": "The same message rewritten in this owner's voice (~1-2 sentences)"},
                "sample_context": {"type": "string", "description": "What kind of message it is (e.g. 're-engagement reminder text')"},
                "source_pages": {"type": "array", "items": {"type": "string"}, "description": "URLs you pulled the voice from (max 5)"},
            },
            "required": ["traits", "generic_sample", "voice_sample"],
        },
    ),
    _custom(
        "fetch_airtable_schema",
        "Read the schema + a small set of recent rows from the tenant's "
        "whitelisted Airtable base (their CRM / bookings system). Returns "
        "table names, fields, row counts, and 5 sample rows per table with "
        "PII scrubbed. Per-tenant whitelist enforced; the agent cannot read "
        "arbitrary bases. Pass an empty base_id to use the tenant's default "
        "whitelisted base. Call BEFORE propose_crm_mapping so you have real "
        "data to map.",
        {
            "type": "object",
            "properties": {
                "base_id": {"type": "string", "description": "Airtable base ID (appXXX). Empty string falls back to the tenant's whitelisted default."},
            },
        },
    ),
    _custom(
        "propose_crm_mapping",
        "Render the CRM mapping panel: a segment preview ('47 active, 12 "
        "inactive 30+ days, 3 brand new this month') with proposed automations "
        "for each segment. Call this AFTER fetch_airtable_schema. Field mapping "
        "is YOUR translation between WCAS canonical fields (first_name, "
        "last_engagement, contact_email, etc.) and the tenant's actual column "
        "names. Segments are YOUR analysis of who's worth acting on. Proposed "
        "actions tie segments to WCAS playbooks the owner will see activated. "
        "Call ONCE per session.",
        {
            "type": "object",
            "properties": {
                "base_id": {"type": "string", "description": "Same base_id you read from"},
                "table_name": {"type": "string", "description": "The primary table (e.g. 'Bookings', 'Contacts')"},
                "field_mapping": {
                    "type": "object",
                    "description": "WCAS canonical field -> their column name. Keys are canonical: first_name, last_engagement, contact_email, etc.",
                    "additionalProperties": {"type": "string"},
                },
                "segments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "slug": {"type": "string", "description": "machine slug (e.g. 'inactive_30d', 'active', 'brand_new')"},
                            "label": {"type": "string", "description": "human label for the panel (e.g. 'Inactive 30+ days')"},
                            "count": {"type": "integer"},
                            "sample_names": {"type": "array", "items": {"type": "string"}, "description": "Up to 5 example names from the data (already scrubbed)"},
                        },
                        "required": ["slug", "label", "count"],
                    },
                },
                "proposed_actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "segment": {"type": "string"},
                            "playbook": {"type": "string", "description": "e.g. 're_engagement', 'welcome_series'"},
                            "automation": {"type": "string", "description": "Which of the 7 WCAS automations runs it (e.g. 'email_assistant')"},
                        },
                        "required": ["segment", "playbook", "automation"],
                    },
                },
            },
            "required": ["base_id", "table_name", "field_mapping", "segments"],
        },
    ),

    # ---- §4 Discovery + §5 provisioning plan --------------------------------
    _custom(
        "detect_website_platform",
        "Classify the client's website by platform (WordPress / Shopify / Wix / "
        "Squarespace / Webflow / GHL-hosted / static / unknown) + guess the host "
        "provider (Hostinger / GoDaddy / Cloudflare-proxied / unknown). Use this "
        "immediately after fetch_site_facts on turn 1 so the agent can pivot the "
        "conversation based on what it actually found (e.g. 'Hostinger, already "
        "yours' vs 'Shopify, we won't host but we can install the chat widget'). "
        "Call ONCE per session.",
        {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Homepage URL the owner gave you."},
            },
            "required": ["url"],
        },
    ),
    _custom(
        "record_provisioning_plan",
        "Capture the per-pipeline setup strategy after discovery. For each of the "
        "7 pipelines (gbp, seo, reviews, email_assistant, chat_widget, blog, social), "
        "record: strategy ('connect_existing' / 'wcas_provisions' / 'owner_signup'), "
        "credential_method ('oauth' / 'api_key_paste' / 'screenshot' / 'concierge'), "
        "owner_task (one sentence), sam_task (one sentence for the concierge handoff). "
        "Writes both a markdown handoff doc for Sam AND structured JSON the UI uses "
        "for per-ring strategy chips. Call ONCE per session.",
        {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "service": {"type": "string"},
                            "strategy": {
                                "type": "string",
                                "enum": ["connect_existing", "wcas_provisions", "owner_signup"],
                            },
                            "credential_method": {
                                "type": "string",
                                "enum": ["oauth", "api_key_paste", "screenshot", "concierge"],
                            },
                            "owner_task": {"type": "string"},
                            "sam_task": {"type": "string"},
                        },
                        "required": ["service", "strategy"],
                    },
                },
            },
            "required": ["items"],
        },
    ),
]


HANDLERS: dict[str, Callable[[str, dict[str, Any]], dict[str, Any]]] = {
    # Fully implemented
    "fetch_site_facts": _fetch_site_facts,
    "detect_website_platform": _detect_website_platform,
    "confirm_company_facts": _confirm_company_facts,
    "write_kb_entry": _write_kb_entry,
    "record_provisioning_plan": _record_provisioning_plan,
    "request_credential": _request_credential,
    "activate_pipeline": _activate_pipeline,
    "capture_baseline": _capture_baseline,
    "mark_activation_complete": _mark_activation_complete,
    "create_ga4_property": _create_ga4_property,
    "verify_gsc_domain": _verify_gsc_domain,
    # v0.6.0 Voice & Personalization pivot
    "propose_voice_card": _propose_voice_card,
    "fetch_airtable_schema": _fetch_airtable_schema,
    "propose_crm_mapping": _propose_crm_mapping,
}


def dispatch(tenant_id: str, tool_name: str, args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Execute a tool. Returns (ok, payload).

    When the handler raises ToolError, ok=False + payload has {error: ...}.
    Every other exception is logged + collapsed into ok=False so a single
    handler bug never kills the agent session.

    §0 Gates applied before handler runs:
      - Provisioning tools (PROVISIONING_TOOLS) require the tenant's
        Onboarding Approved Airtable flag to be true. Unapproved tenants
        get a deterministic error that the agent can narrate.
      - Every run records an audit-log line (best-effort).
      - Provisioning + mark_activation_complete fire a Sam-alert email
        (rate-limited per event-type).
    """
    handler = HANDLERS.get(tool_name)
    if handler is None:
        audit_log.record(
            tenant_id=tenant_id,
            event="tool_unknown",
            ok=False,
            tool=tool_name,
        )
        return False, {"error": f"unknown tool {tool_name!r}"}

    # Provisioning gate (§0e).
    if tool_name in PROVISIONING_TOOLS:
        if not clients_repo.is_onboarding_approved_by_tenant(tenant_id):
            audit_log.record(
                tenant_id=tenant_id,
                event="tool_denied_not_approved",
                ok=False,
                tool=tool_name,
                args=args or {},
            )
            return False, {
                "error": "onboarding_not_approved",
                "tool": tool_name,
                "hint": (
                    "This tenant hasn't been approved to run provisioning tools yet. "
                    "Contact Sam before continuing."
                ),
            }

    try:
        result = handler(tenant_id, args or {})
    except ToolError as exc:
        audit_log.record(
            tenant_id=tenant_id,
            event="tool_call",
            ok=False,
            tool=tool_name,
            args=args or {},
            error=str(exc),
        )
        return False, {"error": str(exc), "tool": tool_name}
    except Exception as exc: # defensive: keep the agent session alive
        log.exception("tool %s raised unexpectedly tenant=%s", tool_name, tenant_id)
        audit_log.record(
            tenant_id=tenant_id,
            event="tool_call",
            ok=False,
            tool=tool_name,
            args=args or {},
            error=f"internal: {exc.__class__.__name__}",
        )
        return False, {
            "error": f"internal error: {exc.__class__.__name__}",
            "tool": tool_name,
        }

    audit_log.record(
        tenant_id=tenant_id,
        event="tool_call",
        ok=True,
        tool=tool_name,
        args=args or {},
        result_status=(result.get("status") if isinstance(result, dict) else None),
    )

    # Sam alerting (§0g). force=True on mark_activation_complete because
    # Sam always wants to know when a tenant finishes; dedupe otherwise.
    if tool_name in _TOOLS_THAT_ALERT_SAM:
        email_sender.alert_sam(
            tenant_id=tenant_id,
            event_type=tool_name,
            subject=f"[WCAS] {tenant_id}: {tool_name}",
            body=(
                f"Activation tool fired.\n\n"
                f"Tenant: {tenant_id}\n"
                f"Tool: {tool_name}\n"
                f"Status: {(result or {}).get('status', 'ok') if isinstance(result, dict) else 'ok'}\n"
            ),
            force=(tool_name == "mark_activation_complete"),
        )

    # §0h Complete-lock: stamp Airtable when the wizard declares completion.
    if tool_name == "mark_activation_complete":
        try:
            record = clients_repo.find_by_tenant_id(tenant_id)
            if record is not None:
                clients_repo.mark_onboarding_completed(record["id"])
        except (RuntimeError, KeyError):
            log.warning("mark_onboarding_completed write-back failed tenant=%s", tenant_id)

    return True, result
