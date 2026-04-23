"""Tests for the real-Opus recommendations generator.

We never hit the real API in CI. Every test mocks `opus.chat` at the
module boundary and asserts the generator's parsing + finalize behavior.
"""

import json
import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import heartbeat_store, opus, recs_generator


def _good_rec_dict():
    return {
        "goal": "GROW REVIEWS",
        "role_slug": "ads",
        "headline": "Ads is pacing 18% under goal this month.",
        "reason": "Nine keyword drops since Monday. Raising the CPC cap on Brand by $0.40 should recover the lost traffic within a week.",
        "proposed_tool": "update_tenant_config",
        "proposed_args": {"path": "ads.brand.cpc_cap", "value": 2.40},
        "impact": {"metric": "brand_impressions", "estimate": 12, "unit": "%",
                   "calculation": "3-week avg impressions 820/day; 18% lift = ~148 more/day"},
        "confidence": 7,
        "reversibility": "instant",
        "evidence": [
            {"source": "heartbeat", "datapoint": "ads.pacing_pct", "value": -0.18, "observed_at": "2026-04-22T08:00:00Z"},
        ],
    }


def _fake_opus_result(text: str, *, usd: float = 0.005) -> opus.OpusResult:
    return opus.OpusResult(
        text=text,
        model="claude-haiku-4-5",
        input_tokens=1234,
        output_tokens=234,
        usd=usd,
        stop_reason="end_turn",
    )


def _seed_one_heartbeat(tenant_id: str = "acme"):
    """Give the global_ask composer something non-empty to package."""
    heartbeat_store.write_snapshot(tenant_id, "ads", {
        "status": "error",
        "last_run": "2026-04-14T07:01:00+00:00",
        "summary": "OAuth token expired",
    })


# -----------------------------------------------------------------------------
# _parse_recs unit tests (no opus call needed)
# -----------------------------------------------------------------------------


def test_parse_recs_accepts_object_with_recommendations_key():
    out = recs_generator._parse_recs(json.dumps({"recommendations": [{"a": 1}, {"b": 2}]}))
    assert len(out) == 2
    assert out[0]["a"] == 1


def test_parse_recs_accepts_bare_array():
    out = recs_generator._parse_recs(json.dumps([{"a": 1}]))
    assert len(out) == 1


def test_parse_recs_strips_json_fence():
    text = "```json\n" + json.dumps({"recommendations": [{"a": 1}]}) + "\n```"
    out = recs_generator._parse_recs(text)
    assert len(out) == 1


def test_parse_recs_caps_at_max():
    payload = {"recommendations": [{"i": i} for i in range(20)]}
    out = recs_generator._parse_recs(json.dumps(payload))
    assert len(out) == recs_generator._MAX_RECS


def test_parse_recs_raises_on_empty():
    with pytest.raises(recs_generator.RecsGenerationError):
        recs_generator._parse_recs("")


def test_parse_recs_raises_on_prose():
    with pytest.raises(recs_generator.RecsGenerationError):
        recs_generator._parse_recs("Sure! Here are some recommendations for you.")


def test_parse_recs_raises_on_unexpected_shape():
    with pytest.raises(recs_generator.RecsGenerationError):
        recs_generator._parse_recs(json.dumps({"other_key": []}))


def test_parse_recs_drops_non_dict_items():
    payload = {"recommendations": [{"a": 1}, "not a dict", 42, {"b": 2}]}
    out = recs_generator._parse_recs(json.dumps(payload))
    assert len(out) == 2


# -----------------------------------------------------------------------------
# generate() integration tests with mocked opus.chat
# -----------------------------------------------------------------------------


def test_generate_happy_path(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_one_heartbeat("acme_happy")

    monkeypatch.setattr(opus, "chat", lambda **kw: _fake_opus_result(
        json.dumps({"recommendations": [_good_rec_dict(), _good_rec_dict() | {"headline": "Reviews look healthy."}]})
    ))

    result = recs_generator.generate("acme_happy")
    assert result["model"] == "claude-haiku-4-5"
    assert result["usd"] == 0.005
    assert len(result["recs"]) == 2
    assert all(not r.get("draft") for r in result["recs"])
    assert all("id" in r for r in result["recs"])


def test_generate_marks_draft_on_low_confidence(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_one_heartbeat("acme_mixed")

    rec_low = _good_rec_dict()
    rec_low["confidence"] = 2  # below threshold
    rec_low["headline"] = "Speculative idea about ads timing."
    monkeypatch.setattr(opus, "chat", lambda **kw: _fake_opus_result(
        json.dumps({"recommendations": [_good_rec_dict(), rec_low]})
    ))

    result = recs_generator.generate("acme_mixed")
    assert len(result["recs"]) == 2
    live = [r for r in result["recs"] if not r.get("draft")]
    drafts = [r for r in result["recs"] if r.get("draft")]
    assert len(live) == 1
    assert len(drafts) == 1
    assert "confidence" in (drafts[0].get("draft_reason") or "")


def test_generate_marks_draft_on_vendor_leak(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    _seed_one_heartbeat("acme_vendor")

    leaky = _good_rec_dict()
    leaky["headline"] = "Claude thinks ads are pacing weird."
    monkeypatch.setattr(opus, "chat", lambda **kw: _fake_opus_result(
        json.dumps({"recommendations": [leaky]})
    ))

    result = recs_generator.generate("acme_vendor")
    assert len(result["recs"]) == 1
    assert result["recs"][0].get("draft") is True


def test_generate_propagates_budget_exceeded(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))

    def boom(**kw):
        raise opus.OpusBudgetExceeded("daily tenant cap hit")
    monkeypatch.setattr(opus, "chat", boom)

    with pytest.raises(opus.OpusBudgetExceeded):
        recs_generator.generate("acme_broke")


def test_generate_raises_on_unparseable_output(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))

    monkeypatch.setattr(opus, "chat", lambda **kw: _fake_opus_result(
        "Sure, here's what I think you should do this week..."
    ))

    with pytest.raises(recs_generator.RecsGenerationError):
        recs_generator.generate("acme_garbled")


def test_generate_handles_empty_recommendations(tmp_path, monkeypatch):
    """Cold-start tenant or healthy tenant: model returns []; we return cleanly."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))

    monkeypatch.setattr(opus, "chat", lambda **kw: _fake_opus_result(
        json.dumps({"recommendations": []})
    ))

    result = recs_generator.generate("acme_clean")
    assert result["recs"] == []
    assert result["usd"] == 0.005


def test_generate_passes_cache_system_to_opus(tmp_path, monkeypatch):
    """The system prompt is identical every call so it should be cached."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))

    captured = {}

    def capture(**kw):
        captured.update(kw)
        return _fake_opus_result(json.dumps({"recommendations": []}))
    monkeypatch.setattr(opus, "chat", capture)

    recs_generator.generate("acme_cache")
    assert captured.get("cache_system") is True
    assert captured.get("kind") == "recommendations"
    assert captured.get("system")  # non-empty
    assert captured.get("max_tokens") == 4096
