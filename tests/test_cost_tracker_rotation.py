"""Tests for cost_tracker log rotation, vendor field, and the
record_call_for_vendor helper.

Covers:
  - Rotation triggers when COST_LOG_MAX_BYTES exceeded
  - Rotated file naming is cost_log.YYYYMMDD-HHMMSS.jsonl
  - Post-rotation, record_call writes to a fresh active file
  - _sum_today reads both rotated and active files for the same UTC day
  - vendor field defaults to "anthropic" when not passed (back-compat)
  - vendor field persists when passed
  - record_call_for_vendor writes a record with vendor + usd, no tokens
"""

from __future__ import annotations

import json
import os
import re

os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-bytes-plus-aaaaa")

from dashboard_app.services import cost_tracker


def _read_jsonl(path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------------------
# vendor field, back-compat
# ---------------------------------------------------------------------------


def test_record_call_default_vendor_is_anthropic(tmp_path, monkeypatch):
    log_path = tmp_path / "cost_log.jsonl"
    monkeypatch.setenv("COST_LOG_PATH", str(log_path))
    cost_tracker.record_call("acme", "claude-haiku-4-5", 100, 50, kind="message")
    rows = _read_jsonl(log_path)
    assert len(rows) == 1
    assert rows[0]["vendor"] == "anthropic"
    assert rows[0]["tenant_id"] == "acme"
    assert rows[0]["model"] == "claude-haiku-4-5"
    assert rows[0]["input_tokens"] == 100
    assert rows[0]["output_tokens"] == 50


def test_record_call_persists_vendor_when_passed(tmp_path, monkeypatch):
    log_path = tmp_path / "cost_log.jsonl"
    monkeypatch.setenv("COST_LOG_PATH", str(log_path))
    cost_tracker.record_call(
        "acme",
        "claude-haiku-4-5",
        10,
        20,
        kind="message",
        vendor="custom-vendor",
    )
    rows = _read_jsonl(log_path)
    assert len(rows) == 1
    assert rows[0]["vendor"] == "custom-vendor"


# ---------------------------------------------------------------------------
# record_call_for_vendor
# ---------------------------------------------------------------------------


def test_record_call_for_vendor_writes_minimal_record(tmp_path, monkeypatch):
    log_path = tmp_path / "cost_log.jsonl"
    monkeypatch.setenv("COST_LOG_PATH", str(log_path))
    out = cost_tracker.record_call_for_vendor(
        "brightlocal",
        tenant_id="acme",
        kind="fetch_rankings",
        usd=0.10,
    )
    assert out == 0.10
    rows = _read_jsonl(log_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["vendor"] == "brightlocal"
    assert row["model"] == "brightlocal"
    assert row["tenant_id"] == "acme"
    assert row["kind"] == "fetch_rankings"
    assert row["usd"] == 0.10
    # Token fields are omitted for non-Anthropic vendors.
    assert "input_tokens" not in row
    assert "output_tokens" not in row


def test_record_call_for_vendor_counts_toward_tenant_spend(tmp_path, monkeypatch):
    log_path = tmp_path / "cost_log.jsonl"
    monkeypatch.setenv("COST_LOG_PATH", str(log_path))
    cost_tracker.record_call_for_vendor(
        "brightlocal", tenant_id="acme", kind="fetch_rankings", usd=0.25
    )
    cost_tracker.record_call_for_vendor(
        "brightlocal", tenant_id="other", kind="fetch_rankings", usd=0.40
    )
    assert cost_tracker.tenant_spend_today("acme") == 0.25
    assert cost_tracker.dev_spend_today() == 0.65


def test_record_call_for_vendor_handles_bad_usd(tmp_path, monkeypatch):
    log_path = tmp_path / "cost_log.jsonl"
    monkeypatch.setenv("COST_LOG_PATH", str(log_path))
    out = cost_tracker.record_call_for_vendor(
        "brightlocal", tenant_id="acme", kind="x", usd="not-a-number"
    )
    assert out == 0.0
    rows = _read_jsonl(log_path)
    assert rows[0]["usd"] == 0.0


# ---------------------------------------------------------------------------
# rotation
# ---------------------------------------------------------------------------


def test_rotation_triggers_when_max_bytes_exceeded(tmp_path, monkeypatch):
    log_path = tmp_path / "cost_log.jsonl"
    monkeypatch.setenv("COST_LOG_PATH", str(log_path))
    monkeypatch.setenv("COST_LOG_MAX_BYTES", "200")

    # Pad the active file past the threshold so the next call rotates.
    log_path.write_text("X" * 300, encoding="utf-8")
    cost_tracker.record_call("acme", "claude-haiku-4-5", 1, 1)

    # The rotated file should exist with the expected naming pattern.
    rotated = [
        p for p in tmp_path.iterdir()
        if p.name != "cost_log.jsonl" and p.name.startswith("cost_log.") and p.name.endswith(".jsonl")
    ]
    assert len(rotated) == 1
    assert re.match(r"cost_log\.\d{8}-\d{6}(-\d+)?\.jsonl", rotated[0].name)


def test_post_rotation_record_call_writes_to_fresh_file(tmp_path, monkeypatch):
    log_path = tmp_path / "cost_log.jsonl"
    monkeypatch.setenv("COST_LOG_PATH", str(log_path))
    monkeypatch.setenv("COST_LOG_MAX_BYTES", "200")
    log_path.write_text("X" * 300, encoding="utf-8")

    cost_tracker.record_call("acme", "claude-haiku-4-5", 1, 1)

    # Active file holds only the new record (no padding bytes).
    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    assert content.count("\n") == 1
    assert "X" * 50 not in content
    rows = _read_jsonl(log_path)
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "acme"


def test_sum_today_reads_rotated_and_active_files(tmp_path, monkeypatch):
    log_path = tmp_path / "cost_log.jsonl"
    monkeypatch.setenv("COST_LOG_PATH", str(log_path))
    monkeypatch.setenv("COST_LOG_MAX_BYTES", "200")

    # First write goes to active file, no rotation yet.
    cost_tracker.record_call_for_vendor(
        "brightlocal", tenant_id="acme", kind="x", usd=0.50
    )
    # Pad to force rotation on next call.
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write("X" * 300 + "\n")
    cost_tracker.record_call_for_vendor(
        "brightlocal", tenant_id="acme", kind="x", usd=0.30
    )

    # Both records should count toward today's spend even though they live
    # in different files (rotated + active).
    assert cost_tracker.tenant_spend_today("acme") == 0.80
    assert cost_tracker.dev_spend_today() == 0.80


def test_list_log_files_for_day_finds_rotated_and_active(tmp_path, monkeypatch):
    log_path = tmp_path / "cost_log.jsonl"
    monkeypatch.setenv("COST_LOG_PATH", str(log_path))

    log_path.write_text('{"ts": "2026-04-29T10:00:00+00:00"}\n', encoding="utf-8")
    rotated_today = tmp_path / "cost_log.20260429-080000.jsonl"
    rotated_today.write_text('{"ts": "2026-04-29T08:00:00+00:00"}\n', encoding="utf-8")
    rotated_yesterday = tmp_path / "cost_log.20260428-235959.jsonl"
    rotated_yesterday.write_text('{"ts": "2026-04-28T23:59:59+00:00"}\n', encoding="utf-8")

    paths = cost_tracker.list_log_files_for_day("2026-04-29")
    names = {p.name for p in paths}
    assert "cost_log.jsonl" in names
    assert "cost_log.20260429-080000.jsonl" in names
    assert "cost_log.20260428-235959.jsonl" not in names


def test_no_rotation_below_threshold(tmp_path, monkeypatch):
    log_path = tmp_path / "cost_log.jsonl"
    monkeypatch.setenv("COST_LOG_PATH", str(log_path))
    monkeypatch.setenv("COST_LOG_MAX_BYTES", "10000")

    cost_tracker.record_call("acme", "claude-haiku-4-5", 1, 1)
    cost_tracker.record_call("acme", "claude-haiku-4-5", 1, 1)

    # Only the active log file should exist; no rotated siblings.
    siblings = [p for p in tmp_path.iterdir() if p.name.startswith("cost_log.")]
    assert len(siblings) == 1
    assert siblings[0].name == "cost_log.jsonl"
