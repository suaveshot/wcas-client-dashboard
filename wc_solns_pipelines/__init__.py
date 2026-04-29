"""Generic per-tenant automation pipelines for the WCAS platform.

Sibling package to dashboard_app/. Pipelines run via:

    docker exec wcas-dashboard python -m wc_solns_pipelines.<name>.run --tenant <id>

triggered by VPS-side cron. See DECISIONS.md ADR-030 for the architecture.
"""
