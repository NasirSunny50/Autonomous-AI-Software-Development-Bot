"""Build the free-provider router from settings."""
from __future__ import annotations

from app.ai.providers.gemini import GeminiProvider
from app.ai.providers.groq import GroqProvider
from app.ai.providers.ollama import OllamaProvider
from app.ai.providers.openrouter import OpenRouterProvider
from app.ai.router import AIRouter
from app.config import Settings


def build_router(settings: Settings) -> AIRouter:
    providers = {
        "gemini": GeminiProvider(settings.gemini_api_key, settings.gemini_model),
        "groq": GroqProvider(settings.groq_api_key, settings.groq_model),
        "openrouter": OpenRouterProvider(settings.openrouter_api_key,
                                         settings.openrouter_model),
        "ollama": OllamaProvider(settings.ollama_api_key, settings.ollama_model,
                                 settings.ollama_base_url),
    }
    return AIRouter(providers)
