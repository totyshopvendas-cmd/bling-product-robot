"""LLM-powered Shopee category selector.

Tries the Emergent LLM key first (same one used for title cleaning) and falls
back to OPENAI_API_KEY when it isn't configured. If neither works, the caller
should fall back to the local heuristic map.
"""
import os
import re
import uuid
from typing import Optional


EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("SHOPEE_CATEGORY_MODEL", "gpt-4o-mini")

SYSTEM = (
    "Você é um especialista em marketplaces brasileiros e precisa escolher a "
    "categoria do produto para cadastro na Shopee.\n\n"
    "REGRAS OBRIGATÓRIAS:\n"
    "1. A categoria escolhida DEVE ser compatível com o produto — esse é o critério mais importante.\n"
    "2. Entre as categorias compatíveis, escolha a mais GENÉRICA possível.\n"
    "3. Dê preferência a caminhos terminando em 'Outros', 'Acessórios' ou 'Peças e Acessórios'.\n"
    "4. NUNCA escolha uma categoria de um segmento diferente do produto "
    "(ex: não coloque eletrônico em 'Moda', nem controle de videogame em 'Eletrodomésticos').\n"
    "5. Use o nome do produto para inferir o segmento correto.\n\n"
    "Responda APENAS com o TEXTO EXATO da categoria escolhida (exatamente como aparece na lista). "
    "Sem aspas, sem explicação, sem numeração."
)


def _normalize(text: str) -> str:
    return (
        text.lower()
        .strip()
        .replace("  ", " ")
        .replace("-", " ")
        .replace("/", " ")
        .replace(">", " ")
    )


def _fuzzy_match(choice: str, options: list) -> Optional[dict]:
    """Match LLM free-text back to the closest option text."""
    choice_norm = _normalize(choice)
    # 1. exact normalized match
    for opt in options:
        if _normalize(opt.get("text", "")) == choice_norm:
            return opt
    # 2. substring
    for opt in options:
        text = _normalize(opt.get("text", ""))
        if choice_norm in text or text in choice_norm:
            return opt
    # 3. token overlap
    best = None
    best_score = 0
    choice_tokens = set(choice_norm.split())
    for opt in options:
        text = _normalize(opt.get("text", ""))
        text_tokens = set(text.split())
        if not text_tokens:
            continue
        overlap = len(choice_tokens & text_tokens)
        score = overlap / max(len(text_tokens), 1)
        if score > best_score:
            best_score = score
            best = opt
    if best_score >= 0.4:
        return best
    return None


async def _ask_emergent(msg: str) -> Optional[str]:
    """Query the Emergent LLM gateway. Returns raw text or None."""
    if not EMERGENT_LLM_KEY:
        return None
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage

        chat = LlmChat(
            api_key=EMERGENT_LLM_KEY,
            session_id=f"shopee-category-{uuid.uuid4()}",
            system_message=SYSTEM,
        ).with_model("anthropic", "claude-haiku-4-5-20251001")
        response = await chat.send_message(UserMessage(text=msg))
        return str(response)
    except Exception:
        return None


async def _ask_openai(msg: str) -> Optional[str]:
    """Query OpenAI directly using OPENAI_API_KEY. Returns raw text or None."""
    if not OPENAI_API_KEY:
        return None
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        resp = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": msg},
            ],
            temperature=0,
            max_tokens=120,
        )
        return resp.choices[0].message.content
    except Exception:
        return None


def llm_available() -> bool:
    """True when at least one LLM provider is configured."""
    return bool(EMERGENT_LLM_KEY or OPENAI_API_KEY)


async def pick_shopee_category(raw_title: str, options: list) -> Optional[dict]:
    """Ask the LLM to pick the best Shopee category from `options`.

    Returns the chosen option dict or None if the LLM is unavailable/fails.
    """
    if not options or not llm_available():
        return None

    # Filter out placeholder-looking options from the prompt, but keep them
    # in the matchable list just in case.
    prompt_options = []
    for i, opt in enumerate(options):
        text = opt.get("text", "").strip()
        if not text:
            continue
        prompt_options.append(f"{i + 1}. {text}")
    if not prompt_options:
        return None

    msg = (
        f"Produto: {raw_title}\n\n"
        f"Categorias disponíveis:\n" + "\n".join(prompt_options) + "\n\n"
        "Qual categoria da Shopee devo escolher? Responda apenas com o texto exato da categoria."
    )

    raw = await _ask_emergent(msg)
    if not raw:
        raw = await _ask_openai(msg)
    if not raw:
        return None

    choice = str(raw).strip().strip('"').strip("'")
    if not choice:
        return None
    match = _fuzzy_match(choice, options)
    if match:
        return match
    # If the model returns a number like "3.", try to map by index
    m = re.search(r"\b(\d+)\b", choice)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(options):
            return options[idx]
    return None
