"""Compatibility guard for description-driven JohnDrop variations.

Variation names and stock labels are taken from explicit Cor/Tamanho/Modelo
sections in the supplier description. Product-feature text and image guesses do
not create, remove, or reclassify options.
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Set

from color_analyzer import detect_dominant_colors
from variation_stock_rules import (
    distribute_stock,
    parse_variation_quantities,
    parse_variation_sections,
    variation_kind,
)


def _image_urls_from_source(images: Iterable[object]) -> List[str]:
    urls: List[str] = []
    for item in images or []:
        if isinstance(item, str) and item.strip():
            urls.append(item.strip())
        elif isinstance(item, dict):
            for key in ("link", "url", "image_url", "src"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    urls.append(value.strip())
                    break
    return urls


def _colors_from_images(images: Optional[Sequence[object]]) -> Set[str]:
    colors: Set[str] = set()
    for image_url in _image_urls_from_source(images):
        try:
            colors.update(str(color).strip().lower() for color in detect_dominant_colors(image_url, top_n=4) if color)
        except Exception:
            continue
    return colors


def _install_runtime_rules() -> None:
    """Bridge the existing enrichment API to the strict parser and stock rules.

    The current call chain passes plain option names to Bling. Install these
    small adapters when that chain reaches the variation guard, avoiding a
    second, image-based filter that previously dropped valid exhausted colors.
    """
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
            # Mark every option explicit so the existing creator applies each
            # exact balance, including zero, without re-splitting the full total.
            kwargs["explicit_quantities"] = plan
            # A positive sentinel prevents a redundant stock poll when the true
            # total is zero. All children are explicit zero in that case.
            kwargs["total_stock"] = max(1, int(total or 0))
        return await original_create(*args, **kwargs)

    create_with_description_stock._description_stock_rules = True
    bling_variations.create_variations = create_with_description_stock


def filter_variations_by_color(
    variations: Optional[List[str]],
    raw_description: str = "",
    images: Optional[Sequence[object]] = None,
) -> List[str]:
    """Return description-declared options without visual/image rejection."""
    _install_runtime_rules()
    parsed = parse_variation_sections(raw_description or "")
    if parsed:
        return parsed
    # Keep backwards compatibility for callers with pre-parsed non-color options;
    # the standard enrichment path always stores and supplies the raw description.
    return [value for value in (variations or []) if value and str(value).strip()]


def should_create_color_variation(raw_description: str, images: Optional[Sequence[object]]) -> bool:
    """Kept for compatibility; explicit description sections are authoritative."""
    return bool(parse_variation_sections(raw_description))


__all__ = ["filter_variations_by_color", "should_create_color_variation"]
