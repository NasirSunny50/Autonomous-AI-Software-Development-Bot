import json

from app.claude.context import read_relevant_files
from app.memory.project_memory import ProjectMemory
from app.security.autonomy import ActionRisk, ApprovalPolicy, is_high_risk
from app.security.secrets import find_secrets, redact


# ---------------- project memory ----------------
def test_memory_roundtrip():
    m = ProjectMemory(tech_stack="nextjs", features=["a", "b"])
    m.note_change("added x")
    m.complete_feature("a")
    m2 = ProjectMemory.from_json(json.dumps(m.to_dict()))
    assert m2.tech_stack == "nextjs"
    assert "a" in m2.completed_features
    assert "added x" in m2.recent_changes
    assert "nextjs" in m.compact_text()


def test_memory_from_bad_json():
    assert ProjectMemory.from_json("not json").tech_stack == ""
    assert ProjectMemory.from_json(None).features == []


def test_memory_note_change_bounded():
    m = ProjectMemory()
    for i in range(20):
        m.note_change(f"c{i}")
    assert len(m.recent_changes) == 8 and m.recent_changes[-1] == "c19"


# ---------------- context minimizer ----------------
def test_context_reads_only_listed(tmp_path):
    (tmp_path / "a.py").write_text("print('a')", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("junk", encoding="utf-8")
    out = read_relevant_files(tmp_path, ["a.py", "node_modules/x.js", "missing.py"])
    assert "a.py" in out and "junk" not in out and "missing" not in out


def test_context_budget(tmp_path):
    (tmp_path / "big.py").write_text("z" * 5000, encoding="utf-8")
    out = read_relevant_files(tmp_path, ["big.py"], per_file=100, max_total=500)
    assert len(out) <= 600


# ---------------- autonomy policy ----------------
def test_policy_high():
    pol = ApprovalPolicy("high")
    assert not pol.needs_approval(ActionRisk.RISKY)
    assert not pol.needs_approval(ActionRisk.NORMAL, major=True)
    assert pol.needs_approval(ActionRisk.DESTRUCTIVE)   # always


def test_policy_medium():
    pol = ApprovalPolicy("medium")
    assert pol.needs_approval(ActionRisk.RISKY)
    assert not pol.needs_approval(ActionRisk.NORMAL)
    assert pol.needs_approval(ActionRisk.DESTRUCTIVE)


def test_policy_low():
    pol = ApprovalPolicy("low")
    assert pol.needs_approval(ActionRisk.NORMAL, major=True)
    assert not pol.needs_approval(ActionRisk.NORMAL, major=False)


def test_risk_classification():
    assert is_high_risk("implement payment checkout with stripe")
    assert is_high_risk("add user authentication and login")
    assert not is_high_risk("change the button color to blue")


# ---------------- secret hygiene ----------------
def test_find_secrets():
    assert "openai-key" in find_secrets("here is sk-abcdefghijklmnopqrstuvwx1234")
    assert "github-token" in find_secrets("ghp_abcdefghijklmnopqrstuvwxyz1234")
    assert find_secrets("nothing sensitive here") == []


def test_redact():
    masked = redact("my token: sk-abcdefghijklmnopqrstuvwx1234 ok")
    assert "sk-abcdefghijkl" not in masked and "redacted" in masked
