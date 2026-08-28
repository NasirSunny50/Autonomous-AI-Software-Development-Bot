from app.ai.providers.base import AIProvider
from app.ai.router import AIRouter, _COOLDOWN_S


class FakeProvider(AIProvider):
    def __init__(self, name, behavior="ok"):
        super().__init__("key" if behavior != "nokey" else "", "m")
        self.name = name
        self.behavior = behavior
        self.calls = 0

    async def complete(self, prompt, system=None, max_tokens=800, temperature=0.2):
        self.calls += 1
        if self.behavior == "ok":
            return self._ok(f"{self.name}-ok")
        if self.behavior == "rate":
            return self._fail("429", rate_limited=True, status=429)
        return self._fail("boom")


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _router(behaviors, clock=None):
    providers = {n: FakeProvider(n, b) for n, b in behaviors.items()}
    return AIRouter(providers, clock=clock or Clock()), providers


async def test_first_provider_used():
    router, provs = _router({"gemini": "ok", "groq": "ok", "openrouter": "ok"})
    r = await router.complete("hi", kind="normal")  # normal -> gemini first
    assert r.ok and r.provider == "gemini"
    assert provs["groq"].calls == 0


async def test_fallback_on_rate_limit_and_cooldown():
    clock = Clock()
    router, provs = _router({"gemini": "rate", "groq": "ok", "openrouter": "ok"}, clock)
    r = await router.complete("hi", kind="normal")
    assert r.ok and r.provider == "groq"          # fell back past gemini
    assert provs["gemini"].calls == 1

    # gemini is now cooling down -> skipped without a call
    r2 = await router.complete("hi", kind="normal")
    assert r2.provider == "groq"
    assert provs["gemini"].calls == 1              # not retried while cooling

    # after cooldown elapses it becomes eligible again
    clock.t += _COOLDOWN_S + 1
    provs["gemini"].behavior = "ok"
    r3 = await router.complete("hi", kind="normal")
    assert r3.provider == "gemini"


async def test_all_unconfigured_returns_not_ok():
    router, _ = _router({"gemini": "nokey", "groq": "nokey", "openrouter": "nokey"})
    r = await router.complete("hi")
    assert not r.ok and "no free AI provider" in (r.error or "")


async def test_all_fail_returns_last_error():
    router, _ = _router({"gemini": "err", "groq": "err", "openrouter": "err"})
    r = await router.complete("hi")
    assert not r.ok and r.error == "boom"


async def test_simple_kind_prefers_groq():
    router, _ = _router({"gemini": "ok", "groq": "ok", "openrouter": "ok"})
    r = await router.complete("hi", kind="simple")
    assert r.provider == "groq"
