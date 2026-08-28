from app.claude.report import parse_report
from app.claude.worker import MAX_PROMPT_CHARS, ClaudeOutcome, ClaudeWorker
from app.utils.process import CommandResult


# ---------------- report parsing ----------------
def test_parse_full_report():
    text = """Some chatter.
CHANGED_FILES: src/app.py, src/auth.py
BUILD: pass
TESTS: fail
SUMMARY: Added login; one test still failing.
"""
    r = parse_report(text)
    assert r.changed_files == ["src/app.py", "src/auth.py"]
    assert r.build == "pass" and r.tests == "fail"
    assert "login" in r.summary.lower()
    assert not r.claims_success        # tests failed


def test_parse_missing_fields_defaults_unknown():
    r = parse_report("no structure here")
    assert r.changed_files == [] and r.build == "unknown" and r.tests == "unknown"
    assert r.claims_success            # nothing declared failed


def test_parse_none_changed_files():
    r = parse_report("CHANGED_FILES: none\nBUILD: not-run")
    assert r.changed_files == [] and r.build == "not-run"


def test_parse_status_with_extra_text():
    r = parse_report("BUILD: pass (compiled ok)\nTESTS: PASS 42/42")
    assert r.build == "pass" and r.tests == "pass"


def test_parse_empty():
    r = parse_report("")
    assert r.changed_files == [] and r.summary == ""


# ---------------- worker parse + outcome ----------------
def _cr(stdout="", exit_code=0, timed_out=False):
    return CommandResult(command="claude", exit_code=exit_code, stdout=stdout,
                         stderr="", duration_s=0.2, timed_out=timed_out)


def test_worker_attaches_report():
    w = ClaudeWorker(output_format="json")
    payload = '{"is_error":false,"result":"done\\nCHANGED_FILES: a.py\\nBUILD: pass"}'
    r = w._parse(_cr(stdout=payload))
    assert r.ok and r.outcome == ClaudeOutcome.SUCCESS
    assert r.report is not None and r.report.changed_files == ["a.py"]
    assert r.report.build == "pass"


def test_worker_outcome_failed():
    w = ClaudeWorker(output_format="json")
    r = w._parse(_cr(stdout='{"is_error":true,"result":"x"}'))
    assert not r.ok and r.outcome == ClaudeOutcome.FAILED


def test_worker_outcome_timeout():
    w = ClaudeWorker(output_format="json")
    r = w._parse(_cr(timed_out=True, exit_code=None))
    assert r.outcome == ClaudeOutcome.TIMEOUT


# ---------------- run guards (no CLI needed) ----------------
async def test_run_rejects_missing_cwd(tmp_path):
    w = ClaudeWorker(command="claude")
    r = await w.run_task("hi", cwd=tmp_path / "does-not-exist")
    assert not r.ok and "does not exist" in (r.error or "")


async def test_run_rejects_oversized_prompt(tmp_path):
    w = ClaudeWorker(command="claude")
    r = await w.run_task("x" * (MAX_PROMPT_CHARS + 1), cwd=tmp_path)
    assert not r.ok and "token guard" in (r.error or "")
