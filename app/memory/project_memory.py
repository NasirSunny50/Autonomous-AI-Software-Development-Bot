"""Compact project memory — the key to keeping AI context (and cost) small.

Instead of sending the whole project to any model, the bot maintains a tiny
structured summary of what matters: stack, features done/pending, current task,
known issues, recent changes, and test status. This is what travels with tasks,
not the codebase.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


@dataclass
class ProjectMemory:
    tech_stack: str = ""
    features: list[str] = field(default_factory=list)
    completed_features: list[str] = field(default_factory=list)
    current_task: str = ""
    known_issues: list[str] = field(default_factory=list)
    recent_changes: list[str] = field(default_factory=list)
    test_status: str = "unknown"

    @classmethod
    def from_json(cls, raw: str | None) -> "ProjectMemory":
        if not raw:
            return cls()
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        known = {f.name for f in field_names()}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> dict:
        return asdict(self)

    def note_change(self, text: str, keep: int = 8) -> None:
        self.recent_changes.append(text)
        self.recent_changes = self.recent_changes[-keep:]

    def complete_feature(self, name: str) -> None:
        if name and name not in self.completed_features:
            self.completed_features.append(name)

    def compact_text(self, max_items: int = 6) -> str:
        """A short block safe to embed in a prompt (token-minimal)."""
        def few(items: list[str]) -> str:
            return ", ".join(items[:max_items]) if items else "-"
        return (
            f"Tech stack: {self.tech_stack or 'TBD'}\n"
            f"Completed: {few(self.completed_features)}\n"
            f"Pending features: {few(self.features)}\n"
            f"Known issues: {few(self.known_issues)}\n"
            f"Recent changes: {few(self.recent_changes)}\n"
            f"Test status: {self.test_status}"
        )


def field_names():
    return ProjectMemory.__dataclass_fields__.values()
