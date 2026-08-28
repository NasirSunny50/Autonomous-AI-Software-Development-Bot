"""Task planner — turn a requirement into an ordered task list.

Token-frugal by design: for a simple request (0-1 features) we skip the AI
planning call entirely and create one bootstrap task. Only a genuinely multi-
feature requirement spends one Claude Code planning call to break the work down.
Falls back to a single task if planning is unavailable or unparseable.
"""
from __future__ import annotations

import re

from app.claude.worker import ClaudeWorker
from app.state.models import Project, Task
from app.state.store import StateStore
from app.utils.jsonparse import extract_json
from app.utils.logging import get_logger

log = get_logger("orchestrator")

_PLAN_PROMPT = """\
Break this software requirement into an ordered list of small, independently
buildable implementation tasks (3-8 tasks). Respond ONLY with JSON:

{{"tasks": [
  {{"key": "SETUP-001", "goal": "short goal", "acceptance": ["criterion", "criterion"]}}
]}}

Requirement:
{requirement}

Known features: {features}
Tech stack: {stack}
"""


def _slug_key(feature: str, i: int) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "", feature.split()[0]).upper()[:6] if feature else "TASK"
    return f"{base or 'TASK'}-{i:03d}"


async def plan_tasks(store: StateStore, worker: ClaudeWorker, project: Project,
                     requirement: str, features: list[str],
                     use_ai: bool = True) -> list[Task]:
    assert project.id is not None

    # --- cheap path: simple requirement -> one bootstrap task ---
    if len(features) <= 1 or not use_ai:
        acceptance = ["Project builds", "App runs without errors"]
        task = await store.add_task(
            project.id, "INIT-001", goal=requirement,
            requirements=features, acceptance=acceptance, order_index=0)
        log.info("plan: single bootstrap task for %s", project.slug)
        return [task]

    # --- AI planning path (one Claude call) for multi-feature work ---
    if not await worker.available():
        return await plan_tasks(store, worker, project, requirement, features, use_ai=False)

    prompt = _PLAN_PROMPT.format(requirement=requirement[:2000],
                                 features=", ".join(features[:20]) or "-",
                                 stack=project.tech_stack or "modern production stack")
    result = await worker.run_task(prompt, cwd=project.workspace_path)
    data = extract_json(result.text) if result.ok else None
    raw_tasks = (data or {}).get("tasks") if isinstance(data, dict) else None

    if not raw_tasks or not isinstance(raw_tasks, list):
        log.info("plan: AI plan unparseable, falling back to single task")
        return await plan_tasks(store, worker, project, requirement, features, use_ai=False)

    created: list[Task] = []
    for i, item in enumerate(raw_tasks[:8], start=1):
        if not isinstance(item, dict):
            continue
        goal = str(item.get("goal", "")).strip()
        if not goal:
            continue
        key = str(item.get("key") or _slug_key(goal, i))[:20]
        acceptance = [str(a) for a in item.get("acceptance", []) if a][:8] \
            or ["Feature works as described", "Project builds"]
        created.append(await store.add_task(
            project.id, key, goal=goal, requirements=[], acceptance=acceptance,
            order_index=i))
    if not created:
        return await plan_tasks(store, worker, project, requirement, features, use_ai=False)
    log.info("plan: %d tasks for %s", len(created), project.slug)
    return created
