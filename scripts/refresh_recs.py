"""CLI smoke test for recs_generator + recs_store.

Usage:
    python scripts/refresh_recs.py <tenant_id> [--model claude-haiku-4-5]

Exits 0 on success, prints count + USD + written path. Uses real API
credentials from the environment; budget is enforced by cost_tracker.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Allow running as a standalone script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard_app.services import opus, recs_generator, recs_store  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh recommendations for a tenant.")
    parser.add_argument("tenant_id")
    parser.add_argument("--model", default=None, help="Override WCAS_RECS_MODEL / default model")
    args = parser.parse_args()

    tenant_id = args.tenant_id

    # Default to Haiku for smoke so we don't burn Opus budget on CLI runs.
    if args.model is None and not os.getenv("WCAS_RECS_MODEL"):
        os.environ["WCAS_RECS_MODEL"] = "claude-haiku-4-5"

    try:
        result = recs_generator.generate(tenant_id, model=args.model)
    except opus.OpusBudgetExceeded as exc:
        print(f"[budget] {exc}", file=sys.stderr)
        return 2
    except opus.OpusUnavailable as exc:
        print(f"[unavailable] {exc}", file=sys.stderr)
        return 3
    except recs_generator.RecsGenerationError as exc:
        print(f"[parse] {exc}", file=sys.stderr)
        return 4

    path = recs_store.write_today(
        tenant_id,
        recs=result["recs"],
        model=result["model"],
        usd=result["usd"],
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
    )

    live = sum(1 for r in result["recs"] if not r.get("draft"))
    drafts = sum(1 for r in result["recs"] if r.get("draft"))
    print(
        f"ok tenant={tenant_id} model={result['model']} "
        f"count={len(result['recs'])} live={live} drafts={drafts} "
        f"usd={result['usd']:.4f} in_tok={result['input_tokens']} out_tok={result['output_tokens']}"
    )
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
