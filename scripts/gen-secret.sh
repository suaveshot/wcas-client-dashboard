#!/usr/bin/env bash
# Generate a URL-safe 32-byte secret for SESSION_SECRET / HEARTBEAT_SHARED_SECRET.
# Usage: ./scripts/gen-secret.sh
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
