"""High-level cheap "glue" tasks for the free models.

These are the ONLY jobs the free models do: summarize logs into a short Telegram
digest, triage an error into a category/hint, and turn a plain-language request
into structured JSON. Each has a deterministic fallback so that if no free
provider is available the bot still works (and never pays).
"""
from __future__ import annotations

import json
import re

from app.ai.router import AIRouter
from app.ai.token_manager import ResponseCache
from app.utils.text import derive_project_name, truncate


def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of a model reply (handles ``` fences)."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


class GlueAI:
    def __init__(self, router: AIRouter, cache: ResponseCache | None = None):
        self.router = router
        self.cache = cache or ResponseCache()

    async def summarize_logs(self, text: str, max_chars: int = 400) -> str:
        """Condense long build/test output into a short digest for Telegram."""
        if len(text) <= max_chars:
            return text.strip()
        cached = self.cache.get(text, "summarize")
        if cached is not None:
            return cached
        prompt = (
            "Summarize this build/test output for a developer in 2-3 short bullet "
            "points. Only the essential facts (what passed/failed and why). "
            f"Be concise.\n\n{truncate(text, 4000)}"
        )
        resp = await self.router.complete(prompt, kind="simple", max_tokens=250)
        result = resp.text if resp.ok else truncate(text, max_chars)  # graceful fallback
        self.cache.set(text, "summarize", result)
        return result

    async def triage_error(self, evidence: str) -> dict:
        """Classify a failure into a coarse category + one-line hint.

        Returns {"category": str, "hint": str}. Deterministic keyword fallback
        if no free provider answers."""
        prompt = (
            "Classify this software failure. Respond ONLY with JSON: "
            '{"category": "<build|test|lint|typecheck|runtime|dependency|unknown>", '
            '"hint": "<one short sentence on the likely cause>"}\n\n'
            f"{truncate(evidence, 3000)}"
        )
        resp = await self.router.complete(prompt, kind="simple", max_tokens=150)
        if resp.ok:
            obj = _extract_json(resp.text)
            if obj and "category" in obj:
                return {"category": str(obj.get("category", "unknown")),
                        "hint": str(obj.get("hint", ""))}
        return {"category": _keyword_category(evidence), "hint": ""}

    async def parse_requirement(self, nl: str) -> dict:
        """Turn a plain-language requirement into structured JSON.

        Returns {"name","tech_stack","features"[]}. Falls back to a deterministic
        best-effort parse when no free provider is available."""
        prompt = (
            "Convert this software request into JSON with keys: name (short), "
            "tech_stack (string), features (array of short strings). "
            "Respond ONLY with JSON.\n\n" + truncate(nl, 3000)
        )
        resp = await self.router.complete(prompt, kind="normal", max_tokens=400)
        if resp.ok:
            obj = _extract_json(resp.text)
            if obj:
                return {
                    "name": str(obj.get("name") or derive_project_name(nl)),
                    "tech_stack": str(obj.get("tech_stack") or ""),
                    "features": [str(f) for f in obj.get("features", []) if f][:20],
                }
        return {"name": derive_project_name(nl), "tech_stack": "", "features": []}


_KEYWORDS = {
    "test": ["assert", "test failed", "failing test", "pytest", "expect("],
    "build": ["build failed", "compilation", "cannot find module", "webpack", "vite"],
    "lint": ["eslint", "ruff", "lint"],
    "typecheck": ["type error", "ts(", "typescript", "mypy"],
    "dependency": ["module not found", "npm err", "pip", "unresolved import"],
    "runtime": ["traceback", "unhandled", "exception", "segfault"],
}


def _keyword_category(evidence: str) -> str:
    low = evidence.lower()
    for cat, kws in _KEYWORDS.items():
        if any(k in low for k in kws):
            return cat
    return "unknown"
