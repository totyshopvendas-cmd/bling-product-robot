"""Bling enrichment service.

After JohnDrop cadastros a product, JohnDrop automatically syncs it to Bling.
This service then ENRICHES the Bling product with:
  1. Short description (rewritten from JohnDrop raw description — SEO + readability)
  2. Exactly 8 bullet points (max 150 chars each)
  3. Best-matching Bling category (or creates one if none exists)

Rules for descriptions:
- No brand names (XLS, Kapbom, etc) or EAN/GTIN
- Only <b>bold</b> and hyphens (-) allowed
- Variations (cores/tamanhos/modelos) must be preserved & highlighted in short description
"""
import os
import re
import uuid
import json
import asyncio
from typing import Optional, List
from datetime import datetime, timezone

from emergentintegrations.llm.chat import LlmChat, UserMessage

import bling_service
from db import db
from robot_service import add_log


EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY")
LLM_PROVIDER = "anthropic"
LLM_MODEL = "claude-haiku-4-5-20251001"

# Bling product sync may take a few seconds — retry finding the product
FIND_PRODUCT_MAX_ATTEMPTS = 18
FIND_PRODUCT_DELAY_S = 15

BLOCKED_BRAND_RE = re.compile(
    r"\b(XLS|Kapbom|Inova|Altomex|Eletromex|Hayamax|JONHDROP|Jonh Variedades|Variedades)\b",
    re.IGNORECASE,
)
EAN_RE = re.compile(r"\b\d{8,14}\b")


def _sanitize(text: str, preserve_newlines: bool = False) -> str:
    """Remove brand names + EAN sequences from any free text."""
    if not text:
        return ""
    text = BLOCKED_BRAND_RE.sub(" ", text)
    text = EAN_RE.sub(" ", text)
    if preserve_newlines:
        # Collapse spaces/tabs but keep newlines
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
    return re.sub(r"\s+", " ", text).strip()


SHORT_DESC_SYSTEM = (
    "Você reformata descrições de produto do fornecedor JohnDrop para o Bling.\n"
    "REGRA DE OURO: PRESERVE o texto original do JohnDrop. Você é um REFORMATADOR, "
    "não um redator. Apenas:\n"
    "  (a) divide em parágrafos com \\n\\n\n"
    "  (b) aplica negrito <b>...</b> em rótulos de seção (ex: <b>Características:</b>)\n"
    "  (c) substitui bullets/asteriscos por hífen (- )\n"
    "PROIBIDO ALTERAR:\n"
    "  - Vocabulário (não troque sinônimos)\n"
    "  - Ordem das informações\n"
    "  - Detalhes técnicos (medidas, capacidade, materiais)\n"
    "  - Lista de cores/tamanhos/modelos disponíveis\n"
    "ÚNICAS REMOÇÕES PERMITIDAS:\n"
    "  1. Nomes de marca (XLS, Kapbom, Inova, Altomex, Eletromex, Aoshi, Lehmox, etc)\n"
    "  2. Códigos EAN/GTIN (sequências de 8-14 dígitos)\n"
    "PROIBIDO:\n"
    "  - Emojis, asteriscos, símbolos ° ™ ® (apenas <b>...</b> e hífen)\n"
    "  - Inventar características que não estão na descrição original\n"
    "  - Reescrever frases (apenas reformate o texto que já existe)\n"
    "  - Acrescentar slogans de marketing genéricos\n"
    "Se a descrição original já estiver bem escrita, copie-a quase literalmente, "
    "apenas dividindo em parágrafos com \\n\\n e aplicando negrito em rótulos.\n"
    "Responda APENAS o texto reformatado. Sem comentários."
)


BULLETS_SYSTEM = (
    "Você é um especialista em copywriting técnico para marketplaces. "
    "Gere EXATAMENTE 8 bullet points técnicos sobre o produto. "
    "REGRAS OBRIGATÓRIAS:\n"
    "1. EXATAMENTE 8 bullets — nem mais, nem menos.\n"
    "2. Cada bullet com MÁXIMO 150 caracteres.\n"
    "3. Cada bullet começa com hífen + espaço: '- '\n"
    "4. NUNCA inclua nomes de marca nem códigos EAN/GTIN.\n"
    "5. Permitido APENAS: negrito via <b>...</b> e hífen.\n"
    "   PROIBIDO: emojis, asteriscos, símbolos como ° ™ ®.\n"
    "6. Cada bullet deve destacar um benefício/característica DIFERENTE.\n"
    "7. Linguagem clara, direta, vendedora.\n"
    "Responda APENAS com os 8 bullets, um por linha. Sem cabeçalho, sem numeração, sem aspas."
)


CATEGORY_SYSTEM = (
    "Você é um especialista em categorização de produtos para o ERP Bling. "
    "Dada a descrição de um produto e uma lista de categorias disponíveis no Bling, "
    "escolha a categoria MAIS APROPRIADA. "
    "Responda APENAS com um JSON válido no formato:\n"
    '{\"category_id\": <id ou null>, \"category_name\": \"<nome se for criar nova>\", '
    '\"confidence\": \"high\"|\"medium\"|\"low\"}\n'
    "Use 'category_id' quando uma categoria existente couber bem (confidence=high ou medium). "
    "Use 'category_id: null' e 'category_name' com um nome curto/genérico se NENHUMA categoria existente couber. "
    "Categorias genéricas válidas: 'Eletrônicos', 'Beleza', 'Casa', 'Acessórios', 'Esporte', 'Brinquedos', 'Saúde'."
)


def _strip_emojis_and_special(text: str) -> str:
    """Strip emojis & forbidden characters, keep only letters, digits, punctuation, hyphen, <b> tags
    AND newline characters (for paragraph breaks)."""
    # Preserve <b>/</b> tags and newlines
    placeholder_open = "\x01BSTART\x01"
    placeholder_close = "\x01BEND\x01"
    placeholder_nl = "\x01NL\x01"
    text = text.replace("<b>", placeholder_open).replace("</b>", placeholder_close).replace("\n", placeholder_nl)
    text = re.sub(r"[^\w\s\-.,;:?!()\"\'áéíóúâêîôûãõàèìòùçÁÉÍÓÚÂÊÎÔÛÃÕÀÈÌÒÙÇ\x01A-Z]+", " ", text, flags=re.UNICODE)
    text = text.replace(placeholder_open, "<b>").replace(placeholder_close, "</b>").replace(placeholder_nl, "\n")
    # Collapse 3+ newlines to exactly 2 (paragraph break)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse spaces but preserve newlines
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text


# Abbreviation map for common Portuguese color/size names → 2-letter code
VARIATION_ABBR = {
    "rosa": "RS", "azul": "AZ", "verde": "VD", "amarelo": "AM", "preto": "PR",
    "branco": "BR", "vermelho": "VM", "cinza": "CZ", "roxo": "RX", "laranja": "LR",
    "marrom": "MR", "bege": "BG", "dourado": "DR", "prata": "PT", "vinho": "VH",
    "azul claro": "AC", "azul escuro": "AE", "rosa claro": "RC", "verde claro": "VC",
    "pequeno": "PQ", "medio": "MD", "grande": "GD", "extra grande": "XG",
    "p": "P", "m": "M", "g": "G", "gg": "GG", "pp": "PP", "xg": "XG", "xs": "XS",
}


def _abbreviate_variation(name: str) -> str:
    """Generate 2-letter abbreviation for a variation name."""
    key = name.lower().strip()
    if key in VARIATION_ABBR:
        return VARIATION_ABBR[key]
    # Fallback: take first 2 consonants/letters uppercase
    letters = re.sub(r"[^A-Za-zÀ-ÿ]", "", name).upper()
    if len(letters) >= 2:
        return letters[0] + letters[-1] if len(letters) > 3 else letters[:2]
    return letters or "VA"


def _parse_variation_quantities(raw_description: str, variations: List[str]) -> dict:
    """Detect explicit per-variation quantities AND out-of-stock markers in the description.

    Supported formats (case-insensitive):
      - "Rosa: 5"     /  "Rosa - 5"
      - "Rosa (5)"    /  "Rosa (5 unidades)"
      - "5 Rosa"      /  "5 unidades de Rosa"
      - "Rosa (esgotado)" / "Rosa (sem estoque)" → returns 0
      - "Rosa - Esgotado"                        → returns 0

    Returns {variation_name: qty} only for the variations explicitly quantified
    or marked as out-of-stock. Variations NOT present in the dict should get the
    equal split of remaining stock.
    """
    out: dict = {}
    if not raw_description or not variations:
        return out
    text = raw_description
    for v in variations:
        v_re = re.escape(v)
        # Out-of-stock markers FIRST (force 0)
        oos_patterns = [
            rf"{v_re}\s*[\(\-:]?\s*esgotad[oa]?",
            rf"{v_re}\s*[\(\-:]?\s*sem\s+estoque",
            rf"{v_re}\s*[\(\-:]?\s*indispon[ií]ve[il]",
            rf"{v_re}\s*[\(\-:]?\s*0\s*(?:un|unidad|peças?)?\s*\)?",
        ]
        is_oos = any(re.search(p, text, re.IGNORECASE) for p in oos_patterns)
        if is_oos:
            out[v] = 0
            continue

        patterns = [
            rf"{v_re}\s*[:\-–]\s*(\d{{1,4}})",
            rf"{v_re}\s*\(\s*(\d{{1,4}})\s*(?:un|unidad|pcs|peças?)?\s*\)",
            rf"(\d{{1,4}})\s+(?:un|unidade?s?|pcs)?\s*(?:de\s+)?{v_re}\b",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                try:
                    out[v] = int(m.group(1))
                    break
                except (TypeError, ValueError):
                    pass
    return out


def _parse_variations(raw_description: str) -> List[str]:
    """Extract variation names from descriptions following strict TotyShop rules:

    1. Must contain the trigger word "Disponível" / "Disponíveis" together with
       "cores" / "tamanhos" / "modelos". Without "Disponível", NO variations are
       extracted (avoids descriptive text being mistaken for variations).
    2. If the description says "conforme disponibilidade do estoque" / "seguindo
       a disponibilidade" / "de acordo com o estoque" anywhere, the seller doesn't
       let the buyer choose → NO variations extracted.
    3. Variations are STRICT: only single-word color/size/model names. Multi-word
       phrases (> 3 words) or descriptive sentences are filtered out.
    """
    if not raw_description:
        return []

    # GATE 1: "conforme disponibilidade do estoque" → buyer doesn't choose → no variations
    disclaimer_patterns = [
        r"conforme\s+(?:a\s+)?disponibilidade\s+(?:do\s+|de\s+)?estoque",
        r"seguindo\s+(?:a\s+)?disponibilidade",
        r"de\s+acordo\s+com\s+(?:o\s+|a\s+)?(?:estoque|disponibilidade)",
        r"enviad[oa]s?\s+conforme\s+(?:a\s+)?disponibilidade",
        r"sujeit[oa]s?\s+(?:à|a)\s+disponibilidade",
        # "Cor única" / "Cor do produto" → produto monocromático, sem variações
        r"\bcor\s+(?:[úu]nica|do\s+produto|fixa|principal)\b",
        r"\b(?:tamanho|modelo)\s+(?:[úu]nico|fixo|padr[ãa]o)\b",
    ]
    for dp in disclaimer_patterns:
        if re.search(dp, raw_description, re.IGNORECASE):
            return []

    # GATE 2: require PLURAL "cores"/"tamanhos"/"modelos" with "Disponível"
    # — singular "Disponível na cor X" is descriptive, NOT a variation
    patterns = [
        r"dispon[ií]ve[il]s?\s+(?:nas?|nos?|em|nas?\s+seguintes)?\s*(?:cores|tamanhos|modelos)[:\s]+([^.\n]+(?:\n[^.\n]+)*?)(?=\n\s*\n|$|\.|\bmedidas?\b|\bdimens|\bideal\b|\bpara\b\s+(?:setup|jogos|trabalho)|\bcaracter)",
        r"(?:cores|tamanhos|modelos)\s+dispon[ií]ve[il]s?[:\s]+([^.\n]+(?:\n[^.\n]+)*?)(?=\n\s*\n|$|\.|\bmedidas?\b|\bdimens|\bideal\b|\bcaracter)",
    ]
    body = ""
    for pat in patterns:
        m = re.search(pat, raw_description, re.IGNORECASE)
        if m:
            body = m.group(1)
            break
    if not body:
        return []

    # Split by hyphens, commas, semicolons, line breaks, AND coordinating conjunctions
    parts = re.split(r"\s*(?:[-,;\n]|\s+(?:e|ou)\s+)\s*", body, flags=re.IGNORECASE)
    out: List[str] = []
    for p in parts:
        p = p.strip(" -")
        if not p or len(p) > 30:
            continue
        # NOTE: we KEEP items marked as "(esgotado)" — variations with 0 stock should be
        # created in Bling with quantity=0 (per TotyShop manual). The status info is
        # extracted later by _parse_variation_quantities.
        # Remove parenthetical content (e.g. "(esgotado)" or "(5)") for the name itself
        p_clean = re.sub(r"\([^)]*\)", "", p).strip()
        if not p_clean or p_clean.lower() in {"e", "ou", "etc", "..."}:
            continue
        # FILTER: descriptive phrases ("Ideal Para Setups Temáticos" has 4 words)
        words = p_clean.split()
        if len(words) > 2:
            continue
        # FILTER: phrases that start with descriptive adjectives
        descriptive_starts = {
            "ideal", "para", "perfeito", "indicado", "recomendado", "compatível",
            "compatíve", "com", "sem", "voltado", "destinado",
        }
        if words and words[0].lower() in descriptive_starts:
            continue
        # Normalize case
        p_clean = " ".join(w.capitalize() for w in words)
        out.append(p_clean)
    seen = set()
    deduped = []
    for v in out:
        kl = v.lower()
        if kl not in seen:
            seen.add(kl)
            deduped.append(v)
    # If only ONE option was extracted, it's a single-color/size product — not a variation
    if len(deduped) <= 1:
        return []
    return deduped[:10]


async def _find_jonh_supplier_id() -> Optional[int]:
    """Find Bling supplier (contato) named JONH VARIEDADES. Cached after first lookup.
    Note: Bling's `pesquisa` param does NOT match multi-word queries verbatim, so we search
    by the unique single word "JONH" and filter the results client-side."""
    if _SUPPLIER_CACHE["id"]:
        return _SUPPLIER_CACHE["id"]
    try:
        resp = await bling_service.bling_request(
            "GET", "/contatos", params={"pesquisa": "JONH", "limite": 20}
        )
        if resp.status_code >= 400:
            return None
        items = (resp.json() or {}).get("data") or []
        for it in items:
            nome = (it.get("nome") or "").upper().strip()
            if "JONH" in nome and "VARIEDADES" in nome:
                _SUPPLIER_CACHE["id"] = it.get("id")
                _SUPPLIER_CACHE["name"] = it.get("nome")
                return _SUPPLIER_CACHE["id"]
    except Exception as e:
        await add_log("warning", f"Bling: falha ao buscar fornecedor JONH: {e}")
    return None


async def _llm_call(system: str, user_prompt: str) -> str:
    if not EMERGENT_LLM_KEY:
        raise RuntimeError("EMERGENT_LLM_KEY não configurada")
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"bling-enrich-{uuid.uuid4()}",
        system_message=system,
    ).with_model(LLM_PROVIDER, LLM_MODEL)
    response = await chat.send_message(UserMessage(text=user_prompt))
    return str(response).strip().strip('"').strip("'")


async def generate_short_description(raw_title: str, raw_description: str) -> str:
    user = (
        f"Título limpo: {raw_title}\n"
        f"Descrição original (do fornecedor):\n{raw_description or '(sem descrição original)'}\n\n"
        "Gere a descrição curta seguindo TODAS as regras do sistema. "
        "LEMBRE: separe os 3 a 5 parágrafos com duas quebras de linha (uma linha em branco entre eles)."
    )
    text = await _llm_call(SHORT_DESC_SYSTEM, user)
    text = _sanitize(text, preserve_newlines=True)
    text = _strip_emojis_and_special(text)
    return text[:2000]


async def generate_bullet_points(raw_title: str, raw_description: str) -> List[str]:
    user = (
        f"Título: {raw_title}\n"
        f"Descrição original: {raw_description or '(sem descrição)'}\n\n"
        "Gere EXATAMENTE 8 bullets."
    )
    raw = await _llm_call(BULLETS_SYSTEM, user)
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    # Normalize: ensure each line starts with "- " and ≤150 chars
    cleaned: List[str] = []
    for ln in lines:
        ln = ln.lstrip("•*·").strip()
        if not ln.startswith("-"):
            ln = "- " + ln
        ln = _sanitize(ln)
        ln = _strip_emojis_and_special(ln)
        if len(ln) > 150:
            ln = ln[:150].rstrip()
        cleaned.append(ln)
    # Pad or truncate to exactly 8
    while len(cleaned) < 8:
        cleaned.append("- Produto de qualidade pronto para entrega rápida")
    return cleaned[:8]


async def _list_bling_categories() -> List[dict]:
    """Fetch all product categories from Bling. Returns list of {id, descricao}."""
    cats: List[dict] = []
    pagina = 1
    while pagina < 20:
        resp = await bling_service.bling_request("GET", "/categorias/produtos", params={"pagina": pagina, "limite": 100})
        if resp.status_code >= 400:
            break
        body = resp.json()
        items = body.get("data") or []
        if not items:
            break
        for it in items:
            cats.append({"id": it.get("id"), "descricao": it.get("descricao", "")})
        if len(items) < 100:
            break
        pagina += 1
    return cats


async def _create_bling_category(name: str) -> Optional[int]:
    """Create a new product category in Bling. Returns new id or None."""
    resp = await bling_service.bling_request(
        "POST", "/categorias/produtos", json={"descricao": name}
    )
    if resp.status_code >= 400:
        await add_log("warning", f"Falha ao criar categoria '{name}' no Bling: {resp.status_code}")
        return None
    try:
        body = resp.json()
        return (body.get("data") or {}).get("id")
    except Exception:
        return None


async def pick_or_create_category(raw_title: str, raw_description: str) -> Optional[int]:
    """Use LLM to pick best Bling category from EXISTING ones; never create new categories.

    Per TotyShop manual: "É proibida a criação desordenada de novas categorias pela IA.
    O robô deve buscar termos correspondentes que já existam criados no Bling."
    """
    cats = await _list_bling_categories()
    cat_list = "\n".join(f"- id={c['id']}: {c['descricao']}" for c in cats[:150])
    user = (
        f"Produto: {raw_title}\n"
        f"Descrição: {(raw_description or '')[:300]}\n\n"
        f"Categorias disponíveis no Bling:\n{cat_list or '(nenhuma)'}\n\n"
        f"IMPORTANTE: você só pode escolher entre as categorias listadas acima. "
        f"NUNCA invente uma nova. Se nenhuma servir, responda category_id=null."
    )
    raw = await _llm_call(CATEGORY_SYSTEM, user)
    m = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
    json_str = m.group(0) if m else raw
    try:
        data = json.loads(json_str)
        cid = data.get("category_id")
        if cid:
            return int(cid)
    except Exception:
        await add_log("warning", f"Categoria: LLM retornou inválido — fallback keyword. Raw: {raw[:120]}")

    # Fallback keyword match against EXISTING categories only
    title_lower = (raw_title or "").lower()
    keyword_map = [
        ("Acessorios para Celular", ["controle gamer celular", "joystick celular", "gamepad celular", "manete celular"]),
        ("Pendrive", ["pendrive", "flash drive", "chaveiro 64gb", "chaveiro 32gb"]),
        ("Smartwatches", ["smartwatch", "smart watch"]),
        ("Relógios Digitais", ["relógio digital", "relogio digital"]),
        ("Câmeras de Segurança", ["câmera segurança", "camera seguranca", "babá eletrônica", "wifi 360"]),
        ("Soundbar Bluetooth", ["soundbar", "sound bar"]),
        ("Caixas de Som Bluetooth", ["caixa som", "caixa de som", "speaker bluetooth"]),
        ("Fones Bluetooth e TWS", ["fone bluetooth", "tws", "fone tws", "earbuds"]),
        ("Fones de Ouvido com Fio", ["fone ouvido", "fone intra"]),
        ("Headphone e Headset Bluetooth", ["headphone", "headset"]),
        ("Cabos USB-C (Tipo-C)", ["usb-c", "tipo-c", "type-c"]),
        ("Cabos V8 / Micro USB", ["micro usb", "v8", "cabo v8"]),
        ("Cabos Lightning / iOS", ["lightning", "ios cable"]),
        ("Carregadores de Parede Turbo", ["carregador parede", "carregador turbo"]),
        ("Carregadores Veiculares", ["carregador veicular", "carregador carro"]),
        ("Power Banks", ["power bank", "powerbank", "carregador portatil"]),
        ("Suportes Veiculares para Celular", ["suporte veicular", "suporte carro"]),
        ("Suportes para Celular", ["suporte celular", "suporte tablet"]),
        ("Controles Remotos Universais", ["controle remoto", "controle universal"]),
        ("Controles e Gamepads", ["gamepad", "controle gamer", "joystick"]),
        ("Mouse Sem Fio e Gamer", ["mouse"]),
        ("Teclados Bluetooth e Gamer", ["teclado"]),
        ("Antenas Digitais", ["antena"]),
        ("Drones e Acessórios", ["drone"]),
        ("Lanternas Táticas e Portáteis", ["lanterna"]),
        ("Caneca Copos e Garrafas", ["caneca", "copo térmico", "garrafa térmica"]),
        ("Camiseta", ["camiseta"]),
        ("Aparadores de Pelos", ["aparador", "depilador"]),
        ("Máquinas de Cortar Cabelo e Barba", ["maquina cortar", "máquina cortar", "barbeador", "cortador cabelo"]),
        ("Calculadoras e Papelaria", ["calculadora", "papelaria"]),
        ("Acessórios para Bicicleta e Ciclismo", ["bicicleta", "ciclismo"]),
        ("Eletrônicos", ["bluetooth", "wifi", "wireless", "led", "lcd", "adaptador", "stylus", "caneta touch"]),
        ("Casa e Decoração", ["organizador", "caixa porta"]),
        ("Beleza e Cuidado Pessoal", ["peeling", "ultrassônico", "cravos", "acne", "cabelo"]),
    ]
    for cat_name, kws in keyword_map:
        if any(k in title_lower for k in kws):
            existing = next((c for c in cats if (c.get("descricao") or "").lower() == cat_name.lower()), None)
            if existing:
                await add_log("info", f"Categoria (fallback): {existing['descricao']} (id={existing['id']})")
                return existing["id"]
            existing = next((c for c in cats if cat_name.lower() in (c.get("descricao") or "").lower()), None)
            if existing:
                return existing["id"]
    await add_log("info", "Nenhuma categoria existente combinou — produto ficará sem categoria (regra do manual).")
    return None


async def find_bling_product_by_sku(sku: str) -> Optional[dict]:
    """Find a Bling product by codigo (SKU). Retries to wait for JohnDrop→Bling sync."""
    for attempt in range(FIND_PRODUCT_MAX_ATTEMPTS):
        resp = await bling_service.bling_request("GET", "/produtos", params={"codigo": sku, "limite": 5})
        if resp.status_code < 400:
            body = resp.json()
            items = body.get("data") or []
            for it in items:
                if (it.get("codigo") or "").strip().upper() == sku.upper():
                    return it
        if attempt < FIND_PRODUCT_MAX_ATTEMPTS - 1:
            await asyncio.sleep(FIND_PRODUCT_DELAY_S)
    return None


_SUPPLIER_CACHE: dict = {"id": None, "name": "JONH VARIEDADES"}


async def update_bling_product(
    product_id: int,
    current: dict,
    payload: dict,
    johndrop_id: Optional[str] = None,
    cost: Optional[float] = None,
    variations: Optional[List[str]] = None,
    images: Optional[list] = None,
) -> bool:
    """PATCH the Bling product, sending ONLY the 7 fields the TotyShop manual asks
    us to change. Every other field (nome, código, preço, peso, dimensões, imagens,
    estoque, etc.) stays EXACTLY as it came from JohnDrop — no defaults, no zeroing.

    Fields we touch:
      1. descricaoCurta (sanitized + bullets)
      2. descricaoComplementar (8 bullets)
      3. marca = "Generico"
      4. condicao = 1 (Novo)
      5. tipoProducao = "T" (Terceiros)
      6. gtin = ""
      7. gtinEmbalagem = ""
      (+ categoria when LLM matched one, + fornecedor via separate endpoint)
    """
    parent_name = current.get("nome") or ""

    # Build a STRICTLY MINIMAL PATCH — never include nome, código, preço, peso,
    # dimensões, imagens, estoque, formato, etc.
    patch_payload: dict = {
        "marca": "Generico",
        "condicao": 1,
        "tipoProducao": "T",
        "gtin": "",
        "gtinEmbalagem": "",
    }
    if payload.get("descricaoCurta"):
        patch_payload["descricaoCurta"] = payload["descricaoCurta"]
    if payload.get("descricaoComplementar"):
        patch_payload["descricaoComplementar"] = payload["descricaoComplementar"]
    if payload.get("categoria"):
        patch_payload["categoria"] = payload["categoria"]

    # Supplier collected here, applied AFTER the patch succeeds (separate endpoint)
    supplier_id = await _find_jonh_supplier_id()
    supplier_entry: Optional[dict] = None
    if supplier_id:
        supplier_entry = {
            "produto": {"id": product_id},
            "fornecedor": {"id": supplier_id},
            "descricao": parent_name[:120],
            "padrao": True,
            "garantia": 0,
        }
        if johndrop_id:
            supplier_entry["codigo"] = str(johndrop_id)
        if cost and cost > 0:
            supplier_entry["precoCusto"] = round(float(cost), 2)
            supplier_entry["precoCompra"] = round(float(cost), 2)

    # Send PATCH — this is parcial, never overwrites unspecified fields
    resp = await bling_service.bling_request(
        "PATCH", f"/produtos/{product_id}", json=patch_payload,
    )
    if resp.status_code >= 400:
        await add_log("warning", f"Bling {product_id}: HTTP {resp.status_code} — {resp.text[:800]}")
        return False

    # AFTER successful PATCH: link supplier (JONH VARIEDADES) via dedicated endpoint.
    if supplier_entry:
        try:
            sr = await bling_service.bling_request(
                "POST", "/produtos/fornecedores", json=supplier_entry,
            )
            if sr.status_code >= 400 and "j\u00e1" not in sr.text.lower():
                await add_log(
                    "info",
                    f"Bling fornecedor {product_id}: HTTP {sr.status_code} — {sr.text[:200]}",
                )
        except Exception as e:
            await add_log("info", f"Bling fornecedor {product_id}: {e}")

    return True


async def _fetch_bling_product_full(product_id: int) -> Optional[dict]:
    resp = await bling_service.bling_request("GET", f"/produtos/{product_id}")
    if resp.status_code >= 400:
        return None
    body = resp.json()
    return body.get("data")


async def _save_log(sku: str, status: str, message: str, **fields) -> None:
    doc = {
        "id": str(uuid.uuid4()),
        "sku": sku,
        "status": status,
        "message": message,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    await db.bling_enrichment_logs.insert_one(doc)


async def _upsert_enriched_cache(product_id: int, sku: str) -> None:
    """Cache product metadata after successful enrichment so /ad/products is fast.

    Without this cache, listing enriched products requires GET /produtos/{id}
    for each item (N+1 queries against Bling). With the cache, the listing
    becomes a single MongoDB query.
    """
    try:
        full = await _fetch_bling_product_full(product_id)
        if not full:
            return
        # Skip variation children — only cache parents/simples
        if (full.get("produtoPai") or {}).get("id"):
            return
        nome = (full.get("nome") or "").strip()
        if re.search(r"\b(Cor|Tamanho|Modelo|Voltagem):", nome):
            return

        img_url = ""
        midia = full.get("midia") or {}
        imgs = midia.get("imagens") or {}
        for img in (imgs.get("internas") or []):
            link = img.get("link") or img.get("linkMiniatura") or ""
            if link:
                img_url = link
                break
        if not img_url:
            for img in (imgs.get("externas") or []):
                link = img.get("link") or ""
                if link:
                    img_url = link
                    break

        await db.bling_enriched_cache.update_one(
            {"product_id": product_id},
            {"$set": {
                "product_id": product_id,
                "sku": (full.get("codigo") or sku or "").strip(),
                "nome": nome,
                "preco": full.get("preco") or 0,
                "image_url": img_url,
                "descricao_curta": (full.get("descricaoCurta") or "")[:200],
                "marca": full.get("marca") or "",
                "enriched_at": datetime.now(timezone.utc).isoformat(),
            }},
            upsert=True,
        )
    except Exception as e:
        await add_log("info", f"Cache enriched falhou para pid={product_id}: {e}")


async def _wait_for_johndrop_sync(
    product_id: int, sku: str,
    max_attempts: int = 12, delay_s: float = 15.0,
) -> dict:
    """Wait until JohnDrop finishes syncing the product into Bling.

    "Done syncing" = the product has either:
      • estoque saldoVirtualTotal > 0  (stock arrived), OR
      • at least 1 image in midia.imagens.internas/externas  (images arrived)

    Either signal means JohnDrop's async sync has reached this product and
    it's safe to convert to formato=V (which zeros simple stock) and to PATCH
    images onto variation children.

    Default budget: 12 × 15s = 180s (3 minutes), enough for most syncs.
    Returns the latest full product dict regardless of timeout.
    """
    import asyncio as _asyncio

    async def _read_full() -> Optional[dict]:
        try:
            r = await bling_service.bling_request("GET", f"/produtos/{product_id}")
            if r.status_code >= 400:
                return None
            return (r.json() or {}).get("data") or None
        except Exception:
            return None

    async def _read_saldo() -> int:
        try:
            r = await bling_service.bling_request(
                "GET", "/estoques/saldos", params={"idsProdutos[]": product_id},
            )
            if r.status_code >= 400:
                return 0
            rows = (r.json() or {}).get("data") or []
            if not rows:
                return 0
            v = rows[0].get("saldoVirtualTotal") or rows[0].get("saldoFisicoTotal") or 0
            return int(v) if v else 0
        except Exception:
            return 0

    def _img_count(p: Optional[dict]) -> int:
        if not p:
            return 0
        imgs = (p.get("midia") or {}).get("imagens") or {}
        return len(imgs.get("internas") or []) + len(imgs.get("externas") or [])

    # First read — many products already have both ready when we get here
    full = await _read_full()
    saldo = await _read_saldo()
    imgs = _img_count(full)
    if saldo > 0 or imgs > 0:
        await add_log(
            "info",
            f"Sync JohnDrop→Bling completo para {sku}: estoque={saldo}, imagens={imgs}",
        )
        return full or {}

    # Poll loop
    for attempt in range(1, max_attempts + 1):
        await add_log(
            "info",
            f"Aguardando sync JohnDrop→Bling de {sku} "
            f"(tentativa {attempt}/{max_attempts}, espera {delay_s:.0f}s) — "
            f"estoque={saldo}, imagens={imgs}",
        )
        await _asyncio.sleep(delay_s)
        full = await _read_full() or full
        saldo = await _read_saldo()
        imgs = _img_count(full)
        if saldo > 0 or imgs > 0:
            await add_log(
                "success",
                f"Sync JohnDrop→Bling completou para {sku} após {attempt * delay_s:.0f}s: "
                f"estoque={saldo}, imagens={imgs}",
            )
            return full or {}

    await add_log(
        "warning",
        f"Sync JohnDrop→Bling NÃO completou para {sku} após "
        f"{max_attempts * delay_s:.0f}s (estoque={saldo}, imagens={imgs}). "
        "Continuando enriquecimento mesmo assim — variações podem ficar com 0 unidades.",
    )
    return full or {}


async def enrich_product_by_sku(
    sku: str,
    raw_title: str,
    raw_description: str = "",
    johndrop_id: Optional[str] = None,
    cost: Optional[float] = None,
    images: Optional[list] = None,
) -> dict:
    """Main enrichment flow. Idempotent — finds product, enriches it, logs result."""
    sku = (sku or "").strip()
    if not sku:
        return {"ok": False, "reason": "sku vazio"}

    await add_log("info", f"Bling: iniciando enriquecimento para SKU {sku}")

    try:
        product = await find_bling_product_by_sku(sku)
    except Exception as e:
        await add_log("error", f"Bling: erro ao buscar produto {sku}: {e}")
        await _save_log(sku, "error", f"busca falhou: {e}")
        return {"ok": False, "reason": str(e)}

    if not product:
        msg = f"Produto {sku} não encontrado no Bling após {FIND_PRODUCT_MAX_ATTEMPTS} tentativas (~{FIND_PRODUCT_MAX_ATTEMPTS * FIND_PRODUCT_DELAY_S}s)"
        await add_log("warning", msg)
        await _save_log(sku, "not_found", msg)
        return {"ok": False, "reason": "not_found"}

    product_id = product.get("id")

    # Wait for JohnDrop async sync to bring in stock + images.
    # Without this, _set_children_stock reads saldo=0 and variations end up with
    # 0 units; and _copy_images_to_children has nothing to copy to children.
    full_after_sync = await _wait_for_johndrop_sync(product_id, sku)
    # If sync brought in images, prefer those over the bot-scraped URLs (Bling
    # versions are more stable than the JohnDrop S3 presigned URLs).
    bling_imgs = (full_after_sync.get("midia") or {}).get("imagens") or {}
    bling_internal = [(i.get("link") or "") for i in (bling_imgs.get("internas") or [])]
    bling_external = [(i.get("link") or "") for i in (bling_imgs.get("externas") or [])]
    bling_urls = [u for u in (bling_internal + bling_external) if u]
    if bling_urls:
        images = bling_urls

    try:
        short_desc, bullets, category_id = await asyncio.gather(
            generate_short_description(raw_title, raw_description),
            generate_bullet_points(raw_title, raw_description),
            pick_or_create_category(raw_title, raw_description),
        )
    except Exception as e:
        await add_log("error", f"Bling: LLM falhou para {sku}: {e}")
        await _save_log(sku, "error", f"LLM: {e}")
        return {"ok": False, "reason": str(e)}

    # Bling renders HTML in description fields — convert newlines to <br>
    short_desc_html = short_desc.replace("\n\n", "<br><br>").replace("\n", "<br>")
    complementar_html = "<br>".join(bullets)
    payload: dict = {
        "descricaoCurta": short_desc_html,
        "descricaoComplementar": complementar_html,
    }
    if category_id:
        payload["categoria"] = {"id": category_id}

    try:
        # Use the post-sync full product instead of an extra GET (the wait
        # already returned the latest version).
        full = full_after_sync or await _fetch_bling_product_full(product_id) or product
        variations = _parse_variations(raw_description)
        if variations:
            await add_log("info", f"Variações detectadas para {sku}: {', '.join(variations)}")
        ok = await update_bling_product(
            product_id, full, payload,
            johndrop_id=johndrop_id, cost=cost, variations=variations or None,
            images=images,
        )
    except Exception as e:
        await add_log("error", f"Bling: PUT falhou para {sku}: {e}")
        await _save_log(sku, "error", f"PUT: {e}", product_id=product_id)
        return {"ok": False, "reason": str(e)}

    if ok:
        await add_log("success", f"Bling enriquecido: {sku} (cat={category_id})")
        await _save_log(
            sku, "success", "enriquecido",
            product_id=product_id,
            short_description=short_desc,
            bullets=bullets,
            category_id=category_id,
            johndrop_id=johndrop_id,
            cost=cost,
            variations=variations,
        )
        # Cache for fast /ad/products listing
        await _upsert_enriched_cache(product_id, sku)
        # AFTER successful enrichment: try to create variations (color/size).
        # This NEVER raises — if Bling rejects, only a warning is logged.
        variations_created = 0
        if variations:
            try:
                import bling_variations
                # Re-fetch product to get latest state (after the PUT just done)
                latest = await _fetch_bling_product_full(product_id) or full
                # Parse explicit per-variation quantities from description (e.g. "Rosa: 5")
                explicit_qty = _parse_variation_quantities(raw_description, variations)
                if explicit_qty:
                    await add_log(
                        "info",
                        f"Quantidades específicas detectadas: {explicit_qty}",
                    )
                result = await bling_variations.create_variations(
                    product_id, latest, variations, total_stock=0,
                    parent_images=images, explicit_quantities=explicit_qty or None,
                )
                variations_created = result.get("created", 0)
            except Exception as e:
                await add_log("warning", f"Variações pós-enrich falharam para {sku}: {e}")
        return {
            "ok": True,
            "product_id": product_id,
            "category_id": category_id,
            "variations_detected": len(variations) if variations else 0,
            "variations_created": variations_created,
        }

    await add_log("warning", f"Bling: PUT retornou erro para {sku}")
    await _save_log(sku, "error", "PUT retornou status >= 400", product_id=product_id)
    return {"ok": False, "reason": "put_failed"}


async def get_enrichment_logs(limit: int = 100) -> list:
    cur = db.bling_enrichment_logs.find({}, {"_id": 0}).sort("created_at", -1).limit(limit)
    return await cur.to_list(limit)


async def get_enrichment_stats() -> dict:
    total = await db.bling_enrichment_logs.count_documents({})
    success = await db.bling_enrichment_logs.count_documents({"status": "success"})
    errors = await db.bling_enrichment_logs.count_documents({"status": "error"})
    not_found = await db.bling_enrichment_logs.count_documents({"status": "not_found"})
    return {"total": total, "success": success, "errors": errors, "not_found": not_found}
