"""
Seed Garcia Folklorico's Students table with realistic synthetic records
so the v0.6.0 demo has a populated CRM to map.

Garcia's real Airtable base (apptsiv5kunJJa81G) has only ~2 real records
(synced from her website registration form). For the hackathon demo we
need a believable customer population the agent can segment + simulate
re-engagement against.

Every seeded record is tagged in the Notes field with the literal string
"[seed]" so this script can find + remove them idempotently. Re-runs
delete + re-create, so the population stays at exactly the planned size.

Distribution targets:
  - 12 INACTIVE   - Block "Spring 2026", Registered On 90-120 days ago
                    (these are the segment the live customer simulation
                    pulls its first name from; sorted by Registered On
                    ascending, the FIRST one is the deterministic demo
                    target so the script seeds Maria Sanchez at the
                    earliest date so the demo always picks her)
  - 15 ACTIVE     - Block "Summer 2026", Registered On 5-25 days ago
  - 3  BRAND NEW  - Block "Summer 2026", Registered On 1-5 days ago

Usage:
  python scripts/seed_garcia_bookings.py --dry-run    # preview only
  python scripts/seed_garcia_bookings.py              # seed for real
  python scripts/seed_garcia_bookings.py --cleanup    # remove all seed records

Requires: AIRTABLE_PAT in environment (the same one the dashboard uses).
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

GARCIA_BASE_ID = "apptsiv5kunJJa81G"
STUDENTS_TABLE = "Students"
SEED_TAG = "[seed]"

# The first inactive record gets a known name + earliest date so the
# live simulation endpoint deterministically picks her on every demo run.
DEMO_HERO_NAME = "Maria Sanchez"


# Realistic Latino/family names for Garcia's audience. Mix of common
# first/last names; deliberately avoids any name appearing in the real
# Airtable rows we sampled (Mireya Gomez, Nancy Torres).
_FIRST_PARENT = [
    "Maria", "Carmen", "Lupe", "Ana", "Sofia", "Rosa", "Gabriela", "Leticia",
    "Patricia", "Adriana", "Veronica", "Diana", "Elena", "Cristina", "Yolanda",
    "Beatriz", "Miguel", "Roberto", "Carlos", "Luis", "Javier", "Ricardo",
    "Hector", "Eduardo", "Fernando", "Manuel",
]
_LAST = [
    "Sanchez", "Mendoza", "Castillo", "Vargas", "Herrera", "Ramirez", "Cruz",
    "Reyes", "Morales", "Ortiz", "Jimenez", "Ruiz", "Aguilar", "Vasquez",
    "Mendez", "Castro", "Salazar", "Delgado", "Soto", "Romero", "Navarro",
    "Acosta", "Flores", "Cabrera", "Estrada",
]
_FIRST_CHILD = [
    "Sofia", "Isabella", "Camila", "Valentina", "Emma", "Mia", "Lucia",
    "Olivia", "Ava", "Aria", "Daniel", "Mateo", "Diego", "Alejandro", "Santiago",
    "Sebastian", "Lucas", "Adrian", "Gabriel", "Nicolas",
]
_CLASS_OPTIONS = ["Botones de Flor", "Semillas", "Hojas", "Flores", "Folklorico Avanzado"]


def _api():
    try:
        from pyairtable import Api
    except ImportError:
        print("ERROR: pyairtable not installed. pip install pyairtable", file=sys.stderr)
        sys.exit(1)
    pat = os.getenv("AIRTABLE_PAT", "")
    if not pat:
        print("ERROR: AIRTABLE_PAT not set in environment.", file=sys.stderr)
        sys.exit(1)
    return Api(pat).table(GARCIA_BASE_ID, STUDENTS_TABLE)


def _phone() -> str:
    return f"805{random.randint(2000000, 9999999):07d}"


def _email(parent: str, last: str) -> str:
    return f"{parent.lower()}.{last.lower()}{random.randint(10, 99)}@example.com"


def _build_records() -> list[dict]:
    """Return the planned 30-record population, with deterministic Maria Sanchez first."""
    now = datetime.now(timezone.utc)
    records: list[dict] = []
    rng = random.Random(424)  # deterministic seed so re-runs produce same names

    # ---- 12 INACTIVE: Block "Spring 2026", 90-120 days ago ----------------
    # First record is the deterministic demo hero. Earliest date = sorts
    # first when the agent reads records sorted by Registered On ascending.
    hero_parent_first, hero_parent_last = "Maria", "Sanchez"
    hero_child = "Sofia"
    hero_date = now - timedelta(days=120)
    records.append(_make("Spring 2026", hero_parent_first, hero_parent_last, hero_child, hero_date, rng))

    for i in range(11):
        days_ago = rng.randint(90, 119)
        parent_first = rng.choice(_FIRST_PARENT)
        parent_last = rng.choice(_LAST)
        child = rng.choice(_FIRST_CHILD)
        when = now - timedelta(days=days_ago)
        records.append(_make("Spring 2026", parent_first, parent_last, child, when, rng))

    # ---- 15 ACTIVE: Block "Summer 2026", 5-25 days ago --------------------
    for _ in range(15):
        days_ago = rng.randint(5, 25)
        parent_first = rng.choice(_FIRST_PARENT)
        parent_last = rng.choice(_LAST)
        child = rng.choice(_FIRST_CHILD)
        when = now - timedelta(days=days_ago)
        records.append(_make("Summer 2026", parent_first, parent_last, child, when, rng))

    # ---- 3 BRAND NEW: Block "Summer 2026", 1-5 days ago -------------------
    for _ in range(3):
        days_ago = rng.randint(1, 5)
        parent_first = rng.choice(_FIRST_PARENT)
        parent_last = rng.choice(_LAST)
        child = rng.choice(_FIRST_CHILD)
        when = now - timedelta(days=days_ago)
        records.append(_make("Summer 2026", parent_first, parent_last, child, when, rng))

    return records


def _make(block: str, parent_first: str, parent_last: str, child: str,
          when: datetime, rng: random.Random) -> dict:
    return {
        "Child Name": child,
        "Parent Name": f"{parent_first} {parent_last}",
        "Phone": _phone(),
        "Email": _email(parent_first, parent_last),
        "Class": rng.choice(_CLASS_OPTIONS),
        "Block": block,
        "Child Age": rng.randint(4, 12),
        "Registered On": when.isoformat(),
        "Notes": f"{SEED_TAG} v0.6.0 demo seed. Safe to delete.",
        "Emergency Contact": f"{parent_last} {_phone()}",
    }


def _existing_seed_ids(table) -> list[str]:
    """Return record ids whose Notes field contains the seed tag."""
    ids = []
    for rec in table.iterate(page_size=100, fields=["Notes"]):
        notes = (rec.get("fields", {}).get("Notes") or "")
        if SEED_TAG in notes:
            ids.append(rec["id"])
    return ids


def cmd_dry_run() -> None:
    table = _api()
    existing = _existing_seed_ids(table)
    planned = _build_records()
    print(f"DRY RUN")
    print(f"  Existing seed records to delete: {len(existing)}")
    print(f"  New records to create:           {len(planned)}")
    print(f"  Distribution:")
    by_block = {}
    for r in planned:
        by_block[r["Block"]] = by_block.get(r["Block"], 0) + 1
    for block, count in sorted(by_block.items()):
        print(f"    Block {block!r}: {count}")
    print(f"  First (sorted-by-date) inactive student: "
          f"{planned[0]['Parent Name']} (child {planned[0]['Child Name']})")
    print(f"  -> Re-run without --dry-run to apply.")


def cmd_seed() -> None:
    table = _api()
    existing = _existing_seed_ids(table)
    if existing:
        print(f"Removing {len(existing)} existing seed record(s)...")
        # Airtable batch delete is 10 at a time.
        for i in range(0, len(existing), 10):
            table.batch_delete(existing[i:i+10])
    planned = _build_records()
    print(f"Creating {len(planned)} fresh seed records...")
    # Airtable batch create is 10 at a time.
    created = 0
    for i in range(0, len(planned), 10):
        result = table.batch_create(planned[i:i+10])
        created += len(result)
    print(f"Done. Created {created} records.")
    print(f"Hero (deterministic first inactive): {DEMO_HERO_NAME}")


def cmd_cleanup() -> None:
    table = _api()
    existing = _existing_seed_ids(table)
    if not existing:
        print("No seed records to remove.")
        return
    print(f"Removing {len(existing)} seed record(s)...")
    for i in range(0, len(existing), 10):
        table.batch_delete(existing[i:i+10])
    print(f"Done.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed Garcia bookings demo data.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing.")
    parser.add_argument("--cleanup", action="store_true", help="Remove all seed records and exit.")
    args = parser.parse_args()

    if args.cleanup:
        cmd_cleanup()
    elif args.dry_run:
        cmd_dry_run()
    else:
        cmd_seed()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
