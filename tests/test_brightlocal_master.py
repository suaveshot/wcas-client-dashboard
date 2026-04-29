"""Tests for dashboard_app.services.brightlocal_master.

Covers:
  - BrightLocalNotProvisioned when master.json missing
  - add_tenant_location calls the API, parses the id, persists to tenant_config
  - add_tenant_location is idempotent (won't re-call if already provisioned)
  - fetch_rankings reads the location id from tenant_config
  - sig + auth params are present on each call
  - error paths (errors in response, no id returned, HTTP failure)
  - tenant_runtime stays free of brightlocal_master imports (Pattern C invariant)
"""

from __future__ import annotations

import json
import os
from typing import Any

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import brightlocal_master as bm


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _seed_master(platform_root, **fields) -> None:
    """Drop a master.json into the given platform root (already
    points at the _platform/ directory)."""
    base = platform_root / "brightlocal"
    base.mkdir(parents=True, exist_ok=True)
    payload = {"api_key": "live-key", "api_secret": "live-secret"}
    payload.update(fields)
    (base / "master.json").write_text(json.dumps(payload), encoding="utf-8")


def _make_post_fn(response: dict[str, Any]):
    captured: dict = {}

    def fn(url: str, fields: dict[str, str], timeout: float) -> dict[str, Any]:
        captured["url"] = url
        captured["fields"] = dict(fields)
        captured["timeout"] = timeout
        return response

    fn.captured = captured  # type: ignore[attr-defined]
    return fn


def _make_get_fn(response: dict[str, Any]):
    captured: dict = {}

    def fn(url: str, params: dict[str, str], timeout: float) -> dict[str, Any]:
        captured["url"] = url
        captured["params"] = dict(params)
        captured["timeout"] = timeout
        return response

    fn.captured = captured  # type: ignore[attr-defined]
    return fn


# ---------------------------------------------------------------------------
# NotProvisioned cases
# ---------------------------------------------------------------------------


def test_add_tenant_location_raises_when_master_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_ROOT", str(tmp_path / "_platform"))
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    with pytest.raises(bm.BrightLocalNotProvisioned):
        bm.add_tenant_location(
            "acme",
            biz_name="Acme HVAC",
            address="123 Main",
            city="Oxnard",
            state="CA",
            postcode="93030",
            post_fn=_make_post_fn({}),
        )


def test_fetch_rankings_raises_when_no_location(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_ROOT", str(tmp_path / "_platform"))
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_master(tmp_path / "_platform")
    with pytest.raises(bm.BrightLocalNotProvisioned):
        bm.fetch_rankings("acme", get_fn=_make_get_fn({}))


def test_fetch_rankings_raises_when_master_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_ROOT", str(tmp_path / "_platform"))
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    # Stamp a location_id directly to bypass the no-location guard
    tdir = tmp_path / "acme"
    tdir.mkdir()
    (tdir / "tenant_config.json").write_text(
        json.dumps({"brightlocal_location_id": "loc-123"}), encoding="utf-8"
    )
    with pytest.raises(bm.BrightLocalNotProvisioned):
        bm.fetch_rankings("acme", get_fn=_make_get_fn({}))


def test_master_missing_api_key_field_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_ROOT", str(tmp_path / "_platform"))
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    base = tmp_path / "_platform" / "brightlocal"
    base.mkdir(parents=True, exist_ok=True)
    (base / "master.json").write_text(json.dumps({"some_other_field": "x"}), encoding="utf-8")
    with pytest.raises(bm.BrightLocalNotProvisioned):
        bm.add_tenant_location(
            "acme",
            biz_name="Acme",
            address="x",
            city="y",
            state="z",
            postcode="00000",
            post_fn=_make_post_fn({}),
        )


# ---------------------------------------------------------------------------
# add_tenant_location happy path
# ---------------------------------------------------------------------------


@pytest.fixture
def provisioned(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_ROOT", str(tmp_path / "_platform"))
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_master(tmp_path / "_platform")
    return tmp_path


def test_add_tenant_location_persists_id(provisioned):
    post = _make_post_fn({"success": True, "location-id": "loc-987"})
    location_id = bm.add_tenant_location(
        "acme",
        biz_name="Acme HVAC",
        address="123 Main St",
        city="Oxnard",
        state="CA",
        postcode="93030",
        lat=34.2,
        lng=-119.18,
        keywords=["ac repair oxnard", "hvac oxnard"],
        post_fn=post,
    )
    assert location_id == "loc-987"
    cfg_path = provisioned / "acme" / "tenant_config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert cfg["brightlocal_location_id"] == "loc-987"
    assert cfg["brightlocal_provisioned_at"]
    # auth params on the wire
    assert post.captured["url"].endswith("/v4/lsr/create-campaign")
    assert post.captured["fields"]["api-key"] == "live-key"
    assert "sig" in post.captured["fields"]
    assert "expires" in post.captured["fields"]
    # business fields on the wire
    assert post.captured["fields"]["business-name"] == "Acme HVAC"
    assert post.captured["fields"]["postcode"] == "93030"
    assert post.captured["fields"]["lat"] == "34.2"
    assert "ac repair oxnard" in post.captured["fields"]["keywords"]


def test_add_tenant_location_idempotent(provisioned):
    """Second call with a location already in tenant_config should return
    the existing id without hitting the API."""
    cfg_path = provisioned / "acme" / "tenant_config.json"
    (provisioned / "acme").mkdir()
    cfg_path.write_text(json.dumps({"brightlocal_location_id": "existing-1"}), encoding="utf-8")

    def boom(*_a, **_kw):
        pytest.fail("API must not be called when already provisioned")

    location_id = bm.add_tenant_location(
        "acme",
        biz_name="x",
        address="x",
        city="x",
        state="x",
        postcode="x",
        post_fn=boom,
    )
    assert location_id == "existing-1"


def test_add_tenant_location_handles_underscored_id_field(provisioned):
    """BrightLocal sometimes returns location_id (underscore) vs location-id (dash);
    the code should accept either."""
    post = _make_post_fn({"success": True, "location_id": "loc-555"})
    out = bm.add_tenant_location(
        "acme",
        biz_name="Acme",
        address="x",
        city="y",
        state="z",
        postcode="00000",
        post_fn=post,
    )
    assert out == "loc-555"


def test_add_tenant_location_raises_on_api_error(provisioned):
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
    cfg_path = provisioned / "acme" / "tenant_config.json"
    assert not cfg_path.exists()  # no partial write


def test_add_tenant_location_raises_when_no_id_returned(provisioned):
    post = _make_post_fn({"success": True})  # missing id
    with pytest.raises(bm.BrightLocalError):
        bm.add_tenant_location(
            "acme",
            biz_name="x",
            address="x",
            city="x",
            state="x",
            postcode="x",
            post_fn=post,
        )


def test_add_tenant_location_raises_on_http_failure(provisioned):
    def boom(*_a, **_kw):
        from urllib.error import URLError
        raise URLError("connection refused")

    with pytest.raises(bm.BrightLocalError):
        bm.add_tenant_location(
            "acme",
            biz_name="x",
            address="x",
            city="x",
            state="x",
            postcode="x",
            post_fn=boom,
        )


# ---------------------------------------------------------------------------
# fetch_rankings
# ---------------------------------------------------------------------------


def test_fetch_rankings_returns_payload(provisioned):
    cfg_path = provisioned / "acme" / "tenant_config.json"
    (provisioned / "acme").mkdir()
    cfg_path.write_text(json.dumps({"brightlocal_location_id": "loc-1"}), encoding="utf-8")
    canned = {
        "success": True,
        "results": [
            {"keyword": "ac repair", "rank": 4, "search-engine": "google"},
            {"keyword": "hvac oxnard", "rank": 11, "search-engine": "google"},
        ],
    }
    get = _make_get_fn(canned)
    out = bm.fetch_rankings("acme", get_fn=get)
    assert out == canned
    assert get.captured["params"]["location-id"] == "loc-1"
    assert get.captured["params"]["api-key"] == "live-key"
    assert "sig" in get.captured["params"]
    assert get.captured["url"].endswith("/v4/lsr/get-search-results")


def test_fetch_rankings_raises_on_api_error(provisioned):
    cfg_path = provisioned / "acme" / "tenant_config.json"
    (provisioned / "acme").mkdir()
    cfg_path.write_text(json.dumps({"brightlocal_location_id": "loc-1"}), encoding="utf-8")
    with pytest.raises(bm.BrightLocalError):
        bm.fetch_rankings(
            "acme",
            get_fn=_make_get_fn({"success": False, "errors": ["rate limit"]}),
        )


# ---------------------------------------------------------------------------
# get_tenant_location_id helper
# ---------------------------------------------------------------------------


def test_get_tenant_location_id_returns_none_for_unprovisioned(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    assert bm.get_tenant_location_id("acme") is None


def test_get_tenant_location_id_returns_str(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    (tmp_path / "acme").mkdir()
    (tmp_path / "acme" / "tenant_config.json").write_text(
        json.dumps({"brightlocal_location_id": "loc-7"}), encoding="utf-8"
    )
    assert bm.get_tenant_location_id("acme") == "loc-7"


def test_get_tenant_location_id_returns_none_for_blank_value(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    (tmp_path / "acme").mkdir()
    (tmp_path / "acme" / "tenant_config.json").write_text(
        json.dumps({"brightlocal_location_id": "  "}), encoding="utf-8"
    )
    assert bm.get_tenant_location_id("acme") is None


# ---------------------------------------------------------------------------
# is_provisioned
# ---------------------------------------------------------------------------


def test_is_provisioned_reflects_master_presence(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_ROOT", str(tmp_path / "_platform"))
    assert bm.is_provisioned() is False
    _seed_master(tmp_path / "_platform")
    assert bm.is_provisioned() is True


# ---------------------------------------------------------------------------
# auth signing
# ---------------------------------------------------------------------------


def test_sign_produces_stable_sig_for_same_inputs():
    a = bm._sign("k", "s", expires=1700000000)
    b = bm._sign("k", "s", expires=1700000000)
    assert a == b


def test_sign_differs_when_secret_changes():
    a = bm._sign("k", "s1", expires=1700000000)
    b = bm._sign("k", "s2", expires=1700000000)
    assert a["sig"] != b["sig"]


def test_sign_passes_expires_in_response():
    out = bm._sign("k", "s", expires=1700000000)
    assert out["expires"] == "1700000000"
    assert out["api-key"] == "k"


# ---------------------------------------------------------------------------
# Pattern C invariant: tenant_runtime stays clean
# ---------------------------------------------------------------------------


def test_tenant_runtime_does_not_import_brightlocal_master():
    from wc_solns_pipelines.shared import tenant_runtime
    text = open(tenant_runtime.__file__, encoding="utf-8").read()
    assert "brightlocal_master" not in text


def test_tenant_runtime_does_not_expose_brightlocal_methods():
    from wc_solns_pipelines.shared import tenant_runtime
    assert not hasattr(tenant_runtime, "brightlocal_master")
    assert not hasattr(tenant_runtime, "add_tenant_location")
    assert not hasattr(tenant_runtime, "fetch_rankings")
