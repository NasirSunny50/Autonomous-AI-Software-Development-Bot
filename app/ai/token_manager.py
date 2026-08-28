"""Call-budget + response cache for the free helper models.

Free providers cost nothing, but we still bound their use and avoid duplicate
calls — this keeps behaviour predictable and fast. (The real money budget, for
Claude Code, lives separately in `app/claude/budget.py`.)
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass
class CallBudget:
    """Per-task limits on how many helper-AI calls of each kind are allowed."""
    limits: dict[str, int] = field(default_factory=lambda: {
        "planning": 1, "debug": 3, "review": 1, "glue": 10,
    })
    used: dict[str, int] = field(default_factory=dict)

    def allow(self, kind: str) -> bool:
        limit = self.limits.get(kind, 0)
        return self.used.get(kind, 0) < limit if limit else False

    def record(self, kind: str) -> None:
        self.used[kind] = self.used.get(kind, 0) + 1

    def remaining(self, kind: str) -> int:
        return max(0, self.limits.get(kind, 0) - self.used.get(kind, 0))

    def reset(self) -> None:
        self.used.clear()


class ResponseCache:
    """Tiny in-memory cache to skip identical prompts within a run."""
    def __init__(self, max_entries: int = 256):
        self._store: dict[str, str] = {}
        self._order: list[str] = []
        self._max = max_entries

    @staticmethod
    def _key(prompt: str, kind: str) -> str:
        return hashlib.sha256(f"{kind}\x00{prompt}".encode()).hexdigest()

    def get(self, prompt: str, kind: str) -> str | None:
        return self._store.get(self._key(prompt, kind))

    def set(self, prompt: str, kind: str, text: str) -> None:
        k = self._key(prompt, kind)
        if k not in self._store:
            self._order.append(k)
            if len(self._order) > self._max:
                oldest = self._order.pop(0)
                self._store.pop(oldest, None)
        self._store[k] = text
