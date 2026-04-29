"""Generic per-tenant Email Assistant pipeline.

Reads inbound mail via IMAP using a Google App Password (or any IMAP-
compatible mailbox), drafts replies in the tenant's voice, and routes
them through services.dispatch (which queues every draft for owner
approval - Email Assistant is draft-only by hard rule).
"""
