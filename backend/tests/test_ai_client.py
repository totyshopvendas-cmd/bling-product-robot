"""Testes da camada de IA própria (ai_client) — sem rede: as chamadas HTTP são
interceptadas com httpx.MockTransport."""
import asyncio
import json
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ai_client  # noqa: E402

ALL_KEYS = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
            "OPENROUTER_API_KEY", "AI_TEXT_PROVIDER", "AI_TEXT_MODEL", "AI_IMAGE_PROVIDER", "AI_IMAGE_MODEL"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ALL_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(ai_client, "_stored_keys", lambda: {})
    yield


@pytest.fixture
def mock_http(monkeypatch):
    calls = []
    routes = {}

    def handler(request: httpx.Request):
        calls.append(request)
        for prefix, fn in routes.items():
            if str(request.url).startswith(prefix):
                return fn(request)
        return httpx.Response(500, text="no route")

    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw.pop("timeout", None)
        return real(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(ai_client.httpx, "AsyncClient", factory)
    return routes, calls


def run(coro):
    return asyncio.run(coro)


def test_no_key_raises():
    assert not ai_client.has_text_ai()
    chat = ai_client.LlmChat(system_message="x").with_model("anthropic", "claude-haiku-4-5-20251001")
    with pytest.raises(ai_client.AIConfigError):
        run(chat.send_message(ai_client.UserMessage("oi")))


def test_requested_provider_used_when_key_present(monkeypatch, mock_http):
    routes, calls = mock_http
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    routes["https://api.anthropic.com/v1/messages"] = lambda r: httpx.Response(
        200, json={"content": [{"type": "text", "text": "Título limpo"}]})
    chat = ai_client.LlmChat(system_message="sys").with_model("anthropic", "claude-haiku-4-5-20251001")
    out = run(chat.send_message(ai_client.UserMessage("bruto")))
    assert out == "Título limpo"
    body = json.loads(calls[0].content)
    assert body["model"] == "claude-haiku-4-5-20251001"
    assert body["system"] == "sys"
    assert calls[0].headers["x-api-key"] == "sk-ant-test"


def test_fallback_to_openai_when_no_anthropic(monkeypatch, mock_http):
    routes, calls = mock_http
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    routes["https://api.openai.com/v1/chat/completions"] = lambda r: httpx.Response(
        200, json={"choices": [{"message": {"content": "ok gpt"}}]})
    chat = ai_client.LlmChat(system_message="sys").with_model("anthropic", "claude-haiku-4-5-20251001")
    assert run(chat.send_message(ai_client.UserMessage("x"))) == "ok gpt"
    body = json.loads(calls[0].content)
    assert body["model"] == "gpt-4o-mini"  # modelo do provedor de fallback, não o Claude
    assert body["messages"][0] == {"role": "system", "content": "sys"}


def test_gemini_text_and_history(monkeypatch, mock_http):
    routes, calls = mock_http
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    routes["https://generativelanguage.googleapis.com/"] = lambda r: httpx.Response(
        200, json={"candidates": [{"content": {"parts": [{"text": "resp"}]}}]})
    chat = ai_client.LlmChat(system_message="s")
    run(chat.send_message(ai_client.UserMessage("um")))
    run(chat.send_message(ai_client.UserMessage("dois")))
    body = json.loads(calls[1].content)
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    assert "gemini-2.5-flash:generateContent" in str(calls[1].url)


def test_image_gemini_returns_inline_data(monkeypatch, mock_http):
    routes, calls = mock_http
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    routes["https://generativelanguage.googleapis.com/"] = lambda r: httpx.Response(
        200, json={"candidates": [{"content": {"parts": [
            {"text": "aqui"}, {"inlineData": {"mimeType": "image/png", "data": "QUJD"}}]}}]})
    chat = ai_client.LlmChat().with_model("gemini", "gemini-2.5-flash-image").with_params(modalities=["image", "text"])
    text, imgs = run(chat.send_message_multimodal_response(
        ai_client.UserMessage("foto", [ai_client.ImageContent("/9j/4AAQSkZJRg==")])))
    assert imgs == [{"mime_type": "image/png", "data": "QUJD"}]
    body = json.loads(calls[0].content)
    assert body["contents"][0]["parts"][1]["inline_data"]["mime_type"] == "image/jpeg"


def test_unknown_model_retries_with_default(monkeypatch, mock_http):
    routes, calls = mock_http
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")

    def gem(r):
        if "gemini-9-inexistente" in str(r.url):
            return httpx.Response(404, text="model not found")
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"inlineData": {"mimeType": "image/png", "data": "WFla"}}]}}]})

    routes["https://generativelanguage.googleapis.com/"] = gem
    chat = ai_client.LlmChat().with_model("gemini", "gemini-9-inexistente")
    _t, imgs = run(chat.send_message_multimodal_response(ai_client.UserMessage("x")))
    assert imgs[0]["data"] == "WFla"
    assert len(calls) == 2


def test_invalid_key_message(monkeypatch, mock_http):
    routes, _ = mock_http
    monkeypatch.setenv("ANTHROPIC_API_KEY", "bad")
    routes["https://api.anthropic.com/"] = lambda r: httpx.Response(401, text="invalid x-api-key")
    chat = ai_client.LlmChat()
    with pytest.raises(ai_client.AIConfigError) as e:
        run(chat.send_message(ai_client.UserMessage("x")))
    assert "chave inválida" in str(e.value)


def test_forced_provider_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("OPENROUTER_API_KEY", "b")
    monkeypatch.setenv("AI_TEXT_PROVIDER", "openrouter")
    monkeypatch.setenv("AI_TEXT_MODEL", "meta/llama")
    assert ai_client.resolve_text("anthropic", "claude-haiku-4-5") == ("openrouter", "meta/llama")


def test_status_never_leaks_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-abcdefghijklmnop1234")
    st = ai_client.status()
    assert st["providers"]["openai"]["configured"] is True
    assert "abcdefghijklmnop" not in json.dumps(st)
    assert st["text"]["provider"] == "openai"
    assert st["image"]["provider"] == "openai"
    assert st["tts"] is True
