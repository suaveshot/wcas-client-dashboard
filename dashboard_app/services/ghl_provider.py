"""GHLProvider - GoHighLevel implementation of CRMProvider.

Mirrors AP's `sales_pipeline.ghl_client.GHLClient` method-by-method so
the dashboard can wire any consumer that today talks to AP's client
without losing a single call site. See
`memory/lessons/mistake_provider_abstraction_incomplete_method_surface.md`
for the regression this is built to prevent.

Construction is credential-injectable so the same class works for any
tenant whose GHL API key is stored under
`/opt/wc-solns/<tenant>/credentials/ghl.json`. Use `for_tenant(tenant_id)`
to load creds + build a provider in one step.

Return shapes are RAW GHL dicts on purpose. The Protocol forbids
normalization so post-proposal consumers that read `customField`,
`firstName`, etc. keep working. New consumers should reach for the
typed accessors as those land.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import requests

from . import credentials as _credentials

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://services.leadconnectorhq.com"
DEFAULT_API_VERSION = "2021-07-28"
_RATE_LIMIT_BACKOFF_SECONDS = 5
_DEFAULT_TIMEOUT = 30


class GHLProviderError(RuntimeError):
    """Raised when GHL returns a non-recoverable HTTP error."""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"GHL {status_code}: {body[:300]}")


class GHLProvider:
    """GoHighLevel CRM provider. Implements `CRMProvider`."""

    def __init__(
        self,
        api_key: str,
        location_id: str,
        *,
        from_email: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        api_version: str = DEFAULT_API_VERSION,
        session: Any | None = None,
    ):
        if not api_key or not isinstance(api_key, str):
            raise ValueError("api_key required")
        if not location_id or not isinstance(location_id, str):
            raise ValueError("location_id required")
        self._api_key = api_key
        self._location_id = location_id
        self._from_email = from_email
        self._base = base_url.rstrip("/")
        self._api_version = api_version
        # Tests inject a fake "session" with .request(method, url, **kwargs).
        self._session = session if session is not None else requests

    # ── HTTP helpers ──────────────────────────────────────────────

    def _headers(self, *, version: str | None = None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Version": version or self._api_version,
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
        version: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base}{path}"
        kwargs: dict[str, Any] = {
            "headers": self._headers(version=version),
            "timeout": _DEFAULT_TIMEOUT,
        }
        if params is not None:
            kwargs["params"] = params
        if json_body is not None:
            kwargs["json"] = json_body
        r = self._session.request(method, url, **kwargs)
        if r.status_code == 429:
            log.warning("GHL rate limited on %s %s; sleeping %ss",
                        method, path, _RATE_LIMIT_BACKOFF_SECONDS)
            time.sleep(_RATE_LIMIT_BACKOFF_SECONDS)
            r = self._session.request(method, url, **kwargs)
        if r.status_code >= 400:
            raise GHLProviderError(r.status_code, getattr(r, "text", ""))
        if not getattr(r, "text", "") and method.upper() == "DELETE":
            return {}
        try:
            return r.json()
        except ValueError:
            return {}

    # ── Contact operations ────────────────────────────────────────

    def list_contacts(self, *, page_size: int = 100) -> list[dict[str, Any]]:
        contacts: list[dict[str, Any]] = []
        params: dict[str, Any] = {
            "locationId": self._location_id,
            "limit": page_size,
        }
        # Bound the cursor walk so a malformed meta block can't loop forever.
        for _ in range(1000):
            data = self._request("GET", "/contacts/", params=params)
            batch = data.get("contacts", []) or []
            contacts.extend(batch)
            if not batch:
                break
            meta = data.get("meta") or {}
            next_after = meta.get("startAfter")
            next_id = meta.get("startAfterId")
            if next_after is not None and next_id:
                params["startAfter"] = int(next_after)
                params["startAfterId"] = next_id
                continue
            break
        return contacts

    def get_contact(self, contact_id: str) -> dict[str, Any]:
        data = self._request("GET", f"/contacts/{contact_id}")
        return data.get("contact", data)

    def update_contact(
        self,
        contact_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request("PUT", f"/contacts/{contact_id}", json_body=updates)

    # ── Conversation & messaging ──────────────────────────────────

    def search_conversations(self, contact_id: str) -> list[dict[str, Any]]:
        params = {
            "locationId": self._location_id,
            "contactId": contact_id,
        }
        data = self._request("GET", "/conversations/search", params=params)
        return data.get("conversations", []) or []

    def get_conversation_messages(
        self,
        conversation_id: str,
    ) -> list[dict[str, Any]]:
        data = self._request("GET", f"/conversations/{conversation_id}/messages")
        msgs = data.get("messages", {})
        if isinstance(msgs, dict):
            return msgs.get("messages", []) or []
        return msgs or []

    def get_full_conversation_history(
        self,
        contact_id: str,
    ) -> list[dict[str, Any]]:
        all_msgs: list[dict[str, Any]] = []
        for conv in self.search_conversations(contact_id):
            cid = conv.get("id")
            if not cid:
                continue
            for msg in self.get_conversation_messages(cid):
                all_msgs.append({
                    "timestamp": msg.get("dateAdded", ""),
                    "direction": msg.get("direction", ""),
                    "type": msg.get("type", ""),
                    "subject": msg.get("subject", ""),
                    "body": msg.get("body", msg.get("message", "")),
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
        if not self._from_email:
            raise GHLProviderError(
                0,
                "from_email not configured on GHLProvider; pass at __init__",
            )
        body: dict[str, Any] = {
            "type": "Email",
            "contactId": contact_id,
            "subject": subject,
            "html": html_body,
            "emailFrom": self._from_email,
        }
        if attachment_urls:
            body["attachments"] = attachment_urls
        if scheduled_at:
            body["scheduledTimestamp"] = int(scheduled_at.timestamp())
        data = self._request("POST", "/conversations/messages", json_body=body)
        return data.get("messageId", data.get("id", ""))

    def send_sms(
        self,
        contact_id: str,
        message: str,
        *,
        scheduled_at: datetime | None = None,
    ) -> str:
        body: dict[str, Any] = {
            "type": "SMS",
            "contactId": contact_id,
            "message": message,
        }
        if scheduled_at:
            body["scheduledTimestamp"] = int(scheduled_at.timestamp())
        data = self._request("POST", "/conversations/messages", json_body=body)
        return data.get("messageId", data.get("id", ""))

    def cancel_scheduled_message(self, message_id: str) -> bool:
        try:
            self._request(
                "DELETE",
                f"/conversations/messages/{message_id}/schedule",
            )
            return True
        except GHLProviderError as exc:
            log.warning("cancel_scheduled_message %s failed: %s", message_id, exc)
            return False

    # ── Estimates ─────────────────────────────────────────────────

    def has_viewed_estimate(self, estimate_id: str) -> dict[str, Any]:
        try:
            data = self._request(
                "GET",
                f"/invoices/estimate/{estimate_id}",
                params={"altId": self._location_id, "altType": "location"},
                version="2021-07-28",
            )
        except GHLProviderError:
            return {"viewed": False, "viewed_at": None, "status": "unknown"}
        estimate = data.get("estimate", data)
        status = estimate.get("status", "")
        viewed = status in ("viewed", "accepted")
        return {
            "viewed": viewed,
            "viewed_at": estimate.get("updatedAt") if viewed else None,
            "status": status,
        }

    # ── Opportunities ─────────────────────────────────────────────

    def search_opportunities(
        self,
        pipeline_id: str,
        *,
        stage_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "location_id": self._location_id,
            "pipeline_id": pipeline_id,
        }
        if stage_id:
            params["pipeline_stage_id"] = stage_id
        data = self._request("GET", "/opportunities/search", params=params)
        return data.get("opportunities", []) or []

    def update_opportunity_stage(
        self,
        opportunity_id: str,
        stage_id: str,
    ) -> None:
        self._request(
            "PUT",
            f"/opportunities/{opportunity_id}",
            json_body={"pipelineStageId": stage_id},
        )


def for_tenant(tenant_id: str) -> GHLProvider | None:
    """Build a `GHLProvider` from a tenant's stored GHL credentials.

    Returns None when the tenant hasn't connected GHL yet (the dashboard
    detect_crm layer treats this as "no GHL configured" and falls back
    to a different provider or queues a connect prompt).
    """
    creds = _credentials.load(tenant_id, "ghl")
    if not creds:
        return None
    api_key = creds.get("api_key")
    location_id = creds.get("location_id")
    if not api_key or not location_id:
        return None
    return GHLProvider(
        api_key=api_key,
        location_id=location_id,
        from_email=creds.get("from_email"),
    )


__all__ = [
    "DEFAULT_API_VERSION",
    "DEFAULT_BASE_URL",
    "GHLProvider",
    "GHLProviderError",
    "for_tenant",
]
