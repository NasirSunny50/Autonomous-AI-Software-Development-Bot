"""Orchestrator — THE COMMANDER.

Deterministic Python decides what happens and when. It creates the project and
its isolated workspace, checkpoints with git, hands ONE focused task to Claude
Code, detects the resulting changes with git (not by trusting AI text), commits
on success, and reports back through a notify callback.

Phase 1 proves the full vertical slice with a single task. Later phases add
planning/task-breakdown (Phase 3), the quality gate (Phase 7), and self-healing
retries (Phase 6) around this same spine.
"""
from __future__ import annotations

from pathlib import Path
from typing import Awaitable, Callable

from app.ai.glue import GlueAI
from app.claude.budget import ClaudeBudget
from app.claude.prompts import build_task_prompt
from app.claude.report import parse_report
from app.claude.worker import ClaudeWorker
from app.config import Settings
from app.git.repo import GitRepo
from app.security.guard import validate_workspace
from app.state.models import Project, ProjectStatus, Task, TaskStatus
from app.state.store import StateStore
from app.utils.logging import get_logger
from app.utils.text import derive_project_name, slugify

log = get_logger("orchestrator")

# An async function the orchestrator calls to send a concise message to the owner.
Notifier = Callable[[str], Awaitable[None]]


async def _noop(_: str) -> None:
    return None


class Orchestrator:
    def __init__(self, settings: Settings, store: StateStore,
                 worker: ClaudeWorker, notify: Notifier | None = None,
                 glue: GlueAI | None = None):
        self.settings = settings
        self.store = store
        self.worker = worker
        self.notify = notify or _noop
        self.glue = glue
        self.budget = ClaudeBudget(
            store,
            max_per_day=settings.claude_max_calls_per_day,
            max_per_project=settings.claude_max_calls_per_project,
        )
        self._project_calls: dict[int, int] = {}

    # ------------------------------------------------------------------
    async def handle_requirement(self, requirement: str,
                                 name: str | None = None) -> Project:
        """Create a project from a plain-language requirement and start it.

        Phase 1: create workspace + git, register a single bootstrap task, and
        execute it end-to-end. (Full AI planning arrives in Phase 3.)
        """
        # Cheap, free-model glue: turn the plain-language requirement into a
        # structured name / stack / features. Deterministic fallback if no free
        # provider is configured, so this never costs anything and never blocks.
        parsed = {"name": "", "tech_stack": "", "features": []}
        if self.glue is not None:
            parsed = await self.glue.parse_requirement(requirement)

        name = name or parsed.get("name") or derive_project_name(requirement)
        tech_stack = parsed.get("tech_stack", "")
        features = parsed.get("features", [])
        slug = await self._unique_slug(slugify(name))
        workspace = self.settings.workspaces_path / slug
        # Guard: the workspace must live under the managed workspaces root.
        validate_workspace(workspace, self.settings.workspaces_path)
        workspace.mkdir(parents=True, exist_ok=True)

        project = await self.store.create_project(
            name=name, slug=slug, requirement=requirement,
            workspace_path=str(workspace),
        )
        await self.store.set_active_project(project.id)  # type: ignore[arg-type]
        await self.store.update_project_status(project.id, ProjectStatus.IN_PROGRESS.value)
        if tech_stack:
            await self.store.update_project_tech_stack(project.id, tech_stack)
            project.tech_stack = tech_stack
        # Seed compact project memory (used later to keep AI context small).
        await self.store.update_project_memory(project.id, {
            "tech_stack": tech_stack, "features": features, "known_issues": [],
        })

        repo = GitRepo(workspace,
                       author_name="AI Dev Bot",
                       author_email="aidevbot@localhost")
        await repo.ensure_repo()

        await self._log(project, "🚀 Project created & workspace initialized")
        await self.notify(
            f"🚀 *Project Started*\n\n*{name}*\n`{slug}`\n\nWorkspace ready. "
            f"Handing the first task to Claude Code…"
        )

        # Phase 1 bootstrap task = build the thing described by the requirement.
        task = await self.store.add_task(
            project_id=project.id,  # type: ignore[arg-type]
            task_key="INIT-001",
            goal=requirement,
            requirements=[],
            acceptance=["Project builds", "Runs without errors"],
            order_index=0,
        )
        await self.execute_task(project, task)
        return project

    # ------------------------------------------------------------------
    async def execute_task(self, project: Project, task: Task) -> bool:
        """Run one task through Claude Code with git checkpointing.

        Returns True on success. Deterministic steps (budget, git, change
        detection, commit) are Python's job; only the coding is Claude's.
        """
        assert project.id is not None and task.id is not None
        workspace = Path(project.workspace_path)
        validate_workspace(workspace, self.settings.workspaces_path)
        repo = GitRepo(workspace)

        # --- budget guard (Claude is the only cost) ---
        used = self._project_calls.get(project.id, 0)
        decision = await self.budget.check(used)
        if not decision.allowed:
            await self.store.update_project_status(project.id, ProjectStatus.PAUSED.value)
            await self._log(project, f"⏸ Paused — {decision.reason}", task)
            await self.notify(
                f"⏸ *Paused* to protect your budget.\n{decision.reason}\n\n"
                f"No paid usage will happen automatically. Send /resume to continue."
            )
            return False

        # --- ensure Claude Code is actually available ---
        if not await self.worker.available():
            await self.store.update_project_status(project.id, ProjectStatus.BLOCKED.value)
            await self._log(project, "❌ Claude Code CLI not available", task)
            await self.notify(
                "❌ Claude Code CLI not found.\n\nInstall it with "
                "`npm i -g @anthropic-ai/claude-code`, run `claude` once to log in, "
                "then set `CLAUDE_COMMAND` in `.env`."
            )
            return False

        # --- checkpoint before the task ---
        await self.store.update_task(task.id, status=TaskStatus.IN_PROGRESS.value)
        checkpoint_hash = await repo.checkpoint(f"before {task.task_key}")
        if checkpoint_hash:
            await self.store.add_checkpoint(project.id, task.id, checkpoint_hash,
                                            f"before {task.task_key}")

        await self.notify(f"👨‍💻 Claude Code working on *{task.task_key}*…")
        await self._log(project, f"👨‍💻 Claude Code started {task.task_key}", task)

        # --- run Claude Code (the one paid step) ---
        prompt = build_task_prompt(project, task)
        await self.budget.record()
        self._project_calls[project.id] = used + 1
        result = await self.worker.run_task(prompt, cwd=workspace)

        # --- deterministic outcome handling ---
        changed = await repo.changed_files()
        if not result.ok:
            await self.store.update_task(
                task.id, status=TaskStatus.FAILED.value,
                result={"error": result.error, "timed_out": result.timed_out},
            )
            await self._log(project, f"❌ {task.task_key} failed: {result.error}", task)
            await self.notify(
                f"❌ *{task.task_key}* failed.\n`{(result.error or 'unknown')[:200]}`\n\n"
                f"(Automatic debugging arrives in Phase 6.)"
            )
            return False

        # Advisory report from Claude (never the source of truth for completion —
        # that's the Phase 7 quality gate. Used here for logging/summaries only).
        report = result.report or parse_report(result.text)

        commit_hash = await repo.commit_all(f"{task.task_key}: {task.goal[:60]}")
        if commit_hash:
            await self.store.add_checkpoint(project.id, task.id, commit_hash,
                                            f"done {task.task_key}")

        await self.store.update_task(
            task.id, status=TaskStatus.COMPLETED.value,
            result={"summary": report.summary or result.text[:800],
                    "changed_files": changed,
                    "claude_build": report.build, "claude_tests": report.tests,
                    "commit": commit_hash, "duration_s": round(result.duration_s, 1)},
        )
        await self._log(
            project,
            f"✅ {task.task_key} completed — {len(changed)} file(s), "
            f"build={report.build}, tests={report.tests}, "
            f"commit {commit_hash[:7] if commit_hash else 'n/a'}",
            task,
        )
        summary = report.summary or result.text[:300]
        await self.notify(
            f"✅ *{task.task_key}* completed.\n"
            f"Files changed: {len(changed)}  ·  build: {report.build}  ·  tests: {report.tests}\n"
            f"Commit: `{(commit_hash or 'n/a')[:7]}`\n\n"
            f"_{summary[:300]}_"
        )
        return True

    # ------------------------------------------------------------------
    async def _unique_slug(self, base: str) -> str:
        slug, i = base, 2
        while await self.store.get_project_by_slug(slug) is not None:
            slug = f"{base}-{i}"
            i += 1
        return slug

    async def _log(self, project: Project, message: str, task: Task | None = None) -> None:
        log.info("%s | %s", project.slug, message)
        await self.store.log(message, project_id=project.id,
                             task_id=(task.id if task else None),
                             category="orchestrator")
