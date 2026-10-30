"""LLM-powered fallback for title cleaning (Claude Haiku 4.5 ou o provedor configurado em ai_client)."""
import os
import uuid
from typing import Optional
from ai_client import LlmChat, UserMessage, has_text_ai



SYSTEM = (
    "Você é um especialista em SEO de marketplaces brasileiros. "
    "Sua única tarefa é limpar títulos de produtos seguindo regras estritas:\n"
    "1. REMOVER nomes de marca (XLS, Kapbom, Inova, Altomex, Eletromex e similares).\n"
    "2. REMOVER códigos EAN (sequências de 8-14 dígitos).\n"
    "3. REMOVER caracteres especiais — manter apenas letras, números, espaços e HÍFEN.\n"
    "4. O CÓDIGO DO PRODUTO (ex: KA-6070, JZ-USBD, EL-1931, B125, A-P18) deve aparecer como ÚLTIMA palavra do título.\n"
    "5. MÁXIMO 60 CARACTERES — conte caracteres incluindo espaços.\n"
    "6. Sem emojis, sem palavras promocionais (ex: 'alta qualidade', 'o melhor').\n"
    "Responda APENAS com o título final limpo, sem aspas, sem explicação."
)


async def llm_clean_title(raw: str, code_hint: Optional[str] = None) -> str:
    if not has_text_ai():
        raise RuntimeError("Nenhuma chave de IA configurada (Configurações → Inteligência Artificial)")
    chat = LlmChat(
        session_id=f"title-clean-{uuid.uuid4()}",
        system_message=SYSTEM,
    ).with_model("anthropic", "claude-haiku-4-5-20251001")

    msg_text = f"Título bruto: {raw}"
    if code_hint:
        msg_text += f"\nCódigo a manter no final: {code_hint}"
    msg_text += "\nGere o título limpo (máx 60 chars):"

    response = await chat.send_message(UserMessage(text=msg_text))
    # response may be a string already; strip quotes & whitespace
    cleaned = str(response).strip().strip('"').strip("'")
    if len(cleaned) > 60:
        cleaned = cleaned[:60].rstrip()
    return cleaned
