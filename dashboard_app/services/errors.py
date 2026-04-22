"""
Error handling service.

Generates short error IDs so the user sees something actionable
(copy-paste into a support email), while full tracebacks stay
server-side in the container log.
"""

import logging
import secrets

log = logging.getLogger("dashboard.errors")


def new_error_id() -> str:
    """8-char token, safe to show the user and search in logs."""
    return secrets.token_hex(4)


def log_error(error_id: str, exc: BaseException, request_path: str) -> None:
    """Record the full context server-side only. Never surface to the client."""
    log.exception(
        "error_id=%s path=%s exc=%s",
        error_id,
        request_path,
        exc.__class__.__name__,
    )
