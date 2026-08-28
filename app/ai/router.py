"""AIRouter — deterministic selection & fallback across FREE providers.

Routing by task kind (recommended in the design):
    simple  -> Groq first (fastest)
    normal  -> Gemini first (good general reasoning)
    complex -> Gemini, then OpenRouter free models
Each falls back to the others. If a provider hits a quota/429 it's put on a short
cooldown and skipped. If ALL free providers are unavailable, the router returns a
not-ok response — the caller must pause & notify, NEVER silently use a paid model.

This layer only ever runs cheap glue tasks (summaries, triage, NL->JSON).
"""
from __future__ import annotations

import time
from collections.abc import Callable

from app.ai.providers.base import AIProvider, AIResponse
from app.utils.logging import get_logger

log = get_logger("ai")

_ORDER: dict[str, list[str]] = {
    # fast first for trivial glue; big hosted model first for hard reasoning.
    "simple": ["groq", "gemini", "mistral", "ollama", "kilo", "openrouter"],
    "normal": ["gemini", "mistral", "ollama", "kilo", "groq", "openrouter"],
    "complex": ["ollama", "kilo", "gemini", "mistral", "openrouter", "groq"],
}
_COOLDOWN_S = 60.0


class AIRouter:
    def __init__(self, providers: dict[str, AIProvider],
                 clock: Callable[[], float] = time.monotonic):
        self.providers = providers
        self._clock = clock
        self._cooldown_until: dict[str, float] = {}
        self.usage: dict[str, int] = {}   # provider -> successful call count (observability)

    def configured(self) -> list[str]:
        return [n for n, p in self.providers.items() if p.available()]

    def _ready(self, name: str) -> bool:
        p = self.providers.get(name)
        if not p or not p.available():
            return False
        return self._clock() >= self._cooldown_until.get(name, 0.0)

    def _cool(self, name: str) -> None:
        self._cooldown_until[name] = self._clock() + _COOLDOWN_S
        log.info("provider %s cooled down for %.0fs", name, _COOLDOWN_S)

    async def complete(self, prompt: str, *, kind: str = "normal",
                       system: str | None = None, max_tokens: int = 800,
                       temperature: float = 0.2) -> AIResponse:
        order = _ORDER.get(kind, _ORDER["normal"])
        last: AIResponse | None = None
        tried = False
        for name in order:
            if not self._ready(name):
                continue
            tried = True
            provider = self.providers[name]
            resp = await provider.complete(prompt, system=system,
                                           max_tokens=max_tokens, temperature=temperature)
            if resp.ok:
                self.usage[name] = self.usage.get(name, 0) + 1
                return resp
            log.info("provider %s failed: %s", name, resp.error)
            if resp.rate_limited:
                self._cool(name)
            last = resp

        if not tried:
            return AIResponse(ok=False, text="", provider="none", model="",
                              error="no free AI provider is configured/available")
        return last or AIResponse(ok=False, text="", provider="none", model="",
                                  error="all free providers failed")
