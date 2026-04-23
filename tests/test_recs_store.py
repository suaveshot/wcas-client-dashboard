"""Tests for the per-tenant recs persistence layer."""

import json
import os
import time
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import heartbeat_store, recs_store


def _sample_recs():
    return [
        {"id": "abc", "headline": "Ads pacing low.", "draft": False, "confidence": 8},
        {"id": "def", "headline": "GBP offline.", "draft": False, "confidence": 9},
    ]


def test_write_today_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    path = recs_store.write_today(
        "acme",
        recs=_sample_recs(),
        model="claude-haiku-4-5",
        usd=0.0042,
        input_tokens=1234,
        output_tokens=234,
    )
    assert path.exists()
    data = recs_store.read_latest("acme")
    assert data is not None
    assert data["model"] == "claude-haiku-4-5"
    assert data["count"] == 2
    assert len(data["recs"]) == 2
    assert data["recs"][0]["headline"] == "Ads pacing low."
    assert data["usd"] == pytest.approx(0.0042)


def test_write_today_overwrites_same_day(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    recs_store.write_today("acme", recs=[{"id": "1"}], model="m", usd=0.01)
    recs_store.write_today("acme", recs=[{"id": "1"}, {"id": "2"}], model="m", usd=0.02)
    data = recs_store.read_latest("acme")
    assert data["count"] == 2


def test_read_latest_returns_none_when_no_files(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    assert recs_store.read_latest("never_existed") is None


def test_read_latest_picks_newest(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    root = heartbeat_store.tenant_root("acme") / "recs"
    root.mkdir(parents=True, exist_ok=True)
    older = root / "2026-04-20.json"
    older.write_text(json.dumps({
        "generated_at": "2026-04-20T08:00:00+00:00",
        "model": "old",
        "recs": [{"id": "old"}],
    }), encoding="utf-8")
    # ensure mtime separation on filesystems with low resolution
    time.sleep(0.01)
    newer_path = recs_store.write_today(
        "acme",
        recs=[{"id": "new"}],
        model="new",
        usd=0.001,
    )
    latest = recs_store.read_latest("acme")
    assert latest["model"] == "new"
    assert latest["recs"][0]["id"] == "new"
    assert newer_path.exists() and older.exists()


def test_path_traversal_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    with pytest.raises(heartbeat_store.HeartbeatError):
        recs_store.write_today("../escape", recs=[], model="m", usd=0.0)
    with pytest.raises(heartbeat_store.HeartbeatError):
        recs_store.write_today("WITH.DOTS", recs=[], model="m", usd=0.0)


def test_is_fresh_true_for_recent_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    payload = {"generated_at": datetime.now(timezone.utc).isoformat()}
    assert recs_store.is_fresh(payload) is True


def test_is_fresh_false_for_old_file():
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    assert recs_store.is_fresh({"generated_at": long_ago}) is False


def test_is_fresh_false_for_none():
    assert recs_store.is_fresh(None) is False
    assert recs_store.is_fresh({}) is False
    assert recs_store.is_fresh({"generated_at": "not-a-date"}) is False


def test_list_dates_returns_iso_dates_newest_first(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    root = heartbeat_store.tenant_root("acme") / "recs"
    root.mkdir(parents=True, exist_ok=True)
    for stem in ("2026-04-20", "2026-04-22", "2026-04-21", "garbage-name"):
        (root / f"{stem}.json").write_text("{}", encoding="utf-8")
    dates = recs_store.list_dates("acme")
    assert dates == ["2026-04-22", "2026-04-21", "2026-04-20"]


def test_list_dates_for_unknown_tenant(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    assert recs_store.list_dates("never_existed") == []
