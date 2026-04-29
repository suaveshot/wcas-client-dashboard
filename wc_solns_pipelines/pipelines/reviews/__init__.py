"""Generic per-tenant Reviews pipeline.

Reads new GBP reviews for the tenant's location, drafts replies in the
tenant's voice, and dispatches them through services.dispatch (which
queues for owner approval per the tenant's prefs).
"""
