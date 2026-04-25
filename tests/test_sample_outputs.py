"""Tests for v0.6.0 additions to sample_outputs: live_simulation template
+ citations metadata. Pure-Python; no Opus call."""

import os

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

from dashboard_app.services import sample_outputs


def test_pipeline_slugs_excludes_live_simulation():
    """live_simulation is on-demand, not part of the 7-card grid."""
    assert "live_simulation" not in sample_outputs.PIPELINE_SLUGS
    assert len(sample_outputs.PIPELINE_SLUGS) == 7


def test_citations_for_returns_three_kinds_per_pipeline():
    """Every standard pipeline carries voice + data + playbook citations."""
    for slug in sample_outputs.PIPELINE_SLUGS:
        cites = sample_outputs.citations_for(slug)
        assert len(cites) == 3
        kinds = {c["kind"] for c in cites}
        assert kinds == {"voice", "data", "playbook"}


def test_citations_for_live_simulation_uses_re_engagement_playbook():
    cites = sample_outputs.citations_for("live_simulation")
    playbook = next(c for c in cites if c["kind"] == "playbook")
    assert playbook["source"] == "re_engagement"
    data = next(c for c in cites if c["kind"] == "data")
    assert data["source"] == "last_engagement"


def test_citations_for_unknown_slug_returns_empty_list():
    assert sample_outputs.citations_for("nope") == []


def test_live_simulation_template_has_format_placeholders():
    """Sanity: the template references {name} and {days_inactive}."""
    tmpl = sample_outputs._TEMPLATES["live_simulation"]
    assert "{name}" in tmpl
    assert "{days_inactive}" in tmpl
