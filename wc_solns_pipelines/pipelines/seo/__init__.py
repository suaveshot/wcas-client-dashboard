"""Generic per-tenant SEO pipelines.

Today: weekly_report.py - emails the owner a plain-language digest of
GA4 + GSC for the trailing 7 days vs the prior 7 days.

Future (W5.5): recommendations.py - the SEO Recommendations Engine that
combines GA4 + GSC + BrightLocal + fetch_site_facts into ranked,
dollar-impact-estimated change lists.
"""
