"""Leitura da página de produto do Dropper (`/cliente/produtos/<id>`).

Duas camadas, de propósito:

* :func:`parse_product_text` — função PURA que recebe o texto da página e devolve
  um :class:`~bling_sheet_export.DropperProduct`. É testável sem navegador e é
  onde ficam todas as regras.
* :func:`read_product_page` — usa Playwright só para abrir a URL e capturar o
  texto/imagens; delega o resto para a função pura.

Estratégia de extração: rótulo -> valor no texto da página. É mais resistente a
mudanças de layout do que seletores CSS (que quebraram várias vezes durante o
desenvolvimento).

Campos lidos (conforme print de referência do usuário)::

    ProdDPF2589                                  -> código
    Kit 12 Tesouras Multiuso 25 cm ... Vonder     -> título
    R$ 190,44                                     -> custo
    Estoque 7
    Enviado por: Dropping                         -> fornecedor
    Marca: VONDER
    Categoria: FERAMENTAS MANUAIS
    Unidade: KIT
    EAN: 2003990403811
    NCM: 82130000
    DESCRIÇÃO ... (texto livre)                   -> peso/dimensões/código fornecedor
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from bling_sheet_export import DropperProduct, DropperVariation

# --------------------------------------------------------------------------- #
# Padrões
# --------------------------------------------------------------------------- #

_PRODUCT_CODE_RE = re.compile(r"\bProd(?:DPF|_?[A-Z]{0,4})\d{3,}\b", re.IGNORECASE)
_PRICE_RE = re.compile(r"R\$\s*([0-9]{1,6}(?:\.[0-9]{3})*(?:,[0-9]{2})?)")
_NCM_RE = re.compile(r"\bNCM\b\s*[:\s]*([0-9]{8}|[0-9]{4}\.[0-9]{2}\.[0-9]{2})", re.IGNORECASE)
_EAN_RE = re.compile(r"\bEAN\b\s*[:\s]*([0-9]{8,14})", re.IGNORECASE)
_STOCK_RE = re.compile(r"ESTOQUE\s*[:\s]*([0-9]{1,6})", re.IGNORECASE)

#: rótulos do bloco "Informações do produto" -> chave interna
_LABELS = {
    "marca": "brand",
    "categoria": "category",
    "unidade": "unit",
    "material": "material",
    "classe": "klass",
    "enviado por": "supplier",
}

#: linhas que nunca são o título do produto
_NOT_TITLE = re.compile(
    r"^(dispon[ií]vel|indispon[ií]vel|anunciar|descri[cç][aã]o|"
    r"informa[cç][oõ]es do produto|seu custo de compra|estoque|"
    r"onde voc[eê] vende|mercado livre|tiktok shop|shopee|"
    r"classe\s|material\s|unidade\s)\b",
    re.IGNORECASE,
)


def _lines(text: str) -> List[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def _label_value(lines: List[str], label: str) -> Optional[str]:
    """Procura ``Rótulo`` seguido do valor — na mesma linha ou na seguinte.

    Aceita os dois layouts vistos no Dropper::

        "Marca: VONDER"          (mesma linha)
        "Marca" / "VONDER"       (rótulo em cima, valor embaixo)
    """
    label_low = label.lower()
    for i, line in enumerate(lines):
        low = line.lower()
        if not low.startswith(label_low):
            continue
        # mesmo formato "Rótulo: valor"
        if ":" in line:
            value = line.split(":", 1)[1].strip()
            if value:
                return value
            continue
        # rótulo sozinho na linha -> valor é a próxima
        rest = line[len(label):].strip(" \t-–—")
        if rest:
            return rest
        if i + 1 < len(lines):
            candidate = lines[i + 1].strip()
            if candidate and len(candidate) < 80 and not _NOT_TITLE.match(candidate):
                return candidate
    return None


def _find_title(lines: List[str]) -> str:
    """O título é a linha "de nome de produto" antes do bloco de custo."""
    stop = len(lines)
    for i, line in enumerate(lines):
        if line.lower().startswith("seu custo de compra") or line.lower().startswith("informa"):
            stop = i
            break

    best = ""
    for line in lines[:stop]:
        if _NOT_TITLE.match(line):
            continue
        if _PRODUCT_CODE_RE.fullmatch(line.replace(" ", "")):
            continue
        if len(line) < 8:
            continue
        if "·" in line:  # "FERAMENTAS MANUAIS · Classe Bronze"
            continue
        if re.fullmatch(r"[0-9.,\s]+", line):
            continue
        if len(line) > len(best):
            best = line
    return best


def _parse_cost(text: str) -> Optional[float]:
    """Primeiro valor em R$ do bloco de custo (ou o primeiro da página)."""
    match = _PRICE_RE.search(text or "")
    if not match:
        return None
    raw = match.group(1).replace(".", "").replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        return None
    return round(value, 2) if value > 0 else None


def _parse_description(lines: List[str]) -> str:
    """Texto após o cabeçalho DESCRIÇÃO."""
    start = None
    for i, line in enumerate(lines):
        if re.fullmatch(r"descri[cç][aã]o", line, re.IGNORECASE):
            start = i + 1
            break
    if start is None:
        return ""
    return "\n".join(lines[start:])


def parse_product_text(text: str, url: str = "", images: Optional[List[str]] = None) -> DropperProduct:
    """Converte o texto da página do produto em :class:`DropperProduct`."""
    lines = _lines(text)

    code_match = _PRODUCT_CODE_RE.search(text or "")
    code = code_match.group(0) if code_match else ""
    if not code and url:
        # último recurso: o id da URL
        code = (url.rstrip("/").rsplit("/", 1)[-1])[:24]

    title = _find_title(lines)

    supplier = _label_value(lines, "enviado por")
    brand = _label_value(lines, "marca")
    category = _label_value(lines, "categoria")
    unit = _label_value(lines, "unidade")

    ncm = None
    m = _NCM_RE.search(text or "")
    if m:
        ncm = m.group(1)
    ean = None
    m = _EAN_RE.search(text or "")
    if m:
        ean = m.group(1)

    stock = None
    m = _STOCK_RE.search(text or "")
    if m:
        try:
            stock = int(m.group(1))
        except ValueError:
            stock = None

    description = _parse_description(lines)

    return DropperProduct(
        code=code,
        title=title,
        cost=_parse_cost(text),
        ncm=ncm,
        ean=ean,
        brand=brand,
        category=category,
        unit=unit,
        supplier=supplier,
        stock=stock,
        description=description,
        images=list(images or []),
        variations=[],
    )


def parse_variations(blocks: List[Dict[str, str]]) -> List[DropperVariation]:
    """Converte blocos ``{"name": "Amarelo", "code": "...", "images": [...]}``."""
    out: List[DropperVariation] = []
    for block in blocks or []:
        name = (block.get("name") or "").strip()
        if not name:
            continue
        out.append(
            DropperVariation(
                name=name,
                code=(block.get("code") or "").strip() or None,
                images=[u for u in (block.get("images") or []) if u],
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Camada Playwright
# --------------------------------------------------------------------------- #

#: Seletores de imagem (reaproveita a lógica já validada no robô).
_IMAGE_JS = """
() => {
    const out = new Set();
    const isProductLike = (src) => {
        if (!src) return false;
        if (src.startsWith('data:') || src.startsWith('blob:')) return false;
        const low = src.toLowerCase();
        if (low.includes('/logo') || low.includes('/icon') || low.includes('favicon')) return false;
        if (low.includes('/avatar') || low.includes('/profile')) return false;
        if (low.includes('placeholder') || low.includes('default')) return false;
        if (low.endsWith('.svg')) return false;
        return true;
    };
    for (const img of document.querySelectorAll('img')) {
        const src = img.currentSrc || img.src || '';
        if (isProductLike(src)) out.add(src);
    }
    return Array.from(out);
}
"""


async def read_product_page(page) -> DropperProduct:
    """Lê a página atual (já aberta) do produto no Dropper."""
    await page.wait_for_timeout(1500)
    try:
        text = await page.inner_text("body")
    except Exception:
        text = ""
    try:
        images = await page.evaluate(_IMAGE_JS)
    except Exception:
        images = []
    product = parse_product_text(text, url=page.url or "", images=images or [])
    return product
