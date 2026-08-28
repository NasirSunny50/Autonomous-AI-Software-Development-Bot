"""Shared base for OpenAI-compatible chat providers (Groq, OpenRouter).

Both expose `/chat/completions` with Bearer auth and identical request/response
shapes, so they share one implementation and differ only by base URL / headers.
"""
from __future__ import annotations

import httpx

from app.ai.providers.base import AIProvider, AIResponse


class OpenAICompatProvider(AIProvider):
    base_url: str = ""          # e.g. https://api.groq.com/openai/v1

    def _extra_headers(self) -> dict:
        return {}

    async def complete(self, prompt: str, system: str | None = None,
                       max_tokens: int = 800, temperature: float = 0.2) -> AIResponse:
        if not self.available():
            return self._fail("no api key")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json", **self._extra_headers()}
        body = {"model": self.model, "messages": messages,
                "max_tokens": max_tokens, "temperature": temperature}

        try:
            resp = await self._post(f"{self.base_url}/chat/completions",
                                    json=body, headers=headers)
        except httpx.HTTPError as exc:
            return self._fail(f"network: {exc}")

        if resp.status_code != 200:
            return self._fail(f"http {resp.status_code}: {resp.text[:200]}",
                              rate_limited=self._is_quota_status(resp.status_code),
                              status=resp.status_code)
        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            return self._fail(f"parse: {exc}")
        return self._ok(text)
