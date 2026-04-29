"""Tests for dashboard_app.services.seo_recommender."""

from __future__ import annotations

import json
import os
from typing import Any

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import seo_recommender as sr


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, text: str) -> None:
        self.text = text


def _stub_chat(text: str):
    captured: dict = {}

    def fn(**kwargs):
        captured.update(kwargs)
        return _Result(text)

    fn.captured = captured  # type: ignore[attr-defined]
    return fn


def _ga4(sessions: int = 200, top: list[dict] | None = None) -> dict[str, Any]:
    return {
        "totals": {"sessions": sessions, "totalUsers": int(sessions * 0.8), "conversions": 5},
        "top_pages": top or [{"path": "/services/hvac", "sessions": 80}],
    }


def _gsc(clicks: int = 50, queries: list[dict] | None = None) -> dict[str, Any]:
    return {
        "totals": {"clicks": clicks, "impressions": clicks * 10, "ctr": 0.1, "position": 9.5},
        "top_queries": queries or [
            {"query": "hvac oxnard", "clicks": 12, "impressions": 230, "position": 11.2},
        ],
    }


def _rankings() -> dict[str, Any]:
    return {
        "success": True,
        "results": [
            {"keyword": "hvac oxnard", "rank": 12, "search-engine": "google"},
            {"keyword": "ac repair oxnard", "rank": 4, "search-engine": "google"},
        ],
    }


def _site_facts(html: str = "<html><h1>AC Repair</h1></html>") -> dict[str, Any]:
    return {
        "url": "https://example.com",
        "pages": [{"url": "https://example.com", "html": html, "status": 200}],
    }


def _make_response(recs: list[dict[str, Any]]) -> str:
    return json.dumps(recs)


def _valid_rec(idx: int = 1, urgency: str = "high") -> dict[str, Any]:
    return {
        "id": f"rec-{idx:03d}",
        "title": f"Title {idx}",
        "rationale": "Reason here.",
        "specific_action": "Do this.",
        "evidence": ["GSC pos 11"],
        "estimated_traffic_lift": "+12 sessions/week",
        "urgency": urgency,
        "category": "title_tag",
        "page": "/x",
    }


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


def test_parse_bare_json_array():
    text = json.dumps([{"id": "rec-1", "title": "x"}])
    out = sr._parse_json_response(text)
    assert out == [{"id": "rec-1", "title": "x"}]


def test_parse_strips_json_fences():
    text = "```json\n" + json.dumps([{"id": "rec-1"}]) + "\n```"
    out = sr._parse_json_response(text)
    assert out == [{"id": "rec-1"}]


def test_parse_unwraps_object_with_recommendations_key():
    text = json.dumps({"recommendations": [{"id": "rec-1"}]})
    out = sr._parse_json_response(text)
    assert out == [{"id": "rec-1"}]


def test_parse_extracts_array_from_prose():
    text = (
        "Here are my recommendations:\n\n"
        + json.dumps([{"id": "rec-1", "title": "x"}])
        + "\n\nLet me know if you'd like more."
    )
    out = sr._parse_json_response(text)
    assert out == [{"id": "rec-1", "title": "x"}]


def test_parse_returns_empty_on_garbage():
    assert sr._parse_json_response("totally not json") == []
    assert sr._parse_json_response("") == []


def test_parse_drops_non_dict_array_entries():
    text = json.dumps([{"id": "rec-1"}, "string", 42, None])
    out = sr._parse_json_response(text)
    assert out == [{"id": "rec-1"}]


# ---------------------------------------------------------------------------
# normalize_rec
# ---------------------------------------------------------------------------


def test_normalize_rec_drops_when_required_fields_missing():
    raw = {"title": "no rationale or action"}
    assert sr._normalize_rec(raw, 0) is None


def test_normalize_rec_keeps_only_known_keys():
    raw = {
        "id": "rec-001",
        "title": "X",
        "rationale": "Y",
        "specific_action": "Z",
        "garbage_key": "ignored",
    }
    out = sr._normalize_rec(raw, 0)
    assert out is not None
    assert "garbage_key" not in out


def test_normalize_rec_default_urgency_medium():
    raw = {"id": "x", "title": "x", "rationale": "x", "specific_action": "x"}
    out = sr._normalize_rec(raw, 0)
    assert out["urgency"] == "medium"


def test_normalize_rec_invalid_urgency_falls_back_to_medium():
    raw = {"id": "x", "title": "x", "rationale": "x", "specific_action": "x", "urgency": "BLOCKER"}
    out = sr._normalize_rec(raw, 0)
    assert out["urgency"] == "medium"


def test_normalize_rec_evidence_string_promoted_to_list():
    raw = {
        "id": "x",
        "title": "x",
        "rationale": "x",
        "specific_action": "x",
        "evidence": "single line",
    }
    out = sr._normalize_rec(raw, 0)
    assert out["evidence"] == ["single line"]


def test_normalize_rec_synthetic_id_when_missing():
    raw = {"title": "x", "rationale": "x", "specific_action": "x"}
    out = sr._normalize_rec(raw, 5)
    assert out is None  # id missing -> entire rec dropped (id is required)


def test_normalize_rec_truncates_long_fields():
    raw = {
        "id": "x",
        "title": "T" * 500,
        "rationale": "R" * 1000,
        "specific_action": "A" * 1000,
    }
    out = sr._normalize_rec(raw, 0)
    assert len(out["title"]) <= 140
    assert len(out["rationale"]) <= 600
    assert len(out["specific_action"]) <= 600


# ---------------------------------------------------------------------------
# ranking
# ---------------------------------------------------------------------------


def test_rank_orders_high_before_medium_before_low():
    recs = [
        _valid_rec(1, urgency="low"),
        _valid_rec(2, urgency="high"),
        _valid_rec(3, urgency="medium"),
        _valid_rec(4, urgency="high"),
    ]
    ranked = sr._rank(recs)
    assert [r["urgency"] for r in ranked] == ["high", "high", "medium", "low"]


def test_rank_preserves_order_within_bucket():
    recs = [
        _valid_rec(1, urgency="high"),
        _valid_rec(2, urgency="high"),
        _valid_rec(3, urgency="high"),
    ]
    ranked = sr._rank(recs)
    assert [r["id"] for r in ranked] == ["rec-001", "rec-002", "rec-003"]


def test_rank_handles_empty():
    assert sr._rank([]) == []


# ---------------------------------------------------------------------------
# data block
# ---------------------------------------------------------------------------


def test_build_data_block_includes_all_sections_when_present():
    out = sr._build_data_block(
        ga4=_ga4(),
        gsc=_gsc(),
        rankings=_rankings(),
        site_facts=_site_facts(),
    )
    assert "GA4" in out
    assert "Google Search Console" in out
    assert "BrightLocal" in out
    assert "Site fetch" in out
    assert "/services/hvac" in out
    assert "hvac oxnard" in out


def test_build_data_block_skips_missing_sources():
    out = sr._build_data_block(ga4=_ga4(), gsc=None, rankings=None, site_facts=None)
    assert "GA4" in out
    assert "Google Search Console" not in out
    assert "BrightLocal" not in out


def test_build_data_block_handles_all_missing():
    out = sr._build_data_block(ga4=None, gsc=None, rankings=None, site_facts=None)
    assert "no source data" in out


# ---------------------------------------------------------------------------
# synthesize end-to-end
# ---------------------------------------------------------------------------


def test_synthesize_returns_normalized_ranked_recs():
    chat = _stub_chat(_make_response([
        _valid_rec(1, urgency="medium"),
        _valid_rec(2, urgency="high"),
        _valid_rec(3, urgency="low"),
    ]))
    out = sr.synthesize(
        "acme",
        ga4=_ga4(),
        gsc=_gsc(),
        rankings=_rankings(),
        site_facts=_site_facts(),
        chat_fn=chat,
    )
    assert len(out) == 3
    assert out[0]["urgency"] == "high"
    assert out[1]["urgency"] == "medium"
    assert out[2]["urgency"] == "low"
    # source data passed through
    assert "hvac oxnard" in chat.captured["messages"][0]["content"]


def test_synthesize_returns_empty_on_chat_error():
    def boom(**_kw):
        raise RuntimeError("opus down")

    out = sr.synthesize("acme", ga4=_ga4(), chat_fn=boom)
    assert out == []


def test_synthesize_returns_empty_on_unparseable_response():
    chat = _stub_chat("not json at all, just words")
    out = sr.synthesize("acme", ga4=_ga4(), chat_fn=chat)
    assert out == []


def test_synthesize_drops_invalid_recs_keeps_valid():
    chat = _stub_chat(_make_response([
        _valid_rec(1),
        {"title": "missing required"},  # missing id, rationale, specific_action
        _valid_rec(2),
    ]))
    out = sr.synthesize("acme", ga4=_ga4(), chat_fn=chat)
    assert len(out) == 2
    assert out[0]["id"] == "rec-001"
    assert out[1]["id"] == "rec-002"


def test_synthesize_caps_at_max_recs():
    chat = _stub_chat(_make_response([_valid_rec(i) for i in range(20)]))
    out = sr.synthesize("acme", ga4=_ga4(), chat_fn=chat)
    assert len(out) <= sr.MAX_RECS


def test_synthesize_uses_jakarta_system_prompt_with_tenant():
    chat = _stub_chat(_make_response([_valid_rec(1)]))
    sr.synthesize("garcia_folklorico", ga4=_ga4(), chat_fn=chat)
    system = chat.captured.get("system", "")
    assert "garcia_folklorico" in system
    assert "JSON array" in system


# ---------------------------------------------------------------------------
# run_weekly: cache write
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    return tmp_path


def test_run_weekly_writes_cache(tenant_root):
    chat = _stub_chat(_make_response([_valid_rec(1, urgency="high")]))
    out = sr.run_weekly(
        "acme",
        ga4=_ga4(sessions=300),
        gsc=_gsc(clicks=80),
        rankings=_rankings(),
        site_facts=_site_facts(),
        synthesize_fn=lambda *a, **k: sr.synthesize(*a, chat_fn=chat, **k),
    )
    assert len(out["recommendations"]) == 1
    cache_path = tenant_root / "acme" / "pipeline_state" / "seo_recommendations.json"
    assert cache_path.exists()
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached["recommendations"][0]["id"] == "rec-001"
    assert cached["source_summary"]["ga4_sessions"] == 300
    assert cached["source_summary"]["gsc_clicks"] == 80
    assert cached["source_summary"]["rankings_count"] == 2
    assert cached["source_summary"]["site_fetched"] is True
    assert "generated_at" in cached


def test_run_weekly_handles_synthesize_returning_empty(tenant_root):
    out = sr.run_weekly(
        "acme",
        ga4=_ga4(),
        synthesize_fn=lambda *a, **k: [],
    )
    assert out["recommendations"] == []
    cache_path = tenant_root / "acme" / "pipeline_state" / "seo_recommendations.json"
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached["recommendations"] == []


def test_run_weekly_overwrites_previous_cache(tenant_root):
    sr.run_weekly(
        "acme",
        ga4=_ga4(),
        synthesize_fn=lambda *a, **k: [_valid_rec(1)],
    )
    sr.run_weekly(
        "acme",
        ga4=_ga4(),
        synthesize_fn=lambda *a, **k: [_valid_rec(2)],
    )
    cached = sr.get_cached("acme")
    assert len(cached) == 1
    assert cached[0]["id"] == "rec-002"


# ---------------------------------------------------------------------------
# get_cached + get_cache_meta
# ---------------------------------------------------------------------------


def test_get_cached_returns_empty_when_no_file(tenant_root):
    assert sr.get_cached("acme") == []


def test_get_cached_returns_recommendations(tenant_root):
    cache_path = tenant_root / "acme" / "pipeline_state" / "seo_recommendations.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "generated_at": 123,
        "recommendations": [_valid_rec(1), _valid_rec(2)],
    }), encoding="utf-8")
    out = sr.get_cached("acme")
    assert len(out) == 2


def test_get_cached_swallows_malformed_json(tenant_root):
    cache_path = tenant_root / "acme" / "pipeline_state" / "seo_recommendations.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("not json", encoding="utf-8")
    assert sr.get_cached("acme") == []


def test_get_cached_caps_at_max_recs(tenant_root):
    cache_path = tenant_root / "acme" / "pipeline_state" / "seo_recommendations.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "recommendations": [_valid_rec(i) for i in range(20)],
    }), encoding="utf-8")
    out = sr.get_cached("acme")
    assert len(out) <= sr.MAX_RECS


def test_get_cached_skips_non_dict_entries(tenant_root):
    cache_path = tenant_root / "acme" / "pipeline_state" / "seo_recommendations.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "recommendations": [_valid_rec(1), "garbage", 42, _valid_rec(2)],
    }), encoding="utf-8")
    out = sr.get_cached("acme")
    assert len(out) == 2


def test_get_cache_meta_returns_meta(tenant_root):
    cache_path = tenant_root / "acme" / "pipeline_state" / "seo_recommendations.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "generated_at": 1700000000,
        "source_summary": {"ga4_sessions": 100},
        "recommendations": [_valid_rec(1)],
    }), encoding="utf-8")
    meta = sr.get_cache_meta("acme")
    assert meta["generated_at"] == 1700000000
    assert meta["source_summary"]["ga4_sessions"] == 100
    assert meta["recommendation_count"] == 1


def test_get_cache_meta_returns_empty_when_no_file(tenant_root):
    assert sr.get_cache_meta("acme") == {}


# ---------------------------------------------------------------------------
# Pattern C: tenant_runtime stays clean of seo_recommender
# ---------------------------------------------------------------------------


def test_tenant_runtime_does_not_import_seo_recommender():
    """The recommender bridges Pattern A (GA4/GSC) and Pattern C
    (BrightLocal). Tenant pipelines should never import it directly -
    the SEO weekly cron orchestrates it server-side."""
    from wc_solns_pipelines.shared import tenant_runtime
    text = open(tenant_runtime.__file__, encoding="utf-8").read()
    assert "seo_recommender" not in text
