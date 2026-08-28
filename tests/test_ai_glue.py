from app.ai.glue import GlueAI, _extract_json, _keyword_category
from app.ai.providers.base import AIResponse
from app.ai.token_manager import CallBudget, ResponseCache


class StubRouter:
    """Minimal stand-in for AIRouter — returns a preset response."""
    def __init__(self, resp: AIResponse):
        self.resp = resp
        self.calls = 0

    async def complete(self, prompt, **kw):
        self.calls += 1
        return self.resp


def _ok(text):
    return AIResponse(ok=True, text=text, provider="stub", model="m")


def _fail():
    return AIResponse(ok=False, text="", provider="none", model="", error="down")


# ---- json extraction ----
def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    assert _extract_json('```json\n{"a": 2}\n```')["a"] == 2


def test_extract_json_garbage():
    assert _extract_json("no json here") is None


# ---- parse_requirement ----
async def test_parse_requirement_from_model():
    router = StubRouter(_ok('{"name":"Shop","tech_stack":"nextjs","features":["auth","cart"]}'))
    g = GlueAI(router)
    out = await g.parse_requirement("build me a shop")
    assert out["name"] == "Shop"
    assert out["tech_stack"] == "nextjs"
    assert out["features"] == ["auth", "cart"]


async def test_parse_requirement_fallback():
    g = GlueAI(StubRouter(_fail()))
    out = await g.parse_requirement("Build a modern portfolio site")
    assert "portfolio" in out["name"].lower()
    assert out["features"] == []


# ---- triage_error ----
async def test_triage_from_model():
    router = StubRouter(_ok('{"category":"build","hint":"missing dep"}'))
    out = await GlueAI(router).triage_error("some error")
    assert out["category"] == "build" and out["hint"] == "missing dep"


async def test_triage_keyword_fallback():
    out = await GlueAI(StubRouter(_fail())).triage_error("E   assert 1 == 2  pytest failed")
    assert out["category"] == "test"


def test_keyword_category():
    assert _keyword_category("Module not found: x") == "dependency"
    assert _keyword_category("nothing familiar") == "unknown"


# ---- summarize_logs ----
async def test_summarize_short_passthrough():
    g = GlueAI(StubRouter(_ok("ignored")))
    assert await g.summarize_logs("tiny") == "tiny"


async def test_summarize_fallback_truncates():
    g = GlueAI(StubRouter(_fail()))
    out = await g.summarize_logs("x" * 5000, max_chars=300)
    assert "trimmed" in out


# ---- budget & cache ----
def test_call_budget():
    b = CallBudget(limits={"debug": 2})
    assert b.allow("debug")
    b.record("debug")
    assert b.remaining("debug") == 1
    b.record("debug")
    assert not b.allow("debug")


def test_response_cache():
    c = ResponseCache()
    assert c.get("p", "k") is None
    c.set("p", "k", "v")
    assert c.get("p", "k") == "v"
    assert c.get("p", "other") is None
