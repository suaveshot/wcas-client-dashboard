"""Tests for the per-tenant activation ring-grid state machine."""

import json
import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import activation_state, heartbeat_store


@pytest.fixture
def _tenant_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    return tmp_path


def test_get_empty_for_new_tenant(_tenant_root):
    state = activation_state.get("acme")
    assert state == {"updated_at": None, "roles": {}}


def test_advance_first_step(_tenant_root):
    state = activation_state.advance("acme", "gbp", "credentials")
    assert state["roles"]["gbp"]["step"] == "credentials"
    assert state["roles"]["gbp"]["step_at"]
    assert state["updated_at"]


def test_advance_is_monotonic(_tenant_root):
    activation_state.advance("acme", "gbp", "credentials")
    activation_state.advance("acme", "gbp", "connected")
    assert activation_state.role_step("acme", "gbp") == "connected"


def test_advance_rejects_regression(_tenant_root):
    activation_state.advance("acme", "gbp", "connected")
    with pytest.raises(activation_state.ActivationError):
        activation_state.advance("acme", "gbp", "credentials")


def test_advance_idempotent_at_same_step(_tenant_root):
    first = activation_state.advance("acme", "gbp", "credentials")
    step_at_first = first["roles"]["gbp"]["step_at"]
    # Same step called again: no new write, step_at unchanged.
    second = activation_state.advance("acme", "gbp", "credentials")
    assert second["roles"]["gbp"]["step_at"] == step_at_first


def test_bulk_advance_six_roles(_tenant_root):
    roles = ["gbp", "seo", "reviews", "gmail", "calendar", "ads"]
    state = activation_state.bulk_advance("acme", roles, "credentials")
    for slug in roles:
        assert state["roles"][slug]["step"] == "credentials"


def test_bulk_advance_skips_already_past(_tenant_root):
    activation_state.advance("acme", "gbp", "connected")
    # Promote gbp to connected; then bulk_advance to credentials shouldn't regress it.
    state = activation_state.bulk_advance("acme", ["gbp", "seo"], "credentials")
    assert state["roles"]["gbp"]["step"] == "connected"
    assert state["roles"]["seo"]["step"] == "credentials"


def test_bulk_advance_noop_when_nothing_changes(_tenant_root):
    activation_state.bulk_advance("acme", ["gbp", "seo"], "credentials")
    path = heartbeat_store.tenant_root("acme") / "activation.json"
    mtime_before = path.stat().st_mtime_ns
    # Re-run at same step, same roles: no write should happen.
    activation_state.bulk_advance("acme", ["gbp", "seo"], "credentials")
    assert path.stat().st_mtime_ns == mtime_before


def test_unknown_step_rejected(_tenant_root):
    with pytest.raises(activation_state.ActivationError):
        activation_state.advance("acme", "gbp", "done")
    with pytest.raises(activation_state.ActivationError):
        activation_state.bulk_advance("acme", ["gbp"], "nope")


def test_invalid_slug_rejected(_tenant_root):
    with pytest.raises(activation_state.ActivationError):
        activation_state.advance("acme", "With Space", "credentials")
    with pytest.raises(activation_state.ActivationError):
        activation_state.advance("acme", "../escape", "credentials")
    with pytest.raises(activation_state.ActivationError):
        activation_state.role_step("acme", "UPPER")


def test_invalid_tenant_rejected(_tenant_root):
    with pytest.raises(heartbeat_store.HeartbeatError):
        activation_state.advance("../escape", "gbp", "credentials")


def test_reset_role_removes_entry(_tenant_root):
    activation_state.advance("acme", "gbp", "connected")
    assert activation_state.reset_role("acme", "gbp") is True
    assert activation_state.role_step("acme", "gbp") is None
    # Reset again: no-op, returns False.
    assert activation_state.reset_role("acme", "gbp") is False


def test_role_step_none_for_unstarted(_tenant_root):
    assert activation_state.role_step("acme", "gbp") is None


def test_ring_view_fills_correct_percents(_tenant_root):
    activation_state.advance("acme", "gbp", "connected")
    activation_state.advance("acme", "seo", "credentials")
    view = activation_state.ring_view("acme", ["gbp", "seo", "gmail"])
    assert view[0] == {"slug": "gbp", "step": "connected", "percent_complete": 0.75}
    assert view[1] == {"slug": "seo", "step": "credentials", "percent_complete": 0.25}
    assert view[2] == {"slug": "gmail", "step": None, "percent_complete": 0.0}


def test_corrupt_state_file_treated_as_empty(_tenant_root):
    path = heartbeat_store.tenant_root("acme") / "activation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("this is not json", encoding="utf-8")
    assert activation_state.get("acme") == {"updated_at": None, "roles": {}}


def test_get_tolerates_wrong_roles_shape(_tenant_root):
    path = heartbeat_store.tenant_root("acme") / "activation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"updated_at": "now", "roles": ["not", "a", "dict"]}), encoding="utf-8")
    assert activation_state.get("acme") == {"updated_at": None, "roles": {}}


def test_concurrent_writes_dont_corrupt(_tenant_root):
    # Simulate two back-to-back writes on the same state file. Atomic
    # os.replace should mean the file is always parseable.
    activation_state.advance("acme", "gbp", "credentials")
    activation_state.advance("acme", "seo", "credentials")
    activation_state.advance("acme", "reviews", "credentials")
    state = activation_state.get("acme")
    assert {"gbp", "seo", "reviews"} <= set(state["roles"].keys())
