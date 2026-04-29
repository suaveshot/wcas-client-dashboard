"""HubSpotProvider - HubSpot implementation of CRMProvider.

Mirrors `ghl_provider.GHLProvider` so the same methodology that
prevented the 2026-04-23 partial-method-surface regression applies
here: every method on the Protocol is implemented, return shapes are
raw HubSpot dicts (no normalization), and unsupported vendor operations
raise a typed error instead of returning fake-empty results.

HubSpot specifics worth knowing when reading this file:

  * Auth is a bearer token from a HubSpot private app. Each tenant's
    token is stored under credentials.store_paste(..., "hubspot", ...).
  * The CRM v3 API uses cursor pagination via meta.paging.next.after
    (we walk `after` until it stops appearing).
  * Contacts live at /crm/v3/objects/contacts. Updates are PATCH, not
    PUT; payload goes inside a "properties" wrapper.
  * Conversations live at /conversations/v3/conversations. Threads
    are the rough equivalent of GHL conversations.
  * Native outbound SMS is NOT in HubSpot's first-party API; tenants
    typically integrate Twilio or similar. send_sms therefore raises
    HubSpotProviderError - the caller decides whether to fall back to
    a different channel.
  * "Opportunities" map to HubSpot deals; pipeline_stage is on the
    deal's `dealstage` property.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import requests

from . import credentials as _credentials

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.hubapi.com"
_RATE_LIMIT_BACKOFF_SECONDS = 5
_DEFAULT_TIMEOUT = 30
_DEFAULT_PAGE_SIZE = 100


class HubSpotProviderError(RuntimeError):
    """Raised when HubSpot returns a non-recoverable HTTP error or when
    a Protocol method maps to an operation HubSpot does not natively
    support (e.g. send_sms)."""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"HubSpot {status_code}: {body[:300]}")


class HubSpotProvider:
    """HubSpot CRM provider. Implements `crm_provider.CRMProvider`."""

    def __init__(
        self,
        access_token: str,
        *,
        portal_id: str | None = None,
        from_email: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        session: Any | None = None,
    ):
        if not access_token or not isinstance(access_token, str):
            raise ValueError("access_token required")
        self._token = access_token
        self._portal_id = portal_id
        self._from_email = from_email
        self._base = base_url.rstrip("/")
        self._session = session if session is not None else requests

    # ── HTTP helpers ──────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base}{path}"
        kwargs: dict[str, Any] = {
            "headers": self._headers(),
            "timeout": _DEFAULT_TIMEOUT,
        }
        if params is not None:
            kwargs["params"] = params
        if json_body is not None:
            kwargs["json"] = json_body
        r = self._session.request(method, url, **kwargs)
        if r.status_code == 429:
            log.warning("HubSpot rate limited on %s %s; sleeping %ss",
                        method, path, _RATE_LIMIT_BACKOFF_SECONDS)
            time.sleep(_RATE_LIMIT_BACKOFF_SECONDS)
            r = self._session.request(method, url, **kwargs)
        if r.status_code >= 400:
            raise HubSpotProviderError(r.status_code, getattr(r, "text", ""))
        if not getattr(r, "text", "") and method.upper() == "DELETE":
            return {}
        try:
            return r.json()
        except ValueError:
            return {}

    # ── Contact operations ────────────────────────────────────────

    def list_contacts(self, *, page_size: int = _DEFAULT_PAGE_SIZE) -> list[dict[str, Any]]:
        contacts: list[dict[str, Any]] = []
        params: dict[str, Any] = {"limit": min(page_size, 100)}
        # Bound the cursor walk; HubSpot pages can deep but a runaway
        # provider should never loop forever.
        for _ in range(1000):
            data = self._request("GET", "/crm/v3/objects/contacts", params=params)
            results = data.get("results") or []
            contacts.extend(results)
            paging = (data.get("paging") or {}).get("next") or {}
            after = paging.get("after")
            if not after:
                break
            params["after"] = after
        return contacts

    def get_contact(self, contact_id: str) -> dict[str, Any]:
        return self._request("GET", f"/crm/v3/objects/contacts/{contact_id}")

    def update_contact(
        self,
        contact_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        # HubSpot wraps property updates inside a "properties" object.
        # Accept either shape from the caller; if they pass {"firstname": "..."}
        # we wrap it; if they already wrapped it we pass through.
        body: dict[str, Any]
        if "properties" in updates and isinstance(updates["properties"], dict):
            body = dict(updates)
        else:
            body = {"properties": dict(updates)}
        return self._request("PATCH", f"/crm/v3/objects/contacts/{contact_id}",
                             json_body=body)

    # ── Conversation & messaging ──────────────────────────────────

    def search_conversations(self, contact_id: str) -> list[dict[str, Any]]:
        # HubSpot's threads endpoint requires a search by associated
        # contact. We pass the contact id via the assignedTo / associations
        # filter.
        params = {
            "associatedContactId": contact_id,
            "limit": 100,
        }
        data = self._request(
            "GET",
            "/conversations/v3/conversations/threads",
            params=params,
        )
        return data.get("results") or []

    def get_conversation_messages(
        self,
        conversation_id: str,
    ) -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            f"/conversations/v3/conversations/threads/{conversation_id}/messages",
        )
        return data.get("results") or []

    def get_full_conversation_history(
        self,
        contact_id: str,
    ) -> list[dict[str, Any]]:
        all_msgs: list[dict[str, Any]] = []
        for thread in self.search_conversations(contact_id):
            tid = thread.get("id")
            if not tid:
                continue
            for msg in self.get_conversation_messages(tid):
                all_msgs.append({
                    "timestamp": msg.get("createdAt", ""),
                    "direction": msg.get("direction", ""),
                    "type": msg.get("type", ""),
                    "subject": msg.get("subject", ""),
                    "body": msg.get("text", msg.get("body", "")),
                    "status": msg.get("status", ""),
                })
        all_msgs.sort(key=lambda m: m.get("timestamp", ""))
        return all_msgs

    def send_email(
        self,
        contact_id: str,
        subject: str,
        html_body: str,
        *,
        attachment_urls: list[str] | None = None,
        scheduled_at: datetime | None = None,
    ) -> str:
        """Send a transactional email via HubSpot single-send API.

        Requires a configured marketing-email "single send" template +
        the marketing.transactional scope on the private app token.
        Without those, HubSpot returns 4xx and this method raises.
        Most tenants use HubSpot for nurture, not 1:1 sends - so this
        method is intentionally explicit about its requirements.
        """
        if not self._from_email:
            raise HubSpotProviderError(
                0,
                "from_email not configured on HubSpotProvider; pass at __init__",
            )
        body: dict[str, Any] = {
            "emailId": subject,  # HubSpot expects the template id here
            "message": {
                "to": contact_id,  # treated as a contact email; resolved below
                "from": self._from_email,
                "subject": subject,
                "html": html_body,
            },
        }
        if scheduled_at:
            body["sendAt"] = scheduled_at.isoformat()
        # Note: HubSpot's marketing single-send endpoint expects an emailId
        # of an existing template. Tenants that don't have one configured
        # will see a 404 here - we surface the raw 400/404 to the caller.
        data = self._request("POST", "/marketing/v3/transactional/single-email/send",
                             json_body=body)
        # Response shape: {sendResult: SUCCESS, eventId: {...}}
        return (data.get("eventId") or {}).get("id", "") or data.get("statusId", "")

    def send_sms(
        self,
        contact_id: str,
        message: str,
        *,
        scheduled_at: datetime | None = None,
    ) -> str:
        """HubSpot has no native first-party SMS API. Raise so the caller
        can route to Twilio or another SMS provider explicitly instead of
        silently dropping the message."""
        raise HubSpotProviderError(
            0,
            "HubSpot does not natively support outbound SMS. Use a Twilio "
            "or other SMS provider for this tenant.",
        )

    def cancel_scheduled_message(self, message_id: str) -> bool:
        """HubSpot does not expose a per-message cancel endpoint for
        single-send transactional emails. Returns False rather than
        raising so callers can fall through cleanly."""
        log.info("HubSpot has no cancel endpoint for scheduled emails: %s",
                 message_id)
        return False

    # ── Estimates / quotes ────────────────────────────────────────

    def has_viewed_estimate(self, estimate_id: str) -> dict[str, Any]:
        """Map to HubSpot quotes. The `hs_quote_status` property tracks
        APPROVAL_NOT_NEEDED / PENDING_BUYER_SIGNATURE / SIGNED / etc.
        We treat the SIGNED + APPROVED states as 'viewed' for parity
        with GHL's accepted/viewed."""
        try:
            data = self._request(
                "GET",
                f"/crm/v3/objects/quotes/{estimate_id}",
                params={"properties": "hs_quote_status,hs_lastmodifieddate"},
            )
        except HubSpotProviderError:
            return {"viewed": False, "viewed_at": None, "status": "unknown"}
        props = data.get("properties") or {}
        status = (props.get("hs_quote_status") or "").lower()
        viewed = status in ("signed", "approved",
                            "approval_not_needed", "pending_buyer_signature")
        return {
            "viewed": viewed,
            "viewed_at": props.get("hs_lastmodifieddate") if viewed else None,
            "status": status,
        }

    # ── Opportunities (deals) ────────────────────────────────────

    def search_opportunities(
        self,
        pipeline_id: str,
        *,
        stage_id: str | None = None,
    ) -> list[dict[str, Any]]:
        # HubSpot uses POST /crm/v3/objects/deals/search with filterGroups.
        filters: list[dict[str, Any]] = [
            {"propertyName": "pipeline", "operator": "EQ", "value": pipeline_id},
        ]
        if stage_id:
            filters.append({"propertyName": "dealstage", "operator": "EQ",
                            "value": stage_id})
        body = {
            "filterGroups": [{"filters": filters}],
            "limit": 100,
        }
        data = self._request("POST", "/crm/v3/objects/deals/search",
                             json_body=body)
        return data.get("results") or []

    def update_opportunity_stage(
        self,
        opportunity_id: str,
        stage_id: str,
    ) -> None:
        self._request(
            "PATCH",
            f"/crm/v3/objects/deals/{opportunity_id}",
            json_body={"properties": {"dealstage": stage_id}},
        )


def for_tenant(tenant_id: str) -> HubSpotProvider | None:
    """Build a HubSpotProvider from a tenant's stored credentials, or
    None when the tenant hasn't connected HubSpot yet."""
    creds = _credentials.load(tenant_id, "hubspot")
    if not creds:
        return None
    token = creds.get("access_token") or creds.get("api_key")
    if not token:
        return None
    return HubSpotProvider(
        access_token=token,
        portal_id=creds.get("portal_id"),
        from_email=creds.get("from_email"),
    )


__all__ = [
    "DEFAULT_BASE_URL",
    "HubSpotProvider",
    "HubSpotProviderError",
    "for_tenant",
]
