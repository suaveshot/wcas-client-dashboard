"""Tests for wc_solns_pipelines.pipelines.reviews.run.

The pipeline accepts injectable callables (fetch_reviews_fn, discover_location_fn,
draft_reply_fn, dispatch_fn, heartbeat_fn) so we can run the full flow without
hitting GBP, Anthropic, or the heartbeat HTTP endpoint.
"""

from __future__ import annotations

import json
import os
from typing import Any

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import credentials as _credentials, tenant_prefs as _tenant_prefs
from wc_solns_pipelines.pipelines.reviews import run as reviews_run


GBP_SCOPE = "https://www.googleapis.com/auth/business.manage"


# ---------------------------------------------------------------------------
# helpers / fakes
# ---------------------------------------------------------------------------


def _seed_google(tenant_id: str, scopes: list[str] | None = None) -> None:
    _credentials.store(
        tenant_id,
        "google",
        refresh_token="1//fake-refresh-token",
        scopes=scopes if scopes is not None else [GBP_SCOPE],
    )


def _make_review(
    review_id: str,
    *,
    stars: str = "FIVE",
    comment: str = "Loved them",
    reviewer: str = "Alice",
    create_time: str = "2026-04-28T10:00:00Z",
) -> dict[str, Any]:
    return {
        "reviewId": review_id,
        "starRating": stars,
        "comment": comment,
        "createTime": create_time,
        "updateTime": create_time,
        "reviewer": {"displayName": reviewer},
    }


class _Heartbeats(list):
    """Captures every heartbeat call so a test can assert the last (or all)."""

    def __call__(self, **kwargs):
        self.append(kwargs)
        return 0


class _Dispatches(list):
    def __init__(self, default_action: str = "queued") -> None:
        super().__init__()
        self.default_action = default_action

    def __call__(self, tenant_id, review, body, account_path, location_path):
        self.append(
            {
                "tenant_id": tenant_id,
                "review": review,
                "body": body,
                "account_path": account_path,
                "location_path": location_path,
            }
        )
        return {"action": self.default_action, "draft_id": f"draft-{len(self)}"}


def _stub_discover(account_path: str = "accounts/123", location_path: str = "locations/9") -> Any:
    return lambda _tok: (account_path, location_path)


def _stub_fetch(reviews: list[dict[str, Any]]) -> Any:
    return lambda _tok, _acc, _loc: list(reviews)


def _stub_draft(text: str = "Thanks!") -> Any:
    return lambda _ctx, _review: text


# ---------------------------------------------------------------------------
# guard rails: invalid tenant / paused / creds / scopes / token / discovery
# ---------------------------------------------------------------------------


def test_run_invalid_tenant_returns_error_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    hb = _Heartbeats()
    rc = reviews_run.run(
        "../bad-slug",
        heartbeat_fn=hb,
        fetch_reviews_fn=lambda *a, **k: pytest.fail("should not fetch"),
        discover_location_fn=lambda *a, **k: pytest.fail("should not discover"),
    )
    assert rc == 0
    assert len(hb) == 1
    assert hb[0]["status"] == "error"
    assert "Invalid tenant" in hb[0]["summary"]


def test_run_paused_tenant_short_circuits(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    tenant_dir = tmp_path / "acme"
    tenant_dir.mkdir(parents=True, exist_ok=True)
    (tenant_dir / "tenant_config.json").write_text(
        json.dumps({"status": "paused"}), encoding="utf-8"
    )
    _seed_google("acme")

    hb = _Heartbeats()
    rc = reviews_run.run(
        "acme",
        heartbeat_fn=hb,
        fetch_reviews_fn=lambda *a, **k: pytest.fail("should not fetch"),
        discover_location_fn=lambda *a, **k: pytest.fail("should not discover"),
    )
    assert rc == 0
    assert hb[-1]["status"] == "success"
    assert "Paused" in hb[-1]["summary"]


def test_run_missing_credentials_returns_error_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    hb = _Heartbeats()
    rc = reviews_run.run(
        "acme",
        heartbeat_fn=hb,
        fetch_reviews_fn=lambda *a, **k: pytest.fail("should not fetch"),
        discover_location_fn=lambda *a, **k: pytest.fail("should not discover"),
    )
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "Google account not connected" in hb[-1]["summary"]


def test_run_missing_scope_returns_error_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_google("acme", scopes=["openid"])
    hb = _Heartbeats()
    rc = reviews_run.run(
        "acme",
        heartbeat_fn=hb,
        fetch_reviews_fn=lambda *a, **k: pytest.fail("should not fetch"),
        discover_location_fn=lambda *a, **k: pytest.fail("should not discover"),
    )
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "business.manage" in hb[-1]["summary"]


def test_run_token_refresh_failure_returns_error_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_google("acme")

    def boom(_tenant, _provider):
        raise RuntimeError("refresh denied")

    monkeypatch.setattr("wc_solns_pipelines.shared.tenant_runtime._credentials.access_token", boom)
    hb = _Heartbeats()
    rc = reviews_run.run(
        "acme",
        heartbeat_fn=hb,
        fetch_reviews_fn=lambda *a, **k: pytest.fail("should not fetch"),
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
        raise RuntimeError("No GBP accounts visible to this credential")

    hb = _Heartbeats()
    rc = reviews_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=boom,
        fetch_reviews_fn=lambda *a, **k: pytest.fail("should not fetch"),
    )
    assert rc == 0
    assert hb[-1]["status"] == "error"
    assert "GBP location discovery failed" in hb[-1]["summary"]


# ---------------------------------------------------------------------------
# happy path: fetch + draft + dispatch + heartbeat
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


def test_run_no_new_reviews_emits_no_new_summary(tenant_with_google):
    hb = _Heartbeats()
    dispatches = _Dispatches()
    rc = reviews_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=_stub_discover(),
        fetch_reviews_fn=_stub_fetch([]),
        draft_reply_fn=_stub_draft(),
        dispatch_fn=dispatches,
    )
    assert rc == 0
    assert dispatches == []
    assert hb[-1]["status"] == "success"
    assert "No new reviews" in hb[-1]["summary"]


def test_run_drafts_and_dispatches_new_reviews(tenant_with_google):
    reviews = [
        _make_review("rev-1", stars="FIVE", comment="Awesome service"),
        _make_review("rev-2", stars="FOUR", comment="Pretty good"),
    ]
    hb = _Heartbeats()
    dispatches = _Dispatches(default_action="queued")
    rc = reviews_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=_stub_discover(),
        fetch_reviews_fn=_stub_fetch(reviews),
        draft_reply_fn=_stub_draft("Thanks so much!"),
        dispatch_fn=dispatches,
    )
    assert rc == 0
    assert len(dispatches) == 2
    # body and account/location threaded through
    for entry in dispatches:
        assert entry["body"] == "Thanks so much!"
        assert entry["account_path"] == "accounts/123"
        assert entry["location_path"] == "locations/9"
    summary = hb[-1]["summary"]
    assert "Drafted 2 of 2" in summary
    assert "2 queued" in summary


def test_run_skips_already_seen_reviews(tenant_with_google):
    # Pre-populate state with rev-1 already seen
    state_path = tenant_with_google / "acme" / "pipeline_state" / "reviews.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"seen_review_ids": ["rev-1"]}), encoding="utf-8"
    )

    reviews = [
        _make_review("rev-1", stars="FIVE", comment="Stale"),
        _make_review("rev-2", stars="FIVE", comment="Fresh"),
    ]
    hb = _Heartbeats()
    dispatches = _Dispatches()
    reviews_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=_stub_discover(),
        fetch_reviews_fn=_stub_fetch(reviews),
        draft_reply_fn=_stub_draft(),
        dispatch_fn=dispatches,
    )
    assert len(dispatches) == 1
    assert dispatches[0]["review"]["reviewId"] == "rev-2"


def test_run_marks_no_text_reviews_seen_without_drafting(tenant_with_google):
    reviews = [
        _make_review("rev-1", stars="FIVE", comment=""),
        _make_review("rev-2", stars="FIVE", comment="   "),  # whitespace only
        _make_review("rev-3", stars="FIVE", comment="Real text"),
    ]
    hb = _Heartbeats()
    dispatches = _Dispatches()
    reviews_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=_stub_discover(),
        fetch_reviews_fn=_stub_fetch(reviews),
        draft_reply_fn=_stub_draft(),
        dispatch_fn=dispatches,
    )
    assert len(dispatches) == 1
    assert dispatches[0]["review"]["reviewId"] == "rev-3"
    summary = hb[-1]["summary"]
    assert "2 no-text" in summary
    # All three should still be marked seen
    state_path = tenant_with_google / "acme" / "pipeline_state" / "reviews.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert set(state["seen_review_ids"]) == {"rev-1", "rev-2", "rev-3"}


# ---------------------------------------------------------------------------
# events: review.posted only for 5-star
# ---------------------------------------------------------------------------


def test_run_emits_review_posted_event_for_five_star_only(tenant_with_google):
    reviews = [
        _make_review("rev-1", stars="FIVE", comment="great"),
        _make_review("rev-2", stars="FOUR", comment="ok"),
        _make_review("rev-3", stars="THREE", comment="meh"),
        _make_review("rev-4", stars="FIVE", comment=""),  # no text but still 5*
    ]
    hb = _Heartbeats()
    dispatches = _Dispatches()
    reviews_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=_stub_discover(),
        fetch_reviews_fn=_stub_fetch(reviews),
        draft_reply_fn=_stub_draft(),
        dispatch_fn=dispatches,
    )
    events = hb[-1]["events"]
    assert events is not None
    kinds = [e["kind"] for e in events]
    assert kinds == ["review.posted", "review.posted"]
    ids = [e["review_id"] for e in events]
    assert ids == ["rev-1", "rev-4"]
    for e in events:
        assert e["stars"] == 5


def test_run_no_events_when_no_five_star(tenant_with_google):
    reviews = [_make_review("rev-1", stars="THREE", comment="meh")]
    hb = _Heartbeats()
    dispatches = _Dispatches()
    reviews_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=_stub_discover(),
        fetch_reviews_fn=_stub_fetch(reviews),
        draft_reply_fn=_stub_draft(),
        dispatch_fn=dispatches,
    )
    assert hb[-1].get("events") is None


# ---------------------------------------------------------------------------
# state: write + cap + drafted_total accumulator
# ---------------------------------------------------------------------------


def test_run_persists_state_round_trip(tenant_with_google):
    reviews = [_make_review("rev-1", stars="FIVE", comment="great")]
    hb = _Heartbeats()
    reviews_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=_stub_discover(),
        fetch_reviews_fn=_stub_fetch(reviews),
        draft_reply_fn=_stub_draft(),
        dispatch_fn=_Dispatches(),
    )
    state_path = tenant_with_google / "acme" / "pipeline_state" / "reviews.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["seen_review_ids"] == ["rev-1"]
    assert state["drafted_total"] == 1
    assert "last_check" in state
    assert "updated_at" in state


def test_run_accumulates_drafted_total_across_runs(tenant_with_google):
    hb = _Heartbeats()
    reviews_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=_stub_discover(),
        fetch_reviews_fn=_stub_fetch([_make_review("rev-1")]),
        draft_reply_fn=_stub_draft(),
        dispatch_fn=_Dispatches(),
    )
    reviews_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=_stub_discover(),
        fetch_reviews_fn=_stub_fetch([_make_review("rev-2")]),
        draft_reply_fn=_stub_draft(),
        dispatch_fn=_Dispatches(),
    )
    state_path = tenant_with_google / "acme" / "pipeline_state" / "reviews.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["drafted_total"] == 2
    assert state["seen_review_ids"] == ["rev-1", "rev-2"]


def test_run_caps_seen_review_ids(tenant_with_google):
    state_path = tenant_with_google / "acme" / "pipeline_state" / "reviews.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    # Pre-load near the cap
    starting = [f"old-{i}" for i in range(reviews_run.SEEN_IDS_CAP - 1)]
    state_path.write_text(
        json.dumps({"seen_review_ids": starting}), encoding="utf-8"
    )
    # 5 new reviews push us 4 over the cap
    reviews = [_make_review(f"new-{i}", stars="FIVE", comment="great") for i in range(5)]
    reviews_run.run(
        "acme",
        heartbeat_fn=_Heartbeats(),
        discover_location_fn=_stub_discover(),
        fetch_reviews_fn=_stub_fetch(reviews),
        draft_reply_fn=_stub_draft(),
        dispatch_fn=_Dispatches(),
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(state["seen_review_ids"]) == reviews_run.SEEN_IDS_CAP
    # The newest reviews should still be present
    assert state["seen_review_ids"][-1] == "new-4"
    # The oldest got dropped
    assert "old-0" not in state["seen_review_ids"]


# ---------------------------------------------------------------------------
# dispatch outcome handling
# ---------------------------------------------------------------------------


def test_run_counts_failed_dispatch(tenant_with_google):
    reviews = [_make_review("rev-1", stars="FIVE", comment="hi")]
    hb = _Heartbeats()

    def failing_dispatch(*_args, **_kwargs):
        return {"action": "failed", "reason": "boom"}

    rc = reviews_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=_stub_discover(),
        fetch_reviews_fn=_stub_fetch(reviews),
        draft_reply_fn=_stub_draft(),
        dispatch_fn=failing_dispatch,
    )
    assert rc == 0
    assert hb[-1]["status"] == "error"  # all-failed = error
    assert "1 failed" in hb[-1]["summary"]


def test_run_breaks_on_mid_run_pause(tenant_with_google):
    reviews = [_make_review(f"rev-{i}", comment="hi") for i in range(3)]
    hb = _Heartbeats()
    dispatches: list[Any] = []

    def dispatch_with_pause(tenant_id, review, body, account_path, location_path):
        dispatches.append(review.get("reviewId"))
        if len(dispatches) == 2:
            return {"action": "skipped", "reason": "tenant_paused"}
        return {"action": "queued", "draft_id": "x"}

    reviews_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=_stub_discover(),
        fetch_reviews_fn=_stub_fetch(reviews),
        draft_reply_fn=_stub_draft(),
        dispatch_fn=dispatch_with_pause,
    )
    # Should bail after the second dispatch returns skipped
    assert dispatches == ["rev-0", "rev-1"]


# ---------------------------------------------------------------------------
# dry run
# ---------------------------------------------------------------------------


def test_dry_run_skips_dispatch_and_heartbeat(tenant_with_google, capsys):
    reviews = [_make_review("rev-1", stars="FIVE", comment="hi")]

    def hb_fn(**_kwargs):
        pytest.fail("dry-run must not push heartbeat")

    def dispatch_fn(*_args, **_kwargs):
        pytest.fail("dry-run must not dispatch")

    rc = reviews_run.run(
        "acme",
        dry_run=True,
        heartbeat_fn=hb_fn,
        discover_location_fn=_stub_discover(),
        fetch_reviews_fn=_stub_fetch(reviews),
        draft_reply_fn=_stub_draft("Test draft"),
        dispatch_fn=dispatch_fn,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "rev-1" in out
    assert "Test draft" in out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_passes_args_through(tenant_with_google, monkeypatch):
    received: dict = {}

    def fake_run(**kwargs):
        received.update(kwargs)
        return 0

    monkeypatch.setattr(reviews_run, "run", fake_run)
    rc = reviews_run.main(["--tenant", "acme", "--max", "5", "--dry-run"])
    assert rc == 0
    assert received["tenant_id"] == "acme"
    assert received["max_reviews"] == 5
    assert received["dry_run"] is True


# ---------------------------------------------------------------------------
# helpers: voice prompt + fallback
# ---------------------------------------------------------------------------


def test_build_voice_system_uses_default_when_no_kb(tenant_with_google):
    from wc_solns_pipelines.shared.tenant_runtime import TenantContext
    ctx = TenantContext("acme")
    out = reviews_run._build_voice_system(ctx)
    assert "warm" in out.lower()


def test_build_voice_system_includes_voice_kb(tenant_with_google):
    from dashboard_app.services import tenant_kb
    from wc_solns_pipelines.shared.tenant_runtime import TenantContext
    tenant_kb.write_section("acme", "voice", "Always sign off with 'See you on the trail.'")
    ctx = TenantContext("acme")
    out = reviews_run._build_voice_system(ctx)
    assert "See you on the trail" in out


def test_fallback_reply_uses_first_name():
    out = reviews_run._fallback_reply("Alice Smith", 5)
    assert "Alice" in out
    assert "Smith" not in out


def test_fallback_reply_handles_low_rating():
    out = reviews_run._fallback_reply("Bob", 1)
    assert "feedback" in out.lower()


# ---------------------------------------------------------------------------
# require_approval pref end-to-end (real dispatch.send, fake handler)
# ---------------------------------------------------------------------------


def test_run_queues_when_require_approval_set(tenant_with_google):
    """When prefs.require_approval[reviews] is True, dispatch.send writes a
    draft to the outgoing queue rather than calling the handler. Use the
    real dispatch.send to verify the wiring end-to-end (no mocks)."""
    _tenant_prefs.set_require_approval("acme", "reviews", True)
    reviews = [_make_review("rev-1", stars="FIVE", comment="great work")]
    hb = _Heartbeats()
    rc = reviews_run.run(
        "acme",
        heartbeat_fn=hb,
        discover_location_fn=_stub_discover(),
        fetch_reviews_fn=_stub_fetch(reviews),
        draft_reply_fn=_stub_draft("Thanks!"),
        # NOTE: no dispatch_fn override -> uses the real dispatch.send via _dispatch_one
    )
    assert rc == 0
    # The outgoing queue should now contain one pending draft
    from dashboard_app.services import outgoing_queue
    pending = outgoing_queue.list_pending("acme")
    assert len(pending) == 1
    assert pending[0]["pipeline_id"] == "reviews"
    assert pending[0]["body"] == "Thanks!"
    assert pending[0]["metadata"]["review_id"] == "rev-1"
    assert "1 queued" in hb[-1]["summary"]
