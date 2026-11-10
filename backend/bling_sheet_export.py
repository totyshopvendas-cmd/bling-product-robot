"""Gera a planilha de importação de produtos do Bling a partir de produtos do Dropper.

Este módulo é PURO: não acessa rede, banco nem navegador. Recebe os dados já
lidos do Dropper (`DropperProduct`) e devolve as linhas no formato exato que o
Bling espera no arquivo de importação.

Formato confirmado com o arquivo modelo real do usuário:
  - 59 colunas, na ordem de `BLING_COLUMNS`
  - separador ``;``
  - UTF-8 **com BOM** (o Excel/Bling precisam do BOM para acentuação)
  - decimais com VÍRGULA (``0,108``), preços sem "R$" (``52`` ou ``52,90``)

Regras de preenchimento (acordadas com o usuário):
  1. campo explícito do Dropper -> usa direto
  2. não achou -> procura na descrição do produto (ex.: "Peso: 108 g")
  3. ainda falta -> a IA extrai do texto livre
  4. não deu -> deixa VAZIO e registra no relatório de revisão

Campos fiscais (NCM, CEST, EAN) NUNCA são inventados: se não vierem do
Dropper, ficam vazios e aparecem no relatório. NCM errado gera problema
fiscal; campo vazio o usuário preenche em segundos.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- #
# Constantes do formato Bling
# --------------------------------------------------------------------------- #

#: As 59 colunas, na ordem exata do arquivo de importação do Bling.
BLING_COLUMNS: Tuple[str, ...] = (
    "ID",
    "Código",
    "Descrição",
    "Unidade",
    "NCM",
    "Origem",
    "Preço",
    "Valor IPI fixo",
    "Observações",
    "Situação",
    "Estoque",
    "Preço de custo",
    "Cód. no fornecedor",
    "Fornecedor",
    "Localização",
    "Estoque máximo",
    "Estoque mínimo",
    "Peso líquido (Kg)",
    "Peso bruto (Kg)",
    "GTIN/EAN",
    "GTIN/EAN da Embalagem",
    "Largura do produto",
    "Altura do Produto",
    "Profundidade do produto",
    "Data Validade",
    "Descrição do Produto no Fornecedor",
    "Descrição Complementar",
    "Itens p/ caixa",
    "Produto Variação",
    "Tipo Produção",
    "Classe de enquadramento do IPI",
    "Código na Lista de Serviços",
    "Tipo do item",
    "Grupo de Tags/Tags",
    "Tributos",
    "Código Pai",
    "Código Integração",
    "Grupo de produtos",
    "Marca",
    "CEST",
    "Volumes",
    "Descrição Curta",
    "Cross-Docking",
    "URL Imagens Externas",
    "Link Externo",
    "Meses Garantia no Fornecedor",
    "Clonar dados do pai",
    "Condição do Produto",
    "Frete Grátis",
    "Número FCI",
    "Vídeo",
    "Departamento",
    "Unidade de Medida",
    "Preço de Compra",
    "Valor base ICMS ST para retenção",
    "Valor ICMS ST para retenção",
    "Valor ICMS próprio do substituto",
    "Categoria do produto",
    "Informações Adicionais",
)

CSV_DELIMITER = ";"
CSV_ENCODING = "utf-8-sig"  # utf-8 COM BOM

#: Valores fixos observados no arquivo modelo real do usuário.
DEFAULTS: Dict[str, str] = {
    "Origem": "0",
    "Valor IPI fixo": "0",
    "Situação": "Ativo",
    "Estoque máximo": "0",
    "Estoque mínimo": "0",
    "Itens p/ caixa": "0",
    "Tipo Produção": "Terceiros",
    "Tributos": "SIM",
    "Código Integração": "0",
    "Volumes": "1",
    "Cross-Docking": "0",
    "Meses Garantia no Fornecedor": "0",
    "Condição do Produto": "NOVO",
    "Frete Grátis": "NÃO",
    "Unidade de Medida": "Centímetro",
}

#: Campos cujo valor é fiscal/tributário e NUNCA pode ser inventado.
FISCAL_COLUMNS: Tuple[str, ...] = ("NCM", "CEST", "GTIN/EAN", "GTIN/EAN da Embalagem")

#: Ordem de preferência dos campos de origem na planilha (usada no relatório).
ORIGIN_LABEL = {
    "campo": "campo",
    "descricao": "descricao",
    "ia": "IA",
    "tabela": "tabela",
    "fixo": "fixo",
}


# --------------------------------------------------------------------------- #
# Modelo de dados
# --------------------------------------------------------------------------- #


@dataclass
class DropperVariation:
    """Uma variação (cor/tamanho) de um produto do Dropper."""

    name: str                       # "Amarelo", "Rosa Coelho"
    code: Optional[str] = None      # "HM677-3-AM" (código próprio da variação)
    images: List[str] = field(default_factory=list)


@dataclass
class DropperProduct:
    """Produto lido da página do Dropper, já normalizado."""

    code: str                                   # "ProdDPF2589"
    title: str                                  # "Kit 12 Tesouras Multiuso ... Vonder"
    cost: Optional[float] = None                # 190.44
    ncm: Optional[str] = None                   # "82130000"
    ean: Optional[str] = None                   # "2003990403811"
    brand: Optional[str] = None                 # "VONDER"
    category: Optional[str] = None              # "FERAMENTAS MANUAIS"
    unit: Optional[str] = None                  # "KIT"
    supplier: Optional[str] = None              # "Dropping"
    stock: Optional[int] = None                 # 7
    description: str = ""                       # texto completo da descrição
    images: List[str] = field(default_factory=list)
    variations: List[DropperVariation] = field(default_factory=list)
    weight_kg: Optional[float] = None           # 0.108
    dimensions: Dict[str, float] = field(default_factory=dict)  # largura/altura/profundidade
    #: proveniência de cada campo, para o relatório de revisão
    sources: Dict[str, str] = field(default_factory=dict)


@dataclass
class SheetRow:
    """Resultado: uma linha da planilha + a proveniência de cada campo."""

    values: Dict[str, str]
    sources: Dict[str, str] = field(default_factory=dict)
    kind: str = "simple"  # simple | parent | child

    def as_list(self) -> List[str]:
        return [self.values.get(col, "") for col in BLING_COLUMNS]


# --------------------------------------------------------------------------- #
# Formatação de valores
# --------------------------------------------------------------------------- #


def format_decimal(value: Optional[float], places: int = 3) -> str:
    """Formata número com VÍRGULA decimal, do jeito que o Bling/Excel espera.

    ``0.108`` -> ``"0,108"`` ; ``52.9`` -> ``"52,9"`` ; ``None`` -> ``""``
    Remove zeros à direita para não poluir (``52,00`` -> ``52``).
    """
    if value is None:
        return ""
    text = f"{value:.{places}f}".rstrip("0").rstrip(".")
    if text in ("", "-0"):
        text = "0"
    return text.replace(".", ",")


def format_price(value: Optional[float]) -> str:
    """Preço com 2 casas, vírgula decimal, sem zeros inúteis (``52`` e ``52,9``)."""
    if value is None:
        return ""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return (text or "0").replace(".", ",")


def format_ncm(raw: Optional[str]) -> str:
    """Normaliza NCM para ``0000.00.00``.

    Aceita ``82130000``, ``8213.00.00``, ``8213 00 00``. Devolve ``""`` se não
    houver 8 dígitos válidos — nunca inventa código.
    """
    if not raw:
        return ""
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) != 8:
        return ""
    return f"{digits[:4]}.{digits[4:6]}.{digits[6:]}"


def format_ean(raw: Optional[str]) -> str:
    """Devolve o EAN só se tiver 8, 12, 13 ou 14 dígitos. Senão, vazio."""
    if not raw:
        return ""
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) in (8, 12, 13, 14):
        return digits
    return ""


def join_images(urls: Iterable[str]) -> str:
    """Junta URLs de imagem separadas por vírgula (formato do Bling)."""
    seen: List[str] = []
    for url in urls or []:
        url = (url or "").strip()
        if url and url not in seen:
            seen.append(url)
    return ",".join(seen)


# --------------------------------------------------------------------------- #
# Extração a partir da descrição (camada 2 da hierarquia)
# --------------------------------------------------------------------------- #

_WEIGHT_RE = re.compile(
    r"peso[^0-9]{0,40}?([0-9]{1,3}(?:[.,][0-9]{1,3})?)\s*(kg|g|gramas?|quilos?)\b",
    re.IGNORECASE,
)


def extract_weight_kg(description: str) -> Optional[float]:
    """Extrai o peso do texto da descrição.

    Exemplos aceitos::

        "Peso aproximado do produto: 108 g"   -> 0.108
        "Peso: 1,5 kg"                        -> 1.5
        "Peso líquido 300g"                   -> 0.3
    """
    if not description:
        return None
    for match in _WEIGHT_RE.finditer(description):
        number, unit = match.group(1), match.group(2).lower()
        try:
            value = float(number.replace(",", "."))
        except ValueError:
            continue
        if value <= 0:
            continue
        if unit.startswith("g"):  # g, grama, gramas
            value = value / 1000.0
        if 0.001 <= value <= 500:  # sanidade: 1 g a 500 kg
            return round(value, 3)
    return None


_DIMENSION_RE = re.compile(
    r"(largura|altura|profundidade|comprimento)\s*(?:da embalagem)?\s*[:\-]?\s*"
    r"([0-9]{1,3}(?:[.,][0-9]{1,3})?)\s*(cm|mm)\b",
    re.IGNORECASE,
)

_DIMENSION_KEYS = {
    "largura": "largura",
    "altura": "altura",
    "profundidade": "profundidade",
    "comprimento": "profundidade",
}


def extract_dimensions(description: str) -> Dict[str, float]:
    """Extrai medidas em CENTÍMETROS, apenas quando o texto fala de embalagem.

    Só aceita quando a frase menciona "embalagem" — caso contrário seriam
    medidas da peça (ex.: "comprimento da lâmina"), que não servem para frete.
    """
    if not description:
        return {}
    out: Dict[str, float] = {}
    for match in _DIMENSION_RE.finditer(description):
        # "embalagem" pode aparecer antes do par medida:valor
        # ("Dimensões da embalagem: largura: 14 cm"), então olhamos a vizinhança.
        window = description[max(0, match.start() - 60) : match.end() + 20].lower()
        if "embalagem" not in window:
            continue
        key = _DIMENSION_KEYS.get(match.group(1).lower())
        unit = match.group(3).lower()
        try:
            value = float(match.group(2).replace(",", "."))
        except ValueError:
            continue
        if unit == "mm":
            value = value / 10.0
        if 0.1 <= value <= 300:
            out[key] = round(value, 2)
    return out


_SUPPLIER_CODE_RE = re.compile(
    r"c[oó]digo\s*(?:do\s*fornecedor|vonder|fabricante)?\s*[:\-]\s*([A-Za-z0-9][A-Za-z0-9.\-/]{2,})",
    re.IGNORECASE,
)


def extract_supplier_code(description: str) -> Optional[str]:
    """Tenta achar o código do fornecedor citado na descrição (ex.: 30.85.250.000)."""
    if not description:
        return None
    match = _SUPPLIER_CODE_RE.search(description)
    if not match:
        return None
    code = match.group(1).strip(" .,-/\t\n")
    return code or None


# --------------------------------------------------------------------------- #
# Montagem das linhas
# --------------------------------------------------------------------------- #


def _base_values() -> Dict[str, str]:
    values = {col: "" for col in BLING_COLUMNS}
    values.update(DEFAULTS)
    return values


def _derive_weight(product: DropperProduct, sources: Dict[str, str]) -> Optional[float]:
    if product.weight_kg:
        sources["Peso líquido (Kg)"] = "campo"
        return product.weight_kg
    weight = extract_weight_kg(product.description)
    if weight:
        sources["Peso líquido (Kg)"] = "descricao"
        return weight
    return None


def _derive_dimensions(
    product: DropperProduct, sources: Dict[str, str]
) -> Dict[str, float]:
    if product.dimensions:
        for key in product.dimensions:
            sources[key.capitalize()] = "campo"
        return product.dimensions
    found = extract_dimensions(product.description)
    for key in found:
        sources[key.capitalize()] = "descricao"
    return found


def build_rows(
    product: DropperProduct,
    *,
    cleaned_title: Optional[str] = None,
    short_description: str = "",
    complementary_description: str = "",
    sale_price: Optional[float] = None,
    supplier: str = "DROPPING",
    color_images: Optional[Dict[str, List[str]]] = None,
) -> List[SheetRow]:
    """Monta as linhas do Bling para um produto (1 linha, ou pai + filhas).

    ``color_images`` permite entregar as imagens já separadas por cor (feito
    pela IA de visão). Se não vier, cada variação usa as imagens que tiver.
    """
    sources: Dict[str, str] = {}
    rows: List[SheetRow] = []

    title = (cleaned_title or product.title or "").strip()
    weight = _derive_weight(product, sources)
    dimensions = _derive_dimensions(product, sources)
    supplier_code = extract_supplier_code(product.description) or product.code

    images_all = join_images(product.images)

    # ---------------- linha principal (pai ou produto único) ----------------
    base = _base_values()
    base["Código"] = product.code
    base["Descrição"] = title
    base["Descrição Curta"] = short_description or ""
    base["Descrição Complementar"] = complementary_description or ""
    base["Descrição do Produto no Fornecedor"] = title
    base["Unidade"] = (product.unit or "UN").upper()
    base["NCM"] = format_ncm(product.ncm)
    base["GTIN/EAN"] = format_ean(product.ean)
    base["Marca"] = product.brand or "Generico"
    base["Categoria do produto"] = product.category or ""
    base["Fornecedor"] = supplier
    base["Cód. no fornecedor"] = supplier_code or ""
    base["Estoque"] = str(product.stock if product.stock is not None else 0)
    base["URL Imagens Externas"] = images_all
    base["Peso líquido (Kg)"] = format_decimal(weight)
    base["Peso bruto (Kg)"] = format_decimal(weight)
    base["Largura do produto"] = format_decimal(dimensions.get("largura"), 2)
    base["Altura do Produto"] = format_decimal(dimensions.get("altura"), 2)
    base["Profundidade do produto"] = format_decimal(dimensions.get("profundidade"), 2)
    if sale_price is not None:
        base["Preço"] = format_price(sale_price)
        sources["Preço"] = "tabela"
    if product.cost is not None:
        base["Preço de custo"] = format_price(product.cost)
        base["Preço de Compra"] = format_price(product.cost)
        sources["Preço de custo"] = "campo"

    if product.variations:
        base["Produto Variação"] = ""
        base["Código Pai"] = ""
        base["Clonar dados do pai"] = ""
        rows.append(SheetRow(values=base, sources=dict(sources), kind="parent"))
    else:
        rows.append(SheetRow(values=base, sources=dict(sources), kind="simple"))
        return rows

    # ---------------- linhas filhas (uma por variação) ----------------
    for variation in product.variations:
        child = _base_values()
        child_code = variation.code or f"{product.code}-{(variation.name or '').upper()[:6]}"
        child["Código"] = child_code
        child["Descrição"] = f"Cor:{variation.name}" if variation.name else "Variação"
        child["Produto Variação"] = "Variação"
        child["Código Pai"] = product.code
        child["Clonar dados do pai"] = "SIM"
        child["Unidade"] = base["Unidade"]
        child["NCM"] = base["NCM"]
        child["GTIN/EAN"] = ""
        child["Marca"] = base["Marca"]
        child["Categoria do produto"] = base["Categoria do produto"]
        child["Fornecedor"] = supplier
        child["Cód. no fornecedor"] = supplier_code or ""
        child["Estoque"] = "0"
        child["Peso líquido (Kg)"] = ""
        child["Peso bruto (Kg)"] = ""
        child["Largura do produto"] = ""
        child["Altura do Produto"] = ""
        child["Profundidade do produto"] = ""
        if sale_price is not None:
            child["Preço"] = format_price(sale_price)
        if product.cost is not None:
            child["Preço de custo"] = format_price(product.cost)
            child["Preço de Compra"] = format_price(product.cost)

        varied_images = (color_images or {}).get(variation.name) or variation.images
        child["URL Imagens Externas"] = join_images(varied_images)

        child_sources = {
            "Código": "campo",
            "Preço": "tabela" if sale_price is not None else "",
            "URL Imagens Externas": "IA" if color_images else "campo",
        }
        rows.append(SheetRow(values=child, sources=child_sources, kind="child"))

    return rows


# --------------------------------------------------------------------------- #
# Escrita do arquivo
# --------------------------------------------------------------------------- #


def build_csv_text(rows: Sequence[SheetRow]) -> str:
    """Monta o conteúdo CSV (delimitador ``;``) pronto para gravar."""
    buffer = io.StringIO()
    writer = csv.writer(
        buffer, delimiter=CSV_DELIMITER, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n"
    )
    writer.writerow(BLING_COLUMNS)
    for row in rows:
        writer.writerow(row.as_list())
    return buffer.getvalue()


def write_sheet(rows: Sequence[SheetRow], path: Path | str) -> Path:
    """Grava o arquivo com BOM (utf-8-sig). Devolve o caminho final."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = build_csv_text(rows)
    path.write_text(text, encoding=CSV_ENCODING, newline="")
    return path


def default_sheet_path(directory: Path | str, now: datetime | None = None) -> Path:
    """Nome padrão: ``planilha_bling_AAAAMMDD-HHMM.csv``."""
    base = Path(directory)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M")
    return base / f"planilha_bling_{stamp}.csv"


# --------------------------------------------------------------------------- #
# Relatório de revisão
# --------------------------------------------------------------------------- #


def _missing_for(row: SheetRow) -> List[str]:
    """Campos obrigatórios que ficaram vazios nesta linha."""
    required = ("Código", "Descrição", "NCM", "Preço", "Unidade", "Marca")
    return [c for c in required if not (row.values.get(c) or "").strip()]


def build_report(rows: Sequence[SheetRow], product_title: str = "") -> str:
    """Relatório legível: o que veio de onde e o que ficou faltando."""
    lines: List[str] = []
    if product_title:
        lines.append(product_title)
    for row in rows:
        kind_label = {"simple": "produto", "parent": "pai", "child": "variação"}[row.kind]
        lines.append(f"  [{kind_label}] {row.values.get('Código', '')}")
        for column in BLING_COLUMNS:
            value = (row.values.get(column) or "").strip()
            if not value:
                if column in FISCAL_COLUMNS:
                    lines.append(f"    ⚠ {column}: vazio (fiscal — preencher manualmente)")
                continue
            if column in ("Descrição Curta", "Descrição Complementar"):
                value = f"{len(value)} caracteres"
            elif len(value) > 60:
                value = value[:57] + "..."
            origin = row.sources.get(column)
            tag = f" ({origin})" if origin else ""
            lines.append(f"    ✔ {column}: {value}{tag}")
        missing = _missing_for(row)
        if missing:
            lines.append(f"    ⚠ faltando: {', '.join(missing)}")
    return "\n".join(lines)


def summarize(rows: Sequence[SheetRow]) -> Dict[str, int]:
    """Contagens rápidas para a tela do robô.

    As contagens de alerta olham apenas linhas de produto (``simple``/``parent``):
    as variações herdam NCM e categoria do pai, então contá-las duplicaria o
    mesmo problema.
    """
    principais = [r for r in rows if r.kind in ("simple", "parent")]
    return {
        "linhas": len(rows),
        "produtos": len(principais),
        "variacoes": sum(1 for r in rows if r.kind == "child"),
        "sem_ncm": sum(1 for r in principais if not r.values.get("NCM")),
        "sem_preco": sum(1 for r in principais if not r.values.get("Preço")),
        "sem_imagem": sum(1 for r in principais if not r.values.get("URL Imagens Externas")),
    }
