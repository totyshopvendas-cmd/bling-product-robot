"""Drop-in replacement for `emergentintegrations.llm.openai.text_to_speech`."""
from __future__ import annotations

import os
from typing import Optional

from openai import AsyncOpenAI


class OpenAITextToSpeech:
    def __init__(self, api_key: Optional[str] = None):
        key = api_key or os.environ.get("EMERGENT_LLM_KEY") or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("Nenhuma chave de LLM configurada (OPENAI_API_KEY).")
        self._client = AsyncOpenAI(api_key=key)

    async def generate_speech(
        self,
        text: str,
        model: str = "tts-1",
        voice: str = "nova",
        response_format: str = "mp3",
        **_: object,
    ) -> bytes:
        response = await self._client.audio.speech.create(
            model=model,
            voice=voice,
            input=text,
            response_format=response_format,
        )
        return response.read()
