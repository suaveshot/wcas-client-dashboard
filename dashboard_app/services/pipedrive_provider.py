"""PipedriveProvider - Pipedrive implementation of CRMProvider.

Mirrors GHLProvider + HubSpotProvider so the same incomplete-method-
surface lesson stays guarded across every CRM. Returns raw Pipedrive
dicts, no normalization.

Pipedrive specifics:

  * Auth is a per-user API token passed as the `api_token` query
    parameter on every request - NOT a bearer header. We inject it
    once in `_request` so handler bodies don't have to think about it.
  * Each company has its own subdomain: `https://<company>.pipedrive.com`.
    Tenants paste both the api_token and the company subdomain.
  * Pagination is cursor-style on the v2 endpoints (`cursor` / `next_cursor`)
    and offset-style on v1. We use v2 wherever available.
  * Native outbound SMS is NOT in Pipedrive's first-party API; same
    raise-rather-than-pretend pattern as HubSpotProvider.send_sms.
  * "Opportunities" = Pipedrive deals; pipeline filtering uses
    `pipeline_id` (int).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import requests

from . import credentials as _credentials

log = logging.getLogger(__name__)

DEFAULT_BASE_URL_TEMPLATE = "https://{company}.pipedrive.com"
_RATE_LIMIT_BACKOFF_SECONDS = 5
_DEFAULT_TIMEOUT = 30
_DEFAULT_PAGE_LIMIT = 100


class PipedriveProviderError(RuntimeError):
    """Raised on non-recoverable Pipedrive HTTP errors or vendor-unsupported
    operations (e.g. send_sms)."""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Pipedrive {status_code}: {body[:300]}")


class PipedriveProvider:
    """Pipedrive CRM provider. Implements `crm_provider.CRMProvider`."""

    def __init__(
        self,
        api_token: str,
        company_domain: str,
        *,
        from_email: str | None = None,
        base_url: str | None = None,
        session: Any | None = None,
    ):
        if not api_token or not isinstance(api_token, str):
            raise ValueError("api_token required")
        if not company_domain or not isinstance(company_domain, str):
            raise ValueError("company_domain required")
        self._token = api_token
        self._company = company_domain
        self._from_email = from_email
        self._base = (base_url or DEFAULT_BASE_URL_TEMPLATE.format(
            company=company_domain
        )).rstrip("/")
        self._session = session if session is not None else requests

    # ── HTTP helpers ──────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base}{path}"
        merged_params = dict(params or {})
        merged_params["api_token"] = self._token
        kwargs: dict[str, Any] = {
            "params": merged_params,
            "timeout": _DEFAULT_TIMEOUT,
            "headers": {"Accept": "application/json"},
        }
        if json_body is not None:
            kwargs["json"] = json_body
            kwargs["headers"]["Content-Type"] = "application/json"
        r = self._session.request(method, url, **kwargs)
        if r.status_code == 429:
            log.warning("Pipedrive rate limited on %s %s; sleeping %ss",
                        method, path, _RATE_LIMIT_BACKOFF_SECONDS)
            time.sleep(_RATE_LIMIT_BACKOFF_SECONDS)
            r = self._session.request(method, url, **kwargs)
        if r.status_code >= 400:
            raise PipedriveProviderError(r.status_code, getattr(r, "text", ""))
        if not getattr(r, "text", "") and method.upper() == "DELETE":
            return {}
        try:
            return r.json()
        except ValueError:
            return {}

    # ── Contact (person) operations ──────────────────────────────

    def list_contacts(self, *, page_size: int = _DEFAULT_PAGE_LIMIT) -> list[dict[str, Any]]:
        contacts: list[dict[str, Any]] = []
        params: dict[str, Any] = {"limit": min(page_size, 500)}
        for _ in range(1000):
            data = self._request("GET", "/api/v2/persons", params=params)
            results = data.get("data") or []
            contacts.extend(results)
            additional = data.get("additional_data") or {}
            cursor = additional.get("next_cursor") or additional.get("nextCursor")
            if not cursor:
                break
            params["cursor"] = cursor
        return contacts

    def get_contact(self, contact_id: str) -> dict[str, Any]:
        data = self._request("GET", f"/api/v2/persons/{contact_id}")
        return data.get("data") or data

    def update_contact(
        self,
        contact_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        # Pipedrive v2 PATCH; the body is the bare property dict.
        data = self._request("PATCH", f"/api/v2/persons/{contact_id}",
                             json_body=dict(updates))
        return data.get("data") or data

    # ── Conversation & messaging ─────────────────────────────────

    def search_conversations(self, contact_id: str) -> list[dict[str, Any]]:
        # Pipedrive's "messaging app" lives behind /api/v1/mailbox/mailMessages
        # and /api/v1/notes. The closest analog to a CRM conversation thread
        # is the contact's notes + mail messages associated with them.
        data = self._request(
            "GET",
            "/api/v1/notes",
            params={"person_id": contact_id, "limit": 100, "sort": "add_time DESC"},
        )
        return data.get("data") or []

    def get_conversation_messages(
        self,
        conversation_id: str,
    ) -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            f"/api/v1/notes/{conversation_id}",
        )
        note = data.get("data") or {}
        # A Pipedrive note IS a single message; wrap so the caller gets a
        # consistent list shape.
        return [note] if note else []

    def get_full_conversation_history(
        self,
        contact_id: str,
    ) -> list[dict[str, Any]]:
        all_msgs: list[dict[str, Any]] = []
        for note in self.search_conversations(contact_id):
            all_msgs.append({
                "timestamp": note.get("add_time", "") or note.get("update_time", ""),
                "direction": "outgoing" if note.get("user_id") else "",
                "type": "note",
                "subject": "",
                "body": note.get("content", ""),
                "status": "",
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
        """Pipedrive's mail integration sends through the user's connected
        Gmail/Outlook account; there's no programmatic single-send API
        for transactional outbound. The closest first-party endpoint is
        creating a note that the Pipedrive UI will later surface to the
        user. Most senders should use SMTP/Gmail directly and link the
        message back via Pipedrive's smart-bcc address instead.

        We raise so callers can route correctly rather than silently
        creating a note that never gets sent."""
        raise PipedriveProviderError(
            0,
            "Pipedrive does not expose a programmatic outbound email API. "
            "Send via Gmail/Outlook and use Pipedrive's smart-bcc address "
            "to log the conversation back to the contact.",
        )

    def send_sms(
        self,
        contact_id: str,
        message: str,
        *,
        scheduled_at: datetime | None = None,
    ) -> str:
        raise PipedriveProviderError(
            0,
            "Pipedrive does not natively support outbound SMS. Route via "
            "Twilio (or another SMS provider) for this tenant.",
        )

    def cancel_scheduled_message(self, message_id: str) -> bool:
        log.info("Pipedrive has no cancel endpoint for scheduled messages: %s",
                 message_id)
        return False

    # ── Estimates - Pipedrive does not have a native concept ─────

    def has_viewed_estimate(self, estimate_id: str) -> dict[str, Any]:
        """Pipedrive doesn't have a built-in estimate/quote object. The
        common pattern is a deal moving to a "Proposal Sent" stage. We
        therefore check the deal's stage history for that signal.

        Callers who need true estimate tracking should use a different
        provider or wire HubSpot/GHL alongside Pipedrive for quotes.
        """
        try:
            data = self._request(
                "GET",
                f"/api/v2/deals/{estimate_id}",
            )
        except PipedriveProviderError:
            return {"viewed": False, "viewed_at": None, "status": "unknown"}
        deal = data.get("data") or {}
        stage_name = (deal.get("stage_name") or "").lower()
        viewed = "won" in stage_name or "proposal" in stage_name
        return {
            "viewed": viewed,
            "viewed_at": deal.get("update_time") if viewed else None,
            "status": stage_name or "unknown",
        }

    # ── Opportunities (deals) ────────────────────────────────────

    def search_opportunities(
        self,
        pipeline_id: str,
        *,
        stage_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "pipeline_id": pipeline_id,
            "limit": 100,
        }
        if stage_id:
            params["stage_id"] = stage_id
        data = self._request("GET", "/api/v2/deals", params=params)
        return data.get("data") or []

    def update_opportunity_stage(
        self,
        opportunity_id: str,
        stage_id: str,
    ) -> None:
        self._request(
            "PATCH",
            f"/api/v2/deals/{opportunity_id}",
            json_body={"stage_id": stage_id},
        )


def for_tenant(tenant_id: str) -> PipedriveProvider | None:
    """Build a PipedriveProvider from stored credentials, or None when
    the tenant hasn't connected Pipedrive yet."""
    creds = _credentials.load(tenant_id, "pipedrive")
    if not creds:
        return None
    token = creds.get("api_token") or creds.get("api_key")
    company = creds.get("company_domain")
    if not token or not company:
        return None
    return PipedriveProvider(
        api_token=token,
        company_domain=company,
        from_email=creds.get("from_email"),
    )


__all__ = [
    "DEFAULT_BASE_URL_TEMPLATE",
    "PipedriveProvider",
    "PipedriveProviderError",
    "for_tenant",
]
