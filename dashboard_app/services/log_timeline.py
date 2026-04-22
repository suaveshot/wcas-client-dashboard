"""
Raw-log -> human timeline.

The push_heartbeat.py tail gives us the last 20 lines of the pipeline's
automation.log in one of two formats:

    patrol:         [YYYY-MM-DD HH:MM:SS] LEVEL: message
    harbor_lights:  YYYY-MM-DD HH:MM:SS LEVEL message

The owner-operator doesn't want to read log lines. They want to see:
"7:00 AM  Morning run started" / "7:01 AM  Sent 3 DAR drafts" / "7:03 AM  Finished".

This module parses the tail, extracts timestamp + level + message, filters
out developer-noise (DEBUG lines, stack trace continuation, duplicates),
humanizes the timestamp, and returns a short list the template can render
as a vertical timeline with status dots.

The raw log stays available behind a <details> disclosure for the few times
Sam or a support rep needs it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

_PATROL_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s*([A-Z]+)?:?\s*(.*)$"
)
_HL_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+([A-Z]+)\s+(.*)$"
)

# Lines to skip entirely. Anything that isn't signal to the owner.
_SKIP_SUBSTR = (
    " DEBUG ",
    "DEBUG:",
    "[DEBUG]",
    "--- Logging error ---",
    "Traceback (most recent call last):",
    "  File \"",
    "    ",  # leading 4 spaces = stack trace continuation
)

# Canonical buckets. The owner sees "success", "warn", "error", "info",
# "start" for the left-edge dot color.
_ERROR_TOKENS = ("ERROR", "CRITICAL", "FATAL", "Traceback")
_WARN_TOKENS = ("WARNING", "WARN", "retry", "timeout")
_START_TOKENS = ("Starting", "started", "Begin", "kicking off", "Run start")
_SUCCESS_TOKENS = ("completed", "finished", "SUCCESS", "done", "All good")


@dataclass
class TimelineEvent:
    time_human: str
    level: str  # start | success | warn | error | info
    message: str


def _classify(level: str, message: str) -> str:
    blob = (level + " " + message).lower()
    for token in _ERROR_TOKENS:
        if token.lower() in blob:
            return "error"
    for token in _START_TOKENS:
        if token.lower() in blob:
            return "start"
    for token in _SUCCESS_TOKENS:
        if token.lower() in blob:
            return "success"
    for token in _WARN_TOKENS:
        if token.lower() in blob:
            return "warn"
    return "info"


def _humanize(ts: str) -> str:
    # Manual 12-hour formatting so Windows hosts (no %-I) behave the same.
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ts
    hour = dt.hour % 12 or 12
    suffix = "AM" if dt.hour < 12 else "PM"
    return f"{hour}:{dt.minute:02d} {suffix}"


def _trim_message(message: str) -> str:
    # Single-line, reasonable length for a card row.
    first_line = message.splitlines()[0] if message else ""
    first_line = first_line.strip().rstrip(".:")
    if len(first_line) > 140:
        first_line = first_line[:137].rstrip() + "..."
    # A few obvious replacements that read more naturally.
    first_line = first_line.replace("\t", " ")
    while "  " in first_line:
        first_line = first_line.replace("  ", " ")
    return first_line


def parse(log_tail: str, max_events: int = 12) -> list[TimelineEvent]:
    if not log_tail:
        return []

    events: list[TimelineEvent] = []
    last_signature: str | None = None

    for raw in log_tail.splitlines():
        line = raw.rstrip()
        if not line:
            continue

        # Skip noisy lines + traceback continuation.
        if any(s in line for s in _SKIP_SUBSTR):
            continue

        m = _PATROL_RE.match(line) or _HL_RE.match(line)
        if not m:
            continue

        ts, level, message = m.group(1), (m.group(2) or "INFO"), m.group(3)
        message = _trim_message(message)
        if not message:
            continue

        # Dedupe near-identical consecutive lines (e.g. heartbeat retry spam).
        signature = f"{level}|{message}"
        if signature == last_signature:
            continue
        last_signature = signature

        events.append(TimelineEvent(
            time_human=_humanize(ts),
            level=_classify(level, message),
            message=message,
        ))

    # Keep the most recent N, oldest first so the timeline reads top-to-bottom.
    if len(events) > max_events:
        events = events[-max_events:]
    return events
