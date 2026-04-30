"""Home-surface composer tests."""

import os
from pathlib import Path

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import json

from dashboard_app.services import heartbeat_store, home_context


def test_empty_tenant_returns_placeholder(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = home_context.build(tenant_id="brand_new", owner_name="Jane")
    assert ctx["tenant_name"]
    assert ctx["owner_initials"] == "JA"
    assert ctx["roles"]  # placeholder row, not empty
    assert ctx["roles"][0]["slug"] == "first-run"
    assert ctx["attention"] is None
    assert ctx["narrative"].startswith("Your roles are connected")


def test_roles_reflect_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    heartbeat_store.write_snapshot("acme", "patrol", {
        "status": "ok",
        "last_run": "2026-04-22T07:00:00+00:00",
        "summary": "3 DARs sent",
    })
    heartbeat_store.write_snapshot("acme", "seo", {
        "status": "error",
        "last_run": "2026-04-22T08:00:00+00:00",
        "summary": "token expired",
    })
    ctx = home_context.build(tenant_id="acme", owner_name="Sam A")
    slugs = {r["slug"] for r in ctx["roles"]}
    assert "patrol" in slugs
    assert "seo" in slugs
    # Errored role -> attention banner surfaces with error kind.
    assert ctx["attention"] is not None
    assert ctx["attention"]["kind"] == "error"


def test_hero_stats_render_honest_placeholders(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = home_context.build(tenant_id="acme", owner_name="Sam A")
    labels = [s["label"] for s in ctx["hero_stats"]]
    assert labels == ["Weeks saved", "Revenue influenced", "Goal progress"]
    for stat in ctx["hero_stats"]:
        assert stat["value"] == "--"
        assert stat["verified_tip"]


# ---------------------------------------------------------------------------
# W6: catalog-driven ring rendering
# ---------------------------------------------------------------------------


def test_seeded_starter_tenant_renders_pending_rings(tmp_path, monkeypatch):
    """A tenant freshly seeded with the Starter tier should see one ring
    per starter-default automation, all in pending state with a cron-derived
    "first run <when>" label (or a generic fallback when the cron is too
    irregular to humanize)."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    from dashboard_app.services import automation_catalog as cat, tenant_automations
    tenant_automations.seed_for_tier("acme", "starter")
    ctx = home_context.build(tenant_id="acme", owner_name="Sam")
    starter_ids = {a.id for a in cat.for_tier("starter")}
    rendered_ids = {r["slug"].replace("-", "_") for r in ctx["roles"]}
    assert starter_ids.issubset(rendered_ids), f"missing rings: {starter_ids - rendered_ids}"
    # All rings are pending (no heartbeats yet) and state_text either
    # surfaces the cron-derived first-run label or falls back to the
    # generic "pending first run" placeholder.
    for r in ctx["roles"]:
        if r["slug"].replace("-", "_") in starter_ids:
            assert r["state"] == "pending"
            assert (
                r["state_text"].startswith("first run ")
                or r["state_text"] == "pending first run"
            ), f"unexpected state_text for {r['slug']}: {r['state_text']!r}"


def test_seeded_pro_tenant_includes_seo_recs_ring(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    from dashboard_app.services import tenant_automations
    tenant_automations.seed_for_tier("acme", "pro")
    ctx = home_context.build(tenant_id="acme", owner_name="Sam")
    rendered_ids = {r["slug"].replace("-", "_") for r in ctx["roles"]}
    assert "seo_recs" in rendered_ids


def test_heartbeat_promotes_pending_to_active(tmp_path, monkeypatch):
    """Once a heartbeat lands for an enabled automation, the ring flips
    from 'pending' to 'active' (or 'error', etc., per the heartbeat)."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    from dashboard_app.services import tenant_automations
    tenant_automations.seed_for_tier("acme", "starter")
    heartbeat_store.write_snapshot("acme", "reviews", {
        "status": "ok",
        "last_run": "2026-04-29T07:00:00+00:00",
        "summary": "ok",
    })
    ctx = home_context.build(tenant_id="acme", owner_name="Sam")
    reviews_ring = next((r for r in ctx["roles"] if r["slug"] == "reviews"), None)
    assert reviews_ring is not None
    assert reviews_ring["state"] != "pending"


def test_heartbeat_for_non_enabled_pipeline_still_renders(tmp_path, monkeypatch):
    """Backward-compat: AP runs pipelines that aren't (yet) in the
    catalog. Their heartbeats must still produce a ring."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    # No tenant_automations.json exists for AP; just heartbeats
    heartbeat_store.write_snapshot("americal_patrol", "patrol", {
        "status": "ok",
        "last_run": "2026-04-29T07:00:00+00:00",
        "summary": "3 DARs",
    })
    ctx = home_context.build(tenant_id="americal_patrol", owner_name="Sam")
    slugs = {r["slug"] for r in ctx["roles"]}
    assert "patrol" in slugs


def test_disabling_automation_removes_its_ring(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    from dashboard_app.services import tenant_automations
    tenant_automations.seed_for_tier("acme", "starter")
    tenant_automations.disable("acme", "reviews")
    ctx = home_context.build(tenant_id="acme", owner_name="Sam")
    rendered_ids = {r["slug"].replace("-", "_") for r in ctx["roles"]}
    assert "reviews" not in rendered_ids


def test_promo_optin_adds_ring(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    from datetime import datetime, timedelta, timezone
    from dashboard_app.services import tenant_automations
    tenant_automations.seed_for_tier("acme", "starter")
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    tenant_automations.enable("acme", "voice_ai", source="promo_optin", expires_at=future)
    ctx = home_context.build(tenant_id="acme", owner_name="Sam")
    rendered_ids = {r["slug"].replace("-", "_") for r in ctx["roles"]}
    assert "voice_ai" in rendered_ids


# ---------------------------------------------------------------------------
# Cold-start UX surfaces (Phase B): is_cold_start, samples carousel,
# this-week timeline, hero stats projection, voice + KB teasers.
# ---------------------------------------------------------------------------


def test_cold_start_flag_set_when_no_heartbeats(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = home_context.build(tenant_id="brand_new", owner_name="Sam")
    assert ctx["is_cold_start"] is True


def test_cold_start_flag_clears_after_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    heartbeat_store.write_snapshot("acme", "patrol", {
        "status": "ok", "last_run": "2026-04-29T07:00:00+00:00",
    })
    ctx = home_context.build(tenant_id="acme", owner_name="Sam")
    assert ctx["is_cold_start"] is False


def test_pending_rings_carry_cron_derived_first_run_label(tmp_path, monkeypatch):
    """A starter-seeded tenant gets pending rings with a "first run <when>"
    label derived from the catalog default cron table."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    from dashboard_app.services import tenant_automations
    tenant_automations.seed_for_tier("acme", "starter")
    ctx = home_context.build(tenant_id="acme", owner_name="Sam")
    pending = [r for r in ctx["roles"] if r["state"] == "pending"]
    assert pending, "starter seed should produce at least one pending ring"
    label_count = sum(1 for r in pending if r["state_text"].startswith("first run"))
    # At least one starter pipeline (gbp / seo / reviews / blog) must humanize.
    assert label_count >= 1


def test_this_week_timeline_lists_one_row_per_enabled_role(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    from dashboard_app.services import tenant_automations
    tenant_automations.seed_for_tier("acme", "starter")
    ctx = home_context.build(tenant_id="acme", owner_name="Sam")
    timeline = ctx["this_week_timeline"]
    assert isinstance(timeline, list)
    assert len(timeline) >= 1
    for row in timeline:
        assert row.get("pipeline_id")
        assert row.get("pipeline_name")
        assert row.get("when_label")


def test_this_week_timeline_empty_for_brand_new_tenant(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = home_context.build(tenant_id="brand_new", owner_name="Sam")
    assert ctx["this_week_timeline"] == []


def test_hero_stats_projects_weeks_saved_for_seeded_tenant(tmp_path, monkeypatch):
    """A tenant with enabled automations but no heartbeats should see a
    projected Weeks Saved value (~Xh) instead of the cold "--" placeholder."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    from dashboard_app.services import tenant_automations
    tenant_automations.seed_for_tier("acme", "starter")
    ctx = home_context.build(tenant_id="acme", owner_name="Sam")
    weeks = ctx["hero_stats"][0]
    assert weeks["label"] == "Weeks saved"
    assert weeks["value"].startswith("~"), f"expected projected value, got {weeks['value']!r}"
    assert weeks["status_text"] == "projected"
    assert "projected once" in weeks["delta_text"]


def test_activation_samples_summary_reads_disk_samples(tmp_path, monkeypatch):
    """When samples/<slug>.json exists, the home context surfaces a
    lightweight summary (no body_markdown) for the cold-start carousel."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    samples_dir = heartbeat_store.tenant_root("acme") / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    (samples_dir / "gbp.json").write_text(json.dumps({
        "slug": "gbp", "kind": "sample_output", "status": "ok",
        "title": "First-month GBP post",
        "body_markdown": "## Long content here\n\nLots of body...",
        "preview": "Sample GBP post preview",
    }), encoding="utf-8")
    ctx = home_context.build(tenant_id="acme", owner_name="Sam")
    samples = ctx["activation_samples"]
    assert isinstance(samples, list) and len(samples) == 1
    s = samples[0]
    assert s["slug"] == "gbp"
    assert s["title"] == "First-month GBP post"
    assert s["preview"] == "Sample GBP post preview"
    assert "body_markdown" not in s


def test_activation_samples_empty_when_no_samples(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = home_context.build(tenant_id="brand_new", owner_name="Sam")
    assert ctx["activation_samples"] == []


def test_voice_teaser_surfaces_when_voice_card_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    from dashboard_app.services import voice_card
    voice_card.save(
        tenant_id="acme",
        traits=["warm", "bilingual", "family-oriented"],
        generic_sample="Don't forget your appointment tomorrow.",
        voice_sample="Hola familia, tomorrow's class is at 6 sharp.",
    )
    ctx = home_context.build(tenant_id="acme", owner_name="Sam")
    teaser = ctx["voice_teaser"]
    assert teaser is not None
    assert teaser["traits"] == ["warm", "bilingual", "family-oriented"]
    assert "tomorrow's class" in teaser["voice_sample"]


def test_voice_teaser_none_without_voice_card(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = home_context.build(tenant_id="brand_new", owner_name="Sam")
    assert ctx["voice_teaser"] is None


def test_kb_summary_reads_company_section(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    from dashboard_app.services import tenant_kb
    tenant_kb.write_section(
        "acme",
        "company",
        "# Acme Co\n\nFamily-owned dance studio in Oxnard. Founded 2018.\n"
        "Specializes in folklorico and youth ballet.\n"
        "Trailing line that should not appear.",
    )
    ctx = home_context.build(tenant_id="acme", owner_name="Sam")
    summary = ctx["kb_summary"]
    assert summary is not None
    assert "Family-owned dance studio" in summary
    # First two non-heading lines only.
    assert "Trailing line" not in summary


def test_kb_summary_none_without_kb(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = home_context.build(tenant_id="brand_new", owner_name="Sam")
    assert ctx["kb_summary"] is None


def test_cold_start_narrative_keeps_pin_prefix_for_empty_tenant(tmp_path, monkeypatch):
    """The empty-tenant pin asserts narrative starts with "Your roles are
    connected"; the relief framing must be added without breaking that."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    ctx = home_context.build(tenant_id="brand_new", owner_name="Sam")
    assert ctx["narrative"].startswith("Your roles are connected")


def test_cold_start_narrative_relief_framing_for_seeded_tenant(tmp_path, monkeypatch):
    """A starter-seeded tenant gets the extended relief-framed cold-start
    narrative referencing role count."""
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    from dashboard_app.services import tenant_automations
    tenant_automations.seed_for_tier("acme", "starter")
    ctx = home_context.build(tenant_id="acme", owner_name="Sam")
    assert ctx["narrative"].startswith("Your roles are connected")
    assert "in your voice" in ctx["narrative"]
    assert "your last word" in ctx["narrative"]
