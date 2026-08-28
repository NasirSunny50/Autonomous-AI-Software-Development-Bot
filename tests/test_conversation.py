from app.ai.conversation import ConversationEngine
from app.ai.providers.base import AIResponse


class StubRouter:
    def __init__(self, resp=None, configured=True):
        self._resp = resp
        self._cfg = configured

    def configured(self):
        return ["x"] if self._cfg else []

    async def complete(self, prompt, **kw):
        return self._resp


def _resp(text, ok=True):
    return AIResponse(ok=ok, text=text, provider="s", model="m",
                      error=None if ok else "down")


# ---- AI path ----
async def test_ai_intent_parsed():
    r = _resp('{"intent":"new_project","project_name":"Shop","reply":"Cool!"}')
    out = await ConversationEngine(StubRouter(r)).interpret("banao ekta shop", {})
    assert out["intent"] == "new_project"
    assert out["project_name"] == "Shop"
    assert out["reply"] == "Cool!"


async def test_ai_failure_falls_back_to_keywords():
    out = await ConversationEngine(StubRouter(_resp("", ok=False))).interpret(
        "build me an app", {})
    assert out["intent"] == "new_project"          # keyword fallback


async def test_ai_bad_intent_falls_back():
    r = _resp('{"intent":"nonsense","reply":"x"}')
    out = await ConversationEngine(StubRouter(r)).interpret("ki obostha?",
                                                            {"active_project": "X"})
    assert out["intent"] == "status"


# ---- deterministic fallback (router=None) ----
async def test_fallback_new_project():
    e = ConversationEngine(None)
    assert (await e.interpret("ekta website banao", {}))["intent"] == "new_project"


async def test_fallback_status():
    e = ConversationEngine(None)
    assert (await e.interpret("kdur holo?", {}))["intent"] == "status"
    assert (await e.interpret("ki obostha", {}))["intent"] == "status"


async def test_fallback_confirm_and_cancel():
    e = ConversationEngine(None)
    assert (await e.interpret("haan start koro", {}))["intent"] == "confirm"
    assert (await e.interpret("na thak", {}))["intent"] == "cancel"


async def test_fallback_switch_not_confuse_with_confirm():
    e = ConversationEngine(None)
    # "kaj koro" must NOT be read as confirm; "onno project" -> switch
    assert (await e.interpret("onno project e kaj koro", {}))["intent"] == "switch_project"


async def test_fallback_question_when_active():
    e = ConversationEngine(None)
    out = await e.interpret("login page ta kaj kore?", {"active_project": "Shop"})
    assert out["intent"] == "question"


async def test_fallback_chitchat_when_idle():
    e = ConversationEngine(None)
    assert (await e.interpret("hello there", {}))["intent"] == "chitchat"
