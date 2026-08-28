from app.browser.playwright_qa import URL_RE, BrowserQA, pick_dev_script
from app.claude.worker import ClaudeResult, ClaudeWorker
from app.orchestrator.planner import plan_tasks
from app.state.store import StateStore


class PlanWorker(ClaudeWorker):
    def __init__(self, text):
        super().__init__(command="fake")
        self._text = text

    async def available(self):
        return True

    async def run_task(self, prompt, cwd):
        return ClaudeResult(ok=True, text=self._text, raw_stdout="", raw_stderr="",
                            exit_code=0, timed_out=False)


async def _project(tmp_path):
    store = StateStore(tmp_path / "s.db")
    await store.init()
    p = await store.create_project("App", "app", "req", str(tmp_path / "ws"))
    return store, p


# ---------------- planner ----------------
async def test_plan_single_for_simple(tmp_path):
    store, p = await _project(tmp_path)
    tasks = await plan_tasks(store, PlanWorker("{}"), p, "build a thing", ["one"])
    assert len(tasks) == 1 and tasks[0].task_key == "INIT-001"


async def test_plan_multi_with_ai(tmp_path):
    store, p = await _project(tmp_path)
    js = ('{"tasks":[{"key":"AUTH-001","goal":"login","acceptance":["works"]},'
          '{"key":"DASH-001","goal":"dashboard","acceptance":["renders"]}]}')
    tasks = await plan_tasks(store, PlanWorker(js), p, "req", ["auth", "dash", "more"])
    assert len(tasks) == 2
    assert tasks[0].task_key == "AUTH-001" and tasks[1].task_key == "DASH-001"


async def test_plan_multi_fallback_on_bad_json(tmp_path):
    store, p = await _project(tmp_path)
    tasks = await plan_tasks(store, PlanWorker("not json at all"), p, "req",
                             ["a", "b", "c"])
    assert len(tasks) == 1  # falls back to a single bootstrap task


# ---------------- browser helpers ----------------
def test_pick_dev_script():
    assert pick_dev_script({"dev": "x", "build": "y"}) == "dev"
    assert pick_dev_script({"start": "x"}) == "start"
    assert pick_dev_script({"build": "y"}) is None


def test_url_regex():
    assert URL_RE.search("  Local:  http://localhost:5173/").group(0) == \
        "http://localhost:5173"
    assert URL_RE.search("ready on http://127.0.0.1:3000").group(0) == \
        "http://127.0.0.1:3000"


async def test_smoke_skips_non_web(tmp_path):
    res = await BrowserQA(tmp_path).smoke_test(tmp_path, {"type": "python"})
    assert res.status == "skip"


async def test_smoke_skips_without_dev_script(tmp_path):
    info = {"type": "node", "scripts": {"build": "vite build"}, "pm": "npm"}
    res = await BrowserQA(tmp_path).smoke_test(tmp_path, info)
    assert res.status == "skip"
