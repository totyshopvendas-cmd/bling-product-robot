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


def _normalize_variation_label(value: str) -> str:
    """Normalize values like 'Cor:Azul' or 'Azul' to a comparable label."""
    text = (value or "").strip()
    if not text:
        return ""
    return text.split(":", 1)[-1].strip() if ":" in text else text.strip()


def _dedupe_image_urls(image_urls: Optional[List[str]]) -> List[str]:
    """Keep only valid URLs and remove duplicates by basename."""
    seen = set()
    deduped: List[str] = []
    for u in image_urls or []:
        if not u or u.startswith(("data:", "blob:")):
            continue
        key = u.split("?")[0].split("#")[0].rsplit("/", 1)[-1]
        if key not in seen:
            seen.add(key)
            deduped.append(u)
    return deduped


async def _count_product_images(product_id: int) -> int:
    """Return the number of images currently attached to a Bling product."""
    try:
        r = await bling_service.bling_request("GET", f"/produtos/{product_id}")
        if r.status_code >= 400:
            return 0
        data = (r.json() or {}).get("data") or {}
        imgs = (data.get("midia") or {}).get("imagens") or {}
        return len((imgs.get("internas") or [])) + len((imgs.get("externas") or []))
    except Exception:
        return 0


async def create_variations(
    parent_id: int,
    parent_current: Optional[dict],
    variations: List[str],
    total_stock: int = 0,
    parent_images: Optional[List[str]] = None,
    explicit_quantities: Optional[dict] = None,
    variation_images: Optional[dict] = None,
) -> dict:
    """Create variations on a Bling parent. Single PATCH call.

    Returns {created, per_child_stock}.

    `variation_images` supports a per-variation image map such as:
      {"Cor:Azul": [url1, url2], "Cor:Rosa": [url3]}
    or simply:
      {"Azul": [url1], "Rosa": [url3]}

    When present, each child variation receives only the images mapped to its
    variation label; otherwise it falls back to the parent images list.
    """
    if not variations:
        return {"created": 0, "per_child_stock": 0}

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

    if not total_stock or total_stock <= 0:
        total_stock = await _read_parent_stock_with_retry(parent_id, parent_current)
        await add_log(
            "info",
            f"Variações pid={parent_id}: estoque capturado do pai = {total_stock} "
            f"(será dividido entre {len(variations)} variações)",
        )

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
        sigla = _variation_sigla(clean)
        child_sku = f"{parent_sku}-{sigla}" if parent_sku else ""
        new_vars.append({
            "nome": f"{parent_name} {clean}"[:120],
            "codigo": child_sku,
            "preco": float(parent_price) if parent_price else 0.0,
            "tipo": "P",
            "situacao": "A",
            "formato": "S",
            "variacao": {
                "nome": var_label,
                "produtoPai": {"cloneInfo": True},
            },
        })

    if not new_vars:
        return {"created": 0, "per_child_stock": 0, "skipped": skipped}

    is_already_v = (parent_current.get("formato") or "").upper() == "V"
    payload: dict = {"variacoes": new_vars}
    if not is_already_v:
        payload["formato"] = "V"
        payload["actionEstoque"] = "Z"

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

    name_to_id: dict = {}
    try:
        body_data = (resp.json() or {}).get("data") or {}
        for s in (body_data.get("variations") or {}).get("saved") or []:
            sid = s.get("id")
            nome_var = s.get("nomeVariacao") or ""
            label = nome_var.split(":", 1)[-1].strip() if ":" in nome_var else nome_var.strip()
            if sid and label:
                name_to_id[label] = sid
    except Exception:
        pass

    per_child = 0
    if saved_ids:
        explicit_map = explicit_quantities or {}
        remaining_ids = list(saved_ids)
        for var_name, qty in explicit_map.items():
            sid = name_to_id.get(var_name)
            if not sid:
                continue
            qty_int = max(0, int(qty))
            await _set_children_stock([sid], qty_int)
            if sid in remaining_ids:
                remaining_ids.remove(sid)
        if total_stock and total_stock > 0 and remaining_ids:
            per_child = total_stock // len(remaining_ids)
            remainder = total_stock - per_child * len(remaining_ids)
            for i, sid in enumerate(remaining_ids):
                qty = per_child + (1 if i < remainder else 0)
                await _set_children_stock([sid], qty)

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

    if saved_ids:
        fallback_urls = _dedupe_image_urls(parent_images or [])
        try:
            r = await bling_service.bling_request("GET", f"/produtos/{parent_id}")
            if r.status_code < 400:
                refreshed = (r.json() or {}).get("data") or {}
                imgs = (refreshed.get("midia") or {}).get("imagens") or {}
                internal = [(i.get("link") or "") for i in (imgs.get("internas") or []) if i.get("link")]
                if internal:
                    fallback_urls = _dedupe_image_urls(internal[:12])
        except Exception:
            pass

        per_child_images: dict[int, List[str]] = {}
        if isinstance(variation_images, dict):
            for label, urls in variation_images.items():
                if not isinstance(urls, list):
                    continue
                target_urls = _dedupe_image_urls(urls)
                if not target_urls:
                    continue
                child_id = name_to_id.get(label)
                if child_id is None:
                    normalized_label = _normalize_variation_label(label)
                    child_id = name_to_id.get(normalized_label)
                if child_id is not None:
                    per_child_images[int(child_id)] = target_urls

        for sid in saved_ids:
            if sid not in per_child_images:
                per_child_images[sid] = fallback_urls

        await _copy_images_to_children(saved_ids, fallback_urls, per_child_images)

    flat = ", ".join(variations[:6]) + ("…" if len(variations) > 6 else "")
    await add_log(
        "success",
        f"Variações criadas pid={parent_id}: {created} ({flat}) — estoque/variação: {per_child}",
    )
    return {"created": created, "per_child_stock": per_child, "skipped": skipped}


_DEPOSITO_CACHE: dict = {"id": None, "checked": False}


_SIGLA_MAP = {
    "azul": "AZ", "amarelo": "AM", "amarela": "AM",
    "branco": "BR", "branca": "BR",
    "preto": "PT", "preta": "PT",
    "verde": "VD",
    "vermelho": "VM", "vermelha": "VM",
    "rosa": "RS", "roxo": "RX", "roxa": "RX",
    "laranja": "LJ", "marrom": "MR",
    "cinza": "CZ", "prata": "PR", "dourado": "DR", "dourada": "DR",
    "bege": "BG", "lilas": "LL", "ciano": "CN",
    "pequeno": "P", "pequena": "P",
    "medio": "M", "media": "M", "médio": "M", "média": "M",
    "grande": "G",
    "pp": "PP", "p": "P", "m": "M", "g": "G", "gg": "GG", "xg": "XG", "xgg": "XGG",
    "110v": "110", "220v": "220", "bivolt": "BV",
}


def _variation_sigla(name: str) -> str:
    """Return a 2-char abbreviation for a variation name."""
    clean = (name or "").strip().lower()
    if not clean:
        return "XX"
    if clean in _SIGLA_MAP:
        return _SIGLA_MAP[clean]
    for k, v in _SIGLA_MAP.items():
        if clean.startswith(k) or clean.endswith(k):
            return v
    alnum = "".join(c for c in clean if c.isalnum())
    return (alnum[:2] or "XX").upper()


async def _read_parent_stock_with_retry(
    parent_id: int, parent_current: Optional[dict] = None,
    max_attempts: int = 6, delay_s: float = 10.0,
) -> int:
    """Read parent product's current stock from Bling with retry/backoff."""
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

    qty = await _try_saldos()
    if qty > 0:
        return qty

    if parent_current:
        est = parent_current.get("estoque") or {}
        captured = est.get("saldoVirtualTotal") or est.get("saldoFisicoTotal") or 0
        try:
            qty = int(captured) if captured else 0
        except (TypeError, ValueError):
            qty = 0
        if qty > 0:
            return qty

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
    """Set each child variation's stock to absolute `qty` via POST /estoques."""
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


async def _count_variation_images(child_id: int) -> int:
    """Return how many images a variation child currently has in Bling."""
    try:
        r = await bling_service.bling_request("GET", f"/produtos/{child_id}")
        if r.status_code >= 400:
            return 0
        data = (r.json() or {}).get("data") or {}
        imgs = (data.get("midia") or {}).get("imagens") or {}
        return len((imgs.get("internas") or [])) + len((imgs.get("externas") or []))
    except Exception:
        return 0


async def _copy_images_to_children(
    child_ids: List[int],
    image_urls: List[str],
    per_child_images: Optional[dict[int, List[str]]] = None,
) -> int:
    """Push parent images onto each child variation.

    When `per_child_images` is supplied, each child receives its own mapped list
    of images; otherwise, the generic parent image list is applied to all.
    """
    fallback_urls = _dedupe_image_urls(image_urls)
    if not child_ids:
        return 0

    ok = 0
    failures: List[str] = []
    for cid in child_ids:
        cid_urls = per_child_images.get(cid, fallback_urls) if per_child_images else fallback_urls
        cid_urls = _dedupe_image_urls(cid_urls)
        if not cid_urls:
            continue
        payload_imgs = [{"link": u} for u in cid_urls[:12]]
        applied = False
        for attempt in range(1, 4):
            try:
                await bling_service.bling_request(
                    "PATCH", f"/produtos/{cid}",
                    json={"midia": {"imagens": {"imagensURL": [], "internas": [], "externas": []}}},
                )
                r = await bling_service.bling_request(
                    "PATCH", f"/produtos/{cid}",
                    json={"midia": {"imagens": {"imagensURL": payload_imgs}}},
                )
                if r.status_code < 400 and await _count_variation_images(cid) > 0:
                    applied = True
                    break
            except Exception as e:
                failures.append(f"{cid}:{type(e).__name__}")
                break
            if not applied and attempt < 3:
                await add_log("info", f"Retry imagem variação {cid} tentativa {attempt + 1}/3")
        if applied:
            ok += 1
        else:
            failures.append(f"{cid}:no_image_after_retry")
    if ok:
        await add_log(
            "info",
            f"Imagens replicadas em {ok}/{len(child_ids)} variações; mapeamento por variação aplicado quando houver imagens específicas.",
        )
    if failures:
        await add_log(
            "warning",
            f"Replicação de imagens falhou em {len(failures)} variações: "
            f"{', '.join(failures[:5])}",
        )
    return ok


async def redistribute_all_variation_stocks(max_items: int = 100) -> dict:
    """Scan Bling for variation parents (formato=V) where children have 0 stock
    while parent has stock. Redistribute from parent to children equally."""
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
            full = await bling_service.bling_request("GET", f"/produtos/{pid}")
            if full.status_code >= 400:
                continue
            data = (full.json() or {}).get("data") or {}
            if (data.get("formato") or "").upper() != "V":
                continue
            children = data.get("variacoes") or []
            if not children:
                continue
            sr = await bling_service.bling_request("GET", "/estoques/saldos", params={"idsProdutos[]": pid})
            total = 0
            rows = (sr.json() or {}).get("data") or [] if sr.status_code < 400 else []
            if rows:
                total = int(rows[0].get("saldoVirtualTotal") or 0)
            if total <= 0:
                continue
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
            await bling_service.bling_request("POST", "/estoques", json={
                "produto": {"id": pid}, "deposito": {"id": dep_id},
                "operacao": "B", "quantidade": 0,
                "observacoes": "Pai zerado pós-distribuição",
            })
            fixed += 1
            await _asyncio.sleep(0.3)
        pagina += 1
    return {"ok": True, "scanned": scanned, "fixed": fixed}


async def fix_existing_variations(parent_id: int, variation_images: Optional[dict] = None) -> dict:
    """Repair image propagation for existing variations.

    If `variation_images` is supplied with per-variation image maps, each child gets
    only the images mapped to its variation; otherwise, the parent image list is
    used as fallback for all children.
    """
    r = await bling_service.bling_request("GET", f"/produtos/{parent_id}")
    if r.status_code >= 400:
        return {"ok": False, "reason": "produto não encontrado"}
    parent = (r.json() or {}).get("data") or {}
    children = parent.get("variacoes") or []
    if not children:
        return {"ok": True, "fixed": 0, "failed": 0, "total": 0, "note": "pai sem variações"}

    imgs = (parent.get("midia") or {}).get("imagens") or {}
    fallback_urls = [(i.get("link") or "") for i in (imgs.get("internas") or []) if i.get("link")]
    if not fallback_urls:
        fallback_urls = [(i.get("link") or "") for i in (imgs.get("externas") or []) if i.get("link")]
    fallback_urls = _dedupe_image_urls(fallback_urls)

    child_ids = [c.get("id") for c in children if c.get("id")]
    per_child_images: dict[int, List[str]] = {}
    if isinstance(variation_images, dict):
        for label, urls in variation_images.items():
            if not isinstance(urls, list):
                continue
            target_urls = _dedupe_image_urls(urls)
            if not target_urls:
                continue
            # The UI typically exposes "Cor:Azul" names, but sometimes only "Azul".
            matched = None
            for child in children:
                child_label = (child.get("variacao") or {}).get("nome") or ""
                if child_label == label or _normalize_variation_label(child_label) == _normalize_variation_label(label):
                    matched = child.get("id")
                    break
            if matched is not None:
                per_child_images[int(matched)] = target_urls

    copied = 0
    if child_ids:
        copied = await _copy_images_to_children(child_ids, fallback_urls, per_child_images)
    return {
        "ok": True,
        "fixed": copied,
        "failed": max(0, len(child_ids) - copied),
        "total": len(child_ids),
        "note": "Imagem do pai propagada para as variações quando possível; imagens por variação são aplicadas em primeiro lugar, fallback no pai em segundo lugar.",
    }


async def find_and_create(
    parent_sku: str,
    variations: List[str],
    total_stock: int = 0,
    parent_images: Optional[List[str]] = None,
    variation_images: Optional[dict] = None,
) -> dict:
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
    full_resp = await bling_service.bling_request("GET", f"/produtos/{target['id']}")
    full = (full_resp.json() or {}).get("data") if full_resp.status_code < 400 else target
    result = await create_variations(
        target["id"],
        full,
        variations,
        total_stock,
        parent_images=parent_images,
        variation_images=variation_images,
    )
    return {"ok": True, **result}
