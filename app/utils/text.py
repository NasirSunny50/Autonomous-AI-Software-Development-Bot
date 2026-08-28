"""Small deterministic text helpers (no AI)."""
from __future__ import annotations

import re


def slugify(value: str, max_len: int = 40) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return (value[:max_len].rstrip("-")) or "project"


def derive_project_name(requirement: str, max_words: int = 6) -> str:
    """Pick a short human name from the first line of a requirement."""
    first = requirement.strip().splitlines()[0] if requirement.strip() else "New Project"
    first = re.sub(r"^(build|create|make|develop)\s+(me\s+)?(a|an|the)?\s*", "",
                   first, flags=re.IGNORECASE).strip()
    words = first.split()
    name = " ".join(words[:max_words]) if words else "New Project"
    return name[:60] or "New Project"


def truncate(text: str, limit: int = 3500) -> str:
    """Keep Telegram messages within limits; trim from the middle."""
    if len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head - 20
    return f"{text[:head]}\n…(trimmed)…\n{text[-tail:]}"
