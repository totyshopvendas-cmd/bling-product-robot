import re
from typing import Any, Dict, List

from bling_service import bling_request
from bling_enrichment import detect_product_brand


def _canonical_brand(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    norm = re.sub(r"[^A-Za-zÀ-ÿ0-9&/ .\-]", " ", text)
    norm = re.sub(r"\s+", " ", norm).strip(" -.,")
    return norm[:60]


def _is_generic_brand(value: str) -> bool:
    v = (value or "").strip().lower()
    if not v:
        return True
    generic_tokens = {
        "generico", "genérico", "geral", "varios", "vários", "variedade", "produto", "item",
        "sem marca", "sem marca definida", "marca nao informada", "sem fabricante",
    }
    return v in generic_tokens or any(tok in v for tok in ("generico", "genérico", "geral", "varios", "vários"))


async def _fetch_all_products(limit: int = 1000) -> List[Dict[str, Any]]:
    products: List[Dict[str, Any]] = []
    pagina = 1
    seen = set()
    while pagina <= 20 and len(products) < limit:
        resp = await bling_request("GET", "/produtos", params={"pagina": pagina, "limite": 100})
        if resp.status_code >= 400:
            raise RuntimeError(f"Bling catalog scan failed: HTTP {resp.status_code} — {resp.text[:300]}")
        body = resp.json() or {}
        itens = body.get("data") or []
        if not itens:
            break
        for item in itens:
            pid = item.get("id")
            if pid in seen:
                continue
            seen.add(pid)
            products.append(item)
        if len(itens) < 100:
            break
        pagina += 1
    return products[:limit]


async def audit_catalog(limit: int = 1000) -> Dict[str, Any]:
    """Audit Bling catalog for brand, description and marketplace gaps.

    This is a read-only pass. It never deletes or disables products.
    """
    products = await _fetch_all_products(limit)
    total = len(products)
    missing_brand = 0
    missing_ia = 0
    missing_market = 0
    review_exclusions = 0
    items: List[Dict[str, Any]] = []

    for item in products:
        codigo = (item.get("codigo") or "").strip()
        nome = (item.get("nome") or "").strip()
        marca = _canonical_brand(item.get("marca"))
        description = " ".join([
            str(item.get("descricaoCurta") or ""),
            str(item.get("descricaoComplementar") or ""),
        ])
        description = " ".join(description.split())

        has_brand = bool(marca) and not _is_generic_brand(marca)
        has_ia = bool(description.strip())
        has_category = bool((item.get("categoria") or {}).get("id") or (item.get("categoria") or {}).get("descricao"))
        has_market_fields = has_brand and has_ia and has_category

        if not has_brand:
            missing_brand += 1
        if not has_ia:
            missing_ia += 1
        if not has_market_fields:
            missing_market += 1
        if (not has_brand) or (not has_ia) or (not has_category):
            review_exclusions += 1

        items.append({
            "id": item.get("id"),
            "codigo": codigo,
            "nome": nome,
            "marca": marca or "—",
            "has_brand": has_brand,
            "has_ia": has_ia,
            "has_category": has_category,
            "needs_review": (not has_brand) or (not has_ia) or (not has_category),
            "needs_marketplace_fields": not has_market_fields,
        })

    return {
        "total": total,
        "varridos": total,
        "marcas": missing_brand,
        "ia_campo": missing_ia,
        "marketplace": missing_market,
        "review_exclusion": review_exclusions,
        "items": items,
    }


async def apply_safe_brands(product_ids: List[int]) -> Dict[str, Any]:
    """Safely repair missing or generic brands without deleting anything."""
    updated = []
    skipped = []

    for product_id in product_ids:
        resp = await bling_request("GET", f"/produtos/{product_id}")
        if resp.status_code >= 400:
            skipped.append({"id": product_id, "reason": "product_not_found"})
            continue
        payload = (resp.json() or {}).get("data") or {}
        current = _canonical_brand(payload.get("marca"))
        if current and not _is_generic_brand(current):
            updated.append({"id": product_id, "marca": current, "status": "unchanged"})
            continue
        inferred = detect_product_brand(payload.get("nome") or "", payload.get("descricaoCurta") or payload.get("descricaoComplementar") or "")
        if not inferred or inferred.lower() == "generico":
            skipped.append({"id": product_id, "reason": "brand_not_inferred"})
            continue
        patch = {"marca": inferred}
        patch_resp = await bling_request("PATCH", f"/produtos/{product_id}", json=patch)
        if patch_resp.status_code >= 400:
            skipped.append({"id": product_id, "reason": f"patch_failed_{patch_resp.status_code}"})
            continue
        updated.append({"id": product_id, "marca": inferred, "status": "patched"})

    return {
        "ok": True,
        "updated": len(updated),
        "skipped": len(skipped),
        "items": updated,
        "skipped_items": skipped,
    }
