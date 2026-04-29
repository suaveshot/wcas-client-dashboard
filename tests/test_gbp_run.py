"""Tests for wc_solns_pipelines.pipelines.gbp.run.

Mirrors the style of test_reviews_run: injectable callables drive the
flow without touching live GBP / Anthropic / heartbeat HTTP.
"""

from __future__ import annotations

import json
import os
from typing import Any

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import credentials as _credentials, tenant_kb, tenant_prefs as _tenant_prefs
from wc_solns_pipelines.pipelines.gbp import run as gbp_run

GBP_SCOPE = "https://www.googleapis.com/auth/business.manage"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _seed_google(tenant_id: str, scopes: list[str] | None = None) -> None:
    _credentials.store(
        tenant_id,
        "google",
        refresh_token="1//fake-refresh-token",
        scopes=scopes if scopes is not None else [GBP_SCOPE],
    )


class _Heartbeats(list):
    def __call__(self, **kwargs):
        self.append(kwargs)
        return 0


class _Dispatches(list):
    def __init__(self, default_action: str = "queued") -> None:
        super().__init__()
        self.default_action = default_action

    def __call__(self, tenant_id, topic, body, account_path, location_path):
        self.append(
            {
                "tenant_id": tenant_id,
                "topic": topic,
                "body": body,
                "account_path": account_path,
                "location_path": location_path,
            }
        )
        return {
            "action": self.default_action,
            "draft_id": f"draft-{len(self)}",
        }


def _stub_discover(account_path: str = "accounts/123", location_path: str = "locations/9") -> Any:
    return lambda _tok: (account_path, location_path)


def _stub_draft(text: str = "Hi! This week, etc.") -> Any:
    return lambda _ctx, _topic: text


# ---------------------------------------------------------------------------
# guard rails
# ---------------------------------------------------------------------------


def test_run_invalid_tenant_returns_error_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    hb = _Heartbeats()
    rc = gbp_run.run(
        "../bad",
        heartbeat_fn=hb,
        discover_location_fn=lambda *a, **k: pytest.fail("should not discover"),
    )
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "Invalid tenant" in hb[-1]["summary"]


def test_run_paused_tenant_short_circuits(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    tenant_dir = tmp_path / "acme"
    tenant_dir.mkdir(parents=True, exist_ok=True)
    (tenant_dir / "tenant_config.json").write_text(
        json.dumps({"status": "paused"}), encoding="utf-8"
    )
    _seed_google("acme")

    hb = _Heartbeats()
    rc = gbp_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=lambda *a, **k: pytest.fail("should not discover"),
    )
    assert rc == 0
    assert hb[-1]["status"] == "success"
    assert "Paused" in hb[-1]["summary"]


def test_run_missing_credentials_returns_error_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    hb = _Heartbeats()
    rc = gbp_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=lambda *a, **k: pytest.fail("should not discover"),
    )
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "Google account not connected" in hb[-1]["summary"]


def test_run_missing_scope_returns_error_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_google("acme", scopes=["openid"])
    hb = _Heartbeats()
    rc = gbp_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=lambda *a, **k: pytest.fail("should not discover"),
    )
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "business.manage" in hb[-1]["summary"]


def test_run_token_refresh_failure_returns_error_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_google("acme")

    def boom(_t, _p):
        raise RuntimeError("refresh denied")

    monkeypatch.setattr("wc_solns_pipelines.shared.tenant_runtime._credentials.access_token", boom)
    hb = _Heartbeats()
    rc = gbp_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=lambda *a, **k: pytest.fail("should not discover"),
    )
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "Token refresh failed" in hb[-1]["summary"]


def test_run_location_discovery_failure_returns_error_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_google("acme")
    monkeypatch.setattr(
        "wc_solns_pipelines.shared.tenant_runtime._credentials.access_token",
        lambda _t, _p: "fake-token",
    )

    def boom(_tok):
        raise RuntimeError("No GBP accounts visible")

    hb = _Heartbeats()
    rc = gbp_run.run("acme", heartbeat_fn=hb, discover_location_fn=boom)
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "GBP location discovery failed" in hb[-1]["summary"]


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_with_google(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_google("acme")
    monkeypatch.setattr(
        "wc_solns_pipelines.shared.tenant_runtime._credentials.access_token",
        lambda _t, _p: "fake-token",
    )
    return tmp_path


def test_run_drafts_and_dispatches_post(tenant_with_google):
    hb = _Heartbeats()
    dispatches = _Dispatches(default_action="queued")
    rc = gbp_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=_stub_discover(),
        draft_post_fn=_stub_draft("Test post body"),
        dispatch_fn=dispatches,
    )
    assert rc == 0
    assert len(dispatches) == 1
    assert dispatches[0]["body"] == "Test post body"
    assert dispatches[0]["account_path"] == "accounts/123"
    assert dispatches[0]["location_path"] == "locations/9"
    assert "queued for approval" in hb[-1]["summary"]


def test_run_records_delivered_action(tenant_with_google):
    hb = _Heartbeats()
    rc = gbp_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=_stub_discover(),
        draft_post_fn=_stub_draft(),
        dispatch_fn=_Dispatches(default_action="delivered"),
    )
    assert rc == 0
    assert hb[-1]["status"] == "success"
    assert "Drafted + published" in hb[-1]["summary"]
    state_path = tenant_with_google / "acme" / "pipeline_state" / "gbp.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["posts_published"] == 1


def test_run_records_no_dispatcher_as_success(tenant_with_google):
    """Today there's no live GBP publish handler. Pipeline should not
    panic about that - it's the expected state until W4+ ships the
    publish side. Heartbeat stays success with a hint to flip approval."""
    hb = _Heartbeats()
    rc = gbp_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=_stub_discover(),
        draft_post_fn=_stub_draft(),
        dispatch_fn=_Dispatches(default_action="no_dispatcher"),
    )
    assert rc == 0
    assert hb[-1]["status"] == "success"
    assert "Approve-Before-Send" in hb[-1]["summary"]


def test_run_records_failed_as_error(tenant_with_google):
    hb = _Heartbeats()

    def failing_dispatch(*_args, **_kwargs):
        return {"action": "failed", "reason": "boom"}

    rc = gbp_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=_stub_discover(),
        draft_post_fn=_stub_draft(),
        dispatch_fn=failing_dispatch,
    )
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "Dispatch failed" in hb[-1]["summary"]


# ---------------------------------------------------------------------------
# topic rotation
# ---------------------------------------------------------------------------


def test_topics_default_when_no_kb(tenant_with_google):
    from wc_solns_pipelines.shared.tenant_runtime import TenantContext
    ctx = TenantContext("acme")
    topics = gbp_run.topics_for_tenant(ctx)
    assert topics == gbp_run.DEFAULT_TOPICS


def test_topics_derived_from_services_kb(tenant_with_google):
    tenant_kb.write_section(
        "acme",
        "services",
        "- AC repair and replacement\n- Furnace tune-ups\n- Indoor air quality testing\n",
    )
    from wc_solns_pipelines.shared.tenant_runtime import TenantContext
    ctx = TenantContext("acme")
    topics = gbp_run.topics_for_tenant(ctx)
    assert any("AC repair" in t for t in topics)
    assert any("Furnace" in t for t in topics)


def test_pick_next_topic_advances_index():
    topics = ["A", "B", "C"]
    topic, next_idx = gbp_run.pick_next_topic(topics, {"topic_index": 0})
    assert topic == "A"
    assert next_idx == 1
    topic, next_idx = gbp_run.pick_next_topic(topics, {"topic_index": 2})
    assert topic == "C"
    assert next_idx == 0  # wraps


def test_pick_next_topic_handles_missing_index():
    topic, next_idx = gbp_run.pick_next_topic(["A", "B"], {})
    assert topic == "A"
    assert next_idx == 1


def test_pick_next_topic_empty_list_safe():
    topic, next_idx = gbp_run.pick_next_topic([], {"topic_index": 5})
    assert "share" in topic.lower() or "useful" in topic.lower()
    assert next_idx == 0


def test_run_advances_topic_across_runs(tenant_with_google):
    hb = _Heartbeats()
    dispatches = _Dispatches()
    gbp_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=_stub_discover(),
        draft_post_fn=_stub_draft(),
        dispatch_fn=dispatches,
    )
    first_topic = dispatches[0]["topic"]
    gbp_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=_stub_discover(),
        draft_post_fn=_stub_draft(),
        dispatch_fn=dispatches,
    )
    second_topic = dispatches[1]["topic"]
    assert first_topic != second_topic


# ---------------------------------------------------------------------------
# voice + draft helpers
# ---------------------------------------------------------------------------


def test_draft_post_truncates_oversize_response(tenant_with_google):
    """If Claude returns over 1500 chars (it shouldn't, but defense in depth),
    draft_post must truncate to GBP_POST_MAX_CHARS."""
    long_text = "x" * (gbp_run.GBP_POST_MAX_CHARS + 500)

    class _R:
        text = long_text

    def fake_chat(**_kwargs):
        return _R()

    import wc_solns_pipelines.pipelines.gbp.run as mod
    orig = mod.chat
    try:
        mod.chat = fake_chat
        from wc_solns_pipelines.shared.tenant_runtime import TenantContext
        ctx = TenantContext("acme")
        body = mod.draft_post(ctx, "Some topic")
        assert len(body) <= gbp_run.GBP_POST_MAX_CHARS
    finally:
        mod.chat = orig


def test_draft_post_falls_back_on_opus_unavailable(tenant_with_google):
    """If Anthropic isn't configured, fall back to a canned template -
    pipelines must never silently skip a week."""
    from dashboard_app.services.opus import OpusUnavailable
    import wc_solns_pipelines.pipelines.gbp.run as mod

    def boom(**_kw):
        raise OpusUnavailable("ANTHROPIC_API_KEY missing")

    orig = mod.chat
    try:
        mod.chat = boom
        from wc_solns_pipelines.shared.tenant_runtime import TenantContext
        ctx = TenantContext("acme")
        body = mod.draft_post(ctx, "Spotlight: AC repair")
        assert "AC repair" in body
    finally:
        mod.chat = orig


# ---------------------------------------------------------------------------
# dry run
# ---------------------------------------------------------------------------


def test_dry_run_skips_dispatch_and_heartbeat(tenant_with_google, capsys):
    def hb_fn(**_kw):
        pytest.fail("dry-run must not push heartbeat")

    def dispatch_fn(*_a, **_kw):
        pytest.fail("dry-run must not dispatch")

    rc = gbp_run.run(
        "acme",
        dry_run=True,
        heartbeat_fn=hb_fn,
        discover_location_fn=_stub_discover(),
        draft_post_fn=_stub_draft("Drafted body"),
        dispatch_fn=dispatch_fn,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Drafted body" in out


# ---------------------------------------------------------------------------
# require_approval pref end-to-end (real dispatch.send)
# ---------------------------------------------------------------------------


def test_run_queues_when_require_approval_set(tenant_with_google):
    _tenant_prefs.set_require_approval("acme", "gbp", True)
    hb = _Heartbeats()
    rc = gbp_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=_stub_discover(),
        draft_post_fn=_stub_draft("Body"),
        # Use real dispatch.send (no dispatch_fn override)
    )
    assert rc == 0
    from dashboard_app.services import outgoing_queue
    pending = outgoing_queue.list_pending("acme")
    assert len(pending) == 1
    assert pending[0]["pipeline_id"] == "gbp"
    assert pending[0]["body"] == "Body"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_passes_args_through(tenant_with_google, monkeypatch):
    received: dict = {}

    def fake_run(**kwargs):
        received.update(kwargs)
        return 0

    monkeypatch.setattr(gbp_run, "run", fake_run)
    rc = gbp_run.main(["--tenant", "acme", "--dry-run"])
    assert rc == 0
    assert received["tenant_id"] == "acme"
    assert received["dry_run"] is True
