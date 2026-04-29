"""Daily watchdog digest emitter.

Wraps `tenant_watchdog.evaluate_all_tenants()`, fingerprint-gates against
the prior day's findings (so persistent issues don't nag Sam every day),
and emits a single grouped digest email via the existing `email_sender.alert_sam`
support channel.

Run via:

    python -m wc_solns_pipelines.platform.watchdog_digest [--dry-run]

Triggered by VPS cron once per day. The fingerprint gate keeps a small
state file at <TENANT_ROOT>/_platform/watchdog_digest_state.json so a
same-fingerprint re-run on the same UTC day is silent.

Design choices:
  * Same UTC date AND same fingerprint -> silent. A new day always re-emits
    even if nothing changed, so Sam gets one daily heartbeat that the
    digest is alive when there ARE issues. (When there are zero issues
    AND the prior state had zero issues, we stay silent.)
  * Fingerprint excludes age_hours and message - those drift every run
    and would defeat the gate. We hash the stable triple
    (tenant_id, pipeline_id, kind) only.
  * Atomic state write (tmp + os.replace) so a crash mid-write doesn't
    corrupt the gate file.
  * Runner exits 0 on every path. Errors surface via log.warning - silent
    failure is preferred over noisy retries because Sam will catch a
    missing digest faster than a flapping one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dashboard_app.services import email_sender, tenant_watchdog

log = logging.getLogger("wcas.platform.watchdog_digest")


STATE_FILENAME = "watchdog_digest_state.json"


# ---------------------------------------------------------------------------
# state path + I/O
# ---------------------------------------------------------------------------


def state_path() -> Path:
    """Resolve the digest's state file location.

    Lives under <TENANT_ROOT>/_platform/ so it's a sibling to tenant
    directories (heartbeat_store rejects the leading-underscore name as
    an invalid tenant_id, which is intentional - this is platform state,
    not tenant state).
    """
    base = os.environ.get("TENANT_ROOT", "/opt/wc-solns")
    return Path(base) / "_platform" / STATE_FILENAME


def read_state(path: Path | None = None) -> dict[str, Any]:
    """Read the state file; return {} if missing or unparseable.

    Never raises - a corrupted state file should not block the digest.
    Worst case we re-send today's digest; the alternative is permanent
    silence, which is much worse.
    """
    p = path or state_path()
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, FileNotFoundError):
        return {}
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        log.warning("watchdog_digest: state file unparseable; treating as empty")
        return {}
    return data if isinstance(data, dict) else {}


def write_state(data: dict[str, Any], path: Path | None = None) -> None:
    """Atomically write the state file. Creates parent dir if missing."""
    p = path or state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


# ---------------------------------------------------------------------------
# fingerprint
# ---------------------------------------------------------------------------


def fingerprint(findings: dict[str, list]) -> str:
    """Stable sha256 of the issue set, ignoring age_hours and message.

    age_hours drifts every run (an overdue pipeline gets older every
    minute), and `message` embeds age_hours in its f-string, so including
    either field would defeat the daily gate. Only kind, tenant_id, and
    pipeline_id matter for "is this the same set of problems".
    """
    triples: list[tuple[str, str, str]] = []
    for tenant_id, issues in findings.items():
        for issue in issues or []:
            pid = getattr(issue, "pipeline_id", None) or (
                issue.get("pipeline_id") if isinstance(issue, dict) else ""
            )
            kind = getattr(issue, "kind", None) or (
                issue.get("kind") if isinstance(issue, dict) else ""
            )
            triples.append((str(tenant_id), str(pid), str(kind)))
    triples.sort()
    payload = json.dumps(triples, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _summary_count(summary: dict[str, Any], key: str) -> int:
    val = summary.get(key, 0)
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def render_digest(
    findings: dict[str, list],
    summary: dict[str, Any],
    *,
    now: datetime,
) -> tuple[str, str]:
    """Build (subject, plain-text body) for the digest email.

    Empty findings -> "All clear" body so Sam can confirm the gate
    transitioned from issues to clean (we only send when the prior state
    had findings; see run() for the gating logic).
    """
    date_str = now.strftime("%Y-%m-%d")
    errored = _summary_count(summary, "errored")
    overdue = _summary_count(summary, "overdue")
    missing = _summary_count(summary, "missing_first_run")
    tenants_with_issues = _summary_count(summary, "tenants_with_issues")
    total = _summary_count(summary, "total")

    if total == 0:
        subject = f"WCAS Watchdog Digest {date_str}: all clear"
        body = (
            f"All tenant pipelines healthy as of {now.isoformat()}.\n"
            f"\n"
            f"No errored, overdue, or missing-first-run pipelines detected.\n"
            f"\n"
            f"Dashboard: /admin/tenants\n"
        )
        return subject, body

    counts_phrase_parts: list[str] = []
    if errored:
        counts_phrase_parts.append(f"{errored} errored")
    if overdue:
        counts_phrase_parts.append(f"{overdue} overdue")
    if missing:
        counts_phrase_parts.append(f"{missing} missing first run")
    counts_phrase = ", ".join(counts_phrase_parts) if counts_phrase_parts else f"{total} issues"
    tenant_phrase = (
        f"across {tenants_with_issues} tenants"
        if tenants_with_issues != 1
        else "across 1 tenant"
    )
    subject = f"WCAS Watchdog Digest {date_str}: {counts_phrase} {tenant_phrase}"

    lines: list[str] = []
    lines.append(f"Watchdog digest for {date_str} ({now.isoformat()}).")
    lines.append("")
    lines.append(
        f"Totals: {errored} errored, {overdue} overdue, {missing} missing first run "
        f"({total} issues across {tenants_with_issues} tenants)."
    )
    lines.append("")

    # Group by tenant_id, sorted alphabetically. Skip tenants with no issues.
    tenants_sorted = sorted(t for t, issues in findings.items() if issues)
    for tenant_id in tenants_sorted:
        issues = findings.get(tenant_id) or []
        lines.append(f"Tenant: {tenant_id}")
        for issue in issues:
            severity = _attr(issue, "severity") or "info"
            pid = _attr(issue, "pipeline_id") or "?"
            kind = _attr(issue, "kind") or "?"
            age = _attr(issue, "age_hours")
            age_str = f"{float(age):.1f}h" if isinstance(age, (int, float)) else "n/a"
            message = (_attr(issue, "message") or "").strip()
            lines.append(
                f"  [{severity}] {pid} ({kind}, {age_str}) {message}"
            )
        lines.append(f"  View tenant: /admin/tenant/{tenant_id}")
        lines.append("")

    lines.append("Dashboard: /admin/tenants")
    lines.append("Watchdog source: dashboard_app/services/tenant_watchdog.py")
    body = "\n".join(lines) + "\n"
    return subject, body


def _attr(issue: Any, name: str) -> Any:
    """Read either a dataclass attr or a dict key."""
    if isinstance(issue, dict):
        return issue.get(name)
    return getattr(issue, name, None)


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


def _default_alert(subject: str, body: str) -> bool:
    return email_sender.alert_sam(
        tenant_id="watchdog",
        event_type="digest",
        subject=subject,
        body=body,
        force=True,
    )


def run(
    *,
    dry_run: bool = False,
    now: datetime | None = None,
    evaluate_fn: Callable[[], dict[str, list]] | None = None,
    alert_fn: Callable[[str, str], bool] | None = None,
    state_reader: Callable[[], dict[str, Any]] | None = None,
    state_writer: Callable[[dict[str, Any]], None] | None = None,
) -> int:
    """Daily digest entrypoint.

    Always returns 0. Failures are logged but never surface as non-zero
    exit codes - cron retries on a flapping watchdog would just swamp
    the inbox.
    """
    try:
        return _run_inner(
            dry_run=dry_run,
            now=now,
            evaluate_fn=evaluate_fn,
            alert_fn=alert_fn,
            state_reader=state_reader,
            state_writer=state_writer,
        )
    except Exception:  # noqa: BLE001 - last-line guard, must never raise
        log.exception("watchdog_digest crashed unexpectedly")
        return 0


def _run_inner(
    *,
    dry_run: bool,
    now: datetime | None,
    evaluate_fn: Callable[[], dict[str, list]] | None,
    alert_fn: Callable[[str, str], bool] | None,
    state_reader: Callable[[], dict[str, Any]] | None,
    state_writer: Callable[[dict[str, Any]], None] | None,
) -> int:
    now_dt = now or datetime.now(timezone.utc)

    if evaluate_fn is not None:
        findings = evaluate_fn()
    else:
        findings = tenant_watchdog.evaluate_all_tenants(now=now_dt)

    summary = tenant_watchdog.summarize(findings)
    fp = fingerprint(findings)

    reader = state_reader if state_reader is not None else read_state
    writer = state_writer if state_writer is not None else write_state

    try:
        prior = reader() or {}
    except Exception as exc:  # noqa: BLE001 - state read failures are non-fatal
        log.warning("watchdog_digest: state read failed: %s", exc)
        prior = {}

    prior_fp = prior.get("last_fingerprint") or ""
    prior_date = prior.get("last_sent_date") or ""
    prior_summary = prior.get("last_summary") or {}
    prior_total = _summary_count(prior_summary if isinstance(prior_summary, dict) else {}, "total")

    today_str = now_dt.strftime("%Y-%m-%d")
    total = _summary_count(summary, "total")

    # Quiet path: no findings now, no findings before -> stay silent.
    # Don't even write state, so the very first run on a healthy fleet
    # leaves no file behind.
    if total == 0 and prior_total == 0:
        log.info("watchdog_digest: no findings, no prior findings; staying silent")
        return 0

    # Same fingerprint AND same UTC day -> already sent today, stay silent.
    # We deliberately re-emit on a new day even with the same fingerprint
    # so Sam gets a daily heartbeat that the digest is alive when issues exist.
    if fp == prior_fp and today_str == prior_date:
        log.info("watchdog_digest: same fingerprint, same day; already sent")
        return 0

    subject, body = render_digest(findings, summary, now=now_dt)

    if dry_run:
        print(subject)
        print(body)
        return 0

    sender = alert_fn if alert_fn is not None else _default_alert
    try:
        sent = bool(sender(subject, body))
    except Exception as exc:  # noqa: BLE001 - SMTP/network errors must not kill the runner
        log.warning("watchdog_digest: alert send failed: %s", exc)
        return 0

    if not sent:
        log.warning("watchdog_digest: alert_sam returned False; not persisting state")
        return 0

    new_state = {
        "last_fingerprint": fp,
        "last_sent_date": today_str,
        "last_sent_at": now_dt.isoformat(),
        "last_summary": dict(summary),
    }
    try:
        writer(new_state)
    except Exception as exc:  # noqa: BLE001 - failed write means we'll retry tomorrow
        log.warning("watchdog_digest: state write failed: %s", exc)

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Daily WCAS watchdog digest emitter.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print subject + body, do not email or persist state.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    return run(dry_run=args.dry_run)


__all__ = [
    "STATE_FILENAME",
    "fingerprint",
    "main",
    "read_state",
    "render_digest",
    "run",
    "state_path",
    "write_state",
]


if __name__ == "__main__":
    sys.exit(main())
