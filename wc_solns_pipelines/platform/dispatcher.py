"""VPS-side scheduler dispatcher (Phase 2D).

Cron fires this module every minute via `docker/dispatcher_cron.sh`.
Each tick:

  1. Walk every tenant directory under `$TENANT_ROOT` (skipping any
     that start with `_` - those are platform-managed, not tenants).
  2. For each tenant, read its `config/schedule.json` and find entries
     whose cron matches "now" (with optional tolerance for missed ticks).
  3. For each due entry, spawn the matching pipeline as a subprocess:

         python -m wc_solns_pipelines.pipelines.<pipeline_id>.run \
             --tenant <tenant_id>

     with `TENANT_ROOT` and `HEARTBEAT_SHARED_SECRET` forwarded through.

A pipeline subprocess that fails (non-zero rc, timeout, OSError) is
logged + counted, but the dispatcher keeps moving. The tick aggregates
counts and writes a tiny visibility file at
`$TENANT_ROOT/_platform/dispatcher_last_tick.json`.

Per memory file `mistake_vps_cron_env_inheritance`: Docker cron does
NOT inherit container env. The wrapper script sources env from
`/etc/wcas/dispatcher.env` and exec's this module so we can rely on
`os.environ` here.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dashboard_app.services import tenant_schedule

log = logging.getLogger("wcas.dispatcher")

# 5 minute hard cap per pipeline. Anything legitimately longer should
# go run as its own VPS job, not block the per-minute dispatcher.
DEFAULT_TIMEOUT = 300

# Env vars we forward to every spawned pipeline. TENANT_ROOT is
# required (we raise if missing); the rest are best-effort.
_FORWARDED_ENV: tuple[str, ...] = (
    "HEARTBEAT_SHARED_SECRET",
    "DISPATCH_DRY_RUN",
    "OPUS_DAILY_BUDGET_USD",
    "ANTHROPIC_API_KEY",
)

# Subprocess runner type for injection in tests.
SubprocessRunner = Callable[..., subprocess.CompletedProcess]


# ---------------------------------------------------------------------------
# single-pipeline dispatch
# ---------------------------------------------------------------------------


def dispatch_one(
    tenant_id: str,
    pipeline_id: str,
    *,
    env: dict[str, str] | None = None,
    subprocess_runner: SubprocessRunner | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Spawn one pipeline run for one tenant.

    Returns a result dict; never raises. The caller logs + aggregates
    based on `ok` and `error`.
    """
    runner = subprocess_runner or subprocess.run
    base_env = dict(env if env is not None else os.environ)

    tenant_root = base_env.get("TENANT_ROOT") or os.environ.get("TENANT_ROOT")
    if not tenant_root:
        # Missing TENANT_ROOT is a hard config error: pipelines write
        # state under it, so spawning blind would corrupt the wrong
        # directory. Surface it instead of silently crashing the child.
        return {
            "tenant_id": tenant_id,
            "pipeline_id": pipeline_id,
            "rc": None,
            "duration_ms": 0,
            "ok": False,
            "error": "TENANT_ROOT not set",
        }

    spawn_env = dict(os.environ)
    spawn_env["TENANT_ROOT"] = tenant_root
    for key in _FORWARDED_ENV:
        val = base_env.get(key)
        if val is not None:
            spawn_env[key] = val

    cmd = [
        sys.executable,
        "-m",
        f"wc_solns_pipelines.pipelines.{pipeline_id}.run",
        "--tenant",
        tenant_id,
    ]

    started = time.monotonic()
    try:
        completed = runner(
            cmd,
            env=spawn_env,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        log.warning(
            "dispatcher: timeout running %s for %s after %ss",
            pipeline_id, tenant_id, exc.timeout,
        )
        return {
            "tenant_id": tenant_id,
            "pipeline_id": pipeline_id,
            "rc": None,
            "duration_ms": elapsed_ms,
            "ok": False,
            "error": f"timeout after {exc.timeout}s",
        }
    except OSError as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        log.warning(
            "dispatcher: OSError spawning %s for %s: %s",
            pipeline_id, tenant_id, exc,
        )
        return {
            "tenant_id": tenant_id,
            "pipeline_id": pipeline_id,
            "rc": None,
            "duration_ms": elapsed_ms,
            "ok": False,
            "error": f"OSError: {exc}",
        }
    except Exception as exc:  # noqa: BLE001 - dispatcher must never crash
        elapsed_ms = int((time.monotonic() - started) * 1000)
        log.exception("dispatcher: unexpected error spawning %s for %s", pipeline_id, tenant_id)
        return {
            "tenant_id": tenant_id,
            "pipeline_id": pipeline_id,
            "rc": None,
            "duration_ms": elapsed_ms,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    elapsed_ms = int((time.monotonic() - started) * 1000)
    rc = getattr(completed, "returncode", None)
    ok = rc == 0
    error: str | None = None
    if not ok:
        stderr = getattr(completed, "stderr", b"") or b""
        if isinstance(stderr, bytes):
            try:
                stderr = stderr.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                stderr = ""
        # Keep the error short; full output is in dispatcher logs.
        tail = (stderr or "").strip().splitlines()[-1:] or [""]
        error = f"rc={rc}: {tail[0][:200]}" if tail[0] else f"rc={rc}"

    return {
        "tenant_id": tenant_id,
        "pipeline_id": pipeline_id,
        "rc": rc,
        "duration_ms": elapsed_ms,
        "ok": ok,
        "error": error,
    }


# ---------------------------------------------------------------------------
# tick visibility state
# ---------------------------------------------------------------------------


def _platform_dir() -> Path:
    """Resolve the platform-level state dir, defaulting to /opt/wc-solns
    when TENANT_ROOT is unset. Matches how heartbeat_store and other
    services treat TENANT_ROOT (default-when-unset, never fail-when-unset)
    so cron-spawned runs that don't carry TENANT_ROOT still land state
    in the canonical location."""
    base = os.environ.get("TENANT_ROOT") or "/opt/wc-solns"
    return Path(base) / "_platform"


def _write_last_tick(tick: dict[str, Any]) -> None:
    """Best-effort write of dispatcher_last_tick.json. Atomic via tmp +
    os.replace. Failures are logged, never raised - visibility file
    must not break the dispatch loop."""
    pdir = _platform_dir()
    try:
        pdir.mkdir(parents=True, exist_ok=True)
        path = pdir / "dispatcher_last_tick.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(tick, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("dispatcher: failed to write last_tick visibility file: %s", exc)


# ---------------------------------------------------------------------------
# main tick
# ---------------------------------------------------------------------------


def run(
    *,
    now: datetime | None = None,
    dry_run: bool = False,
    tolerance_minutes: int = 0,
    list_due_fn: Callable[[], dict[str, list[dict[str, Any]]]] | None = None,
    dispatch_fn: Callable[[str, str], dict[str, Any]] | None = None,
) -> int:
    """One dispatcher tick. Returns 0 always (cron should never see a
    non-zero from us; per-pipeline failures are reflected in heartbeats
    + the visibility file)."""

    now = now or datetime.now(timezone.utc)

    if list_due_fn is None:
        def _default_list_due() -> dict[str, list[dict[str, Any]]]:
            return tenant_schedule.list_due_all(now, tolerance_minutes=tolerance_minutes)
        due = _default_list_due()
    else:
        due = list_due_fn()

    if dispatch_fn is None:
        dispatch_fn = dispatch_one

    total = 0
    ok = 0
    failed = 0
    timed_out = 0
    results: list[dict[str, Any]] = []

    for tenant_id, entries in due.items():
        for entry in entries:
            pid = entry.get("pipeline_id")
            if not isinstance(pid, str) or not pid:
                continue
            total += 1
            if dry_run:
                log.info("dispatcher[dry-run]: would run %s for %s", pid, tenant_id)
                results.append({
                    "tenant_id": tenant_id,
                    "pipeline_id": pid,
                    "rc": None,
                    "duration_ms": 0,
                    "ok": True,
                    "error": None,
                    "dry_run": True,
                })
                ok += 1
                continue
            result = dispatch_fn(tenant_id, pid)
            results.append(result)
            if result.get("ok"):
                ok += 1
                log.info(
                    "dispatcher: %s/%s -> ok in %sms",
                    tenant_id, pid, result.get("duration_ms"),
                )
            else:
                failed += 1
                err = result.get("error") or ""
                if "timeout" in err.lower():
                    timed_out += 1
                log.warning(
                    "dispatcher: %s/%s -> FAIL %s",
                    tenant_id, pid, err,
                )

    log.info(
        "dispatcher tick: total=%d ok=%d failed=%d timed_out=%d (now=%s)",
        total, ok, failed, timed_out, now.isoformat(),
    )

    if not dry_run:
        _write_last_tick({
            "last_tick": now.isoformat(),
            "total": total,
            "ok": ok,
            "failed": failed,
            "timed_out": timed_out,
            "results": results[-50:],  # bounded for the visibility file
        })

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="WCAS Phase 2D scheduler dispatcher (per-minute tick).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Walk schedules + log intent, do not spawn any pipeline.",
    )
    parser.add_argument(
        "--tolerance-minutes",
        type=int,
        default=0,
        help="Catch-up window in minutes (default: 0 = strict same-minute).",
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

    return run(
        dry_run=args.dry_run,
        tolerance_minutes=args.tolerance_minutes,
    )


__all__ = [
    "DEFAULT_TIMEOUT",
    "dispatch_one",
    "main",
    "run",
]


if __name__ == "__main__":
    sys.exit(main())
