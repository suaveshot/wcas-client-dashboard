"""Daily platform tasks.

One entrypoint, fired once per day from VPS cron, orchestrating:

  * watchdog_digest.run() -- fingerprint-gated daily summary email to Sam
  * promo_lifecycle.sweep_expired_all_tenants() -- delete stale promo rows
    across every tenant

Both are idempotent. The digest has its own fingerprint+date gate so a
same-day re-run is silent. The sweep just calls prune_expired, which is a
no-op when nothing is stale. If either side raises, we log and keep going
so a single bad tenant cannot starve the other task.

Run via:

    python -m wc_solns_pipelines.platform.daily [--dry-run]

Crontab entry to install on garcia-vps (alongside the every-minute
dispatcher line):

    5 8 * * * /opt/wc-solns/dashboard_app/docker/platform_daily_cron.sh

08:05 UTC keeps it five minutes off the top-of-hour dispatcher tick so a
tick collision doesn't matter, and the digest lands in Sam's inbox before
the workday starts.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Callable

from dashboard_app.services import promo_lifecycle
from wc_solns_pipelines.platform import watchdog_digest

log = logging.getLogger("wcas.platform.daily")


def run(
    *,
    now: datetime | None = None,
    dry_run: bool = False,
    digest_fn: Callable[..., int] | None = None,
    sweep_fn: Callable[..., dict[str, Any]] | None = None,
) -> int:
    """Programmatic entrypoint. Returns 0 always."""
    moment = now or datetime.now(timezone.utc)
    log.info("daily platform run starting at %s (dry_run=%s)", moment.isoformat(), dry_run)

    digest_runner = digest_fn or watchdog_digest.run
    try:
        digest_rc = digest_runner(now=moment, dry_run=dry_run)
        log.info("watchdog digest rc=%s", digest_rc)
    except Exception:
        log.exception("watchdog digest raised; continuing")

    sweep_runner = sweep_fn or promo_lifecycle.sweep_expired_all_tenants
    try:
        if dry_run:
            log.info("[dry-run] would call sweep_expired_all_tenants")
        else:
            result = sweep_runner(now=moment)
            log.info(
                "promo sweep tenants_swept=%s rows_pruned=%s",
                result.get("tenants_swept"),
                result.get("rows_pruned"),
            )
    except Exception:
        log.exception("promo sweep raised; continuing")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Daily platform tasks (watchdog digest + promo sweep).",
    )
    parser.add_argument("--dry-run", action="store_true")
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


__all__ = ["run", "main"]


if __name__ == "__main__":
    sys.exit(main())
