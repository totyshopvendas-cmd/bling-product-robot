"""Tests for strict description-based variation and stock interpretation."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from variation_stock_rules import (
    distribute_stock,
    parse_variation_quantities,
    parse_variation_sections,
    variation_kind,
)


def test_only_explicit_variation_sections_are_parsed():
    description = (
        "Características:\\nMaterial: plástico\\nPotência: 12W\\n\\n"
        "Cores disponíveis: Azul, Verde\\n\\n"
        "Tamanhos disponíveis:\\nP\\nM\\n\\n"
        "Modelos disponíveis: Modelo A; Modelo B"
    )
    assert parse_variation_sections(description) == ["Azul", "Verde", "P", "M", "Modelo A", "Modelo B"]


def test_features_without_variation_headings_are_ignored():
    description = "Características: Corpo azul, tamanho compacto, modelo resistente, 12W."
    assert parse_variation_sections(description) == []


def test_exhausted_and_explicit_quantities_are_read():
    description = "Cores disponíveis:\\nAzul (esgotado)\\nVerde: 7\\nPreto (sem estoque)"
    variations = ["Azul", "Verde", "Preto"]
    assert parse_variation_quantities(description, variations) == {"Azul": 0, "Verde": 7, "Preto": 0}


def test_stock_uses_explicit_quantities_then_splits_remainder():
    assert distribute_stock(100, ["Azul", "Verde", "Preto"], {"Azul": 0, "Verde": 20}) == {
        "Azul": 0, "Verde": 20, "Preto": 80,
    }


def test_unknown_variation_is_model_not_color():
    assert variation_kind("Modelo A") == "Modelo"
    assert variation_kind("M") == "Tamanho"
    assert variation_kind("Azul claro") == "Cor"
