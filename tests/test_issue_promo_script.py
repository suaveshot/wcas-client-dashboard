"""Tests for scripts/issue_promo.py and scripts/revoke_promo.py.

We invoke the CLIs as subprocesses so the argparse + sys.path bootstrap
runs the way Sam will run them on the VPS.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
ISSUE_PROMO = REPO_ROOT / "scripts" / "issue_promo.py"
REVOKE_PROMO = REPO_ROOT / "scripts" / "revoke_promo.py"


def _run(script: Path, args: list[str], tenant_root: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["TENANT_ROOT"] = str(tenant_root)
    env.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )


def _automations_path(tenant_root: Path, tenant_id: str) -> Path:
    return tenant_root / tenant_id / "config" / "automations.json"


@pytest.fixture
def tenant_root(tmp_path):
    # Pre-create the tenant dir so the slug check has something to land on
    # (heartbeat_store.tenant_root only validates the slug; it doesn't
    # require the dir to exist, but pre-creating keeps tests realistic).
    (tmp_path / "good-tenant").mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# issue_promo
# ---------------------------------------------------------------------------


def test_issue_promo_writes_row(tenant_root):
    result = _run(
        ISSUE_PROMO,
        ["--tenant", "good-tenant", "--automation", "gbp", "--days", "30"],
        tenant_root,
    )
    assert result.returncode == 0, result.stderr
    assert "Granted promo" in result.stdout
    assert "gbp" in result.stdout
    assert "good-tenant" in result.stdout

    path = _automations_path(tenant_root, "good-tenant")
    assert path.exists()
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw["enabled"]
    assert any(
        r["id"] == "gbp"
        and r["source"] == "promo_optin"
        and "expires_at" in r
        for r in rows
    )


def test_issue_promo_unknown_automation_exits_2(tenant_root):
    result = _run(
        ISSUE_PROMO,
        ["--tenant", "good-tenant", "--automation", "made_up_thing", "--days", "30"],
        tenant_root,
    )
    assert result.returncode == 2
    assert "not in catalog" in result.stderr
    # Nothing written.
    assert not _automations_path(tenant_root, "good-tenant").exists()


def test_issue_promo_dry_run_does_not_write(tenant_root):
    result = _run(
        ISSUE_PROMO,
        [
            "--tenant", "good-tenant",
            "--automation", "reviews",
            "--days", "14",
            "--dry-run",
        ],
        tenant_root,
    )
    assert result.returncode == 0, result.stderr
    assert "DRY RUN" in result.stdout
    assert "reviews" in result.stdout
    assert not _automations_path(tenant_root, "good-tenant").exists()


def test_issue_promo_bad_tenant_slug_exits_2(tenant_root):
    result = _run(
        ISSUE_PROMO,
        ["--tenant", "../nope", "--automation", "reviews", "--days", "30"],
        tenant_root,
    )
    assert result.returncode == 2
    assert "invalid tenant_id" in result.stderr or "tenant" in result.stderr.lower()


def test_issue_promo_zero_days_exits_2(tenant_root):
    result = _run(
        ISSUE_PROMO,
        ["--tenant", "good-tenant", "--automation", "reviews", "--days", "0"],
        tenant_root,
    )
    assert result.returncode == 2


def test_issue_promo_missing_args_exits_1(tenant_root):
    # Omit --days entirely.
    result = _run(
        ISSUE_PROMO,
        ["--tenant", "good-tenant", "--automation", "reviews"],
        tenant_root,
    )
    assert result.returncode == 1


# ---------------------------------------------------------------------------
# revoke_promo (light coverage)
# ---------------------------------------------------------------------------


def test_revoke_promo_no_active_promo(tenant_root):
    result = _run(
        REVOKE_PROMO,
        ["--tenant", "good-tenant", "--automation", "reviews"],
        tenant_root,
    )
    assert result.returncode == 0
    assert "No active promo found" in result.stdout


def test_revoke_promo_after_issue(tenant_root):
    issue = _run(
        ISSUE_PROMO,
        ["--tenant", "good-tenant", "--automation", "voice_ai", "--days", "30"],
        tenant_root,
    )
    assert issue.returncode == 0, issue.stderr
    revoke = _run(
        REVOKE_PROMO,
        ["--tenant", "good-tenant", "--automation", "voice_ai"],
        tenant_root,
    )
    assert revoke.returncode == 0, revoke.stderr
    assert "Revoked promo" in revoke.stdout
