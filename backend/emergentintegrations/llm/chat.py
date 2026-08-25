"""Drop-in replacement for `emergentintegrations.llm.chat` backed by OpenAI.

Supported surface (as used by this project):
    LlmChat(api_key=..., session_id=..., system_message=...)
        .with_model(provider, model)
        .with_params(**kwargs)
        await .send_message(UserMessage(text=..., file_contents=[ImageContent(b64)]))
        await .send_message_multimodal_response(msg) -> (text, [{"mime_type", "data"}])
    UserMessage(text=..., file_contents=[...])
    ImageContent(image_base64) / FileContentWithMimeType(data_b64, mime_type)
"""
from __future__ import annotations

import base64
import os
from typing import Any, Dict, List, Optional, Tuple

from openai import AsyncOpenAI

# Models from other providers are routed to these OpenAI equivalents, since the
# Emergent multi-provider gateway is not available.
_PROVIDER_MODEL_MAP = {
    "anthropic": "gpt-4o-mini",
    "gemini": "gpt-4o-mini",
    "google": "gpt-4o-mini",
}
_DEFAULT_TEXT_MODEL = "gpt-4o-mini"
_DEFAULT_IMAGE_MODEL = "gpt-image-1"


def _resolve_key(api_key: Optional[str]) -> str:
    key = api_key or os.environ.get("EMERGENT_LLM_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Nenhuma chave de LLM configurada (OPENAI_API_KEY).")
    return key


class ImageContent:
    """Base64-encoded image attachment."""

    def __init__(self, image_base64: str, mime_type: str = "image/png"):
        self.image_base64 = _strip_data_url(image_base64)
        self.mime_type = mime_type


class FileContentWithMimeType:
    def __init__(self, data_b64: str, mime_type: str = "application/octet-stream"):
        self.image_base64 = _strip_data_url(data_b64)
        self.mime_type = mime_type


def _strip_data_url(value: str) -> str:
    if value.startswith("data:") and "," in value:
        return value.split(",", 1)[1]
    return value


class UserMessage:
    def __init__(self, text: str, file_contents: Optional[List[Any]] = None):
        self.text = text
        self.file_contents = file_contents or []

    def to_openai(self) -> Dict[str, Any]:
        if not self.file_contents:
            return {"role": "user", "content": self.text}
        parts: List[Dict[str, Any]] = [{"type": "text", "text": self.text}]
        for item in self.file_contents:
            b64 = getattr(item, "image_base64", None)
            if not b64:
                continue
            mime = getattr(item, "mime_type", "image/png")
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
        return {"role": "user", "content": parts}


class LlmChat:
    def __init__(
        self,
        api_key: Optional[str] = None,
        session_id: Optional[str] = None,
        system_message: Optional[str] = None,
    ):
        self._client = AsyncOpenAI(api_key=_resolve_key(api_key))
        self.session_id = session_id
        self.system_message = system_message
        self.model = _DEFAULT_TEXT_MODEL
        self.provider = "openai"
        self.params: Dict[str, Any] = {}
        self._history: List[Dict[str, Any]] = []

    def with_model(self, provider: str, model: str) -> "LlmChat":
        self.provider = provider
        self.model = model if provider == "openai" else _PROVIDER_MODEL_MAP.get(provider, _DEFAULT_TEXT_MODEL)
        return self

    def with_params(self, **kwargs: Any) -> "LlmChat":
        self.params.update(kwargs)
        return self

    def _messages(self, msg: UserMessage) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = []
        if self.system_message:
            messages.append({"role": "system", "content": self.system_message})
        messages.extend(self._history)
        messages.append(msg.to_openai())
        return messages

    async def send_message(self, message: UserMessage) -> str:
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=self._messages(message),
        )
        text = response.choices[0].message.content or ""
        self._history.append(message.to_openai())
        self._history.append({"role": "assistant", "content": text})
        return text

    async def send_message_multimodal_response(
        self, message: UserMessage
    ) -> Tuple[str, List[Dict[str, str]]]:
        """Generate an image (and caption text) for the given prompt."""
        wants_image = "image" in (self.params.get("modalities") or ["image"])
        if not wants_image:
            return await self.send_message(message), []

        result = await self._client.images.generate(
            model=_DEFAULT_IMAGE_MODEL,
            prompt=message.text,
            size="1024x1024",
            n=1,
        )
        images: List[Dict[str, str]] = []
        for item in result.data or []:
            b64 = getattr(item, "b64_json", None)
            if not b64:
                url = getattr(item, "url", None)
                if not url:
                    continue
                b64 = await _download_as_b64(url)
            images.append({"mime_type": "image/png", "data": b64})
        return "", images


async def _download_as_b64(url: str) -> str:
    import httpx

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return base64.b64encode(resp.content).decode()
