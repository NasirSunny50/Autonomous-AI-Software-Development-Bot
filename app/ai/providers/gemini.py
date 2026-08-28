"""Google Gemini provider (free tier via AI Studio API key)."""
from __future__ import annotations

import httpx

from app.ai.providers.base import AIProvider, AIResponse

_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiProvider(AIProvider):
    name = "gemini"

    async def complete(self, prompt: str, system: str | None = None,
                       max_tokens: int = 800, temperature: float = 0.2) -> AIResponse:
        if not self.available():
            return self._fail("no api key")

        url = f"{_BASE}/{self.model}:generateContent"
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
            return self._fail(f"http {resp.status_code}: {resp.text[:200]}",
                              rate_limited=self._is_quota_status(resp.status_code),
                              status=resp.status_code)
        try:
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, ValueError) as exc:
            return self._fail(f"parse: {exc}")
        return self._ok(text)
