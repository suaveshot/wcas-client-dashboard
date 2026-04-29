"""Per-provider paste-credential form specs.

When the activation agent calls `request_credential(method="api_key_paste",
service="X")`, the dashboard front-end needs to know which fields to
render. This module is the single source of truth for that form shape.

Each spec lists the fields the dashboard collects, instructions the
agent narrates to the owner, an optional screenshot hint (Phase 2C
screenshot-guided flow), and the credentials.store_paste payload key
the form submission maps to.

The provider name MUST be in `credentials.PASTE_PROVIDERS` - this
module's spec table and that whitelist must stay in sync. A test pins
the invariant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import credentials as _credentials


@dataclass(frozen=True)
class PasteField:
    """One row in a paste-credential form."""
    name: str
    label: str
    type: str = "text"  # text | password | email
    required: bool = True
    placeholder: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "placeholder": self.placeholder,
        }


@dataclass(frozen=True)
class PasteSpec:
    """A complete spec for one provider's paste form."""
    service: str
    label: str
    fields: tuple[PasteField, ...]
    instructions: str
    screenshot_hint: str = ""
    docs_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "label": self.label,
            "fields": [f.to_dict() for f in self.fields],
            "instructions": self.instructions,
            "screenshot_hint": self.screenshot_hint,
            "docs_url": self.docs_url,
        }


# Helper to build commonly-shaped fields without repeating ourselves.
def _api_key(label: str = "API key", placeholder: str = "") -> PasteField:
    return PasteField(name="api_key", label=label, type="password",
                      placeholder=placeholder)


_SPECS: dict[str, PasteSpec] = {
    "gmail_app_password": PasteSpec(
        service="gmail_app_password",
        label="Gmail App Password",
        fields=(
            PasteField(name="email_address", label="Gmail address", type="email",
                       placeholder="owner@yourbusiness.com"),
            PasteField(name="app_password", label="App Password (16 chars)",
                       type="password", placeholder="xxxx xxxx xxxx xxxx"),
        ),
        instructions=(
            "Sign in to your Google Account, open the App Passwords page, "
            "create a new password labeled 'WCAS dashboard', and paste the "
            "16-character value here. We never see your real Google password."
        ),
        docs_url="https://myaccount.google.com/apppasswords",
        screenshot_hint=(
            "If you can't find the App Passwords page, share a screenshot of "
            "your Google Account security tab and we will guide you."
        ),
    ),
    "ghl": PasteSpec(
        service="ghl",
        label="GoHighLevel",
        fields=(
            _api_key(label="GHL API key", placeholder="eyJhbGc..."),
            PasteField(name="location_id", label="Location ID",
                       placeholder="abc123XYZ"),
            PasteField(name="from_email", label="Default sender email",
                       type="email", required=False,
                       placeholder="hello@yourbusiness.com"),
        ),
        instructions=(
            "In GHL, open Settings, then API Keys. Create a Location-scoped "
            "API key with conversation, contact, and opportunity read/write. "
            "The Location ID is in the URL after 'location'."
        ),
        docs_url="https://highlevel.stoplight.io/docs/integrations",
    ),
    "airtable": PasteSpec(
        service="airtable",
        label="Airtable",
        fields=(
            PasteField(name="personal_access_token", label="Personal Access Token",
                       type="password", placeholder="patXXX..."),
            PasteField(name="base_id", label="Base ID (optional)",
                       required=False, placeholder="appXXX..."),
        ),
        instructions=(
            "In Airtable, open your account page, then Developer Hub, then "
            "Personal Access Tokens. Create one with data.records:read on "
            "the base you want WCAS to read. Paste the token here."
        ),
        docs_url="https://airtable.com/create/tokens",
    ),
    "brightlocal": PasteSpec(
        service="brightlocal",
        label="BrightLocal",
        fields=(
            _api_key(label="BrightLocal API key"),
            PasteField(name="api_secret", label="API secret",
                       type="password"),
        ),
        instructions=(
            "Most clients do not need to fill this in. WCAS provides "
            "BrightLocal at the platform level. Only paste here if you "
            "have your own BrightLocal account you want WCAS to use."
        ),
        docs_url="https://www.brightlocal.com/api/",
    ),
    "twilio_paste": PasteSpec(
        service="twilio_paste",
        label="Twilio (your account)",
        fields=(
            PasteField(name="account_sid", label="Account SID",
                       placeholder="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"),
            PasteField(name="auth_token", label="Auth token", type="password"),
            PasteField(name="messaging_service_sid", label="Messaging Service SID",
                       required=False, placeholder="MGxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"),
        ),
        instructions=(
            "In the Twilio console, copy your Account SID and Auth Token "
            "from the dashboard. The Messaging Service SID is optional but "
            "recommended for SMS - find it under Messaging > Services."
        ),
        docs_url="https://console.twilio.com/",
    ),
    "connecteam": PasteSpec(
        service="connecteam",
        label="Connecteam",
        fields=(
            _api_key(label="Connecteam API key"),
        ),
        instructions=(
            "In Connecteam, open Admin > Account Settings > Developer. "
            "Generate an API key with the scopes WCAS needs (Forms read, "
            "Time Off read). Paste the key here."
        ),
        docs_url="https://developer.connecteam.com/",
    ),
    "vapi": PasteSpec(
        service="vapi",
        label="Vapi (Voice AI)",
        fields=(
            _api_key(label="Vapi API key"),
            PasteField(name="assistant_id", label="Assistant ID",
                       required=False),
        ),
        instructions=(
            "In Vapi, open API Keys, create a server-side key, and paste "
            "it here. Most clients do not need this - WCAS provides Vapi "
            "at the platform level under Pattern C."
        ),
        docs_url="https://docs.vapi.ai/",
    ),
    "hubspot": PasteSpec(
        service="hubspot",
        label="HubSpot",
        fields=(
            PasteField(name="access_token", label="Private app access token",
                       type="password", placeholder="pat-na1-..."),
            PasteField(name="portal_id", label="Portal (Hub) ID",
                       required=False, placeholder="12345678"),
            PasteField(name="from_email", label="Default sender email",
                       type="email", required=False,
                       placeholder="hello@yourbusiness.com"),
        ),
        instructions=(
            "In HubSpot, open Settings, then Integrations, then Private Apps. "
            "Create an app with the crm.objects.contacts.read/write, "
            "crm.objects.deals.read/write, and conversations scopes. Copy the "
            "access token here. Your Portal ID is in the URL after /portal/."
        ),
        docs_url="https://developers.hubspot.com/docs/api/private-apps",
    ),
    "pipedrive": PasteSpec(
        service="pipedrive",
        label="Pipedrive",
        fields=(
            PasteField(name="api_token", label="Personal API token",
                       type="password"),
            PasteField(name="company_domain", label="Company domain",
                       placeholder="acme-llc"),
            PasteField(name="from_email", label="Default sender email",
                       type="email", required=False,
                       placeholder="hello@yourbusiness.com"),
        ),
        instructions=(
            "In Pipedrive, click your avatar, then Personal Preferences, "
            "then API. Copy the personal API token. Your company domain is "
            "the subdomain in your Pipedrive URL (e.g. 'acme-llc' from "
            "acme-llc.pipedrive.com)."
        ),
        docs_url="https://pipedrive.readme.io/docs/how-to-find-the-api-token",
    ),
    "wordpress": PasteSpec(
        service="wordpress",
        label="WordPress",
        fields=(
            PasteField(name="site_url", label="Site URL",
                       placeholder="https://yourblog.com"),
            PasteField(name="username", label="WordPress username"),
            PasteField(name="application_password", label="Application Password",
                       type="password", placeholder="xxxx xxxx xxxx xxxx"),
        ),
        instructions=(
            "In WordPress, open Users > Profile, scroll to Application "
            "Passwords, create one labeled 'WCAS', and paste the generated "
            "value. We never see your real WordPress password."
        ),
        docs_url="https://make.wordpress.org/core/2020/11/05/application-passwords-integration-guide/",
    ),
}


# Human-readable list of what each provider unlocks. Used by the agent to
# explain "why am I asking for this" before showing the form.
UNLOCKS: dict[str, str] = {
    "gmail_app_password": "Email assistant drafts, inbox monitoring, and outbound replies.",
    "ghl": "CRM contact lookups, conversation history, and message sending.",
    "hubspot": "CRM contact lookups, deal stage tracking, and quote view detection.",
    "pipedrive": "CRM contact lookups, deal stage tracking, and notes-as-conversation history.",
    "airtable": "Schema-aware automation suggestions tied to the client's existing CRM.",
    "brightlocal": "Local-pack rank tracking that powers SEO recommendations.",
    "twilio_paste": "SMS dispatch on the client's own phone number for review requests.",
    "connecteam": "Guard compliance pulls (AP-only).",
    "vapi": "Voice agent answering after-hours calls.",
    "wordpress": "Blog post auto-publish on the client's WordPress site.",
}


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------


def supported_services() -> list[str]:
    """Every service with a paste spec defined."""
    return sorted(_SPECS.keys())


def get_spec(service: str) -> PasteSpec | None:
    return _SPECS.get(service)


def get_form_spec(service: str) -> dict[str, Any] | None:
    """JSON-serializable form spec for the dashboard UI. Returns None
    when the service has no spec (so callers can render a fallback)."""
    spec = _SPECS.get(service)
    if spec is None:
        return None
    out = spec.to_dict()
    out["unlocks"] = UNLOCKS.get(service, "")
    return out


def has_spec(service: str) -> bool:
    return service in _SPECS


__all__ = [
    "PasteField",
    "PasteSpec",
    "UNLOCKS",
    "get_form_spec",
    "get_spec",
    "has_spec",
    "supported_services",
]
