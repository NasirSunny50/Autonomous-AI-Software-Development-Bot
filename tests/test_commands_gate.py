import sys

import pytest

from app.security.guard import WorkspaceViolation
from app.testing.commands import ProjectCommandRunner
from app.testing.quality_gate import QualityGate


# ---------------- ProjectCommandRunner ----------------
async def test_runner_ok(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    out = await ProjectCommandRunner(tmp_path).run(
        proj, [sys.executable, "-c", "print(1)"], name="x")
    assert out.ok and not out.blocked and not out.skipped


async def test_runner_blocks_destructive(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    out = await ProjectCommandRunner(tmp_path).run(
        proj, ["git", "push", "--force"], name="deploy")
    assert out.blocked and not out.ok and "approval" in out.reason


async def test_runner_workspace_guard(tmp_path):
    (tmp_path / "ws").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(WorkspaceViolation):
        await ProjectCommandRunner(tmp_path / "ws").run(
            outside, [sys.executable, "-c", "print(1)"], name="x")


# ---------------- QualityGate ----------------
def test_detect_node(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "package.json").write_text(
        '{"scripts": {"build": "vite build", "test": "vitest"}}', encoding="utf-8")
    info = QualityGate(ProjectCommandRunner(tmp_path)).detect(proj)
    assert info["type"] == "node" and "build" in info["scripts"]


async def test_gate_python_pass(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("x = 1\nprint(x)\n", encoding="utf-8")
    res = await QualityGate(ProjectCommandRunner(tmp_path)).run(proj)
    assert res.project_type == "python" and res.passed
    build = next(c for c in res.checks if c.name == "build")
    assert build.status == "pass"


async def test_gate_python_fail_gives_evidence(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "bad.py").write_text("def (:\n    pass\n", encoding="utf-8")  # syntax error
    res = await QualityGate(ProjectCommandRunner(tmp_path)).run(proj)
    assert not res.passed
    assert res.failures()
    assert res.evidence()  # non-empty failing output for the debugger
