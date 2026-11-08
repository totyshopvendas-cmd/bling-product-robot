"""Description-driven variation rules used by individual and bulk enrichment."""
from __future__ import annotations

import unicodedata
from typing import List, Optional

import bling_service
from variation_stock_rules import (
    distribute_stock,
    parse_variation_quantities,
    parse_variation_sections,
    variation_kind,
)


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return "".join(char.lower() for char in value if char.isalnum())


def _child_option(child: dict) -> str:
    variation = child.get("variacao") or {}
    label = (variation.get("nome") or "").strip()
    if ":" in label:
        return label.split(":", 1)[1].strip()
    if label:
        return label
    name = (child.get("nome") or "").strip()
    for separator in (" - ", " — ", " – "):
        if separator in name:
            name = name.rsplit(separator, 1)[-1].strip()
    if ":" in name:
        name = name.rsplit(":", 1)[-1].strip()
    return name


def _quantity(product: dict) -> int:
    stock = product.get("estoque") or {}
    try:
        return max(0, int(stock.get("saldoVirtualTotal") or stock.get("saldoFisicoTotal") or 0))
    except (TypeError, ValueError):
        return 0


async def _read_existing_children_stock(children: list) -> int:
    """Sum current child inventory when parent balance is zero (common on Bling)."""
    total = sum(_quantity(child) for child in children)
    if total:
        return total
    for child in children:
        child_id = child.get("id")
        if not child_id:
            continue
        try:
            response = await bling_service.bling_request(
                "GET", "/estoques/saldos", params={"idsProdutos[]": child_id},
            )
            if response.status_code >= 400:
                continue
            rows = (response.json() or {}).get("data") or []
            if rows:
                row = rows[0]
                total += max(0, int(row.get("saldoVirtualTotal") or row.get("saldoFisicoTotal") or 0))
        except Exception:
            continue
    return total


def _install_runtime_rules() -> None:
    """Install the strict parser and rebalance existing as well as new children."""
    import sys

    enrichment = sys.modules.get("bling_enrichment")
    if enrichment is not None:
        enrichment._parse_variations = parse_variation_sections
        enrichment._parse_variation_quantities = parse_variation_quantities

    try:
        import bling_variations
    except Exception:
        return
    bling_variations._attribute_kind = variation_kind
    if getattr(bling_variations.create_variations, "_description_stock_rules", False):
        return

    original_create = bling_variations.create_variations

    async def create_with_description_stock(*args, **kwargs):
        parent_id = args[0] if args else kwargs.get("parent_id")
        parent = args[1] if len(args) > 1 else kwargs.get("parent_current")
        names = list(args[2] if len(args) > 2 else kwargs.get("variations") or [])
        explicit = kwargs.get("explicit_quantities") or {}
        total = max(0, int(kwargs.get("total_stock", 0) or 0))
        children_before = (parent or {}).get("variacoes") or []

        if total <= 0 and parent:
            total = _quantity(parent)
        if total <= 0 and children_before:
            total = await _read_existing_children_stock(children_before)
        if total <= 0:
            total = await bling_variations._read_parent_stock_with_retry(parent_id, parent)

        if not names:
            return await original_create(*args, **kwargs)

        plan = distribute_stock(total, names, explicit)
        kwargs["explicit_quantities"] = plan
        # Positive sentinel makes original creator apply an explicit zero balance
        # when actual total is zero and also ensures the parent is set to zero.
        kwargs["total_stock"] = max(1, total)
        result = await original_create(*args, **kwargs)

        # Original creator deliberately skips already existing children. Re-fetch
        # them and apply balances to all matching options, including those skipped.
        try:
            response = await bling_service.bling_request("GET", f"/produtos/{parent_id}")
            latest = (response.json() or {}).get("data") or {} if response.status_code < 400 else parent or {}
            children = latest.get("variacoes") or children_before
            by_option = {_norm(_child_option(child)): child for child in children if _child_option(child)}
            for option, quantity in plan.items():
                child = by_option.get(_norm(option))
                if child and child.get("id"):
                    await bling_variations._set_children_stock([child["id"]], quantity)

            deposit_id = await bling_variations._get_default_deposito_id()
            if deposit_id:
                await bling_service.bling_request(
                    "POST", "/estoques",
                    json={
                        "produto": {"id": parent_id},
                        "deposito": {"id": deposit_id},
                        "operacao": "B",
                        "quantidade": 0,
                        "observacoes": "Pai zerado — estoque distribuído nas variações",
                    },
                )
        except Exception as error:
            await bling_variations.add_log(
                "warning", f"Rebalanceamento de variações existentes falhou pid={parent_id}: {error}",
            )
        return result

    create_with_description_stock._description_stock_rules = True
    bling_variations.create_variations = create_with_description_stock


def filter_variations_by_color(
    variations: Optional[List[str]],
    raw_description: str = "",
    images: Optional[list] = None,
) -> List[str]:
    """Description is authoritative; technical characteristics and images are not."""
    _install_runtime_rules()
    parsed = parse_variation_sections(raw_description or "")
    return parsed if parsed else [value for value in (variations or []) if value and str(value).strip()]


def should_create_color_variation(raw_description: str, images: Optional[list]) -> bool:
    return bool(parse_variation_sections(raw_description))


__all__ = ["filter_variations_by_color", "should_create_color_variation"]
