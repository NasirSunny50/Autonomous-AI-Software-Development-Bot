"""AIProvider abstraction for the FREE helper models.

These providers are used ONLY for cheap text glue (log summarization, error
triage, NL->JSON) and as a fallback path — never for the core coding/reasoning,
which is Claude Code's job. Every provider here must be a free tier.

Providers are intentionally thin: build a request, POST it, parse the reply, and
map failures to a small typed result so the router can fall back deterministically.
An optional httpx transport can be injected for testing without real network/keys.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx


@dataclass
class AIResponse:
    ok: bool
    text: str
    provider: str
    model: str
    error: str | None = None
    rate_limited: bool = False      # quota / 429 -> router should cool this provider down
    status: int | None = None


class AIProvider(ABC):
    name: str = "base"

    def __init__(self, api_key: str, model: str, timeout: float = 45.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = api_key or ""
        self.model = model
        self.timeout = timeout
        self._transport = transport

    def available(self) -> bool:
        """A provider is usable only if it has a (free-tier) key configured."""
        return bool(self.api_key)

    @abstractmethod
    async def complete(self, prompt: str, system: str | None = None,
                       max_tokens: int = 800, temperature: float = 0.2) -> AIResponse:
        ...

    # ---- shared helpers ----
    async def _post(self, url: str, *, json: dict, headers: dict | None = None,
                    params: dict | None = None) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
            return await client.post(url, json=json, headers=headers, params=params)

    def _fail(self, error: str, *, rate_limited: bool = False,
              status: int | None = None) -> AIResponse:
        return AIResponse(ok=False, text="", provider=self.name, model=self.model,
                          error=error, rate_limited=rate_limited, status=status)

    def _ok(self, text: str) -> AIResponse:
        return AIResponse(ok=True, text=text.strip(), provider=self.name, model=self.model)

    @staticmethod
    def _is_quota_status(status: int) -> bool:
        # 429 rate limit / quota, 402 payment/credits exhausted on free tiers.
        return status in (429, 402)
