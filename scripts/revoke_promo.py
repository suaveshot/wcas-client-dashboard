"""Revoke a tenant's active promo enrollment.

Generic by design: works for ANY automation in the catalog. Refuses to
touch tier_default or admin_added rows (those are managed via different
surfaces).

Usage (from repo root):
    python scripts/revoke_promo.py --tenant garcia_folklorico --automation voice_ai
    python scripts/revoke_promo.py --tenant acme --automation reviews --dry-run

Exit codes:
    0  success, or no-op when no active promo exists for the pair
    1  bad arguments / unknown CLI flag
    2  PromoError (refused to revoke a non-promo row, or invalid input)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as a standalone script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard_app.services import promo_lifecycle, tenant_automations  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Revoke a tenant promo enrollment.")
    parser.add_argument("--tenant", required=True, help="Tenant slug.")
    parser.add_argument(
        "--automation",
        required=True,
        help="Automation id from the catalog.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen; do not write.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 1 if exc.code != 0 else 0

    if args.dry_run:
        # Inspect state without writing so the operator can see what the
        # real run would do.
        try:
            rows = tenant_automations.list_enabled(
                args.tenant, include_expired=True
            )
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        match = next((r for r in rows if r.get("id") == args.automation), None)
        if match is None:
            print(
                f"DRY RUN: no active promo found for {args.automation} on {args.tenant}"
            )
            return 0
        if match.get("source") != "promo_optin":
            print(
                f"DRY RUN: would refuse to revoke {args.automation} on {args.tenant} "
                f"(source is {match.get('source')!r}, not 'promo_optin')"
            )
            return 0
        print(f"DRY RUN: would revoke promo {args.automation} for {args.tenant}")
        return 0

    try:
        revoked = promo_lifecycle.revoke_promo(args.tenant, args.automation)
    except promo_lifecycle.PromoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if revoked:
        print(f"Revoked promo {args.automation} for {args.tenant}")
    else:
        print(f"No active promo found for {args.automation} on {args.tenant}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
