"""Persistence tests for voice_card. Pure file-system; no network."""

import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import voice_card


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    yield


def test_save_returns_payload_with_card_id():
    payload = voice_card.save(
        "acme",
        traits=["warm", "bilingual"],
        generic_sample="Hi.",
        voice_sample="Hola.",
        sample_context="greeting",
    )
    assert payload["card_id"].startswith("vc_")
    assert payload["traits"] == ["warm", "bilingual"]
    assert payload["accepted"] is False
    assert payload["accepted_at"] is None


def test_load_returns_none_when_missing():
    assert voice_card.load("acme") is None


def test_save_then_load_roundtrip():
    saved = voice_card.save("acme", traits=["warm"], generic_sample="g", voice_sample="v")
    loaded = voice_card.load("acme")
    assert loaded == saved


def test_save_strips_blank_traits_and_caps_at_six():
    payload = voice_card.save(
        "acme",
        traits=["a", "  ", "b", "", "c", "d", "e", "f", "g"],
        generic_sample="g",
        voice_sample="v",
    )
    assert payload["traits"] == ["a", "b", "c", "d", "e", "f"]


def test_mark_accepted_flips_flag_and_persists_edits():
    saved = voice_card.save("acme", traits=["warm"], generic_sample="g", voice_sample="v")
    updated = voice_card.mark_accepted(
        "acme",
        card_id=saved["card_id"],
        edits={"voice_sample": "v - edited", "traits": ["warm", "polished"]},
    )
    assert updated is not None
    assert updated["accepted"] is True
    assert updated["accepted_at"] is not None
    assert updated["voice_sample"] == "v - edited"
    assert updated["traits"] == ["warm", "polished"]
    # Reloading from disk preserves the acceptance.
    again = voice_card.load("acme")
    assert again["accepted"] is True


def test_mark_accepted_returns_none_on_card_id_mismatch():
    voice_card.save("acme", traits=["warm"], generic_sample="g", voice_sample="v")
    result = voice_card.mark_accepted("acme", card_id="vc_wrong", edits={})
    assert result is None


def test_save_caps_source_pages_at_five():
    payload = voice_card.save(
        "acme",
        traits=["warm"],
        generic_sample="g",
        voice_sample="v",
        source_pages=["a", "b", "c", "d", "e", "f", "g"],
    )
    assert len(payload["source_pages"]) == 5
