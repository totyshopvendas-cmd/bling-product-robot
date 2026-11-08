"""Regression tests for the G9000PRO JohnDrop description and stock rules."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from variation_stock_rules import distribute_stock, parse_variation_quantities, parse_variation_sections


G9000_DESCRIPTION = """Código de barras: 6954284000320

Headset Gamer Profissional Com Microfone Noise Cancelling G9000 PRO

Características Técnicas:
-Modelo: G9000 Pro
-Diâmetro do Driver: 40mm
-Sensibilidade: 105dB +/- 3dB
-Faixa de Frequência: 15Hz - 20KHz

Disponível na cor:
-Preto
-Branco

Medidas da embalagem:
L: 21,5 x C: 11,5 x A: 23,5cm
Peso do Produto: 510g"""


def test_g9000_description_extracts_only_the_two_colors():
    assert parse_variation_sections(G9000_DESCRIPTION) == ["Preto", "Branco"]


def test_g9000_description_does_not_turn_technical_features_into_variations():
    options = parse_variation_sections(G9000_DESCRIPTION)
    assert "G9000 Pro" not in options
    assert "40mm" not in options
    assert "105dB +/- 3dB" not in options
    assert "15Hz - 20KHz" not in options


def test_g9000_without_explicit_stock_splits_existing_total_evenly():
    options = parse_variation_sections(G9000_DESCRIPTION)
    assert distribute_stock(146, options, parse_variation_quantities(G9000_DESCRIPTION, options)) == {
        "Preto": 73, "Branco": 73,
    }


def test_exhausted_color_is_kept_at_zero_and_remainder_is_split():
    description = "Cores disponíveis:\nPreto (esgotado)\nBranco"
    options = parse_variation_sections(description)
    quantities = parse_variation_quantities(description, options)
    assert options == ["Preto", "Branco"]
    assert distribute_stock(146, options, quantities) == {"Preto": 0, "Branco": 146}
