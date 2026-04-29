"""Multi-tenant heartbeat push for wc_solns_pipelines.

Each generic pipeline calls this at the end of its run to push status to the
dashboard's /api/heartbeat endpoint. Same shared secret as AP, just multi-tenant
(X-Tenant-Id: <slug>) and pipelines pass status/summary explicitly instead of
having log lines re-parsed.

Usage from a pipeline (preferred - in-process):

    from wc_solns_pipelines.shared.push_heartbeat import push

    push(
        tenant_id="acme",
        pipeline_id="reviews",
        status="success",
        summary="Drafted 3 review replies",
        events=[{"kind": "review.posted", "rating": 5}],  # optional, bumps goals
    )

Usage from a CLI (ad-hoc / debug):

    python -m wc_solns_pipelines.shared.push_heartbeat \\
        --tenant acme \\
        --pipeline reviews \\
        --status success \\
        --summary "Drafted 3 review replies" \\
        --events '[{"kind":"review.posted","rating":5}]'

Design rules (lifted from AP shared/push_heartbeat.py):
  - Never crash the calling pipeline. Any network/parse error logs and returns 0.
  - Never block more than 5 seconds (configurable via --timeout).
  - Reads DASHBOARD_URL + HEARTBEAT_SHARED_SECRET from os.environ first
    (Docker convention); falls back to a .env file at project root (local dev).

Per ADR-030, pipelines run inside the same Docker image as the dashboard but
push heartbeats over HTTP rather than calling heartbeat_store.write_snapshot
directly. That keeps the seam clean if pipelines ever move to a separate
container/host.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


def _load_env_file() -> dict[str, str]:
    """Parse a .env at project root if present. os.environ takes priority."""
    project_root = Path(__file__).resolve().parents[2]
    env_path = project_root / ".env"
    out: dict[str, str] = {}
    if not env_path.exists():
        return out
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            v = v.strip().strip("'").strip('"')
            out[k.strip()] = v
    except OSError:
        pass
    return out


def _resolve(name: str, env: dict[str, str]) -> str:
    """os.environ wins over .env (matches Docker convention)."""
    val = os.environ.get(name)
    if val:
        return val
    return env.get(name, "")


def _post(
    url: str,
    secret: str,
    tenant_id: str,
    payload: dict[str, Any],
    timeout: float,
) -> tuple[bool, str]:
    data = json.dumps(payload).encode("utf-8")
    req = Request(
        url.rstrip("/") + "/api/heartbeat",
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Heartbeat-Secret": secret,
            "X-Tenant-Id": tenant_id,
            "User-Agent": "wcas-pipelines-heartbeat/1.0",
        },
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read(2048).decode("utf-8", errors="replace")
            return resp.status == 200, f"HTTP {resp.status}: {body}"
    except HTTPError as e:
        try:
            err_body = e.read()[:500].decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        return False, f"HTTPError {e.code}: {err_body}"
    except URLError as e:
        return False, f"URLError: {e.reason}"
    except Exception as e:  # pragma: no cover - last-resort defensive
        return False, f"{type(e).__name__}: {e}"


def build_payload(
    tenant_id: str,
    pipeline_id: str,
    status: str,
    summary: str,
    events: list[dict[str, Any]] | None = None,
    state_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct the heartbeat payload. Exposed for tests + dry-run."""
    payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "pipeline_id": pipeline_id,
        "status": status,
        "summary": summary,
        "pushed_at": datetime.now(timezone.utc).isoformat(),
    }
    if events:
        payload["events"] = events
    if state_summary is not None:
        payload["state_summary"] = state_summary
    return payload


def push(
    tenant_id: str,
    pipeline_id: str,
    status: str = "success",
    summary: str = "",
    events: list[dict[str, Any]] | None = None,
    state_summary: dict[str, Any] | None = None,
    timeout: float = 5.0,
    dry_run: bool = False,
) -> int:
    """Push a heartbeat. Always returns 0 (fire-and-forget) so the calling
    pipeline never aborts on a heartbeat hiccup. Inspect logs for outcome."""
    env = _load_env_file()
    url = _resolve("DASHBOARD_URL", env)
    secret = _resolve("HEARTBEAT_SHARED_SECRET", env)

    payload = build_payload(
        tenant_id=tenant_id,
        pipeline_id=pipeline_id,
        status=status,
        summary=summary,
        events=events,
        state_summary=state_summary,
    )

    if dry_run:
        print(json.dumps(payload, indent=2, default=str))
        return 0

    if not url or not secret:
        logger.warning(
            "skipped heartbeat for %s/%s: missing DASHBOARD_URL or HEARTBEAT_SHARED_SECRET",
            tenant_id,
            pipeline_id,
        )
        return 0

    start = time.monotonic()
    ok, detail = _post(url, secret, tenant_id, payload, timeout)
    elapsed = time.monotonic() - start
    log = logger.info if ok else logger.warning
    log(
        "heartbeat %s/%s ok=%s elapsed=%.2fs detail=%s",
        tenant_id,
        pipeline_id,
        ok,
        elapsed,
        detail[:200],
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Push a per-tenant pipeline heartbeat to the WCAS dashboard.",
    )
    parser.add_argument("--tenant", required=True, help="tenant_id slug")
    parser.add_argument("--pipeline", required=True, help="pipeline_id (e.g. reviews, gbp)")
    parser.add_argument(
        "--status",
        default="success",
        choices=["success", "error", "unknown"],
    )
    parser.add_argument("--summary", default="")
    parser.add_argument(
        "--events",
        default="",
        help='Optional JSON array of event objects, e.g. \'[{"kind":"review.posted","rating":5}]\'',
    )
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    events: list[dict[str, Any]] | None = None
    if args.events:
        try:
            parsed = json.loads(args.events)
            if isinstance(parsed, list):
                events = parsed
            else:
                print(
                    f"WARN: --events must be a JSON array, got {type(parsed).__name__}; ignoring",
                    file=sys.stderr,
                )
        except json.JSONDecodeError as e:
            print(f"WARN: --events not valid JSON, ignoring: {e}", file=sys.stderr)

    return push(
        tenant_id=args.tenant,
        pipeline_id=args.pipeline,
        status=args.status,
        summary=args.summary,
        events=events,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )


__all__ = ["push", "build_payload", "main"]


if __name__ == "__main__":
    sys.exit(main())
