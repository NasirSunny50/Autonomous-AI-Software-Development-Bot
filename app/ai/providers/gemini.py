"""Google Gemini provider (free tier via AI Studio API key).

Multi-model: `model` may be a comma-separated list. A single free key can call
several Gemini models, and because the free rate limits are per-model, spreading
across models gives more total free headroom. On a per-model quota/429 the
provider cools that model down and tries the next one.
"""
from __future__ import annotations

import time

import httpx

from app.ai.providers.base import AIProvider, AIResponse

_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_COOLDOWN_S = 60.0


class GeminiProvider(AIProvider):
    name = "gemini"

    def __init__(self, api_key, model, timeout=30.0, transport=None):
        super().__init__(api_key, model, timeout, transport)
        # One key, one or more models (comma-separated).
        self.models = [m.strip() for m in str(model).split(",") if m.strip()] or [model]
        self._cooldown: dict[str, float] = {}

    async def complete(self, prompt: str, system: str | None = None,
                       max_tokens: int = 800, temperature: float = 0.2) -> AIResponse:
        if not self.available():
            return self._fail("no api key")

        last: AIResponse | None = None
        for model in self.models:
            if time.monotonic() < self._cooldown.get(model, 0.0):
                continue
            resp = await self._call_model(model, prompt, system, max_tokens, temperature)
            if resp.ok:
                return resp
            if resp.rate_limited:
                self._cooldown[model] = time.monotonic() + _COOLDOWN_S
            last = resp
        return last or self._fail("all gemini models unavailable")

    async def _call_model(self, model: str, prompt: str, system: str | None,
                          max_tokens: int, temperature: float) -> AIResponse:
        url = f"{_BASE}/{model}:generateContent"
        body: dict = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        try:
            resp = await self._post(url, json=body, params={"key": self.api_key})
        except httpx.HTTPError as exc:
            return self._fail(f"network: {exc}")

        if resp.status_code != 200:
            return self._fail(f"http {resp.status_code} ({model}): {resp.text[:160]}",
                              rate_limited=self._is_quota_status(resp.status_code),
                              status=resp.status_code)
        try:
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, ValueError) as exc:
            return self._fail(f"parse ({model}): {exc}")
        return AIResponse(ok=True, text=text.strip(), provider=self.name, model=model)
