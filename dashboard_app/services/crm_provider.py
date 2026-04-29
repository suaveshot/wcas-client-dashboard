"""CRMProvider Protocol - the surface every CRM backend must implement.

This is the abstraction the W6 detect_crm layer hands back to the sales
pipeline / dashboard orchestrators. A single Protocol pinned in one place
prevents the 2026-04-23 regression where a partial provider class shipped
without the methods consumers were actually calling, silently
AttributeError'ing post-proposal touches for ten days. See
`memory/lessons/mistake_provider_abstraction_incomplete_method_surface.md`.

Adding a new method here is a deliberate, reviewed change. Implementations
must port the FULL surface before being wired to a consumer.

All methods take simple Python types and return raw vendor dicts. We
intentionally do NOT normalize - callers that read raw fields would
silently get empty strings if the provider quietly normalized for them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CRMProvider(Protocol):
    """Sales-pipeline-shaped CRM surface.

    Every supported backend (GHL, Pipedrive, HubSpot, ...) implements
    this Protocol. Consumers code against the Protocol, never the
    concrete class.
    """

    # ── Contact operations ────────────────────────────────────────

    def list_contacts(self, *, page_size: int = 100) -> list[dict[str, Any]]: ...

    def get_contact(self, contact_id: str) -> dict[str, Any]: ...

    def update_contact(self, contact_id: str, updates: dict[str, Any]) -> dict[str, Any]: ...

    # ── Conversations & messaging ─────────────────────────────────

    def search_conversations(self, contact_id: str) -> list[dict[str, Any]]: ...

    def get_conversation_messages(self, conversation_id: str) -> list[dict[str, Any]]: ...

    def get_full_conversation_history(self, contact_id: str) -> list[dict[str, Any]]: ...

    def send_email(
        self,
        contact_id: str,
        subject: str,
        html_body: str,
        *,
        attachment_urls: list[str] | None = None,
        scheduled_at: datetime | None = None,
    ) -> str: ...

    def send_sms(
        self,
        contact_id: str,
        message: str,
        *,
        scheduled_at: datetime | None = None,
    ) -> str: ...

    def cancel_scheduled_message(self, message_id: str) -> bool: ...

    # ── Estimates / proposals ─────────────────────────────────────

    def has_viewed_estimate(self, estimate_id: str) -> dict[str, Any]: ...

    # ── Opportunities / pipeline ──────────────────────────────────

    def search_opportunities(
        self,
        pipeline_id: str,
        *,
        stage_id: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def update_opportunity_stage(self, opportunity_id: str, stage_id: str) -> None: ...


__all__ = ["CRMProvider"]
