"""Issue a time-bounded promo enrollment to a tenant.

Generic by design: works for ANY automation in the catalog (gbp, seo,
reviews, email_assistant, chat_widget, blog, social, voice_ai, etc.).
Catalog membership is enforced by promo_lifecycle.grant_promo.

Usage (from repo root):
    python scripts/issue_promo.py --tenant garcia_folklorico --automation voice_ai --days 30
    python scripts/issue_promo.py --tenant acme --automation reviews --days 14 --dry-run

Exit codes:
    0  success (or successful dry-run)
    1  bad arguments / unknown CLI flag
    2  PromoError (validation failure: bad tenant slug, unknown
       automation_id, non-positive days)
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running as a standalone script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard_app.services import promo_lifecycle  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Issue a promo enrollment to a tenant.")
    parser.add_argument("--tenant", required=True, help="Tenant slug (e.g. garcia_folklorico).")
    parser.add_argument(
        "--automation",
        required=True,
        help="Automation id from the catalog (e.g. voice_ai, reviews, gbp).",
    )
    parser.add_argument(
        "--days",
        required=True,
        type=int,
        help="Length of the promo in days (must be positive).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be granted; do not write.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse already printed an error to stderr.
        return 1 if exc.code != 0 else 0

    now = datetime.now(timezone.utc)

    if args.dry_run:
        try:
            promo_lifecycle.validate_grant_args(args.tenant, args.automation, args.days)
        except promo_lifecycle.PromoError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        expires_at = (now + timedelta(days=args.days)).isoformat()
        print(
            f"DRY RUN: would grant promo: {args.automation} to {args.tenant} "
            f"for {args.days} days, expires {expires_at}"
        )
        return 0

    try:
        entry = promo_lifecycle.grant_promo(
            args.tenant,
            args.automation,
            days=args.days,
            now=now,
        )
    except promo_lifecycle.PromoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(
        f"Granted promo: {entry['id']} to {args.tenant} "
        f"for {args.days} days, expires {entry['expires_at']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
