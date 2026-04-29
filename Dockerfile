FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for the app process
RUN useradd --create-home --shell /bin/bash app \
    && mkdir -p /opt/wc-solns \
    && chown -R app:app /app /opt/wc-solns

COPY --chown=app:app requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app dashboard_app ./dashboard_app
# scripts/ ships for: (a) the DEMO_MODE=true hook in home_context.py that
# imports scripts.sanitize_for_demo, (b) docker exec ... python scripts/*
# for ops tasks (sanitize --check/--write, seed_* helpers, refresh_recs).
COPY --chown=app:app scripts ./scripts
# wc_solns_pipelines/ ships the per-tenant pipelines (sales, reviews, gbp,
# email_assistant, seo) and the platform-level runners (dispatcher,
# watchdog_digest, daily orchestrator). The host crontab invokes these via
# `docker exec wcas-dashboard python -m wc_solns_pipelines.platform.dispatcher`.
COPY --chown=app:app wc_solns_pipelines ./wc_solns_pipelines

USER app

# Health check hits /healthz which main.py serves
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

EXPOSE 8000

CMD ["uvicorn", "dashboard_app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
