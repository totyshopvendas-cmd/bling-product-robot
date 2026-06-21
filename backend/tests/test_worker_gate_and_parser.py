"""Regression tests for the two bugs fixed during the JohnDrop→Bling
enrichment investigation:

  1. enrich_worker._is_ready_for_enrichment must wait for IMAGES
     (the "bagagem") to land in Bling — stock alone is NOT a green light.
     Enriching before images arrive corrupts the JohnDrop native sync and
     leaves the Bling product imageless forever.

  2. bling_enrichment._parse_variations must extract compound color
     names ("Cinza com preto", "Vermelho com preto"). The previous 2-word
     cap silently dropped them and the whole list collapsed to []
     (because of the >=2 rule), losing real variations.
"""
import asyncio
import pytest

from enrich_worker import _is_ready_for_enrichment
from bling_enrichment import _parse_variations


# ---------- Worker gate ----------

def _product(images: int = 0, stock: int = 0) -> dict:
    return {
        "midia": {"imagens": {
            "internas": [{"url": f"i{i}.jpg"} for i in range(images)],
            "externas": [],
        }},
        "estoque": {"saldoVirtualTotal": stock},
    }


def test_worker_waits_when_no_images_even_if_stock_present():
    """REGRA do usuário: o robô deve esperar a 'bagagem' (imagens) pousar."""
    ready, reason = asyncio.run(_is_ready_for_enrichment(_product(images=0, stock=89)))
    assert ready is False, "Não pode enriquecer antes das imagens chegarem"
    assert "no_images" in reason


def test_worker_ready_when_at_least_one_image():
    ready, reason = asyncio.run(_is_ready_for_enrichment(_product(images=1, stock=0)))
    assert ready is True
    assert "images=1" in reason


def test_worker_ready_with_many_images():
    ready, _ = asyncio.run(_is_ready_for_enrichment(_product(images=9, stock=120)))
    assert ready is True


def test_worker_waits_with_no_signal():
    ready, _ = asyncio.run(_is_ready_for_enrichment(_product(images=0, stock=0)))
    assert ready is False


# ---------- Variation parser ----------

def test_parse_compound_color_names_three_words():
    """MOU-2820B real description: três cores onde duas são compostas com 'com'."""
    raw = (
        "Mouse Óptico sem Fio PC Notebook com 1600dpi\n"
        "\n"
        "Cores disponíveis: \n"
        " -Preto \n"
        " -Cinza com preto \n"
        " -Vermelho com preto\n"
        "  \n"
        "Medidas da embalagem: 17x5x17cm"
    )
    out = _parse_variations(raw)
    assert "Preto" in out, f"esperava Preto em {out}"
    assert any("Cinza" in v for v in out), f"esperava Cinza... em {out}"
    assert any("Vermelho" in v for v in out), f"esperava Vermelho... em {out}"
    assert len(out) >= 2, f"esperava ao menos 2 variações, achei {out}"


def test_parse_simple_colors():
    raw = "Disponível nas cores: Azul, Verde, Vermelho"
    out = _parse_variations(raw)
    assert set(out) >= {"Azul", "Verde", "Vermelho"}, out


def test_parse_single_color_returns_empty():
    """Uma cor só não conta como variação."""
    raw = "Disponível na cor Preto"
    out = _parse_variations(raw)
    # singular "cor" + 1 item → vazio (gate de plural)
    assert out == []


def test_parse_disclaimer_blocks_variations():
    raw = "Cores disponíveis conforme disponibilidade do estoque: Azul, Verde"
    out = _parse_variations(raw)
    assert out == [], "Disclaimer deve bloquear extração"


def test_parse_filters_descriptive_phrases():
    """Não deve extrair frases descritivas longas."""
    raw = (
        "Cores disponíveis: \n"
        " -Preto \n"
        " -Branco \n"
        " -Ideal Para Setups Temáticos\n"
        "\n"
        "Medidas:"
    )
    out = _parse_variations(raw)
    assert "Preto" in out
    assert "Branco" in out
    assert not any("Ideal" in v for v in out), f"Frase descritiva vazou: {out}"
