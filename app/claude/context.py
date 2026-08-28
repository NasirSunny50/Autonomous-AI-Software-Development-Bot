"""Context minimizer — assemble the SMALLEST useful context for a task.

Never send the whole project. Give Claude only: the compact project memory, and
the contents of the specifically relevant files (each trimmed), under a hard total
budget. This is the single biggest lever on token usage.
"""
from __future__ import annotations

from pathlib import Path

from app.memory.project_memory import ProjectMemory
from app.utils.text import truncate

# Files we never inline (noise / huge / binary / secrets).
_SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next", "__pycache__",
              "venv", ".venv", "coverage", ".turbo"}
_SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".lock",
                  ".map", ".woff", ".woff2", ".ttf", ".pdf", ".zip"}


def _is_inlineable(path: Path) -> bool:
    if path.suffix.lower() in _SKIP_SUFFIXES:
        return False
    parts = set(path.parts)
    return not (parts & _SKIP_DIRS)


def read_relevant_files(project_path: Path | str, files: list[str], *,
                        per_file: int = 2000, max_total: int = 8000) -> str:
    """Read only the listed files, each trimmed, until the total budget is hit."""
    root = Path(project_path)
    chunks: list[str] = []
    total = 0
    for rel in files:
        p = (root / rel)
        if not p.is_file() or not _is_inlineable(p):
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        block = f"--- {rel} ---\n{truncate(content, per_file)}"
        if total + len(block) > max_total:
            break
        chunks.append(block)
        total += len(block)
    return "\n\n".join(chunks)


def build_context_block(memory: ProjectMemory, project_path: Path | str,
                        relevant_files: list[str], max_total: int = 8000) -> str:
    """Compose the minimal context: memory + trimmed relevant files."""
    parts = [f"PROJECT MEMORY:\n{memory.compact_text()}"]
    files_block = read_relevant_files(project_path, relevant_files, max_total=max_total)
    if files_block:
        parts.append(f"RELEVANT FILE CONTENTS:\n{files_block}")
    return "\n\n".join(parts)
