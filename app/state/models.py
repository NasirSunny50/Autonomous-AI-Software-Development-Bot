"""State models & enums shared across the bot.

Kept deliberately small and serializable so the whole project state fits in
SQLite and can be reloaded after a restart (resume-after-restart requirement).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ProjectStatus(str, enum.Enum):
    PLANNING = "planning"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"
    BLOCKED = "blocked"        # needs human review
    COMPLETED = "completed"
    FAILED = "failed"


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    NEEDS_REVIEW = "needs_review"   # exhausted retries -> human
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class Project:
    id: int | None
    name: str
    slug: str
    requirement: str
    tech_stack: str = ""
    status: str = ProjectStatus.PLANNING.value
    workspace_path: str = ""
    is_active: bool = False
    memory_json: str = "{}"          # compact project memory (stack, AC, known issues)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)


@dataclass
class Task:
    id: int | None
    project_id: int
    task_key: str                    # e.g. AUTH-001
    goal: str
    requirements_json: str = "[]"
    acceptance_json: str = "[]"      # list of {text, passed}
    relevant_files_json: str = "[]"
    status: str = TaskStatus.PENDING.value
    retry_count: int = 0
    order_index: int = 0
    result_json: str = "{}"
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)


@dataclass
class Checkpoint:
    id: int | None
    project_id: int
    task_id: int | None
    commit_hash: str
    label: str
    created_at: str = field(default_factory=now_iso)
