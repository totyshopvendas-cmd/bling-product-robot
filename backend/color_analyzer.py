from __future__ import annotations

import io
import os
import re
from collections import Counter
from typing import Iterable, List, Sequence, Union

import numpy as np
import requests
from PIL import Image


COLOR_PALETTE: dict[str, tuple[int, int, int]] = {
    "preto": (0, 0, 0),
    "branco": (255, 255, 255),
    "azul": (30, 90, 190),
    "vermelho": (200, 30, 40),
    "verde": (30, 140, 70),
    "amarelo": (220, 190, 30),
    "rosa": (220, 120, 170),
    "roxo": (120, 60, 170),
    "laranja": (220, 120, 30),
    "bege": (190, 165, 120),
    "cinza": (140, 140, 140),
    "marrom": (120, 80, 50),
    "dourado": (180, 140, 40),
    "prata": (180, 180, 180),
}


def _euclidean_distance(a: Sequence[int], b: Sequence[int]) -> float:
    return float(sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5)


def normalize_rgb_to_name(rgb: Sequence[int]) -> str:
    """Converte um RGB em nome de cor em português usando aproximação por distância."""
    if len(rgb) != 3:
        raise ValueError("rgb precisa conter exatamente 3 canais (R, G, B)")

    r, g, b = (int(v) for v in rgb)
    best_name = "preto"
    best_distance = float("inf")
    for name, target in COLOR_PALETTE.items():
        distance = _euclidean_distance((r, g, b), target)
        if distance < best_distance:
            best_distance = distance
            best_name = name
    return best_name


def _load_image_from_source(source: Union[str, bytes, os.PathLike, io.BytesIO]) -> Image.Image:
    if isinstance(source, (bytes, bytearray)):
        return Image.open(io.BytesIO(source)).convert("RGB")

    if hasattr(source, "read"):
        return Image.open(source).convert("RGB")

    if isinstance(source, str):
        value = source.strip()
        if not value:
            raise ValueError("Caminho ou URL de imagem vazio")

        if re.match(r"^https?://", value, re.IGNORECASE):
            response = requests.get(value, timeout=20)
            response.raise_for_status()
            return Image.open(io.BytesIO(response.content)).convert("RGB")

        return Image.open(value).convert("RGB")

    raise TypeError("Tipo de imagem não suportado")


def _quantize_pixels(pixels: np.ndarray, bucket: int = 32) -> np.ndarray:
    return np.floor(pixels / bucket) * bucket


def detect_dominant_colors(
    image_source: Union[str, bytes, os.PathLike, io.BytesIO],
    top_n: int = 5,
    resize_to: int = 120,
) -> List[str]:
    """Detecta as cores dominantes de uma imagem e devolve nomes em português.

    Exemplos:
      detect_dominant_colors("product.jpg")
      detect_dominant_colors("https://.../image.jpg")
    """
    image = _load_image_from_source(image_source).convert("RGB")
    image = image.resize((resize_to, resize_to), Image.Resampling.LANCZOS)
    pixels = np.asarray(image).reshape(-1, 3)
    quantized = _quantize_pixels(pixels)

    colors = Counter(tuple(int(v) for v in pixel) for pixel in quantized)
    ordered = []
    for rgb, _ in colors.most_common(top_n * 5):
        nome = normalize_rgb_to_name(rgb)
        if nome not in ordered:
            ordered.append(nome)
        if len(ordered) >= top_n:
            break

    return ordered


def extract_variation_names_from_image(
    image_source: Union[str, bytes, os.PathLike, io.BytesIO],
    top_n: int = 4,
) -> List[str]:
    """Alias mais semântico para uso em variações de produto.

    Exemplo de retorno: ['preto', 'azul', 'branco']
    """
    return detect_dominant_colors(image_source=image_source, top_n=top_n)


__all__ = [
    "detect_dominant_colors",
    "extract_variation_names_from_image",
    "normalize_rgb_to_name",
]
