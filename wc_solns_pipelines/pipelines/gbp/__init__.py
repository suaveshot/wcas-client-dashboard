"""Generic per-tenant Google Business Profile pipeline.

Drafts a weekly "What's New" post in the tenant's voice and dispatches
through services.dispatch (which queues for owner approval per the
tenant's prefs.require_approval[gbp] toggle).
"""
