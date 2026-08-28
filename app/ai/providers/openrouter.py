"""OpenRouter provider — access to free community models (`...:free`)."""
from __future__ import annotations

from app.ai.providers.openai_compat import OpenAICompatProvider


class OpenRouterProvider(OpenAICompatProvider):
    name = "openrouter"
    base_url = "https://openrouter.ai/api/v1"

    def _extra_headers(self) -> dict:
        # OpenRouter asks for these for attribution; harmless if generic.
        return {
            "HTTP-Referer": "https://github.com/ai-dev-bot",
            "X-Title": "AI Dev Bot",
        }
