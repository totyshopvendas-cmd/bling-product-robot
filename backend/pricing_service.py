"""CSV pricing table loader and lookup service.

Schema (semicolon separated):
  Custo do Catálogo;Preço da Loja;Preço de Venda
  21,99;50,50;5050
"""
import csv
import io
from typing import Optional, Dict, Tuple
from db import db
from models import PriceLookupResponse


def _to_cents(value: str) -> int:
    value = value.strip().replace(".", "").replace(",", ".")
    return int(round(float(value) * 100))


async def import_csv(content: bytes) -> dict:
    """Parse CSV bytes, store all rows in MongoDB. Returns stats."""
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text), delimiter=";")
    rows = list(reader)
    if not rows:
        return {"imported": 0, "errors": ["Arquivo vazio"]}

    # Detect header
    start = 1 if any(c.lower().startswith("custo") for c in rows[0]) else 0

    docs = []
    errors = []
    for i, row in enumerate(rows[start:], start=start + 1):
        if len(row) < 3:
            errors.append(f"L{i}: colunas insuficientes")
            continue
        try:
            cost_cents = _to_cents(row[0])
            store_price = row[1].strip()
            sale_price_int = int(row[2].strip())
            docs.append({
                "cost_cents": cost_cents,
                "store_price_brl": store_price,
                "sale_price_int": sale_price_int,
            })
        except Exception as e:
            errors.append(f"L{i}: {e}")
            if len(errors) > 20:
                break

    # Replace existing pricing data
    await db.pricing.delete_many({})
    if docs:
        # bulk insert in chunks of 5000
        chunk = 5000
        for i in range(0, len(docs), chunk):
            await db.pricing.insert_many(docs[i:i + chunk], ordered=False)

    return {"imported": len(docs), "errors": errors[:20]}


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
    # Try nearest higher (round up to next cent that exists)
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
