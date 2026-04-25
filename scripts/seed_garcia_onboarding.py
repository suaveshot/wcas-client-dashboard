"""Prepare Garcia Folklorico Studio for the hackathon onboarding demo.

Idempotent. Run once before recording the video. Use --dry-run first to
see what would change.

What this does:
  1. Looks up (or creates, if --create-row) the Garcia Clients row in
     Airtable. Sets Onboarding Approved = True, Status = active, and
     clears Onboarding Completed At + any stale TOS acceptance so the
     wizard runs fresh for the recording.
  2. Wipes demo state files so the chat + samples + provisioning plan
     all start clean:
       /opt/wc-solns/garcia_folklorico/kb/existing_stack.md
       /opt/wc-solns/garcia_folklorico/kb/provisioning_plan.md
       /opt/wc-solns/garcia_folklorico/state_snapshot/provisioning_plan.json
       /opt/wc-solns/garcia_folklorico/samples/
       /opt/wc-solns/garcia_folklorico/agent_session.json
     Does NOT touch Garcia's deployed site or any real OAuth credentials.
  3. Prints the login URL Sam sends to Itzel (or uses himself).

Usage (from repo root):
    python -m scripts.seed_garcia_onboarding --email itzel@example.com --dry-run
    python -m scripts.seed_garcia_onboarding --email itzel@example.com

Required env (the dashboard already uses these):
    AIRTABLE_PAT
    AIRTABLE_BASE_ID
    AIRTABLE_CLIENTS_TABLE_ID
    TENANT_ROOT          (defaults to /opt/wc-solns)
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


TENANT_ID = "garcia_folklorico"


def _load_env_file(path: Path) -> None:
    """Load a simple KEY=VALUE .env file into os.environ (no quote stripping)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.strip().startswith("#"):
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip()
        if k and k not in os.environ:
            os.environ[k] = v


def _clear_tenant_files(
    tenant_id: str,
    *,
    dry_run: bool,
    include_credentials: bool = False,
) -> list[str]:
    """Remove demo state files. Returns a list of what would be / was removed.

    Wipes everything the activation wizard produces so a fresh run starts
    from zero:
      - All KB sections (company, services, voice, policies, pricing, faq,
        known_contacts, existing_stack, provisioning_plan, crm_mapping)
      - State snapshots (voice_card.json, crm_mapping.json,
        provisioning_plan.json)
      - activation.json (resets every ring to 'not started')
      - agent_session.json (drops the cached Managed Agents session id so
        the agent starts a new conversation)
      - samples/ directory

    Does NOT touch credentials/ by default. Pass include_credentials=True
    to also wipe Google OAuth tokens and force re-OAuth on the next run.
    """
    # Import inside so --dry-run without TENANT_ROOT set still works without
    # touching the real production directory.
    from dashboard_app.services import heartbeat_store, tenant_kb

    try:
        root = heartbeat_store.tenant_root(tenant_id)
    except heartbeat_store.HeartbeatError as exc:
        raise SystemExit(f"bad tenant_id {tenant_id!r}: {exc}")

    # Every whitelisted KB section. Pulling from tenant_kb keeps this list
    # in sync automatically as new sections get added to SECTIONS.
    kb_targets = [root / "kb" / f"{section}.md" for section in sorted(tenant_kb.SECTIONS)]

    state_targets = [
        root / "state_snapshot" / "voice_card.json",
        root / "state_snapshot" / "crm_mapping.json",
        root / "state_snapshot" / "provisioning_plan.json",
    ]

    other_targets = [
        root / "agent_session.json",
        root / "activation.json",  # ring grid; deleting resets every role
    ]

    targets = kb_targets + state_targets + other_targets

    if include_credentials:
        targets.extend([
            root / "credentials" / "google.json",
        ])

    dirs = [root / "samples"]
    removed: list[str] = []

    for target in targets:
        if target.exists():
            if not dry_run:
                target.unlink()
            removed.append(str(target))
    for d in dirs:
        if d.exists() and d.is_dir():
            if not dry_run:
                shutil.rmtree(d)
            removed.append(str(d) + " (dir)")
    return removed


def _update_airtable_row(email: str, *, create_if_missing: bool, dry_run: bool) -> dict:
    """Return a dict describing what was updated / would be updated."""
    from dashboard_app.services import clients_repo

    try:
        record = clients_repo.find_by_email(email)
    except RuntimeError as exc:
        raise SystemExit(
            "Airtable not configured: "
            + str(exc)
            + "\nSet AIRTABLE_PAT / AIRTABLE_BASE_ID / AIRTABLE_CLIENTS_TABLE_ID in .env."
        )

    target_fields = {
        "Email": email,
        "Tenant ID": TENANT_ID,
        "Status": "active",
        "Onboarding Approved": True,
        "Onboarding Completed At": "",
        # Clear any stale acceptance so the terms flow fires fresh for the demo.
        "TOS Version Accepted": "",
        "TOS Accepted At": "",
        "TOS Accepted IP": "",
        "TOS Accepted UA": "",
    }

    if record is None:
        if not create_if_missing:
            raise SystemExit(
                f"No Clients row found for {email}. Either create one in Airtable "
                f"(with Tenant ID = {TENANT_ID}) or rerun with --create-row."
            )
        if dry_run:
            return {"action": "would_create", "email": email, "fields": target_fields}
        created = clients_repo._table().create(target_fields)
        return {"action": "created", "record_id": created.get("id", ""), "fields": target_fields}

    record_id = record["id"]
    if dry_run:
        return {
            "action": "would_update",
            "record_id": record_id,
            "fields": target_fields,
        }
    clients_repo._table().update(record_id, target_fields)
    return {
        "action": "updated",
        "record_id": record_id,
        "fields": target_fields,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="Itzel's email on the Garcia row.")
    parser.add_argument(
        "--create-row",
        action="store_true",
        help="Create the Clients row if it doesn't exist yet.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen; don't touch Airtable or the filesystem.",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to a .env file to load before running (default: .env at CWD).",
    )
    parser.add_argument(
        "--login-url",
        default=os.getenv("WCAS_LOGIN_URL", "https://dashboard.westcoastautomationsolutions.com/auth/login"),
        help="Login URL to print for Itzel (default: production).",
    )
    parser.add_argument(
        "--include-credentials",
        action="store_true",
        help=(
            "HARD reset: also delete Google OAuth credentials so the next "
            "wizard run forces re-OAuth. Default keeps credentials so you "
            "can iterate on the wizard without re-authing every time."
        ),
    )
    parser.add_argument(
        "--reset-only",
        action="store_true",
        help=(
            "Skip Airtable updates entirely and only wipe filesystem state. "
            "Use this for fast iteration between local test runs once the "
            "Airtable row is already in the right shape."
        ),
    )
    args = parser.parse_args(argv)

    _load_env_file(Path(args.env_file))
    # Make `import dashboard_app` work when run as `python -m scripts....`.
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    print(f"[seed_garcia] tenant_id           = {TENANT_ID}")
    print(f"[seed_garcia] email               = {args.email}")
    print(f"[seed_garcia] dry_run             = {args.dry_run}")
    print(f"[seed_garcia] include_credentials = {args.include_credentials}")
    print(f"[seed_garcia] reset_only          = {args.reset_only}")
    print()

    # --- Airtable row --------------------------------------------------------
    if args.reset_only:
        print("[seed_garcia] airtable: SKIPPED (--reset-only)")
        print()
    else:
        at_result = _update_airtable_row(
            args.email, create_if_missing=args.create_row, dry_run=args.dry_run
        )
        print(f"[seed_garcia] airtable: {at_result['action']}")
        if "record_id" in at_result:
            print(f"[seed_garcia]   record_id = {at_result['record_id']}")
        for k, v in at_result["fields"].items():
            print(f"[seed_garcia]   {k}: {v!r}")
        print()

    # --- Filesystem cleanup --------------------------------------------------
    removed = _clear_tenant_files(
        TENANT_ID,
        dry_run=args.dry_run,
        include_credentials=args.include_credentials,
    )
    if removed:
        label = "would remove" if args.dry_run else "removed"
        print(f"[seed_garcia] filesystem: {label} {len(removed)} item(s)")
        for p in removed:
            print(f"[seed_garcia]   {p}")
    else:
        print("[seed_garcia] filesystem: nothing to remove (already clean)")
    print()

    # --- Final instructions --------------------------------------------------
    print(f"[seed_garcia] login URL for Itzel: {args.login_url}")
    print(f"[seed_garcia] after she enters {args.email} + clicks the magic link,")
    print(f"[seed_garcia] the wizard redirects to /activate/terms, then /activate.")
    print()
    if args.dry_run:
        print("[seed_garcia] dry-run complete. Re-run without --dry-run to apply.")
    else:
        print("[seed_garcia] done. Garcia is seeded for the demo recording.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
