"""
Lightweight, dependency-free PII redaction.

Scans free-form text for common personally identifiable information (email,
phone, card numbers, government IDs, IPs, secrets) and replaces each match with
a safe label, so raw PII never reaches the telemetry store.

Order matters: the most specific / greedy patterns (cards, keys) run before the
looser ones (phone) so a card number isn't half-eaten by the phone rule.
"""
import re
from core.config import PII_REDACTION_ENABLED

# Each entry: (compiled pattern, replacement label).
# Patterns are intentionally conservative — better to occasionally miss than to
# mangle normal text, but the common high-risk shapes are all covered.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Emails: user@domain.tld
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
     "[REDACTED_EMAIL]"),

    # Credit/debit cards: 13–16 digits, optionally split by spaces or dashes.
    (re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
     "[REDACTED_CARD]"),

    # US SSN: 123-45-6789
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
     "[REDACTED_SSN]"),

    # Indian PAN: ABCDE1234F
    (re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"),
     "[REDACTED_PAN]"),

    # Aadhaar: 12 digits in 4-4-4 groups
    (re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b"),
     "[REDACTED_AADHAAR]"),

    # Bearer / API keys / long secret tokens (sk-..., ghp_..., 20+ char blobs)
    (re.compile(r"\b(?:sk|pk|ghp|gho|xox[baprs])[-_][A-Za-z0-9]{16,}\b"),
     "[REDACTED_SECRET]"),

    # Phone numbers: optional +country, then 10–13 digits with spaces/dashes.
    # Runs LATE so it doesn't swallow card/SSN/aadhaar already handled above.
    (re.compile(r"(?<!\w)(?:\+?\d{1,3}[ -]?)?(?:\d[ -]?){9,12}\d(?!\w)"),
     "[REDACTED_PHONE]"),

    # IPv4 addresses
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
     "[REDACTED_IP]"),
]


def redact(text: str | None) -> str | None:
    """Return `text` with all recognized PII replaced by labels.

    Returns the input unchanged if it is None/empty. Never raises — redaction
    must never break the caller (logging is best-effort).
    """
    if not text or not PII_REDACTION_ENABLED:
        return text

    try:
        for pattern, label in _PATTERNS:
            text = pattern.sub(label, text)
    except Exception:
        # If anything ever goes wrong, prefer dropping the text over leaking it.
        return "[REDACTION_ERROR]"

    return text
