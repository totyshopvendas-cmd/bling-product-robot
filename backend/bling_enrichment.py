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
FIND_PRODUCT_MAX_ATTEMPTS = 6
FIND_PRODUCT_DELAY_S = 10

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
    "Você é um especialista em SEO para marketplaces brasileiros (Mercado Livre, Shopee, Amazon BR). "
    "Reescreva descrições de produtos seguindo regras estritas. "
    "REGRAS OBRIGATÓRIAS:\n"
    "1. NUNCA use nomes de marca (XLS, Kapbom, Inova, Altomex, Eletromex, etc).\n"
    "2. NUNCA inclua códigos EAN/GTIN (sequências de 8-14 dígitos).\n"
    "3. Use APENAS estes recursos de formatação:\n"
    "   - Negrito via tags HTML <b>...</b>\n"
    "   - Hífen (-)\n"
    "   PROIBIDO usar: emojis, asteriscos, listas com bullets/números/símbolos, caracteres especiais.\n"
    "4. ESTRUTURA EM PARÁGRAFOS: divida o texto em 3 a 5 parágrafos curtos.\n"
    "   Separe os parágrafos com DUAS quebras de linha (\\n\\n).\n"
    "   Cada parágrafo deve ter um foco diferente:\n"
    "   - Parágrafo 1: visão geral / proposta de valor do produto\n"
    "   - Parágrafo 2: características técnicas principais\n"
    "   - Parágrafo 3: benefícios para o uso diário / situações de uso\n"
    "   - Parágrafo 4: VARIAÇÕES (se houver — cores, tamanhos, modelos — destaque em <b>...</b>)\n"
    "   - Parágrafo 5 (opcional): garantia de qualidade / chamado para ação\n"
    "5. Foco SEO: use palavras-chave que compradores buscam (ex: 'portátil', 'recarregável', 'sem fio').\n"
    "6. Texto total entre 400 e 900 caracteres. Linguagem persuasiva mas natural.\n"
    "Responda APENAS com o texto da descrição em parágrafos. Sem aspas, sem comentários, sem prefixos."
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


def _parse_variations(raw_description: str) -> List[str]:
    """Extract variation names from descriptions like 'Disponível nas cores: -Rosa -Verde -Azul'.
    Returns list of variation names in original case."""
    if not raw_description:
        return []
    # Look for "Disponível nas cores:" or "Disponivel nos tamanhos:" patterns
    pattern = re.compile(
        r"dispon[ií]ve[il]\s+(?:nas?|nos?)\s+(?:cores?|tamanhos?|modelos?)[:\s]+([^.\n]+)",
        re.IGNORECASE,
    )
    m = pattern.search(raw_description)
    if not m:
        return []
    body = m.group(1)
    # Split by hyphen-prefixed items or commas
    parts = re.split(r"\s*[-,;]\s*", body)
    out: List[str] = []
    for p in parts:
        p = p.strip(" -")
        if not p or len(p) > 30:
            continue
        # Skip generic words
        if p.lower() in {"e", "ou", "etc", "...", ""}:
            continue
        out.append(p)
    # Dedupe preserving order
    seen = set()
    deduped = []
    for v in out:
        kl = v.lower()
        if kl not in seen:
            seen.add(kl)
            deduped.append(v)
    return deduped[:10]  # cap at 10 variations


async def _find_jonh_supplier_id() -> Optional[int]:
    """Find Bling supplier (contato) named JONH VARIEDADES. Cached after first lookup."""
    if _SUPPLIER_CACHE["id"]:
        return _SUPPLIER_CACHE["id"]
    try:
        resp = await bling_service.bling_request(
            "GET", "/contatos", params={"pesquisa": "JONH VARIEDADES", "limite": 10}
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
    """Use LLM to pick best Bling category; fallback to keyword match; create new one if needed."""
    cats = await _list_bling_categories()
    cat_list = "\n".join(f"- id={c['id']}: {c['descricao']}" for c in cats[:150])
    user = (
        f"Produto: {raw_title}\n"
        f"Descrição: {(raw_description or '')[:300]}\n\n"
        f"Categorias disponíveis no Bling:\n{cat_list or '(nenhuma)'}"
    )
    raw = await _llm_call(CATEGORY_SYSTEM, user)
    cid = None
    # Try to extract JSON object even if there's prose around it
    m = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
    json_str = m.group(0) if m else raw
    try:
        data = json.loads(json_str)
        cid = data.get("category_id")
        new_name = data.get("category_name")
        if cid:
            return int(cid)
        if new_name:
            created = await _create_bling_category(new_name)
            if created:
                return created
    except Exception:
        await add_log("warning", f"Categoria: LLM retornou inválido — fallback keyword. Raw: {raw[:120]}")

    # Fallback keyword match against existing categories — covers the user's 132 categories
    title_lower = (raw_title or "").lower()
    keyword_map = [
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
            created = await _create_bling_category(cat_name)
            if created:
                return created
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
) -> bool:
    """Bling v3 requires a FULL product on PUT. Merge new fields + optional variations."""
    parent_sku = current.get("codigo") or ""
    parent_name = current.get("nome") or ""
    parent_price = current.get("preco") or 0
    has_variations = bool(variations)

    merged = {
        "nome": parent_name,
        "codigo": parent_sku,
        "preco": parent_price,
        "tipo": current.get("tipo") or "P",
        "situacao": current.get("situacao") or "A",
        "formato": "V" if has_variations else (current.get("formato") or "S"),
        "descricaoCurta": payload.get("descricaoCurta", current.get("descricaoCurta", "")),
        "descricaoComplementar": payload.get(
            "descricaoComplementar", current.get("descricaoComplementar", "")
        ),
        "marca": "Generica",
        "condicao": 1,
        "gtin": "",
        "gtinEmbalagem": "",
    }
    if payload.get("categoria"):
        merged["categoria"] = payload["categoria"]
    elif current.get("categoria"):
        merged["categoria"] = current["categoria"]

    supplier_id = await _find_jonh_supplier_id()
    if supplier_id:
        fornecedor_entry: dict = {
            "fornecedor": {"id": supplier_id},
            "descricao": parent_name,
            "padrao": True,
        }
        if johndrop_id:
            fornecedor_entry["codigo"] = str(johndrop_id)
        if cost and cost > 0:
            fornecedor_entry["precoCusto"] = round(float(cost), 2)
            fornecedor_entry["precoCompra"] = round(float(cost), 2)
        merged["fornecedores"] = [fornecedor_entry]

    # If parent already has variations registered, skip variation insertion to avoid duplicates
    existing_var_codes = set()
    for v in (current.get("variacoes") or []):
        code = (v.get("codigo") or "").strip().upper()
        if code:
            existing_var_codes.add(code)

    new_variations = []
    if has_variations:
        for v in variations:
            code = f"{parent_sku}-{_abbreviate_variation(v)}".upper()
            if code in existing_var_codes:
                continue
            new_variations.append({
                "codigo": code,
                "preco": float(parent_price) if parent_price else 0.0,
                "situacao": "A",
                "variacao": {
                    "nome": v,
                    "produtoPai": {"cloneInfo": True},
                },
            })

    if new_variations:
        merged["formato"] = "V"
        # Preserve existing variations + add new ones
        existing_payload = []
        for v in (current.get("variacoes") or []):
            existing_payload.append({
                "id": v.get("id"),
                "codigo": v.get("codigo"),
                "preco": v.get("preco") or float(parent_price) if parent_price else 0.0,
                "situacao": v.get("situacao") or "A",
                "variacao": v.get("variacao") or {},
            })
        merged["variacoes"] = existing_payload + new_variations
    elif existing_var_codes:
        # Product already has variations — re-send them as-is so Bling validates
        merged["formato"] = "V"
        merged["variacoes"] = [
            {
                "id": v.get("id"),
                "codigo": v.get("codigo"),
                "preco": v.get("preco") or float(parent_price) if parent_price else 0.0,
                "situacao": v.get("situacao") or "A",
                "variacao": v.get("variacao") or {},
            }
            for v in (current.get("variacoes") or [])
        ]
    else:
        merged["formato"] = current.get("formato") or "S"
        merged.pop("variacoes", None)

    merged = {k: v for k, v in merged.items() if v is not None}
    resp = await bling_service.bling_request("PUT", f"/produtos/{product_id}", json=merged)
    if resp.status_code >= 400:
        await add_log("warning", f"Bling PUT {product_id}: HTTP {resp.status_code} — {resp.text[:800]}")
        return False
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


async def enrich_product_by_sku(
    sku: str,
    raw_title: str,
    raw_description: str = "",
    johndrop_id: Optional[str] = None,
    cost: Optional[float] = None,
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
        full = await _fetch_bling_product_full(product_id) or product
        variations = _parse_variations(raw_description)
        if variations:
            await add_log("info", f"Variações detectadas para {sku}: {', '.join(variations)}")
        ok = await update_bling_product(
            product_id, full, payload,
            johndrop_id=johndrop_id, cost=cost, variations=variations or None
        )
    except Exception as e:
        await add_log("error", f"Bling: PUT falhou para {sku}: {e}")
        await _save_log(sku, "error", f"PUT: {e}", product_id=product_id)
        return {"ok": False, "reason": str(e)}

    if ok:
        await add_log("success", f"Bling enriquecido: {sku} (cat={category_id}, variações={len(variations) if variations else 0})")
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
        return {"ok": True, "product_id": product_id, "category_id": category_id, "variations_count": len(variations) if variations else 0}

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
