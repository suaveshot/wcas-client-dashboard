"""CRM detection.

The orchestrator needs to know what CRM (if any) a tenant is already
running so it can either wire WCAS to it ("integrate, don't migrate")
or queue a connect prompt. This module gives back a deterministic
classification from two signals:

  1. **HTML / header fingerprints** - widget snippets the CRM injects
     into the client's site (GHL's `msgsndr.com` chat, HubSpot's
     `js.hs-scripts.com`, Intercom's `widget.intercom.io`, etc.).
  2. **Stored credentials** - if the tenant already pasted a GHL API
     key, that's the strongest possible signal regardless of what the
     site shows.

Returns a stable dict the activation_tools layer surfaces to the
agent:

    {
      "detected": "ghl" | "hubspot" | "pipedrive" | "intercom" |
                  "calendly" | "salesforce" | "zoho" | "none" | "unknown",
      "confidence": "high" | "medium" | "low",
      "signals": ["msgsndr.com fingerprint", "ghl credentials stored"],
      "candidates": ["ghl", "calendly"],   # everything that matched
      "recommendation": "Use the existing GHL CRM via GHLProvider."
    }

Detection is best-effort. Inconclusive signals -> detected="unknown",
confidence="low", recommendation tells the agent to ask the owner
directly. This is the ADR-005-friendly default; we never guess.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from . import credentials as _credentials

log = logging.getLogger("dashboard.crm_detect")

_DEFAULT_TIMEOUT = 8.0


@dataclass(frozen=True)
class _Fingerprint:
    crm: str
    label: str
    body_markers: tuple[str, ...] = ()
    header_markers: tuple[str, ...] = ()
    url_markers: tuple[str, ...] = ()


# Order matters only for tie-breaking the recommendation when no stored
# credential disambiguates. Strongest, most-specific first.
_FINGERPRINTS: tuple[_Fingerprint, ...] = (
    _Fingerprint(
        crm="ghl",
        label="GoHighLevel",
        body_markers=(
            "msgsndr.com",
            "leadconnectorhq.com",
            "gohighlevel",
            "highlevel.com",
        ),
    ),
    _Fingerprint(
        crm="hubspot",
        label="HubSpot",
        body_markers=(
            "js.hs-scripts.com",
            "js.hsforms.net",
            "js.hubspot.com",
            "track.hubspot.com",
        ),
        header_markers=("x-hubspot",),
    ),
    _Fingerprint(
        crm="salesforce",
        label="Salesforce",
        body_markers=(
            "force.com/embeddedservice",
            "salesforce-experience",
            "//service.force.com",
        ),
    ),
    _Fingerprint(
        crm="pipedrive",
        label="Pipedrive LeadBooster",
        body_markers=(
            "leadbooster-chat.pipedrive.com",
            "pipedrivewebforms.com",
        ),
    ),
    _Fingerprint(
        crm="zoho",
        label="Zoho CRM / SalesIQ",
        body_markers=("salesiq.zoho.com", "zohopublic.com"),
    ),
    _Fingerprint(
        crm="intercom",
        label="Intercom",
        body_markers=("widget.intercom.io", "intercomcdn.com"),
    ),
    _Fingerprint(
        crm="calendly",
        label="Calendly",
        body_markers=("assets.calendly.com", "calendly.com/embed"),
    ),
)

# CRMs we currently have a provider implementation for. Used to make
# the recommendation field actionable instead of just descriptive.
SUPPORTED_PROVIDERS: frozenset[str] = frozenset({"ghl"})


def _fetch_html(url: str, *, http_get: Callable[..., Any]) -> tuple[str, dict[str, str], str]:
    """Return (body, headers, final_url). Raises httpx.HTTPError on failure."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    resp = http_get(url, timeout=_DEFAULT_TIMEOUT)
    body = (getattr(resp, "text", "") or "")[:80_000].lower()
    headers = {k.lower(): (v or "").lower() for k, v in resp.headers.items()}
    final_url = str(getattr(resp, "url", url)).lower()
    return body, headers, final_url


def _scan_fingerprints(
    body: str,
    headers: dict[str, str],
    final_url: str,
) -> tuple[list[str], list[str]]:
    """Return (matched_crm_ids, signals)."""
    matches: list[str] = []
    signals: list[str] = []
    parsed_host = (urlparse(final_url).hostname or "").lower()
    for fp in _FINGERPRINTS:
        for m in fp.body_markers:
            if m in body:
                matches.append(fp.crm)
                signals.append(f"{fp.label}: {m}")
                break
        else:
            for m in fp.header_markers:
                for hk in headers:
                    if m in hk:
                        matches.append(fp.crm)
                        signals.append(f"{fp.label} header: {hk}")
                        break
                else:
                    continue
                break
            else:
                for m in fp.url_markers:
                    if m in parsed_host:
                        matches.append(fp.crm)
                        signals.append(f"{fp.label} host: {parsed_host}")
                        break
    # Preserve order of first match per CRM.
    seen: set[str] = set()
    deduped: list[str] = []
    for m in matches:
        if m not in seen:
            deduped.append(m)
            seen.add(m)
    return deduped, signals


def _credential_signal(tenant_id: str | None) -> str | None:
    """Return the CRM id the tenant has stored credentials for, or None."""
    if not tenant_id:
        return None
    # Only GHL has a CRM-credentials slot today. As we add HubSpot/Pipedrive
    # paste credentials, append them here.
    creds = _credentials.load(tenant_id, "ghl")
    if creds and creds.get("api_key") and creds.get("location_id"):
        return "ghl"
    return None


def _recommend(detected: str, candidates: list[str]) -> str:
    """One-sentence string the agent surfaces back to the owner."""
    if detected == "ghl":
        return "Use the existing GHL CRM via GHLProvider; no new account needed."
    if detected in {"hubspot", "salesforce", "pipedrive", "zoho"}:
        return (
            f"{detected.title()} detected. Connect read-only at the agency's "
            f"convenience; WCAS will sync without migrating data."
        )
    if detected == "intercom":
        return (
            "Intercom in use for chat; pair with WCAS chat widget or hand off "
            "after-hours conversations."
        )
    if detected == "calendly":
        return (
            "Calendly is a scheduler, not a CRM; ask the owner where contacts "
            "actually live."
        )
    if detected == "none":
        return (
            "No CRM detected. Offer GHL setup as part of the activation; "
            "WCAS provisions a sub-account."
        )
    if candidates:
        joined = ", ".join(candidates)
        return f"Multiple CRM signals ({joined}); ask the owner which they consider primary."
    return "Could not detect a CRM from public signals; ask the owner directly."


def detect(
    url: str,
    *,
    tenant_id: str | None = None,
    http_get: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Classify the tenant's CRM. See module docstring for shape."""
    if not url or not isinstance(url, str):
        return {
            "detected": "unknown",
            "confidence": "low",
            "signals": [],
            "candidates": [],
            "recommendation": "url is required to detect a CRM.",
        }

    cred_hit = _credential_signal(tenant_id)
    signals: list[str] = []
    candidates: list[str] = []
    fetch_ok = False

    fetcher = http_get if http_get is not None else httpx.get
    try:
        body, headers, final_url = _fetch_html(url, http_get=fetcher)
        site_matches, site_signals = _scan_fingerprints(body, headers, final_url)
        candidates.extend(site_matches)
        signals.extend(site_signals)
        fetch_ok = True
    except httpx.HTTPError as exc:
        signals.append(f"site fetch failed: {type(exc).__name__}")
    except Exception as exc:
        log.warning("crm_detect fetch failed: %s", exc)
        signals.append(f"site fetch failed: {type(exc).__name__}")

    if cred_hit and cred_hit not in candidates:
        candidates.insert(0, cred_hit)
    if cred_hit:
        signals.insert(0, f"{cred_hit} credentials stored")

    # Decide detected + confidence.
    if cred_hit:
        detected = cred_hit
        confidence = "high"
    elif len(candidates) == 1:
        detected = candidates[0]
        confidence = "high"
    elif len(candidates) > 1:
        # Multiple matches: prefer GHL if present (it's both CRM + comms).
        if "ghl" in candidates:
            detected = "ghl"
        else:
            detected = candidates[0]
        confidence = "medium"
    elif fetch_ok:
        # We hit the site but found nothing. That's "no CRM" with confidence.
        detected = "none"
        confidence = "medium"
    else:
        # Fetch failed - we genuinely don't know.
        detected = "unknown"
        confidence = "low"

    return {
        "detected": detected,
        "confidence": confidence,
        "signals": signals[:8],
        "candidates": candidates,
        "recommendation": _recommend(detected, candidates),
        "supported": detected in SUPPORTED_PROVIDERS,
    }


__all__ = ["SUPPORTED_PROVIDERS", "detect"]
