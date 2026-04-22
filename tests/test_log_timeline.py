"""Log-to-timeline parser tests."""

from dashboard_app.services import log_timeline


def test_parses_patrol_format():
    raw = """[2026-04-22 07:00:00] INFO: Starting morning reports run
[2026-04-22 07:00:15] INFO: Processed DAR for 400 S Ventura
[2026-04-22 07:00:42] INFO: Sent 3 DAR drafts
[2026-04-22 07:01:10] INFO: Finished morning reports run"""
    events = log_timeline.parse(raw)
    assert len(events) == 4
    assert events[0].level == "start"
    assert events[-1].level == "success"
    assert "7:00 AM" in events[0].time_human


def test_parses_harbor_lights_format():
    raw = """2026-04-22 07:00:00 INFO Begin Harbor Lights update
2026-04-22 07:00:30 INFO Appended 12 plates to Excel
2026-04-22 07:00:45 INFO Completed successfully"""
    events = log_timeline.parse(raw)
    assert len(events) == 3
    assert events[0].level == "start"
    assert events[-1].level == "success"


def test_classifies_errors():
    raw = """[2026-04-22 07:00:00] INFO: Starting run
[2026-04-22 07:00:15] ERROR: Connecteam API timeout
[2026-04-22 07:00:30] INFO: Retry 1 of 3"""
    events = log_timeline.parse(raw)
    error_events = [e for e in events if e.level == "error"]
    assert len(error_events) == 1
    assert "Connecteam" in error_events[0].message


def test_dedupes_consecutive_duplicates():
    raw = """[2026-04-22 07:00:00] INFO: Starting run
[2026-04-22 07:00:15] INFO: Same message
[2026-04-22 07:00:16] INFO: Same message
[2026-04-22 07:00:17] INFO: Same message"""
    events = log_timeline.parse(raw)
    assert len(events) == 2  # start + one "same message"


def test_skips_debug_and_tracebacks():
    raw = """[2026-04-22 07:00:00] INFO: Starting run
[2026-04-22 07:00:01] DEBUG: loading config
[2026-04-22 07:00:02] INFO: Run completed
Traceback (most recent call last):
  File "foo.py", line 1, in <module>
    raise ValueError"""
    events = log_timeline.parse(raw)
    # start + complete only; debug + traceback cont filtered
    assert len(events) == 2


def test_trims_long_messages():
    long_msg = "a " * 200
    raw = f"[2026-04-22 07:00:00] INFO: {long_msg}"
    events = log_timeline.parse(raw)
    assert len(events) == 1
    assert len(events[0].message) <= 140


def test_empty_input_returns_empty_list():
    assert log_timeline.parse("") == []
    assert log_timeline.parse(None) == []  # type: ignore[arg-type]


def test_max_events_cap():
    lines = [f"[2026-04-22 07:{i:02d}:00] INFO: Event number {i}" for i in range(30)]
    raw = "\n".join(lines)
    events = log_timeline.parse(raw, max_events=5)
    assert len(events) == 5
    # Should keep the most recent (last 5)
    assert "29" in events[-1].message
