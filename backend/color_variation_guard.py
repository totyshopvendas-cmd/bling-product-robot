from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence, Set

from color_analyzer import detect_dominant_colors


COLOR_ALIASES = {
    "azul": "azul",
    "azul claro": "azul",
    "azul escuro": "azul",
    "blau": "azul",
    "blue": "azul",
    "preto": "preto",
    "preta": "preto",
    "black": "preto",
    "branco": "branco",
    "branca": "branco",
    "white": "branco",
    "vermelho": "vermelho",
    "vermelha": "vermelho",
    "red": "vermelho",
    "verde": "verde",
    "verde claro": "verde",
    "verde escuro": "verde",
    "green": "verde",
    "amarelo": "amarelo",
    "amarela": "amarelo",
    "yellow": "amarelo",
    "rosa": "rosa",
    "rosa claro": "rosa",
    "pink": "rosa",
    "roxo": "roxo",
    "roxa": "roxo",
    "purple": "roxo",
    "laranja": "laranja",
    "orange": "laranja",
    "marrom": "marrom",
    "marron": "marrom",
    "bege": "bege",
    "cinza": "cinza",
    "cinza claro": "cinza",
    "cinza escuro": "cinza",
    "grey": "cinza",
    "gray": "cinza",
    "dourado": "dourado",
    "prata": "prata",
    "silver": "prata",
    "vinho": "vinho",
}


def _normalize_color_token(value: str) -> str:
    if not value:
        return ""
    token = value.strip().lower().replace("-", " ").replace("_", " ")
    token = re.sub(r"\s+", " ", token)
    if token in COLOR_ALIASES:
        return COLOR_ALIASES[token]
    return token


def _is_color_name(value: str) -> bool:
    return _normalize_color_token(value) in set(COLOR_ALIASES.values())


def _contains_color_description(raw_description: str) -> bool:
    if not raw_description:
        return False
    text = raw_description.lower()
    for keyword in COLOR_ALIASES.keys():
        if keyword in text:
            return True
    return False


def _image_urls_from_source(images: Iterable[object]) -> List[str]:
    urls: List[str] = []
    for item in images or []:
        if isinstance(item, str):
            if item.strip():
                urls.append(item.strip())
        elif isinstance(item, dict):
            for key in ("link", "url", "image_url", "src"):
                val = item.get(key)
                if isinstance(val, str) and val.strip():
                    urls.append(val.strip())
                    break
    return urls


def _colors_from_images(images: Optional[Sequence[object]]) -> Set[str]:
    colors: Set[str] = set()
    for image_url in _image_urls_from_source(images):
        try:
            detected = detect_dominant_colors(image_url, top_n=4)
            for item in detected:
                normalized = _normalize_color_token(item)
                if normalized:
                    colors.add(normalized)
        except Exception:
            continue
    return colors


def filter_variations_by_color(
    variations: Optional[List[str]],
    raw_description: str = "",
    images: Optional[Sequence[object]] = None,
) -> List[str]:
    """Mantém variações de tamanho/modelo intactas e filtra apenas variações de cor.

    Regra pedida:
    - somente usa análise visual quando a descrição menciona cor;
    - só mantém a variação de cor se a imagem confirmar a cor;
    - outras variações (tamanho/modelo) continuam sem ser alteradas.
    """
    if not variations:
        return []

    image_colors = _colors_from_images(images)
    if not image_colors:
        return [v for v in variations if not _is_color_name(v)]

    filtered: List[str] = []
    for variation in variations:
        variation_text = (variation or "").strip()
        if not variation_text:
            continue

        if not _is_color_name(variation_text):
            filtered.append(variation_text)
            continue

        if not _contains_color_description(raw_description):
            filtered.append(variation_text)
            continue

        normalized_variation = _normalize_color_token(variation_text)
        if normalized_variation in image_colors:
            filtered.append(variation_text)

    return filtered


def should_create_color_variation(raw_description: str, images: Optional[Sequence[object]]) -> bool:
    if not _contains_color_description(raw_description):
        return False
    return bool(_colors_from_images(images))


__all__ = [
    "filter_variations_by_color",
    "should_create_color_variation",
    "_contains_color_description",
]
