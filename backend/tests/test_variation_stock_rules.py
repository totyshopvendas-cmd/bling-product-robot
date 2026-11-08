"""Regression tests for description-based variation and stock handling."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from variation_stock_rules import distribute_stock, parse_variation_quantities, parse_variation_sections, variation_kind


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


def test_only_explicit_variation_sections_are_parsed():
    description = (
        "Características:\nMaterial: plástico\nPotência: 12W\n\n"
        "Cores disponíveis: Azul, Verde\n\n"
        "Tamanhos disponíveis:\nP\nM\n\n"
        "Modelos disponíveis: Modelo A; Modelo B"
    )
    assert parse_variation_sections(description) == ["Azul", "Verde", "P", "M", "Modelo A", "Modelo B"]


def test_features_without_variation_headings_are_ignored():
    description = "Características: Corpo azul, tamanho compacto, modelo resistente, 12W."
    assert parse_variation_sections(description) == []


def test_g9000_description_extracts_only_the_two_colors():
    assert parse_variation_sections(G9000_DESCRIPTION) == ["Preto", "Branco"]


def test_g9000_technical_specs_are_not_variations():
    options = parse_variation_sections(G9000_DESCRIPTION)
    assert "G9000 Pro" not in options
    assert "40mm" not in options
    assert "105dB +/- 3dB" not in options
    assert "15Hz - 20KHz" not in options


def test_g9000_total_146_splits_evenly_for_existing_colors():
    options = parse_variation_sections(G9000_DESCRIPTION)
    quantities = parse_variation_quantities(G9000_DESCRIPTION, options)
    assert distribute_stock(146, options, quantities) == {"Preto": 73, "Branco": 73}


def test_exhausted_color_stays_at_zero_and_remainder_goes_to_other():
    description = "Cores disponíveis:\nPreto (esgotado)\nBranco"
    options = parse_variation_sections(description)
    quantities = parse_variation_quantities(description, options)
    assert options == ["Preto", "Branco"]
    assert distribute_stock(146, options, quantities) == {"Preto": 0, "Branco": 146}


def test_unknown_variation_is_model_not_color():
    assert variation_kind("Modelo A") == "Modelo"
    assert variation_kind("M") == "Tamanho"
    assert variation_kind("Azul claro") == "Cor"
