"""Pricing table loader (CSV or Excel) and lookup.

Expected columns (header names, any order):
  Custo do Catálogo | Preço da Loja | Preço de Venda

CSV fallback (semicolon):
  Custo do Catálogo;Preço da Loja;Preço de Venda
  21,99;50,50;5050

Preço de Venda is the integer the robot types (5050 = R$ 50,50).
"""
from __future__ import annotations

import csv
import io
import unicodedata
from typing import Any

from db import db
from models import PriceLookupResponse


def _norm(text: str) -> str:
    raw = unicodedata.normalize("NFD", str(text or ""))
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    return raw.lower().strip()


def _to_cents(value: Any) -> int:
    if value is None or value == "":
        raise ValueError("custo vazio")
    if isinstance(value, bool):
        raise ValueError("custo inválido")
    if isinstance(value, (int, float)):
        return int(round(float(value) * 100))
    s = str(value).strip().replace("R$", "").replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    return int(round(float(s) * 100))


def _store_brl(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{float(value):.2f}".replace(".", ",")
    return str(value).strip().replace("R$", "").strip()


def _sale_int(value: Any) -> int:
    if value is None or value == "":
        raise ValueError("preço de venda vazio")
    if isinstance(value, bool):
        raise ValueError("preço de venda inválido")
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if value == int(value):
            return int(value)
        return int(round(value * 100))
    s = str(value).strip().replace("R$", "").replace(" ", "")
    if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
        return int(s)
    if "," in s:
        return int(round(float(s.replace(".", "").replace(",", ".")) * 100))
    if "." in s:
        f = float(s)
        if f == int(f):
            return int(f)
        return int(round(f * 100))
    return int(float(s))


def _pick_columns(header: list) -> tuple[int, int, int]:
    labels = [_norm(c) for c in header]
    cost = store = sale = None
    for i, lab in enumerate(labels):
        if cost is None and "custo" in lab:
            cost = i
        elif store is None and "loja" in lab:
            store = i
        elif sale is None and "venda" in lab:
            sale = i
    if cost is not None and store is not None and sale is not None:
        return cost, store, sale
    if len(header) >= 3:
        return 0, 1, 2
    raise ValueError("A planilha precisa das colunas Custo, Preço da Loja e Preço de Venda")


def _is_xlsx(content: bytes, filename: str = "") -> bool:
    name = (filename or "").lower()
    if name.endswith(".xlsx") or name.endswith(".xlsm") or name.endswith(".xls"):
        return True
    return content[:2] == b"PK"


def _decode_csv(content: bytes) -> str:
    if content.startswith(b"\xff\xfe") or content.startswith(b"\xfe\xff"):
        return content.decode("utf-16")
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _rows_from_csv(content: bytes) -> list[list]:
    """Excel pt-BR often uses ';' — never pick ',' just because decimals contain commas."""
    text = _decode_csv(content)
    best_rows: list[list] = []
    best_score = -1
    for delim in (";", "\t", ","):
        rows = [list(r) for r in csv.reader(io.StringIO(text), delimiter=delim)]
        exact3 = sum(1 for r in rows[:40] if len(r) == 3)
        ge3 = sum(1 for r in rows[:40] if len(r) >= 3)
        score = exact3 * 20 + ge3
        if score > best_score:
            best_score = score
            best_rows = rows
    return best_rows


def _rows_from_xlsx(content: bytes) -> list[list]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        ws = wb.active
        return [list(row) for row in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


async def _store_docs(docs: list[dict], errors: list[str]) -> dict:
    if not docs:
        return {"imported": 0, "errors": errors[:20] or ["Nenhuma linha válida. Salve o Excel como CSV com ponto e vírgula (;)."]}
    await db.pricing.delete_many({})
    chunk = 5000
    for i in range(0, len(docs), chunk):
        await db.pricing.insert_many(docs[i : i + chunk], ordered=False)
    return {"imported": len(docs), "errors": errors[:20]}


async def import_table(content: bytes, filename: str = "") -> dict:
    if not content:
        return {"imported": 0, "errors": ["Arquivo vazio"]}
    try:
        raw_rows = _rows_from_xlsx(content) if _is_xlsx(content, filename) else _rows_from_csv(content)
    except Exception as exc:
        return {"imported": 0, "errors": [f"Não foi possível ler o arquivo: {exc}"]}

    rows = [r for r in raw_rows if r and any(c is not None and str(c).strip() != "" for c in r)]
    if not rows:
        return {"imported": 0, "errors": ["Arquivo vazio"]}

    header_idx = None
    cost_i, store_i, sale_i = 0, 1, 2
    for i, row in enumerate(rows[:20]):
        header = [str(c or "") for c in row]
        labs = [_norm(c) for c in header]
        if any("custo" in x for x in labs) or any("venda" in x for x in labs):
            try:
                cost_i, store_i, sale_i = _pick_columns(header)
                header_idx = i
                break
            except Exception:
                continue

    start = (header_idx + 1) if header_idx is not None else 0

    docs: list[dict] = []
    errors: list[str] = []
    for i, row in enumerate(rows[start:], start=start + 1):
        if max(cost_i, store_i, sale_i) >= len(row):
            if len(errors) < 20:
                errors.append(f"L{i}: colunas insuficientes")
            continue
        try:
            docs.append(
                {
                    "cost_cents": _to_cents(row[cost_i]),
                    "store_price_brl": _store_brl(row[store_i]),
                    "sale_price_int": _sale_int(row[sale_i]),
                }
            )
        except Exception as e:
            if len(errors) < 20:
                errors.append(f"L{i}: {e}")

    return await _store_docs(docs, errors)


async def import_csv(content: bytes) -> dict:
    """Backward-compatible alias used by older tests and the upload endpoint."""
    return await import_table(content, filename="pricing.csv")


async def lookup_price(cost: float) -> PriceLookupResponse:
    cost_cents = int(round(cost * 100))
    doc = await db.pricing.find_one({"cost_cents": cost_cents}, {"_id": 0})
    if doc:
        return PriceLookupResponse(
            cost=cost,
            cost_cents=cost_cents,
            sale_price_int=doc["sale_price_int"],
            store_price_brl=doc["store_price_brl"],
            found=True,
        )
    doc = await db.pricing.find_one(
        {"cost_cents": {"$gte": cost_cents}},
        sort=[("cost_cents", 1)],
        projection={"_id": 0},
    )
    if doc:
        return PriceLookupResponse(
            cost=cost,
            cost_cents=cost_cents,
            sale_price_int=doc["sale_price_int"],
            store_price_brl=doc["store_price_brl"],
            found=True,
        )
    return PriceLookupResponse(
        cost=cost,
        cost_cents=cost_cents,
        sale_price_int=0,
        store_price_brl="",
        found=False,
    )


async def stats() -> dict:
    count = await db.pricing.count_documents({})
    sample = await db.pricing.find({}, {"_id": 0}).sort("cost_cents", 1).limit(5).to_list(5)
    last = await db.pricing.find({}, {"_id": 0}).sort("cost_cents", -1).limit(5).to_list(5)
    return {"count": count, "first": sample, "last": last}
