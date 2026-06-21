"""Unit tests for stock_sync — variation distribution logic and supplier item
deduplication. Does NOT touch any live JohnDrop/Bling API."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from stock_sync import _distribute_among_variations


def test_distribute_even_split_no_explicit():
    """Total=90 across 3 vars without explicit info → 30/30/30."""
    out = _distribute_among_variations(90, ["Preto", "Cinza", "Vermelho"], {})
    assert out == {"Preto": 30, "Cinza": 30, "Vermelho": 30}


def test_distribute_with_remainder():
    """Total=100 across 3 → 34/33/33 (extra goes to first)."""
    out = _distribute_among_variations(100, ["A", "B", "C"], {})
    assert sum(out.values()) == 100
    assert out["A"] == 34
    assert out["B"] == 33
    assert out["C"] == 33


def test_distribute_with_esgotado():
    """Cor esgotada (qty=0 explícito) e duas dividem o resto."""
    out = _distribute_among_variations(60, ["Preto", "Cinza", "Vermelho"], {"Cinza": 0})
    assert out["Cinza"] == 0
    assert out["Preto"] + out["Vermelho"] == 60
    assert out["Preto"] == 30 and out["Vermelho"] == 30


def test_distribute_with_explicit_number():
    """Cinza tem número específico, restante divide o que sobra."""
    out = _distribute_among_variations(100, ["Preto", "Cinza", "Vermelho"], {"Cinza": 20})
    assert out["Cinza"] == 20
    assert out["Preto"] + out["Vermelho"] == 80


def test_distribute_mixed_esgotado_and_explicit():
    out = _distribute_among_variations(50, ["A", "B", "C", "D"], {"A": 10, "B": 0})
    assert out["A"] == 10
    assert out["B"] == 0
    assert out["C"] + out["D"] == 40
    assert out["C"] == 20 and out["D"] == 20


def test_distribute_zero_total():
    """Total=0 deve zerar todas as variações."""
    out = _distribute_among_variations(0, ["Preto", "Cinza"], {})
    assert out == {"Preto": 0, "Cinza": 0}


def test_distribute_all_explicit_exceeds_total():
    """Se a soma explícita excede o total, restantes pegam 0 (não negativo)."""
    out = _distribute_among_variations(50, ["A", "B", "C"], {"A": 30, "B": 30})
    # explícito A+B = 60 > 50; restante=max(0, 50-60)=0 para C
    assert out["A"] == 30
    assert out["B"] == 30
    assert out["C"] == 0


def test_distribute_single_variation():
    out = _distribute_among_variations(75, ["Único"], {})
    assert out == {"Único": 75}
