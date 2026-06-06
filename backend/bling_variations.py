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
    parent_images: Optional[List[str]] = None,
    explicit_quantities: Optional[dict] = None,
) -> dict:
    """Create variations on a Bling parent. Single PATCH call.

    Returns {created, per_child_stock}.

    If `parent_images` is given (URLs from JohnDrop), each child variation will
    receive a copy of those images via a follow-up PATCH using `imagensURL`.

    If `explicit_quantities` is given (e.g. {"Rosa": 5, "Azul": 3}), each variation
    receives its specified amount. Variations not in the dict receive the equal
    split of remaining stock (Regra de Distribuição Balanceada).
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

    # READ parent's CURRENT stock so we can redistribute when total_stock isn't given.
    # Bling stores stock in `estoque.saldoVirtualTotal` (or sometimes `saldoFisicoTotal`).
    # Important: when converting formato S→V we MUST use actionEstoque="Z" which
    # zeroes the old simple stock — so we capture it here BEFORE the PATCH and
    # redistribute to children in step 3.
    if not total_stock or total_stock <= 0:
        # Primary source: dedicated /estoques/saldos endpoint (more reliable than
        # GET /produtos which often returns estoque=None even when stock exists).
        try:
            sr = await bling_service.bling_request(
                "GET", "/estoques/saldos",
                params={"idsProdutos[]": parent_id},
            )
            if sr.status_code < 400:
                rows = (sr.json() or {}).get("data") or []
                if rows:
                    v = rows[0].get("saldoVirtualTotal") or rows[0].get("saldoFisicoTotal") or 0
                    total_stock = int(v) if v else 0
        except Exception:
            pass
        # Fallback: read embedded estoque field from product
        if total_stock <= 0:
            est = parent_current.get("estoque") or {}
            captured = (
                est.get("saldoVirtualTotal")
                or est.get("saldoFisicoTotal")
                or 0
            )
            try:
                total_stock = int(captured) if captured else 0
            except (TypeError, ValueError):
                total_stock = 0
        # Fallback 2: wait for JohnDrop sync (8s) and retry once
        if total_stock <= 0:
            import asyncio as _asyncio
            await add_log(
                "info",
                "Estoque ainda 0 — aguardando 8s pelo sync JohnDrop→Bling...",
            )
            await _asyncio.sleep(8)
            try:
                sr = await bling_service.bling_request(
                    "GET", "/estoques/saldos",
                    params={"idsProdutos[]": parent_id},
                )
                if sr.status_code < 400:
                    rows = (sr.json() or {}).get("data") or []
                    if rows:
                        v = rows[0].get("saldoVirtualTotal") or rows[0].get("saldoFisicoTotal") or 0
                        total_stock = int(v) if v else 0
            except Exception:
                pass
        await add_log(
            "info",
            f"Variações pid={parent_id}: estoque capturado do pai = {total_stock} "
            f"(será dividido entre {len(variations)} variações)",
        )

    # Build variation list — NO codigo (SKU): user explicitly requested to skip SKU
    # generation. Bling will keep variations identified only by `variacao.nome`
    # ("Cor:Rosa", "Tamanho:M" etc).
    existing_names = set()
    for v in (parent_current.get("variacoes") or []):
        nome_var = (v.get("variacao") or {}).get("nome") or ""
        if nome_var:
            existing_names.add(nome_var.strip().lower())

    new_vars: List[dict] = []
    skipped = 0
    for raw_name in variations:
        clean = (raw_name or "").strip()
        if not clean:
            continue
        kind = _attribute_kind(clean)
        var_label = f"{kind}:{clean}"
        if var_label.lower() in existing_names:
            skipped += 1
            continue
        new_vars.append({
            "nome": f"{parent_name} {clean}"[:120],
            "preco": float(parent_price) if parent_price else 0.0,
            "tipo": "P",
            "situacao": "A",  # toggle "Situação do produto" ATIVO
            "formato": "S",
            "variacao": {
                "nome": var_label,
                # toggle "Utilizar informações do produto pai" ATIVO — herda imagens,
                # descrição, categoria, marca, peso, dimensões do pai
                "produtoPai": {"cloneInfo": True},
            },
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

    # Build map: saved variation id → its name (Rosa, Azul, P, M etc) from the response
    name_to_id: dict = {}
    try:
        body_data = (resp.json() or {}).get("data") or {}
        for s in (body_data.get("variations") or {}).get("saved") or []:
            sid = s.get("id")
            nome_var = s.get("nomeVariacao") or ""
            # nomeVariacao format: "Cor:Rosa" → take after the colon
            label = nome_var.split(":", 1)[-1].strip() if ":" in nome_var else nome_var.strip()
            if sid and label:
                name_to_id[label] = sid
    except Exception:
        pass

    # Distribute stock — explicit quantities first (including ZEROS for out-of-stock),
    # then equal split for the remaining ones.
    per_child = 0
    if saved_ids:
        explicit_map = explicit_quantities or {}
        remaining_ids = list(saved_ids)
        for var_name, qty in explicit_map.items():
            sid = name_to_id.get(var_name)
            if not sid:
                continue
            qty_int = max(0, int(qty))
            # Apply absolute (including 0 to ensure out-of-stock variations show 0)
            await _set_children_stock([sid], qty_int)
            if sid in remaining_ids:
                remaining_ids.remove(sid)
        # Equal split between the ones NOT explicitly quantified
        if total_stock and total_stock > 0 and remaining_ids:
            per_child = total_stock // len(remaining_ids)
            remainder = total_stock - per_child * len(remaining_ids)
            for i, sid in enumerate(remaining_ids):
                qty = per_child + (1 if i < remainder else 0)
                await _set_children_stock([sid], qty)

        # After distributing, ZERO the parent's stock — Bling tracks parent + children
        # as separate totals. If we don't zero the parent, the dashboard shows DOUBLE.
        if total_stock and total_stock > 0:
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
                            "observacoes": "Pai zerado — estoque vive nas variações",
                        },
                    )
                except Exception:
                    pass

    # Copy parent images onto each child variation so the Bling listing shows
    # a thumbnail for each variation (cloneInfo alone does NOT do this visually —
    # we have to disable cloneInfo to trigger Bling to materialize parent images).
    if saved_ids:
        await _copy_images_to_children(saved_ids, parent_images or [])

    flat = ", ".join(variations[:6]) + ("…" if len(variations) > 6 else "")
    await add_log(
        "success",
        f"Variações criadas pid={parent_id}: {created} ({flat}) — estoque/variação: {per_child}",
    )
    return {"created": created, "per_child_stock": per_child, "skipped": skipped}


_DEPOSITO_CACHE: dict = {"id": None, "checked": False}


async def _get_default_deposito_id() -> Optional[int]:
    """Cache the default warehouse (depósito padrão) id from Bling."""
    if _DEPOSITO_CACHE["checked"]:
        return _DEPOSITO_CACHE["id"]
    try:
        r = await bling_service.bling_request("GET", "/depositos")
        items = (r.json() or {}).get("data") or []
        for d in items:
            if d.get("padrao"):
                _DEPOSITO_CACHE["id"] = d.get("id")
                break
        if not _DEPOSITO_CACHE["id"] and items:
            _DEPOSITO_CACHE["id"] = items[0].get("id")
    except Exception:
        pass
    _DEPOSITO_CACHE["checked"] = True
    return _DEPOSITO_CACHE["id"]


async def _set_children_stock(child_ids: List[int], qty: int) -> None:
    """Set each child variation's stock to absolute `qty` via POST /estoques.

    Accepts qty=0 to explicitly mark out-of-stock variations (per TotyShop manual:
    variações esgotadas devem ser criadas com saldo zero).

    The PATCH /produtos/{id} endpoint silently ignores stock updates on variations
    (Bling returns 200 but doesn't persist). Must use the dedicated /estoques
    endpoint with operacao="B" (Balanço = define saldo absoluto).
    """
    if not child_ids or qty < 0:
        return
    dep_id = await _get_default_deposito_id()
    if not dep_id:
        await add_log("warning", "Estoque não distribuído: depósito padrão não encontrado")
        return
    ok = 0
    for cid in child_ids:
        try:
            r = await bling_service.bling_request(
                "POST", "/estoques",
                json={
                    "produto": {"id": cid},
                    "deposito": {"id": dep_id},
                    "operacao": "B",
                    "quantidade": qty,
                    "observacoes": "Distribuição balanceada (Regra TotyShop)",
                },
            )
            if r.status_code < 400:
                ok += 1
        except Exception:
            continue
    if ok:
        await add_log("info", f"Estoque definido: {ok}/{len(child_ids)} variações × {qty} unidades")


async def _copy_images_to_children(child_ids: List[int], image_urls: List[str]) -> int:
    """No-op when image_urls is empty (Bling auto-clones via cloneInfo=true).

    When image_urls is provided (real PUBLIC URLs from JohnDrop, NOT Bling S3),
    sends them via `midia.imagens.imagensURL` to each child. Bling downloads
    and stores them as the child's own images.

    NOTE: We discovered that disabling `cloneInfo=true` BREAKS the parent-child
    link in Bling. We MUST keep cloneInfo=true and only push images via the
    writeOnly `imagensURL` field — Bling stores them as own images when it
    accepts them. If the URLs are pre-signed S3 (with `?Signature=...`), Bling
    silently rejects them.
    """
    clean_urls = [u for u in (image_urls or []) if u and not u.startswith("data:")]
    clean_urls = [u for u in clean_urls if "AWSAccessKeyId=" not in u and "X-Amz-Signature=" not in u]
    if not clean_urls or not child_ids:
        return 0
    payload_imgs = [{"link": u} for u in clean_urls[:12]]
    ok = 0
    for cid in child_ids:
        try:
            r = await bling_service.bling_request(
                "PATCH", f"/produtos/{cid}",
                json={"midia": {"imagens": {"imagensURL": payload_imgs}}},
            )
            if r.status_code < 400:
                ok += 1
        except Exception:
            continue
    if ok:
        await add_log(
            "info",
            f"Imagens copiadas para {ok}/{len(child_ids)} variações ({len(payload_imgs)} cada)",
        )
    return ok


async def fix_existing_variations(parent_id: int) -> dict:
    """Push parent's PUBLIC image URLs (if any) to each child variation.
    Safe operation: never disables cloneInfo (which would break parent-child link).
    """
    r = await bling_service.bling_request("GET", f"/produtos/{parent_id}")
    if r.status_code >= 400:
        return {"ok": False, "reason": "produto não encontrado"}
    parent = (r.json() or {}).get("data") or {}
    children = parent.get("variacoes") or []
    child_ids = [v.get("id") for v in children if v.get("id")]
    if not child_ids:
        return {"ok": True, "fixed": 0, "failed": 0, "total": 0,
                "note": "Produto sem variações"}
    # Bling stores parent images as pre-signed S3 (with ?Signature=...) which
    # cannot be re-sent via API. Only PUBLIC URLs (e.g. originals from JohnDrop)
    # work. So we can't auto-copy from a Bling parent — need URL source.
    return {
        "ok": True, "fixed": 0, "failed": 0, "total": len(child_ids),
        "note": "Imagens do pai estão em S3 com expiração — só funcionam se vierem do JohnDrop. Use o robô para um produto novo.",
    }


async def find_and_create(parent_sku: str, variations: List[str], total_stock: int = 0, parent_images: Optional[List[str]] = None) -> dict:
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
    result = await create_variations(target["id"], full, variations, total_stock, parent_images=parent_images)
    return {"ok": True, **result}
