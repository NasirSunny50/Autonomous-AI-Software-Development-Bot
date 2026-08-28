"""Mistral AI provider (free experiment tier via La Plateforme).

OpenAI-compatible chat endpoint; base URL configurable. Use free-eligible models
(e.g. mistral-small-latest) — the one-cost rule (Claude Code only) still holds.
"""
from __future__ import annotations

import httpx

from app.ai.providers.openai_compat import OpenAICompatProvider


class MistralProvider(OpenAICompatProvider):
    name = "mistral"

    def __init__(self, api_key: str, model: str,
                 base_url: str = "https://api.mistral.ai/v1", timeout: float = 60.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(api_key, model, timeout, transport, base_url=base_url)
