"""Orchestrator — THE COMMANDER.

Deterministic Python decides what happens and when. It plans the work, hands ONE
focused task at a time to Claude Code, then runs the deterministic quality gate
(build/test/lint/typecheck/browser) as the real authority on completion. On
failure it self-heals: collect focused evidence, ask Claude for a targeted fix,
re-run the gate — bounded by MAX_RETRIES — else roll back and ask a human.

Loop per task:
    checkpoint → Claude(code) → gate → PASS: commit, next
                                    → FAIL: evidence → Claude(fix) → gate … (≤ N)
                                            → still failing: rollback + human review
"""
from __future__ import annotations

from pathlib import Path
from typing import Awaitable, Callable

from app.ai.glue import GlueAI
from app.claude.budget import ClaudeBudget
from app.claude.context import build_context_block
from app.claude.prompts import build_fix_prompt, build_task_prompt
from app.claude.report import parse_report
from app.claude.worker import ClaudeWorker
from app.config import Settings
from app.git.repo import GitRepo
from app.memory.project_memory import ProjectMemory
from app.orchestrator.planner import plan_tasks
from app.security.autonomy import ActionRisk, ApprovalPolicy, is_high_risk
from app.security.guard import validate_workspace
from app.state.models import Project, ProjectStatus, Task, TaskStatus
from app.state.store import StateStore
from app.testing.commands import ProjectCommandRunner
from app.testing.quality_gate import GateResult, QualityGate
from app.utils.logging import get_logger
from app.utils.text import derive_project_name, slugify

log = get_logger("orchestrator")

Notifier = Callable[[str], Awaitable[None]]
Approver = Callable[[str], Awaitable[bool]]


async def _noop(_: str) -> None:
    return None


class Orchestrator:
    def __init__(self, settings: Settings, store: StateStore, worker: ClaudeWorker,
                 notify: Notifier | None = None, glue: GlueAI | None = None,
                 gate: QualityGate | None = None, browser=None,
                 request_approval: Approver | None = None, deployer=None):
        self.settings = settings
        self.store = store
        self.worker = worker
        self.notify = notify or _noop
        self.glue = glue
        self.browser = browser
        self.request_approval = request_approval
        self.deployer = deployer
        self.runner = ProjectCommandRunner(settings.workspaces_path)
        self.gate = gate or QualityGate(self.runner)
        self.policy = ApprovalPolicy(settings.autonomy_level)
        self.budget = ClaudeBudget(
            store, max_per_day=settings.claude_max_calls_per_day,
            max_per_project=settings.claude_max_calls_per_project)
        self._project_calls: dict[int, int] = {}
        self._autofixes: dict[int, int] = {}
        self._paused: set[int] = set()

    # ================= project entry =================
    async def handle_requirement(self, requirement: str, name: str | None = None) -> Project:
        # Low-autonomy: starting a project is a "major" action -> confirm first.
        if self.policy.needs_approval(ActionRisk.NORMAL, major=True) and self.request_approval:
            ok = await self.request_approval(
                f"Start a new project for:\n{requirement[:300]}?")
            if not ok:
                await self.notify("❌ Cancelled — project not started.")
                raise RuntimeError("approval declined")

        parsed = {"name": "", "tech_stack": "", "features": []}
        if self.glue is not None:
            parsed = await self.glue.parse_requirement(requirement)
        name = name or parsed.get("name") or derive_project_name(requirement)
        tech_stack = parsed.get("tech_stack", "")
        features = parsed.get("features", [])

        slug = await self._unique_slug(slugify(name))
        workspace = self.settings.workspaces_path / slug
        validate_workspace(workspace, self.settings.workspaces_path)
        workspace.mkdir(parents=True, exist_ok=True)

        project = await self.store.create_project(
            name=name, slug=slug, requirement=requirement, workspace_path=str(workspace))
        pid = project.id
        assert pid is not None
        await self.store.set_active_project(pid)
        await self.store.update_project_status(pid, ProjectStatus.IN_PROGRESS.value)
        if tech_stack:
            await self.store.update_project_tech_stack(pid, tech_stack)
            project.tech_stack = tech_stack

        memory = ProjectMemory(tech_stack=tech_stack, features=features)
        await self.store.update_project_memory(pid, memory.to_dict())

        repo = GitRepo(workspace)
        await repo.ensure_repo()

        await self._log(project, "🚀 Project created & workspace initialized")
        await self.notify(f"🚀 *Project Started*\n\n*{name}*\n`{slug}`\n\nPlanning…")

        tasks = await plan_tasks(self.store, self.worker, project, requirement, features)
        await self.notify(f"✅ Plan created — {len(tasks)} task(s).")

        await self.run_project(project)
        return project

    # ================= run loop =================
    async def run_project(self, project: Project) -> None:
        assert project.id is not None
        while True:
            if project.id in self._paused:
                return
            task = await self.store.next_pending_task(project.id)
            if task is None:
                break
            ok = await self.execute_task(project, task)
            if not ok:
                return  # blocked / paused / needs review — stop cleanly
        await self._finalize(project)

    # ================= one task =================
    async def execute_task(self, project: Project, task: Task) -> bool:
        assert project.id is not None and task.id is not None
        workspace = Path(project.workspace_path)
        validate_workspace(workspace, self.settings.workspaces_path)
        repo = GitRepo(workspace)

        if not await self._budget_ok(project, task):
            return False
        if not await self.worker.available():
            await self.store.update_project_status(project.id, ProjectStatus.BLOCKED.value)
            await self._log(project, "❌ Claude Code CLI not available", task)
            await self.notify(
                "❌ Claude Code CLI not found.\nInstall `@anthropic-ai/claude-code`, "
                "run `claude` once to log in, then set `CLAUDE_COMMAND` in `.env`.")
            return False

        await self.store.update_task(task.id, status=TaskStatus.IN_PROGRESS.value)
        checkpoint = await repo.checkpoint(f"before {task.task_key}")
        if checkpoint:
            await self.store.add_checkpoint(project.id, task.id, checkpoint,
                                            f"before {task.task_key}")

        await self.notify(f"👨‍💻 Claude Code working on *{task.task_key}*…")
        prompt = await self._build_prompt(project, task)
        result = await self._claude(project, prompt)
        if result is None:
            return False  # budget hit mid-way
        if not result.ok:
            return await self._handle_hard_failure(project, task, repo, checkpoint,
                                                   result.error or "claude error")

        # --- deterministic quality gate = the real authority ---
        await self.notify("🧪 Running quality gate…")
        gate = await self._run_gate(workspace)

        attempts = 0
        while not gate.passed and attempts < self.settings.max_retries:
            attempts += 1
            fails = ", ".join(c.name for c in gate.failures())
            await self.notify(f"❌ Gate failed ({fails}). 🔧 Auto-fix {attempts}/"
                              f"{self.settings.max_retries}…")
            await self._log(project, f"gate fail ({fails}) attempt {attempts}", task)

            evidence = await self._collect_evidence(gate, repo)
            fix_prompt = build_fix_prompt(project, task, evidence)
            result = await self._claude(project, fix_prompt)
            if result is None:
                return False
            self._autofixes[project.id] = self._autofixes.get(project.id, 0) + 1
            gate = await self._run_gate(workspace)

        if not gate.passed:
            return await self._handle_exhausted(project, task, repo, checkpoint, gate)

        return await self._complete_task(project, task, repo, result, gate)

    # ================= completion / failure =================
    async def _complete_task(self, project: Project, task: Task, repo: GitRepo,
                             result, gate: GateResult) -> bool:
        assert project.id is not None and task.id is not None
        report = result.report or parse_report(result.text)
        changed = await repo.changed_files()
        commit = await repo.commit_all(f"{task.task_key}: {task.goal[:60]}")
        if commit:
            await self.store.add_checkpoint(project.id, task.id, commit, f"done {task.task_key}")

        # mark all acceptance criteria satisfied (gate verified build/test/browser)
        import json
        ac = json.loads(task.acceptance_json or "[]")
        for a in ac:
            a["passed"] = True
        await self.store.update_task(
            task.id, status=TaskStatus.COMPLETED.value, acceptance=ac,
            result={"summary": report.summary or result.text[:800],
                    "changed_files": changed, "gate": gate.summary(),
                    "commit": commit})

        await self._update_memory(project, task, gate)
        await self._log(project, f"✅ {task.task_key} completed — {len(changed)} file(s), "
                        f"commit {commit[:7] if commit else 'n/a'}", task)
        await self.notify(f"✅ *{task.task_key}* completed.\n"
                          f"{gate.summary()}\nCommit: `{(commit or 'n/a')[:7]}`")

        if is_high_risk(task.goal):
            await self._risk_review(project, repo)
        return True

    async def _handle_hard_failure(self, project, task, repo, checkpoint, error) -> bool:
        await self.store.update_task(task.id, status=TaskStatus.FAILED.value,
                                     result={"error": error})
        await self._log(project, f"❌ {task.task_key} hard-failed: {error}", task)
        await self.notify(f"❌ *{task.task_key}* failed: `{error[:180]}`")
        if checkpoint:
            await repo.rollback(checkpoint)
        await self.store.update_project_status(project.id, ProjectStatus.BLOCKED.value)
        return False

    async def _handle_exhausted(self, project, task, repo, checkpoint, gate: GateResult) -> bool:
        await self.store.update_task(task.id, status=TaskStatus.NEEDS_REVIEW.value,
                                     retry_count=self.settings.max_retries,
                                     result={"gate": gate.summary()})
        if checkpoint:
            await repo.rollback(checkpoint)  # restore last stable state
        await self.store.update_project_status(project.id, ProjectStatus.BLOCKED.value)
        await self._log(project, f"⚠️ {task.task_key} needs human review", task)
        fails = ", ".join(c.name for c in gate.failures())
        await self.notify(
            f"⚠️ *HUMAN REVIEW REQUIRED*\n\nTask: *{task.task_key}*\n"
            f"Failed after {self.settings.max_retries} auto-fix attempts.\n"
            f"Failing: {fails}\n\n```\n{gate.evidence(800)}\n```\n"
            f"Rolled back to the last stable checkpoint. Fix manually or /retry.")
        return False

    # ================= finalize =================
    async def _finalize(self, project: Project) -> None:
        assert project.id is not None
        await self.notify("🏁 All tasks done — running final QA…")
        gate = await self._run_gate(Path(project.workspace_path))
        tasks = await self.store.list_tasks(project.id)
        done = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED.value)
        import json
        ac_total = ac_pass = 0
        for t in tasks:
            for a in json.loads(t.acceptance_json or "[]"):
                ac_total += 1
                ac_pass += 1 if a.get("passed") else 0
        status = ProjectStatus.COMPLETED if gate.passed else ProjectStatus.BLOCKED
        await self.store.update_project_status(project.id, status.value)

        icon = "🎉" if gate.passed else "⚠️"
        await self.notify(
            f"{icon} *PROJECT {'COMPLETED' if gate.passed else 'NEEDS ATTENTION'}*\n\n"
            f"*{project.name}*\n\n"
            f"Tasks: {done}/{len(tasks)}\n"
            f"Acceptance: {ac_pass}/{ac_total}\n"
            f"{gate.summary()}\n"
            f"Auto-fixes: {self._autofixes.get(project.id, 0)}\n"
            f"Status: {'READY' if gate.passed else 'REVIEW'}")
        await self._log(project, f"finalized: {status.value}")

        # Auto-deploy to a free host (only when it passed and deploy is configured).
        if gate.passed and self.deployer is not None:
            await self.deploy_project(project)

    # ================= helpers =================
    async def _budget_ok(self, project: Project, task: Task) -> bool:
        used = self._project_calls.get(project.id, 0)
        decision = await self.budget.check(used)
        if decision.allowed:
            return True
        self._paused.add(project.id)
        await self.store.update_project_status(project.id, ProjectStatus.PAUSED.value)
        await self._log(project, f"⏸ Paused — {decision.reason}", task)
        await self.notify(f"⏸ *Paused* to protect your budget.\n{decision.reason}\n"
                          f"No paid usage happens automatically. /resume to continue.")
        return False

    async def _claude(self, project: Project, prompt: str):
        """Run one Claude call after a budget re-check; returns result or None."""
        if not await self._budget_ok_light(project):
            return None
        await self.budget.record()
        self._project_calls[project.id] = self._project_calls.get(project.id, 0) + 1
        return await self.worker.run_task(prompt, cwd=project.workspace_path)

    async def _budget_ok_light(self, project: Project) -> bool:
        used = self._project_calls.get(project.id, 0)
        decision = await self.budget.check(used)
        if not decision.allowed:
            self._paused.add(project.id)
            await self.store.update_project_status(project.id, ProjectStatus.PAUSED.value)
            await self.notify(f"⏸ Paused mid-task — {decision.reason}. /resume to continue.")
            return False
        return True

    async def _run_gate(self, workspace: Path) -> GateResult:
        return await self.gate.run(workspace, browser=self.browser)

    async def _collect_evidence(self, gate: GateResult, repo: GitRepo) -> str:
        diff = await repo.short_diff()
        triage = ""
        if self.glue is not None:
            t = await self.glue.triage_error(gate.evidence(2000))
            triage = f"Likely category: {t['category']}. {t['hint']}\n"
        return f"{triage}\n{gate.evidence(2500)}\n\nRecent diff:\n{diff}"

    async def _build_prompt(self, project: Project, task: Task) -> str:
        import json
        base = build_task_prompt(project, task)
        memory = ProjectMemory.from_json(
            (await self.store.get_project(project.id)).memory_json)
        files = json.loads(task.relevant_files_json or "[]")
        ctx = build_context_block(memory, project.workspace_path, files)
        return f"{base}\n\n{ctx}"

    async def _update_memory(self, project: Project, task: Task, gate: GateResult) -> None:
        fresh = await self.store.get_project(project.id)
        memory = ProjectMemory.from_json(fresh.memory_json)
        memory.complete_feature(task.goal[:60])
        memory.note_change(f"{task.task_key}: {task.goal[:50]}")
        memory.current_task = ""
        test_check = next((c for c in gate.checks if c.name == "test"), None)
        memory.test_status = test_check.status if test_check else "unknown"
        await self.store.update_project_memory(project.id, memory.to_dict())

    async def _risk_review(self, project: Project, repo: GitRepo) -> None:
        if self.glue is None:
            return
        diff = await repo.short_diff()
        resp = await self.glue.router.complete(
            "This change touches a high-risk area (auth/payments/security/db). "
            "List up to 3 concrete risks or say 'none'. Be terse.\n\n" + diff,
            kind="simple", max_tokens=200)
        if resp.ok and "none" not in resp.text.lower()[:20]:
            await self.notify(f"🔎 *Risk review* (high-risk change):\n_{resp.text[:400]}_")

    # ================= owner-facing helpers (Telegram) =================
    async def answer_question(self, question: str) -> str:
        """Answer an owner question about the active project (Phase 10 Q&A).

        Uses only the compact project memory + recent logs — never the codebase.
        Free-model powered; deterministic state dump if no provider configured."""
        project = await self.store.get_active_project()
        if not project:
            return "No active project yet. Send a requirement to start one."
        memory = ProjectMemory.from_json(project.memory_json)
        logs = await self.store.recent_logs(project.id, limit=10)
        tasks = await self.store.list_tasks(project.id)
        done = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED.value)
        context = (f"Project: {project.name} ({project.status})\n"
                   f"Tasks: {done}/{len(tasks)} done\n{memory.compact_text()}\n"
                   f"Recent activity:\n" + "\n".join(logs))
        if self.glue is None:
            return context
        resp = await self.glue.router.complete(
            "Answer the owner's question about their software project concisely, "
            "using only this state.\n\n" + context + f"\n\nQ: {question}",
            kind="normal", max_tokens=300)
        return resp.text if resp.ok else context

    async def run_gate_now(self, project: Project) -> GateResult:
        """Run the quality gate on demand (/test)."""
        return await self._run_gate(Path(project.workspace_path))

    async def deploy_project(self, project: Project, prod: bool | None = None):
        """Deploy to a free host and report the live URL (Phase 10 auto-deploy).

        Publishing is public + outward-facing, so it respects the autonomy policy:
        preview deploys are RISKY (auto only at high autonomy), production deploys
        ALWAYS require explicit approval."""
        if self.deployer is None:
            await self.notify("ℹ️ Deployment is not configured. "
                              "Set `DEPLOY_PROVIDER` + a token in `.env`.")
            return None
        prod = self.settings.deploy_prod if prod is None else prod

        needs = prod or self.policy.needs_approval(ActionRisk.RISKY)
        target = "production" if prod else "preview"
        if needs:
            if not self.request_approval:
                await self.notify(f"⏭ Deploy skipped — {target} needs approval but "
                                  f"no approval channel is available.")
                return None
            ok = await self.request_approval(
                f"Deploy *{project.name}* to {self.deployer.name} ({target})?")
            if not ok:
                await self.notify("❌ Deploy cancelled.")
                return None

        await self.notify(f"🚀 Deploying *{project.name}* to {self.deployer.name} "
                          f"({target})…")
        result = await self.deployer.deploy(project.workspace_path, prod=prod)
        if result.ok and result.url:
            fresh = await self.store.get_project(project.id)
            memory = ProjectMemory.from_json(fresh.memory_json)
            memory.deploy_url = result.url
            await self.store.update_project_memory(project.id, memory.to_dict())
            await self._log(project, f"🌐 deployed: {result.url}")
            await self.notify(f"🌐 *Live URL*\n{result.url}")
        else:
            await self._log(project, f"deploy failed: {result.error}")
            await self.notify(f"⚠️ Deploy failed: `{result.error[:200]}`")
        return result

    async def rollback_project(self, project: Project) -> bool:
        cp = await self.store.last_checkpoint(project.id)
        if not cp:
            return False
        repo = GitRepo(Path(project.workspace_path))
        return await repo.rollback(cp.commit_hash)

    def clear_pause(self, project_id: int) -> None:
        self._paused.discard(project_id)

    async def _unique_slug(self, base: str) -> str:
        slug, i = base, 2
        while await self.store.get_project_by_slug(slug) is not None:
            slug = f"{base}-{i}"
            i += 1
        return slug

    async def _log(self, project: Project, message: str, task: Task | None = None) -> None:
        log.info("%s | %s", project.slug, message)
        await self.store.log(message, project_id=project.id,
                             task_id=(task.id if task else None), category="orchestrator")
