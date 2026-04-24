"""Tests for scripts/sanitize_for_demo.py."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

import pytest

# The script lives under scripts/, not dashboard_app/; add the repo root so we can import it.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import sanitize_for_demo as s  # noqa: E402


@pytest.fixture(autouse=True)
def _stable_salt(monkeypatch):
    monkeypatch.setenv("DEMO_SCRAMBLE_SALT", "deterministic-test-salt")
    yield


# ---------------------------------------------------------------------------
# Deterministic scramblers
# ---------------------------------------------------------------------------


def test_scramble_name_is_deterministic():
    a = s.scramble_name("Jane Doe")
    b = s.scramble_name("Jane Doe")
    assert a == b
    assert "jane" not in a.lower()
    assert "doe" not in a.lower()
    assert "customer" in a.lower() or "client" in a.lower() or "property" in a.lower() or "account" in a.lower()


def test_scramble_name_property_kind_returns_property_label():
    name = s.scramble_name("Manhattan Plaza", kind="property")
    assert name.startswith("Property #")
    assert name[-1].isalpha()


def test_scramble_name_handles_empty_input():
    assert s.scramble_name("") == ""
    assert s.scramble_name(None) == ""  # type: ignore[arg-type]


def test_scramble_email_returns_demo_local():
    out = s.scramble_email("sam@americalpatrol.com")
    assert out.endswith("@demo.local")
    assert "americalpatrol" not in out


def test_scramble_email_passes_non_emails_through():
    assert s.scramble_email("not an email") == "not an email"


def test_scramble_phone_masks_middle_and_derives_last4():
    a = s.scramble_phone("(562) 968-4474")
    b = s.scramble_phone("(562) 968-4474")
    assert a == b
    assert a.startswith("(555) XXX-")
    assert "968" not in a
    assert "4474" not in a


def test_scramble_phone_passes_non_phones_through():
    assert s.scramble_phone("N/A") == "N/A"


def test_scramble_dollars_redacts_large_amounts():
    assert s.scramble_dollars("deal for $12,400") == "deal for $X,XXX"
    assert s.scramble_dollars(12400) == "$X,XXX"
    assert s.scramble_dollars(50000) == "$X,XXX"


def test_scramble_dollars_rounds_medium_amounts():
    out = s.scramble_dollars("$1,840 influenced")
    # rounded to nearest 500: 1840 -> 2000
    assert out == "~$2,000 influenced"


def test_scramble_dollars_preserves_small_amounts():
    assert s.scramble_dollars(420) == "$420"
    assert s.scramble_dollars("$299 one-time") == "$299 one-time"


# ---------------------------------------------------------------------------
# Dict walker
# ---------------------------------------------------------------------------


def test_apply_to_activity_row_scrubs_name_and_phone():
    row = {
        "time": "12:38 PM",
        "role": "Reviews",
        "customer_name": "Jane Doe",
        "contact_email": "jane@acme.com",
        "phone": "(562) 968-4474",
        "action": "Replied to 5-star review",
    }
    scrubbed = s.apply_to_activity_row(row)
    assert "Jane" not in str(scrubbed)
    assert "Doe" not in str(scrubbed)
    assert "acme.com" not in str(scrubbed)
    assert "968-4474" not in str(scrubbed)
    # Untouched fields still there.
    assert scrubbed["time"] == "12:38 PM"
    assert scrubbed["role"] == "Reviews"


def test_apply_to_context_scrubs_nested_dollars():
    ctx = {
        "tenant_name": "Americal Patrol",
        "hero_stats": [
            {"label": "Revenue", "value": "$38,260", "delta_text": "+$4,120 vs last month"},
        ],
        "roles": [
            {"slug": "sales_pipeline", "actions": 56, "influenced": "12,400"},
        ],
    }
    out = s.apply_to_context(ctx)
    # Hero value gets redacted.
    assert "$38,260" not in out["hero_stats"][0]["value"]
    assert "X,XXX" in out["hero_stats"][0]["value"]
    # Delta redacted (delta_text flagged by key).
    assert "$4,120" not in out["hero_stats"][0]["delta_text"]
    # Roles influenced is a plain number string but key 'influenced' triggers the rule.
    assert out["roles"][0]["influenced"] != "12,400"


def test_apply_to_context_leaves_non_sensitive_strings_alone():
    ctx = {"narrative": "Here's your week. Reviews and Morning Reports did the heavy lifting."}
    out = s.apply_to_context(ctx)
    assert out["narrative"] == ctx["narrative"]


def test_determinism_across_runs():
    # Same input + same salt = same output. Matters for multi-take video:
    # "Jane Doe" should scramble identically in every scene.
    name1 = s.scramble_name("Kyle Johnson")
    name2 = s.scramble_name("Kyle Johnson")
    assert name1 == name2


def test_cli_check_runs_without_error_on_empty_tenant(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    # No tenant data -> home_context still produces an empty-state dict.
    rc = s.main(["--check", "--tenant", "brand_new"])
    # 0 or 1; both are valid depending on whether empty-state renders anything scrubbable.
    assert rc in (0, 1)
    out = capsys.readouterr().out
    assert "brand_new" in out


def test_cli_write_produces_snapshot_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_ROOT", str(tmp_path))
    rc = s.main(["--write", "--tenant", "acme"])
    assert rc == 0
    snapshot = tmp_path / "acme" / "demo_snapshot" / "home_context.json"
    assert snapshot.exists()
    # File is valid JSON.
    import json
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
