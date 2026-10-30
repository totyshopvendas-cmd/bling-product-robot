"""Camada de IA própria do TotyShop — substitui a "Universal Key" da Emergent.

Usa a SUA chave, direto no provedor. Basta configurar pelo menos uma:

    ANTHROPIC_API_KEY   → Claude (padrão do projeto: claude-haiku-4-5)
    OPENAI_API_KEY      → GPT (texto), gpt-image-1 (imagem), tts-1 (voz)
    GEMINI_API_KEY      → Gemini (texto) e "Nano Banana" (imagem)
    OPENROUTER_API_KEY  → qualquer modelo via openrouter.ai (uma chave para todos)

As chaves podem vir do .env OU ser salvas pela tela Configurações (ficam em
backend/.secrets/ai_keys.json, fora do Git).

Overrides opcionais:
    AI_TEXT_PROVIDER / AI_TEXT_MODEL    (anthropic | openai | gemini | openrouter)
    AI_IMAGE_PROVIDER / AI_IMAGE_MODEL  (gemini | openai | openrouter)

A interface pública (LlmChat, UserMessage, ImageContent, OpenAITextToSpeech)
é a mesma que o código já usava, então os módulos não precisaram mudar de lógica.
Se o provedor pedido pelo código não tiver chave, cai automaticamente para o
próximo disponível.
"""
from __future__ import annotations

import base64
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx

PROVIDERS = ("anthropic", "openai", "gemini", "openrouter")
KEY_ENV = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openrouter": ("OPENROUTER_API_KEY",),
}
DEFAULT_TEXT_MODEL = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-2.5-flash",
    "openrouter": "anthropic/claude-haiku-4.5",
}
DEFAULT_IMAGE_MODEL = {
    "gemini": "gemini-2.5-flash-image",
    "openai": "gpt-image-1",
    "openrouter": "google/gemini-2.5-flash-image",
}
TEXT_ORDER = ("anthropic", "openai", "gemini", "openrouter")
IMAGE_ORDER = ("gemini", "openai", "openrouter")
TIMEOUT = httpx.Timeout(120.0, connect=20.0)


# ------------------------------------------------------------------ keys ---
def _stored_keys() -> Dict[str, str]:
    try:
        import secrets_store
        data = secrets_store.read("ai_keys") or {}
        return {k: str(v) for k, v in data.items() if v}
    except Exception:
        return {}


def get_key(provider: str) -> Optional[str]:
    for env in KEY_ENV.get(provider, ()):
        val = (os.environ.get(env) or "").strip()
        if val:
            return val
    return (_stored_keys().get(provider) or "").strip() or None


def configured_providers() -> List[str]:
    return [p for p in PROVIDERS if get_key(p)]


def has_text_ai() -> bool:
    return bool(configured_providers())


def has_image_ai() -> bool:
    return any(get_key(p) for p in IMAGE_ORDER)


def status() -> dict:
    """Resumo para a tela Configurações (nunca devolve a chave)."""
    out = {}
    for p in PROVIDERS:
        k = get_key(p)
        src = None
        if k:
            src = "env" if any((os.environ.get(e) or "").strip() for e in KEY_ENV[p]) else "painel"
        out[p] = {"configured": bool(k), "source": src, "hint": (k[:4] + "…" + k[-4:]) if k and len(k) > 10 else None}
    text_p, text_m = resolve_text(None, None)
    img_p, img_m = resolve_image(None, None)
    return {
        "providers": out,
        "text": {"provider": text_p, "model": text_m},
        "image": {"provider": img_p, "model": img_m},
        "tts": bool(get_key("openai")),
    }


def resolve_text(provider: Optional[str], model: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    forced_p = (os.environ.get("AI_TEXT_PROVIDER") or "").strip().lower()
    forced_m = (os.environ.get("AI_TEXT_MODEL") or "").strip()
    if forced_p and get_key(forced_p):
        return forced_p, forced_m or DEFAULT_TEXT_MODEL.get(forced_p)
    if provider in ("google",):
        provider = "gemini"
    if provider and get_key(provider):
        return provider, model or DEFAULT_TEXT_MODEL[provider]
    for p in TEXT_ORDER:
        if get_key(p):
            return p, DEFAULT_TEXT_MODEL[p]
    return None, None


def resolve_image(provider: Optional[str], model: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    forced_p = (os.environ.get("AI_IMAGE_PROVIDER") or "").strip().lower()
    forced_m = (os.environ.get("AI_IMAGE_MODEL") or "").strip()
    if forced_p in DEFAULT_IMAGE_MODEL and get_key(forced_p):
        return forced_p, forced_m or DEFAULT_IMAGE_MODEL[forced_p]
    if provider in ("google",):
        provider = "gemini"
    if provider in DEFAULT_IMAGE_MODEL and get_key(provider):
        # Só honra o modelo do código se for do mesmo provedor
        return provider, model or DEFAULT_IMAGE_MODEL[provider]
    for p in IMAGE_ORDER:
        if get_key(p):
            return p, DEFAULT_IMAGE_MODEL[p]
    return None, None


class AIConfigError(RuntimeError):
    pass


class ModelNotFound(RuntimeError):
    """Modelo pedido não existe nesse provedor → tenta o modelo padrão."""


def _no_key_error() -> AIConfigError:
    return AIConfigError(
        "Nenhuma chave de IA configurada. Abra Configurações → Inteligência Artificial "
        "e cole uma chave (Anthropic, OpenAI, Gemini ou OpenRouter)."
    )


# --------------------------------------------------------------- messages --
def _strip_data_url(value: str) -> Tuple[str, Optional[str]]:
    if value.startswith("data:") and "," in value:
        head, b64 = value.split(",", 1)
        mime = head[5:].split(";")[0] or None
        return b64, mime
    return value, None


def _sniff_mime(b64: str) -> str:
    try:
        head = base64.b64decode(b64[:32] + "=" * (-len(b64[:32]) % 4))
    except Exception:
        return "image/png"
    if head.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if head.startswith(b"RIFF") and b"WEBP" in head:
        return "image/webp"
    if head.startswith(b"GIF8"):
        return "image/gif"
    return "image/png"


class ImageContent:
    def __init__(self, image_base64: str, mime_type: Optional[str] = None):
        b64, mime = _strip_data_url(image_base64 or "")
        self.image_base64 = b64
        self.mime_type = mime_type or mime or _sniff_mime(b64)


class FileContentWithMimeType(ImageContent):
    def __init__(self, data_b64: str, mime_type: str = "application/octet-stream"):
        super().__init__(data_b64, mime_type)


class UserMessage:
    def __init__(self, text: str, file_contents: Optional[List[Any]] = None):
        self.text = text
        self.file_contents = [f for f in (file_contents or []) if getattr(f, "image_base64", None)]


# ---------------------------------------------------------------- chat -----
class LlmChat:
    """Compatível com a API antiga: LlmChat(...).with_model(p, m).send_message(msg)."""

    def __init__(self, api_key: Optional[str] = None, session_id: Optional[str] = None,
                 system_message: Optional[str] = None):
        # `api_key` é ignorado de propósito: as chaves vêm de get_key().
        self.session_id = session_id
        self.system_message = system_message or ""
        self.req_provider: Optional[str] = None
        self.req_model: Optional[str] = None
        self.params: Dict[str, Any] = {}
        self._history: List[Tuple[str, str]] = []  # (role, text)

    def with_model(self, provider: str, model: str) -> "LlmChat":
        self.req_provider = (provider or "").lower() or None
        self.req_model = model
        return self

    def with_params(self, **kwargs: Any) -> "LlmChat":
        self.params.update(kwargs)
        return self

    # ---- texto
    async def send_message(self, message: UserMessage) -> str:
        provider, model = resolve_text(self.req_provider, self.req_model)
        if not provider:
            raise _no_key_error()
        fn = {"anthropic": _anthropic_text, "openai": _openai_text,
              "gemini": _gemini_text, "openrouter": _openrouter_text}[provider]
        try:
            text = await fn(model, self.system_message, self._history, message, self.params)
        except ModelNotFound:
            if model == DEFAULT_TEXT_MODEL[provider]:
                raise
            text = await fn(DEFAULT_TEXT_MODEL[provider], self.system_message, self._history, message, self.params)
        self._history.append(("user", message.text))
        self._history.append(("assistant", text))
        return text

    # ---- imagem
    async def send_message_multimodal_response(self, message: UserMessage) -> Tuple[str, List[Dict[str, str]]]:
        provider, model = resolve_image(self.req_provider, self.req_model)
        if not provider:
            raise AIConfigError(
                "Geração de imagem precisa de uma chave Gemini, OpenAI ou OpenRouter "
                "(Configurações → Inteligência Artificial)."
            )
        fn = {"gemini": _gemini_image, "openai": _openai_image, "openrouter": _openrouter_image}[provider]
        try:
            return await fn(model, self.system_message, message)
        except ModelNotFound:
            if model == DEFAULT_IMAGE_MODEL[provider]:
                raise
            return await fn(DEFAULT_IMAGE_MODEL[provider], self.system_message, message)


# ------------------------------------------------------------ providers ----
def _raise_for(resp: httpx.Response, provider: str) -> None:
    if resp.status_code >= 400:
        detail = resp.text[:300]
        if resp.status_code in (401, 403):
            raise AIConfigError(f"{provider}: chave inválida ou sem permissão ({resp.status_code}). {detail}")
        if resp.status_code == 404 or (resp.status_code == 400 and "model" in detail.lower() and
                                        ("not found" in detail.lower() or "does not exist" in detail.lower()
                                         or "not supported" in detail.lower() or "invalid model" in detail.lower())):
            raise ModelNotFound(f"{provider}: modelo não encontrado ({resp.status_code}). {detail}")
        if resp.status_code == 429:
            raise RuntimeError(f"{provider}: limite/saldo excedido (429). {detail}")
        raise RuntimeError(f"{provider}: erro {resp.status_code}: {detail}")


async def _anthropic_text(model, system, history, msg: UserMessage, params) -> str:
    content: List[Dict[str, Any]] = []
    for f in msg.file_contents:
        content.append({"type": "image", "source": {"type": "base64", "media_type": f.mime_type, "data": f.image_base64}})
    content.append({"type": "text", "text": msg.text})
    messages = [{"role": r, "content": t} for r, t in history] + [{"role": "user", "content": content}]
    body: Dict[str, Any] = {"model": model, "max_tokens": int(params.get("max_tokens", 2048)), "messages": messages}
    if system:
        body["system"] = system
    if "temperature" in params:
        body["temperature"] = params["temperature"]
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": get_key("anthropic"), "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json=body,
        )
    _raise_for(r, "Anthropic")
    data = r.json()
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def _openai_messages(system, history, msg: UserMessage) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages += [{"role": r, "content": t} for r, t in history]
    if msg.file_contents:
        parts: List[Dict[str, Any]] = [{"type": "text", "text": msg.text}]
        for f in msg.file_contents:
            parts.append({"type": "image_url", "image_url": {"url": f"data:{f.mime_type};base64,{f.image_base64}"}})
        messages.append({"role": "user", "content": parts})
    else:
        messages.append({"role": "user", "content": msg.text})
    return messages


async def _chat_completions(base: str, key: str, model, system, history, msg, params, provider, extra_headers=None) -> str:
    body: Dict[str, Any] = {"model": model, "messages": _openai_messages(system, history, msg)}
    for k in ("temperature", "max_tokens"):
        if k in params:
            body[k] = params[k]
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    headers.update(extra_headers or {})
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(f"{base}/chat/completions", headers=headers, json=body)
    _raise_for(r, provider)
    return (r.json()["choices"][0]["message"].get("content") or "")


async def _openai_text(model, system, history, msg, params) -> str:
    return await _chat_completions("https://api.openai.com/v1", get_key("openai"), model, system, history, msg, params, "OpenAI")


async def _openrouter_text(model, system, history, msg, params) -> str:
    return await _chat_completions(
        "https://openrouter.ai/api/v1", get_key("openrouter"), model, system, history, msg, params, "OpenRouter",
        {"HTTP-Referer": os.environ.get("APP_BASE_URL", "http://127.0.0.1:8000"), "X-Title": "TotyShop"},
    )


def _gemini_parts(msg: UserMessage) -> List[Dict[str, Any]]:
    parts: List[Dict[str, Any]] = [{"text": msg.text}]
    for f in msg.file_contents:
        parts.append({"inline_data": {"mime_type": f.mime_type, "data": f.image_base64}})
    return parts


async def _gemini_call(model: str, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": get_key("gemini"), "Content-Type": "application/json"},
            json=body,
        )
    _raise_for(r, "Gemini")
    return r.json()


async def _gemini_text(model, system, history, msg, params) -> str:
    contents = [{"role": "model" if r == "assistant" else "user", "parts": [{"text": t}]} for r, t in history]
    contents.append({"role": "user", "parts": _gemini_parts(msg)})
    body: Dict[str, Any] = {"contents": contents}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if "temperature" in params:
        body["generationConfig"] = {"temperature": params["temperature"]}
    data = await _gemini_call(model, body)
    out = []
    for cand in data.get("candidates", [])[:1]:
        for p in cand.get("content", {}).get("parts", []):
            if "text" in p:
                out.append(p["text"])
    return "".join(out)


async def _gemini_image(model, system, msg) -> Tuple[str, List[Dict[str, str]]]:
    body: Dict[str, Any] = {
        "contents": [{"role": "user", "parts": _gemini_parts(msg)}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    data = await _gemini_call(model, body)
    text, images = [], []
    for cand in data.get("candidates", [])[:1]:
        for p in cand.get("content", {}).get("parts", []):
            inline = p.get("inline_data") or p.get("inlineData")
            if inline and inline.get("data"):
                images.append({"mime_type": inline.get("mime_type") or inline.get("mimeType") or "image/png",
                               "data": inline["data"]})
            elif "text" in p:
                text.append(p["text"])
    return "".join(text), images


async def _openai_image(model, system, msg) -> Tuple[str, List[Dict[str, str]]]:
    headers = {"Authorization": f"Bearer {get_key('openai')}"}
    prompt = msg.text if not system else f"{system}\n\n{msg.text}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(240.0, connect=20.0)) as c:
        if msg.file_contents:
            f = msg.file_contents[0]
            ext = f.mime_type.split("/")[-1]
            files = {"image[]": (f"ref.{ext}", base64.b64decode(f.image_base64), f.mime_type)}
            r = await c.post("https://api.openai.com/v1/images/edits", headers=headers,
                             data={"model": model, "prompt": prompt, "size": "1024x1024"}, files=files)
        else:
            r = await c.post("https://api.openai.com/v1/images/generations", headers=headers,
                             json={"model": model, "prompt": prompt, "size": "1024x1024", "n": 1})
    _raise_for(r, "OpenAI")
    images = []
    for item in r.json().get("data", []):
        b64 = item.get("b64_json")
        if not b64 and item.get("url"):
            async with httpx.AsyncClient(timeout=TIMEOUT) as c:
                img = await c.get(item["url"])
                b64 = base64.b64encode(img.content).decode()
        if b64:
            images.append({"mime_type": "image/png", "data": b64})
    return "", images


async def _openrouter_image(model, system, msg) -> Tuple[str, List[Dict[str, str]]]:
    body = {"model": model, "messages": _openai_messages(system, [], msg), "modalities": ["image", "text"]}
    async with httpx.AsyncClient(timeout=httpx.Timeout(240.0, connect=20.0)) as c:
        r = await c.post("https://openrouter.ai/api/v1/chat/completions",
                         headers={"Authorization": f"Bearer {get_key('openrouter')}"}, json=body)
    _raise_for(r, "OpenRouter")
    m = r.json()["choices"][0]["message"]
    images = []
    for im in m.get("images") or []:
        url = (im.get("image_url") or {}).get("url", "")
        b64, mime = _strip_data_url(url)
        if b64:
            images.append({"mime_type": mime or "image/png", "data": b64})
    return m.get("content") or "", images


# ------------------------------------------------------------------ voz ----
class OpenAITextToSpeech:
    def __init__(self, api_key: Optional[str] = None):
        self._key = get_key("openai")
        if not self._key:
            raise AIConfigError("A narração dos Shorts usa OpenAI TTS — configure a chave OpenAI em Configurações.")

    async def generate_speech(self, text: str, model: str = "tts-1", voice: str = "nova",
                              response_format: str = "mp3", **_: object) -> bytes:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post("https://api.openai.com/v1/audio/speech",
                             headers={"Authorization": f"Bearer {self._key}"},
                             json={"model": model, "voice": voice, "input": text, "response_format": response_format})
        _raise_for(r, "OpenAI TTS")
        return r.content


async def test_provider(provider: str) -> dict:
    """Faz uma chamada mínima para validar a chave."""
    if not get_key(provider):
        return {"ok": False, "error": "sem chave"}
    # chama o provedor pedido diretamente (sem fallback)
    fn = {"anthropic": _anthropic_text, "openai": _openai_text,
          "gemini": _gemini_text, "openrouter": _openrouter_text}[provider]
    try:
        txt = await fn(DEFAULT_TEXT_MODEL[provider], "Responda apenas OK.", [], UserMessage("Diga OK"), {"max_tokens": 10})
        return {"ok": True, "reply": (txt or "").strip()[:40]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
