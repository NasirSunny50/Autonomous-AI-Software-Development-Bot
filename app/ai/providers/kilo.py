"""Kilo Code gateway provider — OpenAI-compatible access to many models.

The gateway hosts BOTH free and paid models. To honour the one-cost rule
(Claude Code is the only thing we pay for), configure a FREE model only —
`kilo-auto/free` (auto-routes among free models) or any `...:free` id. A token
floor is applied because some free routes are reasoning models.
"""
from __future__ import annotations

import httpx

from app.ai.providers.openai_compat import OpenAICompatProvider


class KiloProvider(OpenAICompatProvider):
    name = "kilo"

    def __init__(self, api_key: str, model: str,
                 base_url: str = "https://api.kilo.ai/api/gateway",
                 timeout: float = 90.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(api_key, model, timeout, transport,
                         base_url=base_url, min_tokens=1024)
