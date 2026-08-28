"""Secret hygiene — detect & redact credentials before they leak.

Used to keep secrets out of logs, Telegram messages, and anything sent to AI
models. Detection is pattern-based (no values are ever returned, only the kind).
"""
from __future__ import annotations

import re

# (label, pattern). Patterns intentionally conservative to limit false positives.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("telegram-token", re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")),
    ("generic-secret", re.compile(
        r"(?i)\b(?:api[_-]?key|secret|password|token)\b\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{16,}")),
]


def find_secrets(text: str) -> list[str]:
    """Return the KINDS of secrets found (never the values)."""
    if not text:
        return []
    found = []
    for label, rx in _PATTERNS:
        if rx.search(text):
            found.append(label)
    return found


def contains_secret(text: str) -> bool:
    return bool(find_secrets(text))


def redact(text: str) -> str:
    """Mask any detected secrets so text is safe to log / send."""
    if not text:
        return text
    out = text
    for _label, rx in _PATTERNS:
        out = rx.sub("«redacted»", out)
    return out
