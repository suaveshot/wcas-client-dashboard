"""Tests for wc_solns_pipelines.pipelines.sales.run.

The pipeline accepts injectable callables (provider_fn, draft_message_fn,
dispatch_fn, heartbeat_fn) so we can run the full flow without hitting any
real CRM, Anthropic, or the heartbeat HTTP endpoint.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import crm_mapping as _crm_mapping
from wc_solns_pipelines.pipelines.sales import run as sales_run


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class _FakeCRMProvider:
    """Implements the full 11-method CRMProvider Protocol so isinstance()
    passes. Methods record calls for assertion or return canned data."""

    def __init__(
        self,
        *,
        contacts: list[dict[str, Any]] | None = None,
        conversations: dict[str, list[dict[str, Any]]] | None = None,
        messages: dict[str, list[dict[str, Any]]] | None = None,
        cancel_returns: bool = True,
    ) -> None:
        self._contacts = contacts or []
        self._conversations = conversations or {}
        self._messages = messages or {}
        self._cancel_returns = cancel_returns
        self.list_contacts_calls = 0
        self.cancelled: list[str] = []
        self.send_email_calls: list[tuple[str, str, str]] = []
        self.send_sms_calls: list[tuple[str, str]] = []

    def list_contacts(self, *, page_size: int = 100) -> list[dict[str, Any]]:
        self.list_contacts_calls += 1
        return list(self._contacts)

    def get_contact(self, contact_id: str) -> dict[str, Any]:
        for c in self._contacts:
            if c.get("id") == contact_id:
                return c
        return {}

    def update_contact(self, contact_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        return {"id": contact_id, **updates}

    def search_conversations(self, contact_id: str) -> list[dict[str, Any]]:
        return list(self._conversations.get(contact_id, []))

    def get_conversation_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        return list(self._messages.get(conversation_id, []))

    def get_full_conversation_history(self, contact_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for conv in self.search_conversations(contact_id):
            cid = conv.get("id") or ""
            out.extend(self._messages.get(cid, []))
        return out

    def send_email(
        self,
        contact_id: str,
        subject: str,
        html_body: str,
        *,
        attachment_urls: list[str] | None = None,
        scheduled_at: Any | None = None,
    ) -> str:
        self.send_email_calls.append((contact_id, subject, html_body))
        return f"email-{len(self.send_email_calls)}"

    def send_sms(
        self,
        contact_id: str,
        message: str,
        *,
        scheduled_at: Any | None = None,
    ) -> str:
        self.send_sms_calls.append((contact_id, message))
        return f"sms-{len(self.send_sms_calls)}"

    def cancel_scheduled_message(self, message_id: str) -> bool:
        self.cancelled.append(message_id)
        return self._cancel_returns

    def has_viewed_estimate(self, estimate_id: str) -> dict[str, Any]:
        return {"viewed": False}

    def search_opportunities(
        self,
        pipeline_id: str,
        *,
        stage_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return []

    def update_opportunity_stage(self, opportunity_id: str, stage_id: str) -> None:
        return None


class _Heartbeats(list):
    def __call__(self, **kwargs):
        self.append(kwargs)
        return 0


class _Dispatches(list):
    def __init__(self, default_action: str = "queued") -> None:
        super().__init__()
        self.default_action = default_action

    def __call__(self, tenant_id, contact, body, *, channel):
        self.append(
            {
                "tenant_id": tenant_id,
                "contact": contact,
                "body": body,
                "channel": channel,
            }
        )
        return {"action": self.default_action, "draft_id": f"draft-{len(self)}"}


def _stub_draft(text: str = "Hey there, quick hello.") -> Any:
    return lambda _ctx, _contact: text


# ---------------------------------------------------------------------------
# tenant scaffolding
# ---------------------------------------------------------------------------


def _seed_mapping(tenant_id: str, kind: str = "ghl") -> None:
    """Persist a minimal crm_mapping with a kind hint so the pipeline can
    resolve which provider factory to call."""
    payload = _crm_mapping.save(
        tenant_id,
        base_id="appFAKE",
        table_name="Leads",
        field_mapping={"first_name": "Name"},
        segments=[],
    )
    # Stamp a kind into the persisted file - the pipeline reads it via
    # _provider_kind which honors top-level "kind".
    from pathlib import Path
    from dashboard_app.services import heartbeat_store
    path = heartbeat_store.tenant_root(tenant_id) / "state_snapshot" / "crm_mapping.json"
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data["kind"] = kind
    Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")


@pytest.fixture
def tenant_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture
def acme_with_ghl(tenant_root):
    _seed_mapping("acme", kind="ghl")
    return tenant_root


# ---------------------------------------------------------------------------
# guard rails
# ---------------------------------------------------------------------------


def test_run_invalid_tenant_returns_error_heartbeat(tenant_root):
    hb = _Heartbeats()
    rc = sales_run.run(
        "../bad-slug",
        heartbeat_fn=hb,
        provider_fn=lambda _ctx: pytest.fail("should not build provider"),
    )
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "Invalid tenant" in hb[-1]["summary"]


def test_run_paused_tenant_short_circuits(tenant_root):
    tenant_dir = tenant_root / "acme"
    tenant_dir.mkdir(parents=True, exist_ok=True)
    (tenant_dir / "tenant_config.json").write_text(
        json.dumps({"status": "paused"}), encoding="utf-8"
    )
    _seed_mapping("acme")

    hb = _Heartbeats()
    rc = sales_run.run(
        "acme",
        heartbeat_fn=hb,
        provider_fn=lambda _ctx: pytest.fail("should not build provider when paused"),
    )
    assert rc == 0
    assert hb[-1]["status"] == "success"
    assert "Paused" in hb[-1]["summary"]


def test_run_no_crm_mapping_returns_error_heartbeat(tenant_root):
    hb = _Heartbeats()
    rc = sales_run.run(
        "acme",
        heartbeat_fn=hb,
        provider_fn=lambda _ctx: pytest.fail("should not build provider when unmapped"),
    )
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "CRM not configured" in hb[-1]["summary"]


def test_run_unsupported_kind_returns_error_heartbeat(tenant_root):
    _seed_mapping("acme", kind="zoho")
    hb = _Heartbeats()
    rc = sales_run.run(
        "acme",
        heartbeat_fn=hb,
        provider_fn=lambda _ctx: pytest.fail("should not build provider for unsupported kind"),
    )
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "Unsupported CRM kind" in hb[-1]["summary"]


def test_run_provider_factory_returns_none_emits_error(acme_with_ghl):
    hb = _Heartbeats()
    rc = sales_run.run(
        "acme",
        heartbeat_fn=hb,
        provider_fn=lambda _ctx: None,
    )
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "credentials missing" in hb[-1]["summary"].lower()


def test_run_provider_not_protocol_compliant_emits_error(acme_with_ghl):
    """Lesson: mistake_provider_abstraction_incomplete_method_surface.md.
    A partial provider missing required methods must fail at the top of
    run() instead of AttributeError'ing mid-tick."""

    class Stub:
        def list_contacts(self, *, page_size: int = 100):
            return []
        # Intentionally missing the other 10 Protocol methods.

    hb = _Heartbeats()
    rc = sales_run.run(
        "acme",
        heartbeat_fn=hb,
        provider_fn=lambda _ctx: Stub(),
    )
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "Protocol" in hb[-1]["summary"]


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


def test_run_drafts_and_dispatches_new_leads(acme_with_ghl):
    contacts = [
        {"id": "c-1", "firstName": "Alice", "email": "a@x.com", "phone": "+15551"},
        {"id": "c-2", "firstName": "Bob", "email": "b@x.com"},
        {"id": "c-3", "firstName": "Cara", "phone": "+15553"},
    ]
    fake = _FakeCRMProvider(contacts=contacts)

    hb = _Heartbeats()
    dispatches = _Dispatches(default_action="queued")
    rc = sales_run.run(
        "acme",
        heartbeat_fn=hb,
        provider_fn=lambda _ctx: fake,
        draft_message_fn=_stub_draft("Hi friend, quick hello."),
        dispatch_fn=dispatches,
    )
    assert rc == 0
    # 3 contacts, all should get a draft
    assert len(dispatches) == 3
    assert {d["contact"]["id"] for d in dispatches} == {"c-1", "c-2", "c-3"}
    # Channel selection: c-3 has only phone => sms; others have email
    by_id = {d["contact"]["id"]: d["channel"] for d in dispatches}
    assert by_id["c-1"] == "sales_cold_email"
    assert by_id["c-2"] == "sales_cold_email"
    assert by_id["c-3"] == "sales_cold_sms"
    summary = hb[-1]["summary"]
    assert "Drafted 3 of 3" in summary
    assert "3 queued" in summary

    # State persists seen_contacts
    state_path = acme_with_ghl / "acme" / "pipeline_state" / "sales.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert set(state["seen_contacts"].keys()) == {"c-1", "c-2", "c-3"}
    for entry in state["seen_contacts"].values():
        assert entry["touched"] is True


def test_run_no_new_leads_returns_success_summary(acme_with_ghl):
    fake = _FakeCRMProvider(contacts=[])
    hb = _Heartbeats()
    dispatches = _Dispatches()
    rc = sales_run.run(
        "acme",
        heartbeat_fn=hb,
        provider_fn=lambda _ctx: fake,
        draft_message_fn=_stub_draft(),
        dispatch_fn=dispatches,
    )
    assert rc == 0
    assert dispatches == []
    assert hb[-1]["status"] == "success"
    assert "No new leads" in hb[-1]["summary"]


def test_run_skips_already_seen_contacts(acme_with_ghl):
    state_path = acme_with_ghl / "acme" / "pipeline_state" / "sales.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({
            "seen_contacts": {
                "c-1": {
                    "first_seen_at": "2026-04-01T00:00:00+00:00",
                    "touched": True,
                    "scheduled_message_ids": [],
                }
            }
        }),
        encoding="utf-8",
    )

    contacts = [
        {"id": "c-1", "firstName": "Alice", "email": "a@x.com"},
        {"id": "c-2", "firstName": "Bob", "email": "b@x.com"},
    ]
    fake = _FakeCRMProvider(contacts=contacts)
    hb = _Heartbeats()
    dispatches = _Dispatches()
    sales_run.run(
        "acme",
        heartbeat_fn=hb,
        provider_fn=lambda _ctx: fake,
        draft_message_fn=_stub_draft(),
        dispatch_fn=dispatches,
    )
    # Only c-2 should get a touch
    assert len(dispatches) == 1
    assert dispatches[0]["contact"]["id"] == "c-2"


# ---------------------------------------------------------------------------
# reply detection
# ---------------------------------------------------------------------------


def test_reply_detection_cancels_scheduled_messages(acme_with_ghl):
    """When a contact has scheduled messages and an inbound reply newer than
    last_scheduled_at exists, the pipeline cancels each scheduled id."""
    state_path = acme_with_ghl / "acme" / "pipeline_state" / "sales.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    scheduled_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    state_path.write_text(
        json.dumps({
            "seen_contacts": {
                "c-1": {
                    "first_seen_at": "2026-04-01T00:00:00+00:00",
                    "touched": True,
                    "scheduled_message_ids": ["sched-A", "sched-B"],
                    "last_scheduled_at": scheduled_at,
                }
            }
        }),
        encoding="utf-8",
    )

    reply_ts = datetime.now(timezone.utc).isoformat()
    fake = _FakeCRMProvider(
        contacts=[],  # no new leads, just the reply detection path
        conversations={"c-1": [{"id": "conv-1"}]},
        messages={
            "conv-1": [{"direction": "inbound", "dateAdded": reply_ts, "body": "hi"}]
        },
    )
    hb = _Heartbeats()
    dispatches = _Dispatches()
    rc = sales_run.run(
        "acme",
        heartbeat_fn=hb,
        provider_fn=lambda _ctx: fake,
        draft_message_fn=_stub_draft(),
        dispatch_fn=dispatches,
    )
    assert rc == 0
    assert sorted(fake.cancelled) == ["sched-A", "sched-B"]
    # State updated
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["seen_contacts"]["c-1"]["scheduled_message_ids"] == []
    assert state["seen_contacts"]["c-1"]["replied"] is True
    # Heartbeat reflects the cancel
    assert "2 cancelled" in hb[-1]["summary"]


def test_reply_detection_no_reply_no_cancel(acme_with_ghl):
    state_path = acme_with_ghl / "acme" / "pipeline_state" / "sales.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({
            "seen_contacts": {
                "c-1": {
                    "first_seen_at": "2026-04-01T00:00:00+00:00",
                    "touched": True,
                    "scheduled_message_ids": ["sched-A"],
                    "last_scheduled_at": datetime.now(timezone.utc).isoformat(),
                }
            }
        }),
        encoding="utf-8",
    )

    fake = _FakeCRMProvider(
        contacts=[],
        # outbound message only, no inbound reply
        conversations={"c-1": [{"id": "conv-1"}]},
        messages={"conv-1": [{"direction": "outbound", "dateAdded": "2026-04-29T00:00:00+00:00"}]},
    )
    hb = _Heartbeats()
    sales_run.run(
        "acme",
        heartbeat_fn=hb,
        provider_fn=lambda _ctx: fake,
        draft_message_fn=_stub_draft(),
        dispatch_fn=_Dispatches(),
    )
    assert fake.cancelled == []


# ---------------------------------------------------------------------------
# HubSpot tenant: SMS path is not exercised
# ---------------------------------------------------------------------------


def test_hubspot_tenant_skips_sms_path(tenant_root):
    """A HubSpot-mapped tenant should never trigger a sales_cold_sms branch
    even for contacts with only a phone number, because HubSpotProvider's
    send_sms raises HubSpotProviderError."""
    _seed_mapping("acme", kind="hubspot")

    contacts = [
        {"id": "c-email", "firstName": "Alice", "email": "a@x.com"},
        {"id": "c-phone-only", "firstName": "Cara", "phone": "+15553"},
    ]
    fake = _FakeCRMProvider(contacts=contacts)
    hb = _Heartbeats()
    dispatches = _Dispatches()
    sales_run.run(
        "acme",
        heartbeat_fn=hb,
        provider_fn=lambda _ctx: fake,
        draft_message_fn=_stub_draft(),
        dispatch_fn=dispatches,
    )
    # Only the email contact should be dispatched. phone-only contact is
    # skipped under HubSpot since there's no native SMS path.
    assert len(dispatches) == 1
    assert dispatches[0]["contact"]["id"] == "c-email"
    assert dispatches[0]["channel"] == "sales_cold_email"
    # Provider's send_sms must not have been called by the dispatcher path
    # we exercise (we use a dispatch fake, but the channel selection
    # already excluded it).
    assert fake.send_sms_calls == []
    summary = hb[-1]["summary"]
    assert "1 no-channel" in summary


# ---------------------------------------------------------------------------
# dispatch outcome handling
# ---------------------------------------------------------------------------


def test_run_counts_failed_dispatch(acme_with_ghl):
    contacts = [{"id": "c-1", "firstName": "Alice", "email": "a@x.com"}]
    fake = _FakeCRMProvider(contacts=contacts)
    hb = _Heartbeats()

    def failing_dispatch(*_args, **_kwargs):
        return {"action": "failed", "reason": "boom"}

    rc = sales_run.run(
        "acme",
        heartbeat_fn=hb,
        provider_fn=lambda _ctx: fake,
        draft_message_fn=_stub_draft(),
        dispatch_fn=failing_dispatch,
    )
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "1 failed" in hb[-1]["summary"]


def test_run_breaks_on_mid_run_pause(acme_with_ghl):
    contacts = [
        {"id": f"c-{i}", "firstName": f"User{i}", "email": f"u{i}@x.com"}
        for i in range(3)
    ]
    fake = _FakeCRMProvider(contacts=contacts)
    hb = _Heartbeats()
    seen: list[str] = []

    def dispatch_with_pause(tenant_id, contact, body, *, channel):
        seen.append(contact["id"])
        if len(seen) == 2:
            return {"action": "skipped", "reason": "tenant_paused"}
        return {"action": "queued", "draft_id": "x"}

    sales_run.run(
        "acme",
        heartbeat_fn=hb,
        provider_fn=lambda _ctx: fake,
        draft_message_fn=_stub_draft(),
        dispatch_fn=dispatch_with_pause,
    )
    # Should bail after the second dispatch returns skipped
    assert len(seen) == 2


# ---------------------------------------------------------------------------
# dry run
# ---------------------------------------------------------------------------


def test_dry_run_skips_dispatch_and_heartbeat(acme_with_ghl, capsys):
    contacts = [{"id": "c-1", "firstName": "Alice", "email": "a@x.com"}]
    fake = _FakeCRMProvider(contacts=contacts)

    def hb_fn(**_kwargs):
        pytest.fail("dry-run must not push heartbeat")

    def dispatch_fn(*_args, **_kwargs):
        pytest.fail("dry-run must not dispatch")

    rc = sales_run.run(
        "acme",
        dry_run=True,
        heartbeat_fn=hb_fn,
        provider_fn=lambda _ctx: fake,
        draft_message_fn=_stub_draft("Test cold draft"),
        dispatch_fn=dispatch_fn,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "c-1" in out
    assert "Test cold draft" in out


# ---------------------------------------------------------------------------
# scoring helper
# ---------------------------------------------------------------------------


def test_score_contact_prefers_dual_channel():
    dual = {"id": "x", "email": "a@x.com", "phone": "+1"}
    email_only = {"id": "y", "email": "a@x.com"}
    bare = {"id": "z"}
    assert sales_run.score_contact(dual) > sales_run.score_contact(email_only)
    assert sales_run.score_contact(email_only) > sales_run.score_contact(bare)


def test_score_contact_recent_add_bonus():
    recent = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    fresh = {"id": "x", "email": "a@x.com", "dateAdded": recent}
    stale = {"id": "y", "email": "a@x.com", "dateAdded": old}
    assert sales_run.score_contact(fresh) > sales_run.score_contact(stale)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_passes_args_through(monkeypatch):
    received: dict = {}

    def fake_run(**kwargs):
        received.update(kwargs)
        return 0

    monkeypatch.setattr(sales_run, "run", fake_run)
    rc = sales_run.main(["--tenant", "acme", "--max", "9", "--dry-run"])
    assert rc == 0
    assert received["tenant_id"] == "acme"
    assert received["max_drafts"] == 9
    assert received["dry_run"] is True


# ---------------------------------------------------------------------------
# max_drafts cap with score-based ordering
# ---------------------------------------------------------------------------


def test_max_drafts_caps_drafted_with_score_priority(acme_with_ghl):
    contacts = [
        # No channel - skipped entirely
        {"id": "c-bare"},
        # Email only, score 1
        {"id": "c-email", "firstName": "E", "email": "e@x.com"},
        # Dual channel, score 2
        {"id": "c-dual", "firstName": "D", "email": "d@x.com", "phone": "+1"},
    ]
    fake = _FakeCRMProvider(contacts=contacts)
    hb = _Heartbeats()
    dispatches = _Dispatches()
    sales_run.run(
        "acme",
        max_drafts=1,
        heartbeat_fn=hb,
        provider_fn=lambda _ctx: fake,
        draft_message_fn=_stub_draft(),
        dispatch_fn=dispatches,
    )
    # Only one draft should fire, and it must be the dual-channel contact
    # (highest score).
    assert len(dispatches) == 1
    assert dispatches[0]["contact"]["id"] == "c-dual"
