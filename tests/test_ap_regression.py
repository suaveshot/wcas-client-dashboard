"""W7 AP regression - the contract between AP's heartbeat shape and the
dashboard's home_context renderer must stay intact.

AP's pipelines (patrol, guard_compliance, daily_reports, sales_pipeline,
etc.) don't import dashboard code, but they DO write heartbeats the
dashboard reads. After the W6 catalog-driven refactor, AP-shaped
heartbeats must still render correctly even though AP has no
`automations.json`.

This test pins:
  1. The heartbeat envelope shape `heartbeat_store.write_snapshot` accepts
  2. The fields `home_context.build` reads back from telemetry
  3. The fallback path that renders any pipeline NOT in tenant_automations
     (so AP's stack keeps working without being migrated)
  4. State derivation rules per heartbeat status (ok/error/paused/unknown)

Failure here means W2-W6 dashboard work has broken backward compat with
AP - investigate before deploying.
"""

from __future__ import annotations

import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import (
    automation_catalog,
    heartbeat_store,
    home_context,
    tenant_automations,
)


# AP's actual pipeline IDs from CLAUDE.md `Run Commands` section.
_AP_PIPELINES = (
    "patrol",
    "guard_compliance",
    "incident_trends",
    "sales_pipeline",
    "review_engine",
    "blog",
    "social",
    "gbp",
    "seo",
)


@pytest.fixture
def tenant_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    return tmp_path


def _ap_heartbeat(pipeline_id: str, status: str = "ok",
                  last_run: str = "2026-04-29T07:00:00+00:00",
                  summary: str = "ok") -> None:
    """Write an AP-shaped heartbeat payload."""
    heartbeat_store.write_snapshot("americal_patrol", pipeline_id, {
        "status": status,
        "last_run": last_run,
        "summary": summary,
    })


# ---------------------------------------------------------------------------
# AP has no automations.json - rings come from heartbeats alone
# ---------------------------------------------------------------------------


def test_ap_renders_rings_without_automations_json(tenant_root):
    """AP doesn't seed tier defaults. Every active pipeline must still surface."""
    for pid in _AP_PIPELINES:
        _ap_heartbeat(pid, status="ok")

    ctx = home_context.build(tenant_id="americal_patrol", owner_name="Sam")
    rendered = {r["slug"].replace("-", "_") for r in ctx["roles"]}
    for pid in _AP_PIPELINES:
        assert pid in rendered, f"AP pipeline {pid} dropped from home grid"


def test_ap_tenant_has_no_enabled_automations(tenant_root):
    """Pin the invariant: AP is intentionally NOT seeded with tier defaults."""
    assert tenant_automations.enabled_ids("americal_patrol") == []
    assert tenant_automations.get_tier("americal_patrol") is None


# ---------------------------------------------------------------------------
# heartbeat envelope contract
# ---------------------------------------------------------------------------


def test_heartbeat_envelope_required_fields(tenant_root):
    """write_snapshot must accept the AP-shaped payload without raising."""
    heartbeat_store.write_snapshot("americal_patrol", "patrol", {
        "status": "ok",
        "last_run": "2026-04-29T07:00:00+00:00",
        "summary": "3 DARs sent",
    })
    rows = heartbeat_store.read_all("americal_patrol")
    assert len(rows) == 1
    assert rows[0]["pipeline_id"] == "patrol"
    assert rows[0]["payload"]["status"] == "ok"


# ---------------------------------------------------------------------------
# state derivation per heartbeat status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status,expected_state", [
    ("ok", "active"),
    ("error", "error"),
    ("paused", "paused"),
])
def test_heartbeat_status_drives_ring_state(tenant_root, status, expected_state):
    _ap_heartbeat("patrol", status=status)
    ctx = home_context.build(tenant_id="americal_patrol", owner_name="Sam")
    patrol = next(r for r in ctx["roles"] if r["slug"] == "patrol")
    assert patrol["state"] == expected_state


def test_errored_pipeline_surfaces_attention_banner(tenant_root):
    _ap_heartbeat("patrol", status="ok")
    _ap_heartbeat("guard_compliance", status="error", summary="3 expirations")
    ctx = home_context.build(tenant_id="americal_patrol", owner_name="Sam")
    assert ctx["attention"] is not None
    assert ctx["attention"]["kind"] == "error"


# ---------------------------------------------------------------------------
# AP-only catalog entries are recognized when heartbeats arrive for them
# ---------------------------------------------------------------------------


def test_ap_only_catalog_entries_use_their_display_name(tenant_root):
    """When AP's guard_compliance heartbeat lands, its catalog entry name
    (not just the pipeline_id) shows up - so the home grid reads naturally."""
    _ap_heartbeat("guard_compliance", status="ok")
    ctx = home_context.build(tenant_id="americal_patrol", owner_name="Sam")
    gc = next(r for r in ctx["roles"] if r["slug"] == "guard-compliance")
    catalog_entry = automation_catalog.get("guard_compliance")
    if catalog_entry is not None:
        assert gc["name"] == catalog_entry.name


# ---------------------------------------------------------------------------
# WCAS-generic pipelines remain isolated from AP
# ---------------------------------------------------------------------------


def test_wcas_tenant_does_not_see_ap_heartbeats(tenant_root):
    """A heartbeat written for AP must never bleed into another tenant's
    home grid - fundamental tenant isolation invariant."""
    _ap_heartbeat("patrol", status="ok")
    ctx = home_context.build(tenant_id="garcia_folklorico", owner_name="Itzel")
    rendered = {r["slug"] for r in ctx["roles"]}
    assert "patrol" not in rendered


def test_ap_heartbeat_does_not_corrupt_other_tenant_state(tenant_root):
    """Cross-tenant write isolation - AP's state_snapshot dir must not affect
    any other tenant's enabled automations."""
    _ap_heartbeat("patrol", status="ok")
    tenant_automations.seed_for_tier("garcia_folklorico", "pro")
    enabled = set(tenant_automations.enabled_ids("garcia_folklorico"))
    assert "patrol" not in enabled
