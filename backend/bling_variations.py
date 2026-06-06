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
    # Important: JohnDrop syncs the product to Bling in TWO phases — the product
    # data first (which lets us find it), and the stock saldo a few moments later.
    # If we PATCH formato=V before stock arrives, actionEstoque="Z" zeros it and
    # we permanently lose the count.
    #
    # Solution: poll /estoques/saldos with backoff for up to ~60 seconds. Only
    # proceed once we've captured a real number (or definitively given up).
    if not total_stock or total_stock <= 0:
        total_stock = await _read_parent_stock_with_retry(parent_id, parent_current)
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


async def _read_parent_stock_with_retry(
    parent_id: int, parent_current: Optional[dict] = None,
    max_attempts: int = 6, delay_s: float = 10.0,
) -> int:
    """Read parent product's current stock from Bling with retry/backoff.

    JohnDrop pushes the product to Bling, then a few moments later pushes the
    stock saldo (separate API call inside JohnDrop). We poll
    `/estoques/saldos` until we either capture a real positive value or exhaust
    `max_attempts × delay_s` seconds.

    Returns the captured quantity (0 if nothing arrived in time)."""
    import asyncio as _asyncio

    async def _try_saldos() -> int:
        try:
            sr = await bling_service.bling_request(
                "GET", "/estoques/saldos", params={"idsProdutos[]": parent_id},
            )
            if sr.status_code >= 400:
                return 0
            rows = (sr.json() or {}).get("data") or []
            if not rows:
                return 0
            v = rows[0].get("saldoVirtualTotal") or rows[0].get("saldoFisicoTotal") or 0
            return int(v) if v else 0
        except Exception:
            return 0

    # Quick first read — many products already have stock at this point
    qty = await _try_saldos()
    if qty > 0:
        return qty

    # Embedded fallback (sometimes stock comes inside GET /produtos but not saldos)
    if parent_current:
        est = parent_current.get("estoque") or {}
        captured = est.get("saldoVirtualTotal") or est.get("saldoFisicoTotal") or 0
        try:
            qty = int(captured) if captured else 0
        except (TypeError, ValueError):
            qty = 0
        if qty > 0:
            return qty

    # Poll with backoff — JohnDrop sync can lag up to ~45s after product creation
    for attempt in range(1, max_attempts + 1):
        await add_log(
            "info",
            f"Estoque ainda 0 — aguardando {delay_s:.0f}s pelo sync "
            f"JohnDrop→Bling (tentativa {attempt}/{max_attempts})",
        )
        await _asyncio.sleep(delay_s)
        qty = await _try_saldos()
        if qty > 0:
            await add_log("info", f"Estoque sincronizado: {qty} unidades capturadas")
            return qty
    await add_log(
        "warning",
        f"Estoque do pai pid={parent_id} permaneceu 0 após {max_attempts * delay_s:.0f}s "
        "— variações serão criadas com 0 unidades.",
    )
    return 0


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
    """Push parent images onto each child variation so the Bling listing shows
    a thumbnail per variation.

    IMPORTANT history note: an earlier version of this function filtered out
    "AWSAccessKeyId" / "X-Amz-Signature" URLs (S3 presigned). That was WRONG —
    Bling downloads the image at request time, so a short-lived presigned URL is
    fine. The filter caused JohnDrop images (which are S3 presigned) to be
    silently dropped, leaving variations imageless. The filter is now removed.

    Returns the count of variations that accepted the images. We send via
    `midia.imagens.imagensURL` which Bling does honor on variations.
    """
    clean_urls = [u for u in (image_urls or []) if u and not u.startswith("data:") and not u.startswith("blob:")]
    if not clean_urls or not child_ids:
        return 0
    payload_imgs = [{"link": u} for u in clean_urls[:12]]
    ok = 0
    failures: List[str] = []
    for cid in child_ids:
        try:
            r = await bling_service.bling_request(
                "PATCH", f"/produtos/{cid}",
                json={"midia": {"imagens": {"imagensURL": payload_imgs}}},
            )
            if r.status_code < 400:
                ok += 1
            else:
                failures.append(f"{cid}:{r.status_code}")
        except Exception as e:
            failures.append(f"{cid}:{type(e).__name__}")
            continue
    if ok:
        await add_log(
            "info",
            f"Imagens enviadas para {ok}/{len(child_ids)} variações ({len(payload_imgs)} cada)",
        )
    if failures:
        await add_log(
            "warning",
            f"Imagens em variações: {len(failures)} falhas — {', '.join(failures[:5])}",
        )
    return ok


async def redistribute_all_variation_stocks(max_items: int = 100) -> dict:
    """Scan Bling for variation parents (formato=V) where children have 0 stock
    while parent has stock. Redistribute from parent to children equally.
    Used to fix products cadastrated before the stock fix landed."""
    import asyncio as _asyncio
    fixed = 0
    scanned = 0
    pagina = 1
    while scanned < max_items and pagina < 20:
        r = await bling_service.bling_request("GET", "/produtos", params={"pagina": pagina, "limite": 50})
        if r.status_code >= 400:
            break
        items = (r.json() or {}).get("data") or []
        if not items:
            break
        for it in items:
            scanned += 1
            pid = it.get("id")
            if not pid:
                continue
            # Read full
            full = await bling_service.bling_request("GET", f"/produtos/{pid}")
            if full.status_code >= 400:
                continue
            data = (full.json() or {}).get("data") or {}
            if (data.get("formato") or "").upper() != "V":
                continue
            children = data.get("variacoes") or []
            if not children:
                continue
            # Get parent stock via /saldos
            sr = await bling_service.bling_request("GET", "/estoques/saldos", params={"idsProdutos[]": pid})
            total = 0
            rows = (sr.json() or {}).get("data") or [] if sr.status_code < 400 else []
            if rows:
                total = int(rows[0].get("saldoVirtualTotal") or 0)
            if total <= 0:
                continue
            # Distribute
            dep_id = await _get_default_deposito_id()
            if not dep_id:
                continue
            per_child = total // len(children)
            remainder = total - per_child * len(children)
            for i, v in enumerate(children):
                cid = v.get("id")
                if not cid:
                    continue
                qty = per_child + (1 if i < remainder else 0)
                await bling_service.bling_request("POST", "/estoques", json={
                    "produto": {"id": cid}, "deposito": {"id": dep_id},
                    "operacao": "B", "quantidade": qty,
                    "observacoes": "Redistribuição em lote",
                })
            # Zero parent
            await bling_service.bling_request("POST", "/estoques", json={
                "produto": {"id": pid}, "deposito": {"id": dep_id},
                "operacao": "B", "quantidade": 0,
                "observacoes": "Pai zerado pós-distribuição",
            })
            fixed += 1
            await _asyncio.sleep(0.3)
        pagina += 1
    return {"ok": True, "scanned": scanned, "fixed": fixed}


async def fix_existing_variations(parent_id: int) -> dict:
    """Read existing variations of a parent and report their state.

    Note: the manual UI trick "disable cloneInfo, save, re-enable, save" to
    materialize per-variation thumbnails CANNOT be replicated via Bling API.
    Variations created via API still inherit parent images via cloneInfo on the
    detail screen. For per-variation thumbnails in the LIST view, the user must
    perform the toggle manually in the Bling UI.

    This function exists so the UI can show how many children a parent has.
    """
    r = await bling_service.bling_request("GET", f"/produtos/{parent_id}")
    if r.status_code >= 400:
        return {"ok": False, "reason": "produto não encontrado"}
    parent = (r.json() or {}).get("data") or {}
    children = parent.get("variacoes") or []
    return {
        "ok": True, "fixed": 0, "failed": 0, "total": len(children),
        "note": "Para materializar thumbnails de variação use a UI do Bling: desabilitar cloneInfo, salvar, reabilitar, salvar.",
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
