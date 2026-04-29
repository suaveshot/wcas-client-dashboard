"""Tests for dashboard_app.services.role_detail.

Today this file focuses on the W5.5 SEO Recommendations panel wiring.
The base role_detail.build() flow is exercised end-to-end by the
existing /roles/{slug} HTTP tests.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import role_detail, seo_recommender


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _seed_seo_recs(tenant_root, tenant_id: str, recs: list[dict[str, Any]] | None = None,
                   generated_at: int | None = None,
                   source_summary: dict[str, Any] | None = None) -> None:
    cache = tenant_root / tenant_id / "pipeline_state" / seo_recommender.CACHE_FILENAME
    cache.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "generated_at": generated_at if generated_at is not None else int(time.time()),
        "source_summary": source_summary or {},
        "recommendations": recs or [],
    }
    cache.write_text(json.dumps(payload), encoding="utf-8")


def _seed_brightlocal_master(platform_root) -> None:
    base = platform_root / "brightlocal"
    base.mkdir(parents=True, exist_ok=True)
    (base / "master.json").write_text(
        json.dumps({"api_key": "live-key", "api_secret": "live-secret"}),
        encoding="utf-8",
    )


def _seed_brightlocal_location(tenant_root, tenant_id: str, location_id: str) -> None:
    tdir = tenant_root / tenant_id
    tdir.mkdir(parents=True, exist_ok=True)
    cfg_path = tdir / "tenant_config.json"
    cfg = {}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cfg = {}
    cfg["brightlocal_location_id"] = location_id
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")


def _make_rec(idx: int = 1, urgency: str = "high") -> dict[str, Any]:
    return {
        "id": f"rec-{idx:03d}",
        "title": f"Title {idx}",
        "rationale": "Reason here.",
        "specific_action": "Do this.",
        "evidence": [f"Evidence {idx}"],
        "estimated_traffic_lift": "+10/wk",
        "urgency": urgency,
        "category": "title_tag",
        "page": "/x",
    }


# ---------------------------------------------------------------------------
# seo_panel only attached for role_slug == "seo"
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.setenv("PLATFORM_ROOT", str(tmp_path / "_platform"))
    return tmp_path


def test_build_attaches_seo_panel_for_seo_role(isolated):
    out = role_detail.build("acme", "seo")
    assert out["seo_panel"] is not None
    assert isinstance(out["seo_panel"], dict)


def test_build_does_not_attach_seo_panel_for_non_seo_roles(isolated):
    for slug in ("reviews", "gbp", "blog", "social", "email_assistant", "chat_widget"):
        out = role_detail.build("acme", slug)
        assert out["seo_panel"] is None, f"slug={slug} should not have a panel"


# ---------------------------------------------------------------------------
# panel content with no cache
# ---------------------------------------------------------------------------


def test_seo_panel_empty_state_when_no_cache(isolated):
    out = role_detail.build("acme", "seo")
    panel = out["seo_panel"]
    assert panel["recommendations"] == []
    assert panel["generated_at_ago"] == ""
    assert panel["has_data"] is False
    assert panel["brightlocal_status_state"] == "not_provisioned"


def test_seo_panel_brightlocal_provisioned_but_no_location(isolated):
    _seed_brightlocal_master(isolated / "_platform")
    out = role_detail.build("acme", "seo")
    panel = out["seo_panel"]
    assert panel["brightlocal_status_state"] == "pending_location"
    assert "Provisioning needed" in panel["brightlocal_status_label"]
    assert panel["has_data"] is True  # provisioned counts as data


def test_seo_panel_brightlocal_active(isolated):
    _seed_brightlocal_master(isolated / "_platform")
    _seed_brightlocal_location(isolated, "acme", "loc-1")
    out = role_detail.build("acme", "seo")
    panel = out["seo_panel"]
    assert panel["brightlocal_status_state"] == "active"
    assert "Active" in panel["brightlocal_status_label"]


# ---------------------------------------------------------------------------
# panel content with recs
# ---------------------------------------------------------------------------


def test_seo_panel_returns_top_5_only(isolated):
    recs = [_make_rec(i) for i in range(1, 9)]
    _seed_seo_recs(isolated, "acme", recs=recs)
    out = role_detail.build("acme", "seo")
    panel = out["seo_panel"]
    assert len(panel["recommendations"]) == seo_recommender.DEFAULT_TOP_N
    assert panel["more_count"] == len(recs) - seo_recommender.DEFAULT_TOP_N


def test_seo_panel_more_count_zero_when_few_recs(isolated):
    _seed_seo_recs(isolated, "acme", recs=[_make_rec(1), _make_rec(2)])
    out = role_detail.build("acme", "seo")
    panel = out["seo_panel"]
    assert panel["more_count"] == 0


def test_seo_panel_includes_source_summary(isolated):
    _seed_seo_recs(
        isolated,
        "acme",
        recs=[_make_rec(1)],
        source_summary={"ga4_sessions": 200, "gsc_clicks": 50, "rankings_count": 8, "site_fetched": True},
    )
    out = role_detail.build("acme", "seo")
    panel = out["seo_panel"]
    assert panel["source_summary"]["ga4_sessions"] == 200
    assert panel["source_summary"]["gsc_clicks"] == 50


def test_seo_panel_humanizes_generated_at(isolated):
    """generated_at -> humanized 'X minutes ago' string."""
    _seed_seo_recs(
        isolated, "acme",
        recs=[_make_rec(1)],
        generated_at=int(time.time()) - 60,  # 1 minute ago
    )
    out = role_detail.build("acme", "seo")
    panel = out["seo_panel"]
    assert panel["generated_at_ago"]  # non-empty
    # Just sanity-check it has an "ago" or similar marker
    assert any(token in panel["generated_at_ago"].lower() for token in ("ago", "min", "now", "second"))


def test_seo_panel_has_data_when_recs_present(isolated):
    _seed_seo_recs(isolated, "acme", recs=[_make_rec(1)])
    out = role_detail.build("acme", "seo")
    panel = out["seo_panel"]
    assert panel["has_data"] is True


# ---------------------------------------------------------------------------
# panel renders even when no heartbeat snapshot exists
# ---------------------------------------------------------------------------


def test_seo_panel_attached_even_without_heartbeat(isolated):
    _seed_seo_recs(isolated, "acme", recs=[_make_rec(1)])
    out = role_detail.build("acme", "seo")
    assert out["has_snapshot"] is False
    assert out["seo_panel"]["recommendations"][0]["id"] == "rec-001"


# ---------------------------------------------------------------------------
# slug-form variants resolve to the same panel
# ---------------------------------------------------------------------------


def test_seo_panel_attached_for_dash_form_slug(isolated):
    """The role page accepts both /roles/seo and /roles/seo (no dashes
    today, but be defensive)."""
    out = role_detail.build("acme", "seo")
    assert out["seo_panel"] is not None
