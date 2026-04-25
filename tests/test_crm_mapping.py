"""Persistence tests for crm_mapping. Pure file-system; no network."""

import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import crm_mapping


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    yield


def _segments() -> list[dict]:
    return [
        {"slug": "active", "label": "Active", "count": 15, "sample_names": ["Sofia", "Diego"]},
        {"slug": "inactive_30d", "label": "Inactive 30+ days", "count": 12,
         "sample_names": ["Maria Sanchez", "Juan Diaz"]},
        {"slug": "brand_new", "label": "Brand new", "count": 3, "sample_names": ["Olivia"]},
    ]


def test_save_returns_mapping_id():
    payload = crm_mapping.save(
        "acme",
        base_id="appXXX",
        table_name="Students",
        field_mapping={"first_name": "Child Name"},
        segments=_segments(),
    )
    assert payload["mapping_id"].startswith("cm_")
    assert payload["table_name"] == "Students"
    assert payload["accepted"] is False


def test_save_then_load_roundtrip():
    saved = crm_mapping.save(
        "acme", base_id="appXXX", table_name="Students",
        field_mapping={"a": "b"}, segments=_segments(),
    )
    loaded = crm_mapping.load("acme")
    assert loaded == saved


def test_load_returns_none_when_missing():
    assert crm_mapping.load("acme") is None


def test_save_caps_sample_names_per_segment():
    long_seg = {
        "slug": "x", "label": "X", "count": 99,
        "sample_names": ["a", "b", "c", "d", "e", "f", "g", "h"],
    }
    payload = crm_mapping.save(
        "acme", base_id="appXXX", table_name="T",
        field_mapping={"a": "b"}, segments=[long_seg],
    )
    assert len(payload["segments"][0]["sample_names"]) == 5


def test_mark_accepted_flips_flag():
    saved = crm_mapping.save(
        "acme", base_id="appXXX", table_name="T",
        field_mapping={"a": "b"}, segments=_segments(),
    )
    updated = crm_mapping.mark_accepted("acme", mapping_id=saved["mapping_id"], edits={})
    assert updated is not None
    assert updated["accepted"] is True
    assert updated["accepted_at"] is not None


def test_mark_accepted_returns_none_on_mapping_id_mismatch():
    crm_mapping.save(
        "acme", base_id="appXXX", table_name="T",
        field_mapping={"a": "b"}, segments=_segments(),
    )
    assert crm_mapping.mark_accepted("acme", mapping_id="cm_wrong", edits={}) is None


def test_first_inactive_for_simulation_picks_first_named():
    crm_mapping.save(
        "acme", base_id="appXXX", table_name="T",
        field_mapping={"a": "b"}, segments=_segments(),
    )
    target = crm_mapping.first_inactive_for_simulation("acme")
    assert target is not None
    assert target["name"] == "Maria Sanchez"
    assert target["days_inactive"] == 37


def test_first_inactive_for_simulation_returns_none_without_segment():
    crm_mapping.save(
        "acme", base_id="appXXX", table_name="T",
        field_mapping={"a": "b"},
        segments=[{"slug": "active", "label": "A", "count": 1, "sample_names": ["X"]}],
    )
    assert crm_mapping.first_inactive_for_simulation("acme") is None


def test_first_inactive_for_simulation_returns_none_when_segment_empty():
    crm_mapping.save(
        "acme", base_id="appXXX", table_name="T",
        field_mapping={"a": "b"},
        segments=[{"slug": "inactive_30d", "label": "L", "count": 0, "sample_names": []}],
    )
    assert crm_mapping.first_inactive_for_simulation("acme") is None
