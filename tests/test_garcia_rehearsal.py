"""Garcia activation rehearsal - end-to-end W7 integration test.

Walks the full activation flow for a Pro-tier tenant the way the agent
would in production:

  1. detect_crm against the tenant's site -> classification
  2. activate_pipeline rings move through the steps
  3. mark_activation_complete with tier=pro -> automations.json seeded,
     handoff letter sent
  4. home_context.build renders the right rings + state

This is the local rehearsal the W7 plan calls for. It also serves as
the regression backstop: if any of W2-W6's services drift in a way that
breaks new-tenant onboarding, this test fails first.
"""

from __future__ import annotations

import os
from typing import Any

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import (
    activation_state,
    activation_tools,
    automation_catalog as cat,
    crm_detect,
    handoff,
    heartbeat_store,
    home_context,
    tenant_automations,
)


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, body: str = "", headers: dict[str, str] | None = None):
        self.text = body
        self.headers = headers or {}
        self.url = "https://garciafolklorico.com/"


def _fake_garcia_site(url: str, *, timeout: float = 8.0) -> _FakeResp:
    """Garcia's site - WordPress, no CRM widget, no fingerprints."""
    return _FakeResp(
        body=(
            "<html><head><meta name='generator' content='WordPress 6.5'>"
            "<title>Garcia Folklorico Studio</title></head>"
            "<body><h1>Folklorico classes</h1></body></html>"
        ),
    )


@pytest.fixture
def tenant_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _stub_email(monkeypatch):
    """Capture handoff emails in-memory; never reach SMTP."""
    sent: list[dict[str, Any]] = []

    def fake_send(**kwargs: Any) -> bool:
        sent.append(kwargs)
        return True

    monkeypatch.setattr(handoff, "send_handoff", fake_send)
    return sent


# ---------------------------------------------------------------------------
# rehearsal
# ---------------------------------------------------------------------------


def test_garcia_full_activation_rehearsal(tenant_root, monkeypatch, _stub_email):
    """The end-to-end script that proves a Pro-tier tenant can be onboarded
    locally without any human intervention beyond the agent's tool calls."""
    tenant_id = "garcia_folklorico"

    # Step 1: detect_crm. Garcia has no CRM yet - we expect "none".
    monkeypatch.setattr(crm_detect, "_fetch_html", lambda url, *, http_get: (
        "<html>plain</html>", {}, "https://garciafolklorico.com/"
    ))
    ok, payload = activation_tools.dispatch(
        tenant_id,
        "detect_crm",
        {"url": "https://garciafolklorico.com/"},
    )
    assert ok is True
    assert payload["detected"] == "none"
    assert "No CRM" in payload["recommendation"]

    # Step 2: walk activation rings for the four roles Garcia cares about.
    for role in ("gbp", "seo", "reviews", "social"):
        for step in ("credentials", "config", "connected", "first_run"):
            ok, _ = activation_tools.dispatch(
                tenant_id,
                "activate_pipeline",
                {"role_slug": role, "step": step},
            )
            assert ok is True, f"activate_pipeline failed at {role} {step}"

    state = activation_state.get(tenant_id)
    assert {"gbp", "seo", "reviews", "social"}.issubset(state["roles"].keys())
    for role in ("gbp", "seo", "reviews", "social"):
        assert state["roles"][role]["step"] == "first_run"

    # Step 3: mark_activation_complete with tier=pro, owner info.
    ok, payload = activation_tools.dispatch(
        tenant_id,
        "mark_activation_complete",
        {
            "tier": "pro",
            "owner_name": "Itzel Garcia",
            "owner_email": "itzel@garciafolklorico.com",
            "business_name": "Garcia Folklorico Studio",
        },
    )
    assert ok is True
    assert payload["status"] == "activated"
    assert payload["tier"] == "pro"
    assert payload["tier_default_count"] > 0
    assert payload["handoff_sent"] is True
    assert payload["tier_seed_error"] is None

    # Step 4: automations.json should have every Pro tier_default.
    enabled = set(tenant_automations.enabled_ids(tenant_id))
    expected = {a.id for a in cat.for_tier("pro")}
    assert expected.issubset(enabled)
    # seo_recs is the Pro-only tier addition - it MUST be there.
    assert "seo_recs" in enabled
    # AP-only items must NOT leak in.
    assert "daily_reports" not in enabled
    assert "guard_compliance" not in enabled

    # Step 5: handoff letter went out with the right addressing.
    assert len(_stub_email) == 1
    letter = _stub_email[0]
    assert letter["owner_email"] == "itzel@garciafolklorico.com"
    assert letter["business_name"] == "Garcia Folklorico Studio"

    # Step 6: home_context renders pending rings (no heartbeats yet).
    ctx = home_context.build(tenant_id=tenant_id, owner_name="Itzel Garcia")
    rendered_ids = {r["slug"].replace("-", "_") for r in ctx["roles"]}
    assert expected.issubset(rendered_ids)
    # All rings should be in the pending state (no real heartbeats).
    pending_states = [r["state"] for r in ctx["roles"]
                      if r["slug"].replace("-", "_") in expected]
    assert all(s == "pending" for s in pending_states), pending_states

    # Step 7: a heartbeat lands -> ring promotes off pending.
    heartbeat_store.write_snapshot(tenant_id, "reviews", {
        "status": "ok",
        "last_run": "2026-04-29T07:00:00+00:00",
        "summary": "1 reply drafted",
    })
    ctx2 = home_context.build(tenant_id=tenant_id, owner_name="Itzel Garcia")
    reviews_ring = next(r for r in ctx2["roles"] if r["slug"] == "reviews")
    assert reviews_ring["state"] != "pending"


def test_rehearsal_is_idempotent(tenant_root, monkeypatch, _stub_email):
    """Running the activation flow twice on the same tenant doesn't double-seed
    or corrupt state. Sam re-running is a real concierge scenario."""
    tenant_id = "garcia_folklorico"

    monkeypatch.setattr(crm_detect, "_fetch_html", lambda url, *, http_get: (
        "<html>plain</html>", {}, "https://garciafolklorico.com/"
    ))

    for _ in range(2):
        activation_tools.dispatch(
            tenant_id,
            "mark_activation_complete",
            {
                "tier": "pro",
                "owner_name": "Itzel Garcia",
                "owner_email": "itzel@garciafolklorico.com",
            },
        )

    enabled = tenant_automations.enabled_ids(tenant_id)
    # No duplicates from the second run.
    assert len(enabled) == len(set(enabled))
    # Tier still recorded as pro.
    assert tenant_automations.get_tier(tenant_id) == "pro"
    # Two letters (one per completion); business decision can debounce later.
    assert len(_stub_email) == 2
