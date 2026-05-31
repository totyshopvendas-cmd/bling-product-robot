"""Bling product variations creator (FINAL — TotyShop manual compliant).

Uses PATCH /produtos/{id} with formato="V" + actionEstoque="Z" + variacoes[] —
the only flow Bling actually accepts for converting a Simple product into a
Variation parent.

Flow per the manual:
  1. PATCH parent: formato=V, actionEstoque=Z (zera estoque antigo), variacoes=[...]
  2. After variations exist, distribute total stock equally between them (Regra Balanceada)

Designed to FAIL SAFELY — main enrichment is never undone if this throws.
"""
import re
from typing import List, Optional

import bling_service
from robot_service import add_log


SIZE_HINTS = re.compile(
    r"^(pp|p|m|g|gg|xg|xs|s|l|xl|xxl|\d+\s*(?:ml|l|g|kg|cm|mm)?)$",
    re.IGNORECASE,
)


# Short-form abbreviations for variation codes (parent_sku + "-" + abbr)
COLOR_ABBR = {
    "preto": "PR", "branco": "BR", "azul": "AZ", "vermelho": "VM", "verde": "VD",
    "amarelo": "AM", "rosa": "RS", "roxo": "RX", "laranja": "LR", "cinza": "CZ",
    "marrom": "MR", "bege": "BG", "dourado": "DR", "prata": "PT", "vinho": "VH",
}


def _abbr(name: str) -> str:
    key = name.lower().strip()
    if key in COLOR_ABBR:
        return COLOR_ABBR[key]
    letters = re.sub(r"[^A-Za-z]", "", name).upper()
    return letters[:2] if len(letters) >= 2 else (letters or "VA")


def _attribute_kind(name: str) -> str:
    return "Tamanho" if SIZE_HINTS.match(name.strip()) else "Cor"


async def create_variations(
    parent_id: int,
    parent_current: Optional[dict],
    variations: List[str],
    total_stock: int = 0,
) -> dict:
    """Create variations on a Bling parent. Single PATCH call.

    Returns {created, per_child_stock}.
    """
    if not variations:
        return {"created": 0, "per_child_stock": 0}

    # Fetch current parent state (we need name, code, price)
    if not parent_current:
        r = await bling_service.bling_request("GET", f"/produtos/{parent_id}")
        if r.status_code >= 400:
            await add_log("warning", f"Variações pid={parent_id}: produto não encontrado")
            return {"created": 0, "per_child_stock": 0}
        parent_current = (r.json() or {}).get("data") or {}

    parent_name = (parent_current.get("nome") or "").strip()
    parent_sku = (parent_current.get("codigo") or "").strip()
    parent_price = parent_current.get("preco") or 0
    if not parent_sku:
        return {"created": 0, "per_child_stock": 0}

    # Build variation list
    existing_codes = set()
    for v in (parent_current.get("variacoes") or []):
        c = (v.get("codigo") or "").strip().upper()
        if c:
            existing_codes.add(c)

    new_vars: List[dict] = []
    skipped = 0
    for raw_name in variations:
        clean = (raw_name or "").strip()
        if not clean:
            continue
        kind = _attribute_kind(clean)
        sub_code = f"{parent_sku}-{_abbr(clean)}".upper()
        if sub_code in existing_codes:
            skipped += 1
            continue
        new_vars.append({
            "nome": f"{parent_name} {clean}"[:120],
            "codigo": sub_code,
            "preco": float(parent_price) if parent_price else 0.0,
            "tipo": "P",
            "situacao": "A",
            "formato": "S",
            "variacao": {"nome": f"{kind}:{clean}"},
        })

    if not new_vars:
        return {"created": 0, "per_child_stock": 0, "skipped": skipped}

    # Single PATCH — formato change + variations in one shot
    is_already_v = (parent_current.get("formato") or "").upper() == "V"
    payload: dict = {"variacoes": new_vars}
    if not is_already_v:
        payload["formato"] = "V"
        payload["actionEstoque"] = "Z"  # Z = zera estoque antigo do simples

    try:
        resp = await bling_service.bling_request(
            "PATCH", f"/produtos/{parent_id}", json=payload,
        )
    except Exception as e:
        await add_log("warning", f"Variações pid={parent_id}: erro de rede — {e}")
        return {"created": 0, "per_child_stock": 0}

    if resp.status_code >= 400:
        await add_log(
            "warning",
            f"Variações pid={parent_id}: Bling rejeitou (HTTP {resp.status_code}) — {resp.text[:500]}",
        )
        return {"created": 0, "per_child_stock": 0}

    created = 0
    saved_ids: List[int] = []
    try:
        body = resp.json()
        vinfo = (body.get("data") or {}).get("variations") or {}
        saved = vinfo.get("saved") or []
        created = len(saved)
        saved_ids = [s["id"] for s in saved if s.get("id")]
    except Exception:
        created = len(new_vars)

    # Distribute total stock equally
    per_child = 0
    if total_stock and total_stock > 0 and saved_ids:
        per_child = total_stock // len(saved_ids)
        if per_child > 0:
            await _set_children_stock(saved_ids, per_child)

    flat = ", ".join(variations[:6]) + ("…" if len(variations) > 6 else "")
    await add_log(
        "success",
        f"Variações criadas pid={parent_id}: {created} ({flat}) — estoque/variação: {per_child}",
    )
    return {"created": created, "per_child_stock": per_child, "skipped": skipped}


async def _set_children_stock(child_ids: List[int], qty: int) -> None:
    """Update each child variation's stock to `qty` using PATCH /produtos/{id}."""
    for cid in child_ids:
        try:
            await bling_service.bling_request(
                "PATCH", f"/produtos/{cid}",
                json={
                    "estoque": {"minimo": 0, "maximo": 0, "crossdocking": 0, "saldoVirtualTotal": qty},
                    "actionEstoque": "E",
                },
            )
        except Exception:
            continue


async def find_and_create(parent_sku: str, variations: List[str], total_stock: int = 0) -> dict:
    """Convenience wrapper: fetch parent by SKU, then run full flow."""
    if not parent_sku or not variations:
        return {"ok": False, "reason": "sku ou variações vazios"}
    try:
        resp = await bling_service.bling_request(
            "GET", "/produtos", params={"codigo": parent_sku, "limite": 5}
        )
    except Exception as e:
        return {"ok": False, "reason": f"busca: {e}"}
    if resp.status_code >= 400:
        return {"ok": False, "reason": f"busca HTTP {resp.status_code}"}
    items = (resp.json() or {}).get("data") or []
    target: Optional[dict] = None
    for it in items:
        if (it.get("codigo") or "").strip().upper() == parent_sku.upper():
            target = it
            break
    if not target:
        return {"ok": False, "reason": "produto não encontrado"}
    # Fetch full for variacoes list
    full_resp = await bling_service.bling_request("GET", f"/produtos/{target['id']}")
    full = (full_resp.json() or {}).get("data") if full_resp.status_code < 400 else target
    result = await create_variations(target["id"], full, variations, total_stock)
    return {"ok": True, **result}
