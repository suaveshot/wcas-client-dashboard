"""Tests for wc_solns_pipelines.pipelines.seo.weekly_report.

Same injection style as the reviews + gbp tests so the full flow runs
without GA4 / GSC / Anthropic / heartbeat HTTP.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import credentials as _credentials, tenant_prefs as _tenant_prefs
from wc_solns_pipelines.pipelines.seo import weekly_report as wr

SCOPE_GA4 = "https://www.googleapis.com/auth/analytics.readonly"
SCOPE_GSC = "https://www.googleapis.com/auth/webmasters.readonly"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _seed_google(tenant_id: str, scopes: list[str] | None = None) -> None:
    _credentials.store(
        tenant_id,
        "google",
        refresh_token="1//fake",
        scopes=scopes if scopes is not None else [SCOPE_GA4, SCOPE_GSC],
    )


def _seed_tenant_config(tenant_id: str, tmp_path, **fields) -> None:
    tdir = tmp_path / tenant_id
    tdir.mkdir(parents=True, exist_ok=True)
    cfg = {"ga4_property_id": "properties/123", "gsc_site_url": "sc-domain:example.com"}
    cfg.update(fields)
    (tdir / "tenant_config.json").write_text(json.dumps(cfg), encoding="utf-8")


class _Heartbeats(list):
    def __call__(self, **kwargs):
        self.append(kwargs)
        return 0


class _Dispatches(list):
    def __init__(self, default_action: str = "queued") -> None:
        super().__init__()
        self.default_action = default_action

    def __call__(self, tenant_id, body, recipient, week_label, metadata):
        self.append(
            {
                "tenant_id": tenant_id,
                "body": body,
                "recipient": recipient,
                "week_label": week_label,
                "metadata": metadata,
            }
        )
        return {"action": self.default_action, "draft_id": f"d-{len(self)}"}


def _stub_ga4(sessions: int = 100, top_pages: list[dict] | None = None) -> Any:
    pages = top_pages if top_pages is not None else [{"path": "/", "sessions": sessions}]
    return lambda *_a, **_kw: {
        "totals": {"sessions": sessions, "totalUsers": int(sessions * 0.8), "conversions": 5},
        "top_pages": pages,
    }


def _stub_gsc(clicks: int = 50, top_queries: list[dict] | None = None) -> Any:
    queries = top_queries if top_queries is not None else [
        {"query": "test query", "clicks": clicks, "impressions": clicks * 10, "position": 8.4}
    ]
    return lambda *_a, **_kw: {
        "totals": {"clicks": clicks, "impressions": clicks * 10, "ctr": 0.1, "position": 8.4},
        "top_queries": queries,
    }


def _stub_compose(text: str = "Here's your week.") -> Any:
    return lambda _ctx, _ga4, _gsc, _prior, *, week_label: f"{text} ({week_label})"


# ---------------------------------------------------------------------------
# date windows helper
# ---------------------------------------------------------------------------


def test_date_windows_layout():
    today = datetime(2026, 4, 30, tzinfo=timezone.utc)
    cs, ce, ps, pe = wr._date_windows(today)
    assert ce == "2026-04-29"
    assert cs == "2026-04-23"
    assert pe == "2026-04-22"
    assert ps == "2026-04-16"


# ---------------------------------------------------------------------------
# guard rails
# ---------------------------------------------------------------------------


def test_run_invalid_tenant_returns_error_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    hb = _Heartbeats()
    rc = wr.run(
        "../bad",
        heartbeat_fn=hb,
        fetch_ga4_fn=lambda *a, **k: pytest.fail("should not fetch"),
        fetch_gsc_fn=lambda *a, **k: pytest.fail("should not fetch"),
    )
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "Invalid tenant" in hb[-1]["summary"]


def test_run_paused_tenant_short_circuits(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_tenant_config("acme", tmp_path, status="paused")
    _seed_google("acme")
    hb = _Heartbeats()
    rc = wr.run(
        "acme",
        heartbeat_fn=hb,
        fetch_ga4_fn=lambda *a, **k: pytest.fail("should not fetch"),
        fetch_gsc_fn=lambda *a, **k: pytest.fail("should not fetch"),
    )
    assert rc == 0
    assert hb[-1]["status"] == "success"
    assert "Paused" in hb[-1]["summary"]


def test_run_missing_credentials_returns_error_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_tenant_config("acme", tmp_path)
    hb = _Heartbeats()
    rc = wr.run(
        "acme",
        heartbeat_fn=hb,
        fetch_ga4_fn=lambda *a, **k: pytest.fail("should not fetch"),
        fetch_gsc_fn=lambda *a, **k: pytest.fail("should not fetch"),
    )
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "Google account not connected" in hb[-1]["summary"]


def test_run_missing_ga4_scope_returns_error_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_tenant_config("acme", tmp_path)
    _seed_google("acme", scopes=[SCOPE_GSC])  # GSC only
    hb = _Heartbeats()
    rc = wr.run("acme", heartbeat_fn=hb,
                fetch_ga4_fn=lambda *a, **k: pytest.fail("should not fetch"),
                fetch_gsc_fn=lambda *a, **k: pytest.fail("should not fetch"))
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "analytics.readonly" in hb[-1]["summary"]


def test_run_missing_gsc_scope_returns_error_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_tenant_config("acme", tmp_path)
    _seed_google("acme", scopes=[SCOPE_GA4])
    hb = _Heartbeats()
    rc = wr.run("acme", heartbeat_fn=hb,
                fetch_ga4_fn=lambda *a, **k: pytest.fail("should not fetch"),
                fetch_gsc_fn=lambda *a, **k: pytest.fail("should not fetch"))
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "webmasters.readonly" in hb[-1]["summary"]


def test_run_missing_ga4_property_returns_error_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    # Seed config WITHOUT ga4_property_id
    tdir = tmp_path / "acme"
    tdir.mkdir()
    (tdir / "tenant_config.json").write_text(
        json.dumps({"gsc_site_url": "sc-domain:example.com"}), encoding="utf-8"
    )
    _seed_google("acme")
    hb = _Heartbeats()
    rc = wr.run("acme", heartbeat_fn=hb,
                fetch_ga4_fn=lambda *a, **k: pytest.fail("should not fetch"),
                fetch_gsc_fn=lambda *a, **k: pytest.fail("should not fetch"))
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "GA4 property" in hb[-1]["summary"]


def test_run_missing_gsc_site_returns_error_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    tdir = tmp_path / "acme"
    tdir.mkdir()
    (tdir / "tenant_config.json").write_text(
        json.dumps({"ga4_property_id": "properties/123"}), encoding="utf-8"
    )
    _seed_google("acme")
    hb = _Heartbeats()
    rc = wr.run("acme", heartbeat_fn=hb,
                fetch_ga4_fn=lambda *a, **k: pytest.fail("should not fetch"),
                fetch_gsc_fn=lambda *a, **k: pytest.fail("should not fetch"))
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "GSC site" in hb[-1]["summary"]


def test_run_token_refresh_failure_returns_error_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_tenant_config("acme", tmp_path)
    _seed_google("acme")

    def boom(_t, _p):
        raise RuntimeError("refresh denied")

    monkeypatch.setattr("wc_solns_pipelines.shared.tenant_runtime._credentials.access_token", boom)
    hb = _Heartbeats()
    rc = wr.run("acme", heartbeat_fn=hb,
                fetch_ga4_fn=_stub_ga4(),
                fetch_gsc_fn=_stub_gsc())
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "Token refresh failed" in hb[-1]["summary"]


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_with_seo(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_tenant_config("acme", tmp_path, owner_email="owner@example.com")
    _seed_google("acme")
    monkeypatch.setattr(
        "wc_solns_pipelines.shared.tenant_runtime._credentials.access_token",
        lambda _t, _p: "fake-token",
    )
    return tmp_path


def test_run_drafts_and_dispatches_digest(tenant_with_seo):
    hb = _Heartbeats()
    dispatches = _Dispatches(default_action="queued")
    rc = wr.run(
        "acme",
        heartbeat_fn=hb,
        fetch_ga4_fn=_stub_ga4(sessions=210),
        fetch_gsc_fn=_stub_gsc(clicks=42),
        compose_digest_fn=_stub_compose("Hello!"),
        dispatch_fn=dispatches,
    )
    assert rc == 0
    assert len(dispatches) == 1
    assert dispatches[0]["recipient"] == "owner@example.com"
    assert "Hello!" in dispatches[0]["body"]
    assert dispatches[0]["metadata"]["ga4_totals"]["sessions"] == 210
    assert dispatches[0]["metadata"]["gsc_totals"]["clicks"] == 42
    assert "Sessions: 210" in hb[-1]["summary"]
    assert "clicks: 42" in hb[-1]["summary"]


def test_run_persists_metrics_for_next_week(tenant_with_seo):
    hb = _Heartbeats()
    wr.run(
        "acme",
        heartbeat_fn=hb,
        fetch_ga4_fn=_stub_ga4(sessions=300),
        fetch_gsc_fn=_stub_gsc(clicks=80),
        compose_digest_fn=_stub_compose(),
        dispatch_fn=_Dispatches(),
    )
    state_path = tenant_with_seo / "acme" / "pipeline_state" / "seo.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last_metrics"]["ga4"]["totals"]["sessions"] == 300
    assert state["last_metrics"]["gsc"]["totals"]["clicks"] == 80


def test_run_passes_prior_state_to_compose(tenant_with_seo):
    state_path = tenant_with_seo / "acme" / "pipeline_state" / "seo.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"last_metrics": {"ga4": {"totals": {"sessions": 200}}, "gsc": {"totals": {"clicks": 30}}}}),
        encoding="utf-8",
    )

    received: dict = {}

    def capture_compose(_ctx, _ga4, _gsc, prior, *, week_label):
        received["prior"] = prior
        return "body"

    wr.run(
        "acme",
        heartbeat_fn=_Heartbeats(),
        fetch_ga4_fn=_stub_ga4(sessions=300),
        fetch_gsc_fn=_stub_gsc(clicks=80),
        compose_digest_fn=capture_compose,
        dispatch_fn=_Dispatches(),
    )
    assert received["prior"]["ga4"]["totals"]["sessions"] == 200


def test_run_handles_no_dispatcher_as_success(tenant_with_seo):
    hb = _Heartbeats()
    rc = wr.run(
        "acme",
        heartbeat_fn=hb,
        fetch_ga4_fn=_stub_ga4(),
        fetch_gsc_fn=_stub_gsc(),
        compose_digest_fn=_stub_compose(),
        dispatch_fn=_Dispatches(default_action="no_dispatcher"),
    )
    assert rc == 0
    assert hb[-1]["status"] == "success"
    assert "Approve-Before-Send" in hb[-1]["summary"]


def test_run_handles_failed_dispatch_as_error(tenant_with_seo):
    hb = _Heartbeats()
    rc = wr.run(
        "acme",
        heartbeat_fn=hb,
        fetch_ga4_fn=_stub_ga4(),
        fetch_gsc_fn=_stub_gsc(),
        compose_digest_fn=_stub_compose(),
        dispatch_fn=lambda *a, **k: {"action": "failed", "reason": "down"},
    )
    assert rc == 0
    assert hb[-1]["status"] == "error"


# ---------------------------------------------------------------------------
# helpers: deltas, owner email, format block
# ---------------------------------------------------------------------------


def test_delta_pct_handles_zero_prior():
    assert "no prior" in wr._delta_pct(50, 0)


def test_delta_pct_positive_change():
    assert wr._delta_pct(150, 100) == "+50.0%"


def test_delta_pct_negative_change():
    assert wr._delta_pct(50, 100) == "-50.0%"


def test_format_metrics_block_contains_top_pages_and_queries():
    ga4 = {
        "totals": {"sessions": 100, "totalUsers": 80, "conversions": 5},
        "top_pages": [{"path": "/svc", "sessions": 50}],
    }
    gsc = {
        "totals": {"clicks": 30, "impressions": 300, "ctr": 0.1, "position": 7.0},
        "top_queries": [{"query": "ac repair", "clicks": 12, "impressions": 100, "position": 6.5}],
    }
    out = wr._format_metrics_block(ga4, gsc, prior={})
    assert "/svc" in out
    assert "ac repair" in out


def test_format_metrics_block_marks_errors():
    ga4 = {
        "totals": {"sessions": 0, "totalUsers": 0, "conversions": 0},
        "top_pages": [],
        "error": "GA4 down",
    }
    gsc = {
        "totals": {"clicks": 0, "impressions": 0, "ctr": 0.0, "position": 0.0},
        "top_queries": [],
    }
    out = wr._format_metrics_block(ga4, gsc, prior={})
    assert "GA4 NOTE" in out


def test_resolve_owner_email_uses_top_level_field(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_tenant_config("acme", tmp_path, owner_email="o@x.com")
    from wc_solns_pipelines.shared.tenant_runtime import TenantContext
    ctx = TenantContext("acme")
    assert wr._resolve_owner_email(ctx) == "o@x.com"


def test_resolve_owner_email_falls_back_to_contact_block(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    tdir = tmp_path / "acme"
    tdir.mkdir()
    (tdir / "tenant_config.json").write_text(
        json.dumps({"contact": {"email": "contact@x.com"}}), encoding="utf-8"
    )
    from wc_solns_pipelines.shared.tenant_runtime import TenantContext
    ctx = TenantContext("acme")
    assert wr._resolve_owner_email(ctx) == "contact@x.com"


def test_resolve_owner_email_returns_empty_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    tdir = tmp_path / "acme"
    tdir.mkdir()
    (tdir / "tenant_config.json").write_text("{}", encoding="utf-8")
    from wc_solns_pipelines.shared.tenant_runtime import TenantContext
    ctx = TenantContext("acme")
    assert wr._resolve_owner_email(ctx) == ""


# ---------------------------------------------------------------------------
# compose digest fallback
# ---------------------------------------------------------------------------


def test_compose_digest_falls_back_when_opus_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_tenant_config("acme", tmp_path)
    from dashboard_app.services.opus import OpusUnavailable

    def boom(**_kw):
        raise OpusUnavailable("no key")

    monkeypatch.setattr(wr, "chat", boom)
    from wc_solns_pipelines.shared.tenant_runtime import TenantContext
    ctx = TenantContext("acme")
    body = wr.compose_digest(
        ctx,
        ga4={"totals": {"sessions": 1, "totalUsers": 1, "conversions": 0}, "top_pages": []},
        gsc={"totals": {"clicks": 1, "impressions": 10, "ctr": 0.1, "position": 5.0}, "top_queries": []},
        prior_state={},
        week_label="Week of X",
    )
    assert "Sessions: 1" in body
    assert "Week of X" in body


# ---------------------------------------------------------------------------
# dry run + CLI
# ---------------------------------------------------------------------------


def test_dry_run_skips_dispatch_and_heartbeat(tenant_with_seo, capsys):
    def hb_fn(**_kw):
        pytest.fail("dry-run must not push heartbeat")

    def dispatch_fn(*_a, **_kw):
        pytest.fail("dry-run must not dispatch")

    rc = wr.run(
        "acme",
        dry_run=True,
        heartbeat_fn=hb_fn,
        fetch_ga4_fn=_stub_ga4(),
        fetch_gsc_fn=_stub_gsc(),
        compose_digest_fn=_stub_compose("Body!"),
        dispatch_fn=dispatch_fn,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Body!" in out


def test_run_queues_when_require_approval_set(tenant_with_seo):
    _tenant_prefs.set_require_approval("acme", "seo", True)
    hb = _Heartbeats()
    rc = wr.run(
        "acme",
        heartbeat_fn=hb,
        fetch_ga4_fn=_stub_ga4(),
        fetch_gsc_fn=_stub_gsc(),
        compose_digest_fn=_stub_compose("Body"),
        # Use real dispatch.send (no dispatch_fn override)
    )
    assert rc == 0
    from dashboard_app.services import outgoing_queue
    pending = outgoing_queue.list_pending("acme")
    assert len(pending) == 1
    assert pending[0]["pipeline_id"] == "seo"


def test_main_passes_args_through(tenant_with_seo, monkeypatch):
    received: dict = {}

    def fake_run(**kwargs):
        received.update(kwargs)
        return 0

    monkeypatch.setattr(wr, "run", fake_run)
    rc = wr.main(["--tenant", "acme", "--dry-run"])
    assert rc == 0
    assert received["tenant_id"] == "acme"
    assert received["dry_run"] is True
