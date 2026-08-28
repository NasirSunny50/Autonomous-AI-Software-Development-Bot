from app.config import Settings
from app.deploy.base import DeployResult, extract_url, find_build_dir
from app.deploy.factory import build_deployer
from app.deploy.surge import SurgeDeployer
from app.deploy.vercel import VercelDeployer
from app.orchestrator.core import Orchestrator
from app.security.autonomy import ActionRisk
from app.state.store import StateStore
from app.testing.commands import ProjectCommandRunner


# ---------------- url + build dir helpers ----------------
def test_extract_url_last_wins():
    text = "Inspect: https://vercel.com/x\nPreview: https://my-app-abc.vercel.app"
    assert extract_url(text) == "https://my-app-abc.vercel.app"


def test_extract_url_none():
    assert extract_url("no urls here") == ""


def test_find_build_dir(tmp_path):
    assert find_build_dir(tmp_path) is None
    (tmp_path / "dist").mkdir()
    assert find_build_dir(tmp_path) == "dist"


# ---------------- availability + factory ----------------
def test_vercel_availability():
    s = Settings()
    runner = ProjectCommandRunner(".")
    assert not VercelDeployer(s, runner).available()
    s.vercel_token = "tok"
    assert VercelDeployer(s, runner).available()


def test_factory_disabled_by_default():
    assert build_deployer(Settings(), ProjectCommandRunner(".")) is None


def test_factory_explicit_provider():
    s = Settings()
    s.deploy_provider = "surge"
    s.surge_token = "tok"
    dep = build_deployer(s, ProjectCommandRunner("."))
    assert isinstance(dep, SurgeDeployer)


def test_factory_auto_picks_first_configured():
    s = Settings()
    s.deploy_provider = "auto"
    s.surge_token = "tok"          # only surge configured
    dep = build_deployer(s, ProjectCommandRunner("."))
    assert dep is not None and dep.name == "surge"


def test_factory_auto_none_when_no_token():
    s = Settings()
    s.deploy_provider = "auto"
    assert build_deployer(s, ProjectCommandRunner(".")) is None


# ---------------- result mapping ----------------
class _Outcome:
    def __init__(self, ok, stdout="", blocked=False):
        self.ok = ok
        self.blocked = blocked
        self.skipped = False
        self.reason = ""
        self.result = object() if stdout else None
        self.stdout = stdout
        self.stderr = ""


def test_result_from_success():
    dep = VercelDeployer(Settings(), ProjectCommandRunner("."))
    r = dep._result_from(_Outcome(True, "Preview: https://app-x.vercel.app"))
    assert r.ok and r.url == "https://app-x.vercel.app"


def test_result_from_failure():
    dep = VercelDeployer(Settings(), ProjectCommandRunner("."))
    r = dep._result_from(_Outcome(False, "boom"))
    assert not r.ok


# ---------------- approval gating in orchestrator ----------------
class StubDeployer:
    name = "stub"

    def __init__(self):
        self.called = False

    def available(self):
        return True

    async def deploy(self, project_path, *, prod=False):
        self.called = True
        return DeployResult(True, "https://stub.example.com", "stub")


async def _orch(tmp_path, autonomy="high", approve=None):
    s = Settings()
    s.workspaces_dir = str(tmp_path / "ws")
    s.state_db_path = str(tmp_path / "s.db")
    s.autonomy_level = autonomy
    s.ensure_dirs()
    store = StateStore(s.db_path)
    await store.init()
    (tmp_path / "ws" / "app").mkdir(parents=True)
    project = await store.create_project("App", "app", "req", str(tmp_path / "ws" / "app"))
    dep = StubDeployer()

    msgs = []

    async def notify(m):
        msgs.append(m)

    approver = None
    if approve is not None:
        async def approver(_):  # noqa: E306
            return approve

    from app.claude.worker import ClaudeWorker
    orch = Orchestrator(s, store, ClaudeWorker("fake"), notify=notify,
                        deployer=dep, request_approval=approver)
    return orch, project, dep, msgs


async def test_deploy_high_autonomy_preview_no_approval(tmp_path):
    orch, project, dep, msgs = await _orch(tmp_path, autonomy="high")
    res = await orch.deploy_project(project, prod=False)
    assert dep.called and res.ok
    assert any("Live URL" in m for m in msgs)


async def test_deploy_prod_requires_approval_declined(tmp_path):
    orch, project, dep, msgs = await _orch(tmp_path, autonomy="high", approve=False)
    res = await orch.deploy_project(project, prod=True)
    assert not dep.called and res is None
    assert any("cancelled" in m.lower() for m in msgs)


async def test_deploy_prod_requires_approval_granted(tmp_path):
    orch, project, dep, msgs = await _orch(tmp_path, autonomy="high", approve=True)
    res = await orch.deploy_project(project, prod=True)
    assert dep.called and res.ok


async def test_deploy_medium_autonomy_preview_needs_approval(tmp_path):
    # medium autonomy -> preview (RISKY) needs approval; none available -> skipped
    orch, project, dep, msgs = await _orch(tmp_path, autonomy="medium", approve=None)
    res = await orch.deploy_project(project, prod=False)
    assert not dep.called and res is None
    assert orch.policy.needs_approval(ActionRisk.RISKY)
