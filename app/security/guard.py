"""Security guards — deterministic, no AI.

Two jobs:
1. Working-directory validation: a project's commands must run INSIDE its own
   `workspaces/<project>/` folder, never leaking into another project or the host.
2. Destructive-command detection: certain operations always require explicit
   human approval, regardless of autonomy level.
"""
from __future__ import annotations

import re
from pathlib import Path

# Patterns that are never run automatically. Matched case-insensitively against
# the full command string. This is a guard, not a parser — err on the side of
# flagging.
_DESTRUCTIVE_PATTERNS = [
    r"\brm\s+-rf?\s+[/~]",          # rm -rf / or ~ (root/home wipes)
    r"\brmdir\s+/s",                # windows recursive dir delete
    r"\bgit\s+push\s+.*--force",    # force push
    r"\bgit\s+push\s+.*-f\b",
    r"\bgit\s+branch\s+-D\b",       # force delete branch
    r"\bgit\s+reset\s+--hard\s+origin",
    r"\bdrop\s+database\b",
    r"\bdrop\s+table\b",
    r"\btruncate\s+table\b",
    r"\bcurl\b.*\|\s*(sh|bash)\b",  # curl | sh
    r"\bwget\b.*\|\s*(sh|bash)\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r":\s*\(\)\s*\{",               # fork bomb
    r"\bshutdown\b", r"\breboot\b",
    r"\bformat\s+[a-zA-Z]:",        # windows format drive
    r"\bremove-item\b.*-recurse.*-force.*[\\/]$",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in _DESTRUCTIVE_PATTERNS]


class WorkspaceViolation(Exception):
    """Raised when a command would run outside the allowed workspace."""


def validate_workspace(target: Path | str, workspaces_root: Path | str) -> Path:
    """Return the resolved target if it lives inside workspaces_root, else raise.

    Prevents the bot from ever operating on a directory outside its managed
    workspaces (e.g. another of the owner's projects)."""
    target_p = Path(target).resolve()
    root_p = Path(workspaces_root).resolve()
    try:
        target_p.relative_to(root_p)
    except ValueError:
        raise WorkspaceViolation(
            f"Refusing to operate outside workspaces root:\n  target={target_p}\n  root={root_p}"
        )
    return target_p


def is_destructive(command: str) -> bool:
    """True if the command matches a destructive pattern (needs approval)."""
    return any(rx.search(command) for rx in _COMPILED)


def first_destructive_match(command: str) -> str | None:
    for rx in _COMPILED:
        if rx.search(command):
            return rx.pattern
    return None
