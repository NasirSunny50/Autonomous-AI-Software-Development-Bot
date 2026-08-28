from pathlib import Path

from app.ai.glue import GlueAI
from app.ai.providers.base import AIResponse
from app.claude.worker import ClaudeResult, ClaudeWorker
from app.config import Settings
from app.orchestrator.core import Orchestrator
from app.state.store import StateStore


class FakeWorker(ClaudeWorker):
    """Pretends to be Claude Code: writes a file (so git sees a change) and
    reports success — no real CLI involved."""
    def __init__(self):
        super().__init__(command="fake")

    async def available(self):
        return True

    async def run_task(self, prompt, cwd):
        Path(cwd, "app.py").write_text("print('hello')\n", encoding="utf-8")
        return ClaudeResult(ok=True, text="CHANGED_FILES: app.py\nBUILD: pass",
                            raw_stdout="", raw_stderr="", exit_code=0, timed_out=False)


class StubRouter:
    async def complete(self, prompt, **kw):
        return AIResponse(ok=True, provider="stub", model="m",
                          text='{"name":"Todo App","tech_stack":"fastapi","features":["crud"]}')


def _settings(tmp_path) -> Settings:
    s = Settings()
    s.workspaces_dir = str(tmp_path / "ws")
    s.state_db_path = str(tmp_path / "state.db")
    s.claude_max_calls_per_day = 100
    s.claude_max_calls_per_project = 40
    return s


async def test_full_slice_creates_builds_commits(tmp_path):
    s = _settings(tmp_path)
    s.ensure_dirs()
    store = StateStore(s.db_path)
    await store.init()

    messages: list[str] = []

    async def notify(msg: str):
        messages.append(msg)

    orch = Orchestrator(s, store, FakeWorker(), notify=notify, glue=GlueAI(StubRouter()))
    project = await orch.handle_requirement("build me a todo app")

    # glue-derived stack was applied
    assert project.tech_stack == "fastapi"
    assert project.slug == "todo-app"

    # workspace + generated file + git commit exist
    ws = Path(project.workspace_path)
    assert (ws / "app.py").exists()
    assert (ws / ".git").exists()

    # task recorded as completed
    tasks = await store.list_tasks(project.id)
    assert len(tasks) == 1 and tasks[0].status == "completed"

    # exactly one Claude call was billed to the budget
    from datetime import date
    assert await store.claude_calls_today(date.today().isoformat()) == 1

    # owner got start + completion updates, not internal noise
    joined = "\n".join(messages)
    assert "Project Started" in joined and "completed" in joined


async def test_target_dir_builds_outside_workspaces(tmp_path):
    s = _settings(tmp_path)
    s.ensure_dirs()
    store = StateStore(s.db_path)
    await store.init()

    external = tmp_path / "elsewhere" / "Rooftop Cricket"   # NOT under workspaces/

    async def notify(_m):
        pass

    orch = Orchestrator(s, store, FakeWorker(), notify=notify, glue=None)
    project = await orch.handle_requirement("build a scoring app",
                                            target_dir=str(external))
    # built in the exact designated folder, not workspaces/<slug>
    assert Path(project.workspace_path) == external.resolve()
    assert (external / "app.py").exists()
    assert (external / ".git").exists()
    tasks = await store.list_tasks(project.id)
    assert len(tasks) == 1 and tasks[0].status == "completed"


async def test_budget_cap_pauses_instead_of_running(tmp_path):
    from datetime import date

    s = _settings(tmp_path)
    s.claude_max_calls_per_day = 1       # only one Claude call allowed today
    s.ensure_dirs()
    store = StateStore(s.db_path)
    await store.init()
    await store.incr_claude_usage(date.today().isoformat())  # already at the cap

    messages: list[str] = []

    async def notify(msg: str):
        messages.append(msg)

    orch = Orchestrator(s, store, FakeWorker(), notify=notify, glue=None)
    # daily cap already reached -> the first task must be blocked & the project paused
    project = await orch.handle_requirement("build something")
    refreshed = await store.get_project(project.id)
    assert refreshed.status == "paused"
    assert any("Paused" in m for m in messages)
