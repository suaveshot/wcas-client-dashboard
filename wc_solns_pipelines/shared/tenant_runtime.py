"""TenantContext - the single object every pipeline holds onto.

A pipeline's `run.py` looks like:

    from wc_solns_pipelines.shared.tenant_runtime import TenantContext

    def main(tenant_id: str) -> int:
        ctx = TenantContext(tenant_id)
        if ctx.is_paused:
            return 0  # tenant_config.json:status=paused honored at the top
        voice = ctx.kb("voice")
        creds = ctx.credentials("google")
        ...

This module is a thin facade over `dashboard_app.services` so the pipeline
side never reaches into the dashboard's internals directly. When the
service surface inside dashboard_app changes, only this module updates
and every pipeline keeps working.

See DECISIONS.md ADR-030 for why pipelines live as a sibling package
rather than a separate repo.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dashboard_app.services import (
    credentials as _credentials,
    crm_mapping as _crm_mapping,
    dispatch as _dispatch,
    heartbeat_store as _heartbeat_store,
    tenant_kb as _tenant_kb,
    tenant_prefs as _tenant_prefs,
    voice_card as _voice_card,
)


class TenantNotFound(LookupError):
    """The tenant slug doesn't pass the safe-slug regex used by the dashboard."""


class TenantContext:
    """Per-tenant runtime context for a pipeline run.

    Construction validates the tenant_id via heartbeat_store.tenant_root,
    which enforces the same `[a-z0-9_-]+` slug rule the dashboard uses.
    Invalid slugs raise TenantNotFound.
    """

    def __init__(self, tenant_id: str) -> None:
        try:
            self._root = _heartbeat_store.tenant_root(tenant_id)
        except _heartbeat_store.HeartbeatError as exc:
            raise TenantNotFound(str(exc)) from exc
        self.tenant_id = tenant_id

    # ------------------------------------------------------------------
    # paths + raw config
    # ------------------------------------------------------------------

    @property
    def root(self) -> Path:
        """/opt/wc-solns/<tenant_id>/. Always rooted via TENANT_ROOT env."""
        return self._root

    def config(self) -> dict[str, Any]:
        """Read tenant_config.json. Returns {} if absent or unreadable."""
        path = self._root / "tenant_config.json"
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    @property
    def is_paused(self) -> bool:
        """True when the owner has hit Pause Every Role from /settings.
        Pipelines should short-circuit at the top of run() when this is True."""
        return _dispatch.is_paused(self.tenant_id)

    @property
    def prefs(self) -> dict[str, Any]:
        """Read prefs.json (with DEFAULTS merged in)."""
        return _tenant_prefs.read(self.tenant_id)

    def requires_approval(self, pipeline_id: str) -> bool:
        """Per-pipeline Approve-Before-Send pref. When True, the pipeline
        should call dispatch.send() (which routes to outgoing_queue) rather
        than emit directly."""
        return _dispatch.requires_approval(self.tenant_id, pipeline_id)

    # ------------------------------------------------------------------
    # credentials (Pattern A OAuth + Pattern B paste)
    # ------------------------------------------------------------------

    def credentials(self, provider: str) -> dict[str, Any] | None:
        """Read the stored credential record for a provider.
        Returns None if not connected. Tenant-scoped, chmod 600 on POSIX."""
        return _credentials.load(self.tenant_id, provider)

    def access_token(self, provider: str) -> str:
        """Get a live access token for the provider. Hits the in-process
        50-min cache; falls through to the vendor's refresh endpoint when
        cold. Raises CredentialError / ProviderExchangeError on failure."""
        return _credentials.access_token(self.tenant_id, provider)

    def has_scope(self, provider: str, required_scope: str) -> bool:
        """True when the stored credential record lists the scope. Useful
        for pipeline self-checks before making a scope-dependent API call."""
        return _credentials.has_scope(self.tenant_id, provider, required_scope)

    # ------------------------------------------------------------------
    # KB + voice + CRM mapping (the activation artifacts)
    # ------------------------------------------------------------------

    def kb(self, section: str) -> str | None:
        """Read a KB markdown section by name. Allowed sections enumerated
        in dashboard_app.services.tenant_kb.SECTIONS."""
        return _tenant_kb.read_section(self.tenant_id, section)

    def list_kb_sections(self) -> list[str]:
        return _tenant_kb.list_sections(self.tenant_id)

    def voice_card(self) -> dict[str, Any] | None:
        """Read voice_card.json. Returns None if no voice card persisted yet."""
        return _voice_card.load(self.tenant_id)

    def crm_mapping(self) -> dict[str, Any] | None:
        """Read crm_mapping.json. Returns None if no mapping persisted yet."""
        return _crm_mapping.load(self.tenant_id)

    # ------------------------------------------------------------------
    # per-pipeline state (one JSON file per pipeline per tenant)
    # ------------------------------------------------------------------

    def state_path(self, pipeline_id: str) -> Path:
        """/opt/wc-solns/<tenant_id>/pipeline_state/<pipeline_id>.json. Pipelines
        own this file; the dashboard does not read it."""
        return self._root / "pipeline_state" / f"{pipeline_id}.json"

    def read_state(self, pipeline_id: str) -> dict[str, Any]:
        """Return the pipeline's persisted state, or {} if no file."""
        path = self.state_path(pipeline_id)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def write_state(self, pipeline_id: str, data: dict[str, Any]) -> Path:
        """Atomically write the pipeline's state. tmp + os.replace."""
        path = self.state_path(pipeline_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(data)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, path)
        return path


__all__ = ["TenantContext", "TenantNotFound"]
