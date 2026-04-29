"""Tests for cost-tracking + budget-kill-switch instrumentation in
dashboard_app.services.brightlocal_master.

Covers:
  - Successful BrightLocal call records cost via record_call_for_vendor
  - Failed BrightLocal call (HTTPError, URLError, API-level error) does NOT
    record cost
  - When should_allow returns False, BrightLocalBudgetExceeded is raised
    BEFORE the HTTP call and no cost record is written
  - record_call_for_vendor is wired with the constant
    BRIGHTLOCAL_COST_PER_CALL_USD
"""

from __future__ import annotations

import json
import os
from typing import Any

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import brightlocal_master as bm
from dashboard_app.services import cost_tracker


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _seed_master(platform_root) -> None:
    base = platform_root / "brightlocal"
    base.mkdir(parents=True, exist_ok=True)
    (base / "master.json").write_text(
        json.dumps({"api_key": "live-key", "api_secret": "live-secret"}),
        encoding="utf-8",
    )


def _make_post_fn(response: dict[str, Any]):
    captured: dict = {"calls": 0}

    def fn(url: str, fields: dict[str, str], timeout: float) -> dict[str, Any]:
        captured["calls"] += 1
        captured["url"] = url
        captured["fields"] = dict(fields)
        return response

    fn.captured = captured  # type: ignore[attr-defined]
    return fn


def _make_get_fn(response: dict[str, Any]):
    captured: dict = {"calls": 0}

    def fn(url: str, params: dict[str, str], timeout: float) -> dict[str, Any]:
        captured["calls"] += 1
        captured["url"] = url
        captured["params"] = dict(params)
        return response

    fn.captured = captured  # type: ignore[attr-defined]
    return fn


def _read_jsonl(path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


@pytest.fixture
def provisioned(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_ROOT", str(tmp_path / "_platform"))
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    monkeypatch.setenv("COST_LOG_PATH", str(tmp_path / "cost_log.jsonl"))
    _seed_master(tmp_path / "_platform")
    return tmp_path


# ---------------------------------------------------------------------------
# successful call records cost
# ---------------------------------------------------------------------------


def test_add_tenant_location_records_cost_on_success(provisioned):
    post = _make_post_fn({"success": True, "location-id": "loc-987"})
    bm.add_tenant_location(
        "acme",
        biz_name="Acme HVAC",
        address="123 Main St",
        city="Oxnard",
        state="CA",
        postcode="93030",
        post_fn=post,
    )
    rows = _read_jsonl(provisioned / "cost_log.jsonl")
    assert len(rows) == 1
    assert rows[0]["vendor"] == "brightlocal"
    assert rows[0]["tenant_id"] == "acme"
    assert rows[0]["kind"] == "add_tenant_location"
    assert rows[0]["usd"] == bm.BRIGHTLOCAL_COST_PER_CALL_USD


def test_fetch_rankings_records_cost_on_success(provisioned):
    (provisioned / "acme").mkdir()
    (provisioned / "acme" / "tenant_config.json").write_text(
        json.dumps({"brightlocal_location_id": "loc-1"}), encoding="utf-8"
    )
    get = _make_get_fn({"success": True, "results": []})
    bm.fetch_rankings("acme", get_fn=get)
    rows = _read_jsonl(provisioned / "cost_log.jsonl")
    assert len(rows) == 1
    assert rows[0]["vendor"] == "brightlocal"
    assert rows[0]["tenant_id"] == "acme"
    assert rows[0]["kind"] == "fetch_rankings"
    assert rows[0]["usd"] == bm.BRIGHTLOCAL_COST_PER_CALL_USD


# ---------------------------------------------------------------------------
# failures do NOT record cost
# ---------------------------------------------------------------------------


def test_add_tenant_location_does_not_record_on_http_failure(provisioned):
    def boom(*_a, **_kw):
        from urllib.error import URLError
        raise URLError("connection refused")

    with pytest.raises(bm.BrightLocalError):
        bm.add_tenant_location(
            "acme",
            biz_name="x",
            address="x",
            city="y",
            state="z",
            postcode="00000",
            post_fn=boom,
        )
    rows = _read_jsonl(provisioned / "cost_log.jsonl")
    assert rows == []


def test_add_tenant_location_does_not_record_on_api_error(provisioned):
    post = _make_post_fn({"success": False, "errors": ["invalid postcode"]})
    with pytest.raises(bm.BrightLocalError):
        bm.add_tenant_location(
            "acme",
            biz_name="Acme",
            address="x",
            city="y",
            state="z",
            postcode="bad",
            post_fn=post,
        )
    rows = _read_jsonl(provisioned / "cost_log.jsonl")
    assert rows == []


def test_fetch_rankings_does_not_record_on_http_failure(provisioned):
    (provisioned / "acme").mkdir()
    (provisioned / "acme" / "tenant_config.json").write_text(
        json.dumps({"brightlocal_location_id": "loc-1"}), encoding="utf-8"
    )

    def boom(*_a, **_kw):
        from urllib.error import HTTPError
        raise HTTPError("https://x", 500, "bad", {}, None)  # type: ignore[arg-type]

    with pytest.raises(bm.BrightLocalError):
        bm.fetch_rankings("acme", get_fn=boom)
    rows = _read_jsonl(provisioned / "cost_log.jsonl")
    assert rows == []


def test_fetch_rankings_does_not_record_on_api_error(provisioned):
    (provisioned / "acme").mkdir()
    (provisioned / "acme" / "tenant_config.json").write_text(
        json.dumps({"brightlocal_location_id": "loc-1"}), encoding="utf-8"
    )
    get = _make_get_fn({"success": False, "errors": ["rate limit"]})
    with pytest.raises(bm.BrightLocalError):
        bm.fetch_rankings("acme", get_fn=get)
    rows = _read_jsonl(provisioned / "cost_log.jsonl")
    assert rows == []


# ---------------------------------------------------------------------------
# kill switch: should_allow=False blocks the call
# ---------------------------------------------------------------------------


def test_add_tenant_location_raises_budget_exceeded_when_capped(provisioned, monkeypatch):
    def deny(_tenant_id):
        return False, "Daily tenant cap reached ($2.00)"

    monkeypatch.setattr(cost_tracker, "should_allow", deny)
    post = _make_post_fn({"success": True, "location-id": "loc-987"})
    with pytest.raises(bm.BrightLocalBudgetExceeded):
        bm.add_tenant_location(
            "acme",
            biz_name="Acme",
            address="x",
            city="y",
            state="z",
            postcode="00000",
            post_fn=post,
        )
    # HTTP layer should not have been called when budget is exhausted.
    assert post.captured["calls"] == 0
    rows = _read_jsonl(provisioned / "cost_log.jsonl")
    assert rows == []


def test_fetch_rankings_raises_budget_exceeded_when_capped(provisioned, monkeypatch):
    (provisioned / "acme").mkdir()
    (provisioned / "acme" / "tenant_config.json").write_text(
        json.dumps({"brightlocal_location_id": "loc-1"}), encoding="utf-8"
    )

    def deny(_tenant_id):
        return False, "Daily platform cap reached ($20.00)"

    monkeypatch.setattr(cost_tracker, "should_allow", deny)
    get = _make_get_fn({"success": True, "results": []})
    with pytest.raises(bm.BrightLocalBudgetExceeded):
        bm.fetch_rankings("acme", get_fn=get)
    assert get.captured["calls"] == 0
    rows = _read_jsonl(provisioned / "cost_log.jsonl")
    assert rows == []


def test_brightlocal_budget_exceeded_is_brightlocal_error_subclass():
    # Callers that broadly except BrightLocalError should still catch this.
    assert issubclass(bm.BrightLocalBudgetExceeded, bm.BrightLocalError)


def test_cost_per_call_constant_is_set():
    assert isinstance(bm.BRIGHTLOCAL_COST_PER_CALL_USD, float)
    assert bm.BRIGHTLOCAL_COST_PER_CALL_USD > 0
