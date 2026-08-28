"""Structured prompt builders for Claude Code.

Claude Code is given ONE focused task at a time with explicit acceptance
criteria — never the whole project. Prompts request concise, actionable work and
a short structured report, to keep token usage down.
"""
from __future__ import annotations

import json

from app.state.models import Project, Task


def build_task_prompt(project: Project, task: Task) -> str:
    reqs = json.loads(task.requirements_json or "[]")
    accept = [a["text"] for a in json.loads(task.acceptance_json or "[]")]
    files = json.loads(task.relevant_files_json or "[]")

    req_lines = "\n".join(f"- {r}" for r in reqs) or "- (derive from the goal)"
    acc_lines = "\n".join(f"{i+1}. {a}" for i, a in enumerate(accept)) or \
        "1. Feature works as described in the goal"
    file_lines = "\n".join(f"- {f}" for f in files) or "- (choose the appropriate files)"

    return f"""\
TASK: {task.task_key}

Goal:
{task.goal}

Project context:
- Name: {project.name}
- Requirement: {project.requirement}
- Tech stack: {project.tech_stack or "choose a modern, production-ready stack"}

Requirements:
{req_lines}

Acceptance Criteria:
{acc_lines}

Relevant files:
{file_lines}

Instructions:
- You are working inside this project's own directory. Modify only the files
  needed for THIS task; do not change unrelated functionality.
- Follow the existing project architecture and conventions.
- If this is a brand-new project, scaffold it with the appropriate official CLI
  (e.g. create-next-app / vite) rather than writing boilerplate by hand.
- After implementing, run the build; run relevant tests if they exist.
- Keep your final answer CONCISE. End with a short report in exactly this format:

  CHANGED_FILES: <comma-separated paths>
  BUILD: <pass|fail|not-run>
  TESTS: <pass|fail|not-run>
  SUMMARY: <one or two sentences>
"""


def build_fix_prompt(project: Project, task: Task, evidence: str) -> str:
    """Focused debugging task: evidence only, never the whole project."""
    return f"""\
FIX TASK for: {task.task_key}

Goal (unchanged):
{task.goal}

A quality check FAILED. Here is the focused evidence (error output, failing test,
and recent diff). Do NOT re-read the whole project — use this evidence:

--- EVIDENCE ---
{evidence}
--- END EVIDENCE ---

Instructions:
- Diagnose the specific cause and apply the smallest correct fix.
- Do not introduce unrelated changes.
- Re-run the build/tests you can.
- End with the same concise report format:

  CHANGED_FILES: <comma-separated paths>
  BUILD: <pass|fail|not-run>
  TESTS: <pass|fail|not-run>
  SUMMARY: <what was wrong and what you changed>
"""
