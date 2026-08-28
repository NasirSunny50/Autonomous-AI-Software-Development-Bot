"""Groq provider (free tier, OpenAI-compatible, very fast)."""
from __future__ import annotations

from app.ai.providers.openai_compat import OpenAICompatProvider


class GroqProvider(OpenAICompatProvider):
    name = "groq"
    base_url = "https://api.groq.com/openai/v1"
