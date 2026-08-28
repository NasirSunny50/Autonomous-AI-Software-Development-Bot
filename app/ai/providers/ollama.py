"""Ollama Cloud provider — hosted open models (e.g. gpt-oss:120b).

Ollama Cloud exposes an OpenAI-compatible endpoint, so it reuses the shared
chat implementation. The base URL is configurable in case the host differs.
Use only on Ollama's free tier — the one-cost rule (Claude Code only) still holds.
"""
from __future__ import annotations

import httpx

from app.ai.providers.base import AIResponse
from app.ai.providers.openai_compat import OpenAICompatProvider

# gpt-oss models are reasoning models: they spend completion tokens on internal
# reasoning before the visible answer. Give a floor so a small requested budget
# doesn't get fully consumed by reasoning, leaving message.content empty.
_MIN_TOKENS = 1024


class OllamaProvider(OpenAICompatProvider):
    name = "ollama"

    def __init__(self, api_key: str, model: str,
                 base_url: str = "https://ollama.com/v1", timeout: float = 90.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(api_key, model, timeout, transport)
        self.base_url = base_url.rstrip("/")

    async def complete(self, prompt: str, system: str | None = None,
                       max_tokens: int = 800, temperature: float = 0.2) -> AIResponse:
        return await super().complete(prompt, system=system,
                                      max_tokens=max(max_tokens, _MIN_TOKENS),
                                      temperature=temperature)
