"""Pure helpers for the JonhDrop → planilha export.

NO network, NO Mongo, NO Playwright here — só normalização de dados e geração
do arquivo (.xlsx via openpyxl, com fallback para .csv). Mantido separado de
`johndrop_export.py` para poder ser testado isoladamente.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, List, Optional

# --------------------------------------------------------------- parsing ---

def normalize_label(text: str | None) -> str:
    """'Preço de Custo (R$)' → 'preco de custo r'. Lowercase, sem acento,
    só letras/números/espaço."""
    if not text:
        return ""
    txt = unicodedata.normalize("NFKD", str(text))
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    txt = re.sub(r"[^a-z0-9]+", " ", txt.lower())
    return re.sub(r"\s+", " ", txt).strip()


def parse_brl(text) -> Optional[float]:
    """'R$ 1.234,56' → 1234.56 ; '21.99' → 21.99 ; '' → None."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    txt = str(text).replace("R$", "").replace("\xa0", "").replace(" ", "").strip()
    if not txt:
        return None
    if "," in txt:
        txt = txt.replace(".", "").replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", txt)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def parse_int(text) -> Optional[int]:
    if text is None:
        return None
    if isinstance(text, int):
        return text
    m = re.search(r"-?\d+", str(text).replace(".", "").replace(",", ""))
    return int(m.group(0)) if m else None


def clean_sku_cell(text: str | None) -> str:
    """A célula SKU de 'Meus Produtos' traz o SKU + badge 'Catálogo: XYZ'
    (às vezes na mesma linha). Mantém só o SKU."""
    if not text:
        return ""
    first = re.split(r"\s*Cat[áa]logo\s*:", str(text), maxsplit=1, flags=re.IGNORECASE)[0]
    return first.strip().split("\n")[0].strip()


def catalog_sku_from_cell(text: str | None) -> str:
    """Extrai o SKU do badge 'Catálogo: XYZ' (SKU original do fornecedor)."""
    if not text:
        return ""
    m = re.search(r"Cat[áa]logo\s*:\s*([^\s]+)", str(text), flags=re.IGNORECASE)
    return m.group(1).strip() if m else ""


# Ordem importa: regras mais específicas primeiro ("preço de custo" → custo,
# "código de barras" → gtin antes de "código" → sku).
_FIELD_RULES: List[tuple] = [
    ("gtin", ("gtin", "ean", "codigo de barras", "cod barras")),
    ("ncm", ("ncm",)),
    ("cest", ("cest",)),
    ("custo", ("custo",)),
    ("preco_sugerido", ("sugerido", "preco de venda", "venda sugerida")),
    ("preco", ("preco", "valor")),
    ("sku", ("sku", "codigo", "referencia")),
    ("estoque", ("estoque", "quantidade", "qtd", "saldo")),
    ("peso_bruto", ("peso bruto",)),
    ("peso", ("peso",)),
    ("altura", ("altura",)),
    ("largura", ("largura",)),
    ("comprimento", ("comprimento", "profundidade")),
    ("marca", ("marca", "fabricante")),
    ("categoria", ("categoria",)),
    ("unidade", ("unidade",)),
    ("origem", ("origem",)),
    ("descricao", ("descricao",)),
    ("nome", ("nome", "titulo", "produto")),
]


def match_field(label: str | None, name: str | None = None) -> Optional[str]:
    """Mapeia um rótulo/atributo `name` de campo da página JonhDrop para uma
    coluna conhecida da planilha. Retorna None se não reconhecer."""
    candidates = [normalize_label(label), normalize_label(name)]
    for norm in candidates:
        if not norm:
            continue
        for field, keywords in _FIELD_RULES:
            for kw in keywords:
                if re.search(rf"(?:^|\s){re.escape(kw)}(?:\s|$)", norm) or norm.replace(" ", "") == kw.replace(" ", ""):
                    return field
    return None


def map_detail_fields(raw_fields: Iterable[dict]) -> Dict[str, str]:
    """Recebe a lista [{label, name, value}] extraída da página de detalhe e
    devolve {campo_conhecido: valor}. O primeiro valor encontrado vence."""
    out: Dict[str, str] = {}
    for f in raw_fields or []:
        value = str(f.get("value") or "").strip()
        if not value:
            continue
        field = match_field(f.get("label"), f.get("name"))
        if field and field not in out:
            out[field] = value
    return out


# ------------------------------------------------------------- workbook ----

PRODUCT_COLUMNS: List[tuple] = [
    # (key, header, width)
    ("jonhdrop_id", "ID JonhDrop", 12),
    ("sku", "SKU", 18),
    ("sku_catalogo", "SKU Catálogo", 18),
    ("nome", "Nome", 50),
    ("canal", "Canal", 16),
    ("integracao", "Integração", 22),
    ("preco", "Preço", 11),
    ("custo", "Custo", 11),
    ("preco_sugerido", "Preço sugerido", 13),
    ("estoque", "Estoque", 9),
    ("variacoes", "Variações", 30),
    ("qtd_variacoes", "Qtd variações", 12),
    ("peso", "Peso", 9),
    ("peso_bruto", "Peso bruto", 10),
    ("altura", "Altura", 9),
    ("largura", "Largura", 9),
    ("comprimento", "Comprimento", 12),
    ("ncm", "NCM", 12),
    ("cest", "CEST", 10),
    ("gtin", "GTIN/EAN", 16),
    ("marca", "Marca", 14),
    ("categoria", "Categoria", 22),
    ("unidade", "Unidade", 9),
    ("origem", "Origem", 10),
    ("data", "Data", 18),
    ("qtd_imagens", "Qtd imagens", 11),
    ("imagem_principal", "Imagem principal", 40),
    ("descricao", "Descrição", 80),
    ("link", "Link JonhDrop", 40),
    ("detalhe_status", "Status detalhe", 16),
]

NUMERIC_KEYS = {"preco", "custo", "preco_sugerido"}
INT_KEYS = {"estoque", "qtd_variacoes", "qtd_imagens"}
DECIMAL_KEYS = {"peso", "peso_bruto", "altura", "largura", "comprimento"}
EXCEL_CELL_LIMIT = 32000

_ILLEGAL_XML_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _cell(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value if v not in (None, ""))
    txt = _ILLEGAL_XML_RE.sub("", str(value))
    if len(txt) > EXCEL_CELL_LIMIT:
        txt = txt[:EXCEL_CELL_LIMIT] + " …[cortado]"
    # Evita que o Excel interprete texto como fórmula (=, +, -, @)
    if txt[:1] in ("=", "+", "@") or (txt[:1] == "-" and not re.fullmatch(r"-?\d+(?:[.,]\d+)?", txt)):
        txt = "'" + txt
    return txt


def product_row(p: dict) -> List:
    """Converte o dict do produto na linha da aba 'Produtos'."""
    row = []
    for key, _h, _w in PRODUCT_COLUMNS:
        v = p.get(key)
        if key in NUMERIC_KEYS:
            num = parse_brl(v) if v not in (None, "") else None
            row.append(num)
        elif key in DECIMAL_KEYS:
            num = parse_brl(v) if v not in (None, "") else None
            row.append(num if num is not None else _cell(v))
        elif key in INT_KEYS:
            row.append(parse_int(v) if v not in (None, "") else None)
        else:
            row.append(_cell(v))
    return row


def _ensure_openpyxl():
    try:
        import openpyxl  # noqa: F401
        return True
    except Exception:
        pass
    # Tenta instalar em tempo de execução (mesma estratégia do Chromium)
    try:
        import subprocess
        import sys
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "openpyxl"],
            check=False, capture_output=True, timeout=180,
        )
        import importlib
        importlib.invalidate_caches()
        import openpyxl  # noqa: F401
        return True
    except Exception:
        return False


def build_workbook(products: List[dict], path: Path, meta: Optional[dict] = None) -> Path:
    """Gera o arquivo. Retorna o caminho final (.xlsx, ou .csv se openpyxl
    não estiver disponível)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not _ensure_openpyxl():
        return _build_csv(products, path.with_suffix(".csv"))

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    orange = PatternFill("solid", fgColor="EE7B22")
    head_font = Font(bold=True, color="FFFFFF")

    def _style_header(ws, widths):
        for idx, w in enumerate(widths, start=1):
            c = ws.cell(row=1, column=idx)
            c.fill = orange
            c.font = head_font
            c.alignment = Alignment(vertical="center")
            ws.column_dimensions[get_column_letter(idx)].width = w
        ws.freeze_panes = "A2"
        if ws.max_row >= 1 and ws.max_column >= 1:
            ws.auto_filter.ref = ws.dimensions

    wb = Workbook()

    # --- Aba 1: Produtos ---------------------------------------------------
    ws = wb.active
    ws.title = "Produtos"
    ws.append([h for _k, h, _w in PRODUCT_COLUMNS])
    for p in products:
        ws.append(product_row(p))
    money_cols = [i for i, (k, _h, _w) in enumerate(PRODUCT_COLUMNS, start=1) if k in NUMERIC_KEYS]
    for col in money_cols:
        for r in range(2, ws.max_row + 1):
            ws.cell(row=r, column=col).number_format = 'R$ #,##0.00'
    # Links clicáveis
    link_cols = [i for i, (k, _h, _w) in enumerate(PRODUCT_COLUMNS, start=1) if k in ("link", "imagem_principal")]
    for col in link_cols:
        for r in range(2, ws.max_row + 1):
            c = ws.cell(row=r, column=col)
            if isinstance(c.value, str) and c.value.startswith("http"):
                c.hyperlink = c.value
                c.font = Font(color="0563C1", underline="single")
    _style_header(ws, [w for _k, _h, w in PRODUCT_COLUMNS])

    # --- Aba 2: Variações --------------------------------------------------
    wv = wb.create_sheet("Variações")
    wv.append(["SKU", "Nome", "Variação", "Qtd informada"])
    for p in products:
        qtys = p.get("variacoes_qtd") or {}
        for v in p.get("variacoes_lista") or []:
            wv.append([_cell(p.get("sku")), _cell(p.get("nome")), _cell(v), qtys.get(v)])
    _style_header(wv, [18, 50, 30, 14])

    # --- Aba 3: Imagens ----------------------------------------------------
    wi = wb.create_sheet("Imagens")
    wi.append(["SKU", "Nome", "#", "URL"])
    for p in products:
        for i, url in enumerate(p.get("imagens") or [], start=1):
            wi.append([_cell(p.get("sku")), _cell(p.get("nome")), i, _cell(url)])
            c = wi.cell(row=wi.max_row, column=4)
            if isinstance(c.value, str) and c.value.startswith("http"):
                c.hyperlink = c.value
    _style_header(wi, [18, 50, 5, 90])

    # --- Aba 4: Todos os campos (dump bruto da página de detalhe) ----------
    wc = wb.create_sheet("Todos os campos")
    wc.append(["SKU", "Campo", "Valor"])
    for p in products:
        for f in p.get("campos_brutos") or []:
            label = f.get("label") or f.get("name") or ""
            wc.append([_cell(p.get("sku")), _cell(label), _cell(f.get("value"))])
    _style_header(wc, [18, 35, 80])

    # --- Aba 5: Resumo -----------------------------------------------------
    wr = wb.create_sheet("Resumo")
    wr.append(["Item", "Valor"])
    for k, v in (meta or {}).items():
        wr.append([_cell(k), _cell(v)])
    wr.append(["Total de produtos", len(products)])
    _style_header(wr, [30, 60])

    wb.save(path)
    return path


def _build_csv(products: List[dict], path: Path) -> Path:
    """Fallback: CSV com ';' e BOM (abre direto no Excel em pt-BR)."""
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow([h for _k, h, _w in PRODUCT_COLUMNS] + ["Imagens"])
        for p in products:
            row = product_row(p)
            row = [
                (f"{v:.2f}".replace(".", ",") if isinstance(v, float) else v)
                for v in row
            ]
            w.writerow(row + [" | ".join(p.get("imagens") or [])])
    return path
