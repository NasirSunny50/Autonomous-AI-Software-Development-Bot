from app.claude.worker import ClaudeWorker
from app.utils.process import CommandResult
from app.utils.text import derive_project_name, slugify, truncate


def test_slugify():
    assert slugify("My Cool App!!") == "my-cool-app"
    assert slugify("   ") == "project"
    assert slugify("A/B  C") == "a-b-c"


def test_derive_name():
    assert derive_project_name("Build me a modern e-commerce website") \
        .lower().startswith("modern e-commerce")
    assert derive_project_name("") == "New Project"


def test_truncate():
    assert truncate("short") == "short"
    long = "x" * 5000
    out = truncate(long, limit=1000)
    assert len(out) <= 1050 and "trimmed" in out


def _cr(stdout: str, exit_code: int = 0) -> CommandResult:
    return CommandResult(command="claude", exit_code=exit_code, stdout=stdout,
                         stderr="", duration_s=0.1)


def test_claude_parse_json_success():
    w = ClaudeWorker(output_format="json")
    r = w._parse(_cr('{"type":"result","is_error":false,"result":"done","session_id":"s1"}'))
    assert r.ok and r.text == "done" and r.session_id == "s1"


def test_claude_parse_json_error():
    w = ClaudeWorker(output_format="json")
    r = w._parse(_cr('{"is_error":true,"result":"boom"}', exit_code=0))
    assert not r.ok and r.text == "boom"


def test_claude_parse_plain_fallback():
    w = ClaudeWorker(output_format="json")
    r = w._parse(_cr("just text, not json"))
    assert r.text == "just text, not json"


def test_claude_parse_timeout():
    w = ClaudeWorker(output_format="json")
    cr = CommandResult(command="claude", exit_code=None, stdout="", stderr="",
                       duration_s=1.0, timed_out=True)
    r = w._parse(cr)
    assert not r.ok and r.timed_out
