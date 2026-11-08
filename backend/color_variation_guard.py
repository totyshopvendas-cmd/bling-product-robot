"""Compatibility guard for description-driven JohnDrop variations."""
from __future__ import annotations

from typing import List, Optional, Sequence

from variation_stock_rules import (
    distribute_stock,
    parse_variation_quantities,
    parse_variation_sections,
    variation_kind,
)


def _install_runtime_rules() -> None:
    """Use the same strict parser and stock allocation in single and bulk enrichment."""
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
        names = args[2] if len(args) > 2 else kwargs.get("variations") or []
        explicit = kwargs.get("explicit_quantities") or {}
        total = kwargs.get("total_stock", 0)
        if not total or total <= 0:
            total = await bling_variations._read_parent_stock_with_retry(parent_id, parent)
        if names:
            plan = distribute_stock(total, list(names), explicit)
            # Apply an exact balance to every option, including explicit zero.
            kwargs["explicit_quantities"] = plan
            # Existing creator uses this value to decide whether to zero parent.
            # Make it positive even when captured stock is zero; all planned child
            # amounts are explicitly set to zero in that case.
            kwargs["total_stock"] = max(1, int(total or 0))
        return await original_create(*args, **kwargs)

    create_with_description_stock._description_stock_rules = True
    bling_variations.create_variations = create_with_description_stock


def filter_variations_by_color(
    variations: Optional[List[str]],
    raw_description: str = "",
    images: Optional[Sequence[object]] = None,
) -> List[str]:
    """Use explicit description sections as source of truth; never reject by image."""
    _install_runtime_rules()
    parsed = parse_variation_sections(raw_description or "")
    return parsed if parsed else [value for value in (variations or []) if value and str(value).strip()]


def should_create_color_variation(raw_description: str, images: Optional[Sequence[object]]) -> bool:
    """Compatibility helper: only explicit variation sections qualify."""
    return bool(parse_variation_sections(raw_description))


__all__ = ["filter_variations_by_color", "should_create_color_variation"]
