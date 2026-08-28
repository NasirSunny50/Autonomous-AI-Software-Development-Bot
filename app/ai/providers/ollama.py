"""Ollama Cloud provider — hosted open models (e.g. gpt-oss:120b).

OpenAI-compatible; base URL configurable. gpt-oss are reasoning models, so a
token floor keeps `message.content` from being emptied by hidden reasoning.
Use Ollama's free tier — the one-cost rule (Claude Code only) still holds.
"""
from __future__ import annotations

import httpx

from app.ai.providers.openai_compat import OpenAICompatProvider


class OllamaProvider(OpenAICompatProvider):
    name = "ollama"

    def __init__(self, api_key: str, model: str,
                 base_url: str = "https://ollama.com/v1", timeout: float = 90.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(api_key, model, timeout, transport,
                         base_url=base_url, min_tokens=1024)
