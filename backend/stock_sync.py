"""Stock Sync — keeps Bling inventory aligned with JohnDrop supplier stock.

ISOLATED module. Does NOT touch johndrop_bot.py, enrich_worker.py or
bling_enrichment.py. Reuses ONLY pure helpers (`_parse_variation_quantities`,
`_get_default_deposito_id`, `_set_children_stock`) so the existing flow is
untouched.

Flow (per SKU):
  1. Find product in Bling by codigo. If missing → skip (per user rule).
  2. If product is simple (`formato != 'V'`) → POST /estoques to set new total.
  3. If product has variations (`formato == 'V'`):
       a. Load raw_description from `product_raw`.
       b. Parse variation names + per-variation quantities. If a color is
          "esgotado" → 0; if a number is given → that number.
       c. Distribute remaining `total - sum(explicit)` evenly across the
          variations that DON'T have an explicit quantity.
       d. Apply to each child via POST /estoques (Balanço).
       e. Zero the parent stock so dashboard total isn't double-counted.
  4. If price changed → PATCH /produtos/{id} with new preco.

Persists a run report in `stock_sync_runs` so the UI can show last-run summary.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import bling_service
from bling_enrichment import _parse_variation_quantities, _parse_variations
from bling_variations import (
    _get_default_deposito_id,
    _set_children_stock,
)
from db import db
from robot_service import add_log

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- types ----
class SupplierItem(dict):
    """Type marker — keys: sku (str), stock (int|None), price (float|None),
    source ('catalog'|'alert'), seen_at (iso str)."""


# ---------------------------------------------------------------- bling ----
async def _find_bling_product(sku: str) -> Optional[dict]:
    """Find product in Bling by exact codigo (case-insensitive). Returns the
    full Bling product dict, or None if not found."""
    try:
        r = await bling_service.bling_request(
            "GET", "/produtos", params={"codigo": sku, "limite": 5},
        )
        if r.status_code >= 400:
            return None
        items = (r.json() or {}).get("data") or []
        target = next(
            (it for it in items
             if (it.get("codigo") or "").strip().upper() == sku.strip().upper()),
            None,
        )
        if not target:
            return None
        pid = target.get("id")
        fr = await bling_service.bling_request("GET", f"/produtos/{pid}")
        if fr.status_code >= 400:
            return None
        return (fr.json() or {}).get("data") or None
    except Exception as e:
        logger.warning("find_bling_product %s failed: %s", sku, e)
        return None


async def _set_simple_stock(product_id: int, qty: int) -> bool:
    """Set absolute stock (operacao=B Balanço) on a simple Bling product."""
    if qty < 0:
        qty = 0
    dep_id = await _get_default_deposito_id()
    if not dep_id:
        return False
    try:
        r = await bling_service.bling_request(
            "POST", "/estoques",
            json={
                "produto": {"id": product_id},
                "deposito": {"id": dep_id},
                "operacao": "B",
                "quantidade": qty,
                "observacoes": "Sync JohnDrop → estoque atualizado",
            },
        )
        return r.status_code < 400
    except Exception as e:
        logger.warning("set_simple_stock %s failed: %s", product_id, e)
        return False


async def _patch_price(product_id: int, new_price: float) -> bool:
    """Update a Bling product price via PATCH."""
    try:
        r = await bling_service.bling_request(
            "PATCH", f"/produtos/{product_id}",
            json={"preco": float(new_price)},
        )
        return r.status_code < 400
    except Exception as e:
        logger.warning("patch_price %s failed: %s", product_id, e)
        return False


# -------------------------------------------------------- variation split ----
def _distribute_among_variations(
    total: int, variations: List[str], explicit: dict
) -> dict:
    """Given `total` stock, list of variation names and explicit per-variation
    quantities (from description parsing), return {variation_name: qty}.

    Rules:
      - Variations marked esgotado (qty=0 in explicit) → 0.
      - Variations with explicit number → that number.
      - Remaining variations split the remainder equally (rounding).
    """
    out: dict = {}
    explicit_sum = 0
    assigned = []
    for v in variations:
        if v in explicit:
            out[v] = max(0, int(explicit[v]))
            explicit_sum += out[v]
            assigned.append(v)
    remaining = [v for v in variations if v not in assigned]
    leftover = max(0, total - explicit_sum)
    if remaining:
        per = leftover // len(remaining)
        rem = leftover - per * len(remaining)
        for i, v in enumerate(remaining):
            out[v] = per + (1 if i < rem else 0)
    return out


def _norm(s: str) -> str:
    return "".join(c.lower() for c in (s or "").strip() if c.isalnum())


async def _apply_to_variations(parent: dict, total: int, raw_desc: str) -> dict:
    """Distribute `total` across the variations of `parent` based on `raw_desc`.

    Returns {"distributed": {sigla: qty}, "skipped": [], "applied": int}.
    """
    parent_id = parent.get("id")
    children = parent.get("variacoes") or []
    if not children:
        # Has formato='V' but no children loaded — skip safely
        return {"distributed": {}, "applied": 0, "skipped_reason": "no_children"}

    # Try to extract variation names from raw description so we can map by name.
    parsed_names = _parse_variations(raw_desc or "") or []
    explicit = _parse_variation_quantities(raw_desc or "", parsed_names) if parsed_names else {}

    # Map children to a name (use last token of variation name "Parent — Color")
    child_map = {}  # normalized_name -> child dict
    for child in children:
        nome = (child.get("nome") or "").strip()
        # Bling variation names usually end with the option after " - " or " — "
        suffix = nome.split(" - ")[-1].split(" — ")[-1].strip()
        child_map[_norm(suffix)] = child

    # Resolve names from desc → child ids
    distribute_for: List[str] = []
    explicit_for: dict = {}
    for name in parsed_names:
        n = _norm(name)
        if n in child_map:
            distribute_for.append(name)
            if name in explicit:
                explicit_for[name] = explicit[name]

    # If parsing failed → fall back to using ALL children, evenly split
    if not distribute_for:
        distribute_for = [c.get("nome") or f"child_{c.get('id')}" for c in children]
        # Build child_map keyed by these synthetic names
        child_map = {
            _norm(c.get("nome") or f"child_{c.get('id')}"): c
            for c in children
        }

    plan = _distribute_among_variations(total, distribute_for, explicit_for)

    # Apply to each child (one POST /estoques per child)
    applied = 0
    distributed: dict = {}
    for name, qty in plan.items():
        child = child_map.get(_norm(name))
        if not child:
            continue
        cid = child.get("id")
        if not cid:
            continue
        await _set_children_stock([cid], qty)
        applied += 1
        distributed[name] = qty

    # Zero parent so dashboard total isn't double-counted
    if total > 0 and parent_id:
        dep_id = await _get_default_deposito_id()
        if dep_id:
            try:
                await bling_service.bling_request(
                    "POST", "/estoques",
                    json={
                        "produto": {"id": parent_id},
                        "deposito": {"id": dep_id},
                        "operacao": "B",
                        "quantidade": 0,
                        "observacoes": "Sync JohnDrop — pai zerado (estoque nas variações)",
                    },
                )
            except Exception:
                pass
    return {"distributed": distributed, "applied": applied}


# ---------------------------------------------------------------- single ----
async def sync_one_sku(item: dict) -> dict:
    """Sync a single supplier item to Bling.

    `item` = {sku, stock, price, source}. Returns a report dict suitable for
    the UI table.
    """
    sku = (item.get("sku") or "").strip()
    new_stock = item.get("stock")
    new_price = item.get("price")
    report = {
        "sku": sku,
        "source": item.get("source") or "catalog",
        "stock": new_stock,
        "price": new_price,
        "found_in_bling": False,
        "product_id": None,
        "formato": None,
        "stock_applied": False,
        "price_applied": False,
        "distribution": None,
        "error": None,
    }
    if not sku:
        report["error"] = "sku_vazio"
        return report

    product = await _find_bling_product(sku)
    if not product:
        report["error"] = "not_in_bling"
        return report

    report["found_in_bling"] = True
    report["product_id"] = product.get("id")
    report["formato"] = product.get("formato")

    # --- STOCK ---
    if isinstance(new_stock, int) and new_stock >= 0:
        if product.get("formato") == "V":
            raw_doc = await db.product_raw.find_one({"sku": sku})
            raw_desc = (raw_doc or {}).get("raw_description") or ""
            try:
                res = await _apply_to_variations(product, new_stock, raw_desc)
                report["stock_applied"] = res.get("applied", 0) > 0
                report["distribution"] = res.get("distributed")
            except Exception as e:
                report["error"] = f"vars_failed:{str(e)[:80]}"
        else:
            ok = await _set_simple_stock(product["id"], new_stock)
            report["stock_applied"] = ok

    # --- PRICE ---
    if isinstance(new_price, (int, float)) and float(new_price) > 0:
        current = float(product.get("preco") or 0)
        if abs(current - float(new_price)) > 0.001:
            ok = await _patch_price(product["id"], float(new_price))
            report["price_applied"] = ok
            report["price_was"] = current

    return report


# ----------------------------------------------------------- orchestrator ----
async def run_sync(items: List[dict], run_id: Optional[str] = None) -> dict:
    """Sync a batch of supplier items. Persists a run report in
    `stock_sync_runs` and returns the in-memory summary.
    """
    run_id = run_id or str(uuid.uuid4())
    started = datetime.now(timezone.utc).isoformat()

    # Deduplicate by SKU keeping the freshest item (alerts override catalog)
    by_sku: dict = {}
    for it in items:
        sku = (it.get("sku") or "").strip()
        if not sku:
            continue
        prev = by_sku.get(sku)
        # alert wins over catalog when both present
        if prev and prev.get("source") == "alert" and it.get("source") == "catalog":
            continue
        # merge: keep latest non-null fields
        merged = dict(prev or {})
        merged.update({k: v for k, v in it.items() if v is not None})
        by_sku[sku] = merged

    await db.stock_sync_runs.insert_one({
        "run_id": run_id,
        "started_at": started,
        "status": "running",
        "total": len(by_sku),
    })

    reports: List[dict] = []
    success = 0
    not_found = 0
    errors = 0
    price_updates = 0
    stock_updates = 0
    for sku, item in by_sku.items():
        rep = await sync_one_sku(item)
        reports.append(rep)
        if rep.get("error") == "not_in_bling":
            not_found += 1
        elif rep.get("error"):
            errors += 1
        elif rep.get("stock_applied") or rep.get("price_applied"):
            success += 1
        if rep.get("stock_applied"):
            stock_updates += 1
        if rep.get("price_applied"):
            price_updates += 1
        await add_log(
            "info" if not rep.get("error") else "warning",
            f"SyncEstoque {sku}: stock={rep.get('stock_applied')} "
            f"price={rep.get('price_applied')} err={rep.get('error')}",
        )

    finished = datetime.now(timezone.utc).isoformat()
    summary = {
        "run_id": run_id,
        "started_at": started,
        "finished_at": finished,
        "total": len(by_sku),
        "success": success,
        "not_found": not_found,
        "errors": errors,
        "stock_updates": stock_updates,
        "price_updates": price_updates,
        "reports": reports,
    }

    await db.stock_sync_runs.update_one(
        {"run_id": run_id},
        {"$set": {
            "status": "done",
            "finished_at": finished,
            "success": success,
            "not_found": not_found,
            "errors": errors,
            "stock_updates": stock_updates,
            "price_updates": price_updates,
            "reports": reports,
        }},
    )
    return summary


async def get_last_run() -> Optional[dict]:
    doc = await db.stock_sync_runs.find_one({}, {"_id": 0}, sort=[("started_at", -1)])
    return doc
