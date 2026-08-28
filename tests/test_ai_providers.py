import httpx

from app.ai.providers.gemini import GeminiProvider
from app.ai.providers.groq import GroqProvider
from app.ai.providers.openrouter import OpenRouterProvider


def _gemini_tp(status=200, text="hi"):
    def handler(request):
        if status != 200:
            return httpx.Response(status, text="quota exceeded")
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": text}]}}]})
    return httpx.MockTransport(handler)


def _openai_tp(status=200, text="ok"):
    def handler(request):
        if status != 200:
            return httpx.Response(status, text="rate limited")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": text}}]})
    return httpx.MockTransport(handler)


async def test_gemini_success():
    p = GeminiProvider("key", "gemini-2.0-flash", transport=_gemini_tp(text="hello"))
    r = await p.complete("hi")
    assert r.ok and r.text == "hello" and r.provider == "gemini"


async def test_gemini_rate_limited():
    p = GeminiProvider("key", "m", transport=_gemini_tp(status=429))
    r = await p.complete("hi")
    assert not r.ok and r.rate_limited and r.status == 429


async def test_provider_no_key():
    r = await GeminiProvider("", "m").complete("hi")
    assert not r.ok and "no api key" in (r.error or "")


async def test_groq_success():
    p = GroqProvider("key", "llama", transport=_openai_tp(text="groq-reply"))
    r = await p.complete("hi", system="be terse")
    assert r.ok and r.text == "groq-reply" and r.provider == "groq"


async def test_openrouter_success_and_headers():
    captured = {}

    def handler(request):
        captured["auth"] = request.headers.get("authorization")
        captured["title"] = request.headers.get("x-title")
        return httpx.Response(200, json={"choices": [{"message": {"content": "or"}}]})

    p = OpenRouterProvider("secret", "m:free", transport=httpx.MockTransport(handler))
    r = await p.complete("hi")
    assert r.ok and r.text == "or"
    assert captured["auth"] == "Bearer secret"
    assert captured["title"] == "AI Dev Bot"


async def test_openai_compat_rate_limited():
    p = GroqProvider("key", "m", transport=_openai_tp(status=429))
    r = await p.complete("hi")
    assert not r.ok and r.rate_limited
