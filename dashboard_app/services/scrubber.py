"""
PII scrubber for prompt + log writes.

Any string written to dashboard_decisions.jsonl, to the cost-tracker
log, or sent to a remote observability service passes through here
first. When DEBUG_LOG_PROMPTS=true (dev only), the scrubber is a
no-op; prod ALWAYS scrubs.

Patterns:
  - emails            foo@bar.com          -> [email]
  - phones            (310) 555-1212       -> [phone]
  - dollar amounts    $18,240 / $1.5k      -> [money]
  - bearer secrets    sk-ant-api03-...     -> [secret]

Intentionally conservative: false positives are fine (over-redaction),
false negatives leak. This is not a substitute for structured logging.
"""

import os
import re

_PATTERNS = (
    (re.compile(r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), "[email]"),
    (re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)"), "[phone]"),
    (re.compile(r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?[kKmM]?"), "[money]"),
    (re.compile(r"\bsk-ant-[a-zA-Z0-9_-]{20,}\b"), "[secret]"),
    (re.compile(r"\bpat[a-zA-Z0-9]{14,}\b"), "[secret]"),
    (re.compile(r"\bghp_[a-zA-Z0-9]{20,}\b"), "[secret]"),
    (re.compile(r"\bghs_[a-zA-Z0-9]{20,}\b"), "[secret]"),
)


def scrub(text: str) -> str:
    if not text:
        return text
    if os.getenv("DEBUG_LOG_PROMPTS", "false").lower() == "true":
        return text
    out = text
    for pattern, placeholder in _PATTERNS:
        out = pattern.sub(placeholder, out)
    return out
