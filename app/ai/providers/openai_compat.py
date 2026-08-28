"""Shared base for OpenAI-compatible chat providers (Groq, OpenRouter, Ollama, Kilo).

They all expose `/chat/completions` with Bearer auth and identical request/response
shapes, so they share one implementation and differ only by base URL / headers.

Features:
- `model` may be a COMMA-SEPARATED list — one key, several models, with per-model
  quota cooldown/fallback (free models rate-limit or get retired often, so this
  matters a lot for OpenRouter/Kilo).
- `min_tokens` gives reasoning models (gpt-oss, some Kilo/OpenRouter routes) enough
  budget that their visible answer isn't consumed entirely by hidden reasoning.
"""
from __future__ import annotations

import time

import httpx

from app.ai.providers.base import AIProvider, AIResponse

_COOLDOWN_S = 60.0


class OpenAICompatProvider(AIProvider):
    base_url: str = ""          # e.g. https://api.groq.com/openai/v1
    min_tokens: int = 0         # floor for reasoning models (0 = no floor)

    def __init__(self, api_key: str, model: str, timeout: float = 45.0,
                 transport: httpx.AsyncBaseTransport | None = None, *,
                 base_url: str | None = None, min_tokens: int | None = None):
        super().__init__(api_key, model, timeout, transport)
        if base_url is not None:
            self.base_url = base_url.rstrip("/")
        if min_tokens is not None:
            self.min_tokens = min_tokens
        self.models = [m.strip() for m in str(model).split(",") if m.strip()] or [model]
        self._cooldown: dict[str, float] = {}

    def _extra_headers(self) -> dict:
        return {}

    async def complete(self, prompt: str, system: str | None = None,
                       max_tokens: int = 800, temperature: float = 0.2) -> AIResponse:
        if not self.available():
            return self._fail("no api key")

        max_tokens = max(max_tokens, self.min_tokens)
        last: AIResponse | None = None
        for model in self.models:
            if time.monotonic() < self._cooldown.get(model, 0.0):
                continue
            resp = await self._call(model, prompt, system, max_tokens, temperature)
            if resp.ok:
                return resp
            if resp.rate_limited:
                self._cooldown[model] = time.monotonic() + _COOLDOWN_S
            last = resp
        return last or self._fail("all models unavailable")

    async def _call(self, model: str, prompt: str, system: str | None,
                    max_tokens: int, temperature: float) -> AIResponse:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json", **self._extra_headers()}
        body = {"model": model, "messages": messages,
                "max_tokens": max_tokens, "temperature": temperature}

        try:
            resp = await self._post(f"{self.base_url}/chat/completions",
                                    json=body, headers=headers)
        except httpx.HTTPError as exc:
            return self._fail(f"network: {exc}")

        if resp.status_code != 200:
            return self._fail(f"http {resp.status_code} ({model}): {resp.text[:160]}",
                              rate_limited=self._is_quota_status(resp.status_code),
                              status=resp.status_code)
        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            return self._fail(f"parse ({model}): {exc}")
        return AIResponse(ok=True, text=(text or "").strip(), provider=self.name,
                          model=model)
