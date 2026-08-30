"""Unit tests for pricing table parsers (CSV/Excel helpers)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pricing_service as ps  # noqa: E402


def test_to_cents_br_and_float():
    assert ps._to_cents("21,99") == 2199
    assert ps._to_cents(21.99) == 2199
    assert ps._to_cents("1,00") == 100


def test_sale_int_plain_and_decimal():
    assert ps._sale_int(5250) == 5250
    assert ps._sale_int("5250") == 5250
    assert ps._sale_int(5250.0) == 5250


def test_pick_columns_by_header():
    cost, store, sale = ps._pick_columns(
        ["Custo do Catálogo", "Preço da Loja", "Preço de Venda"]
    )
    assert (cost, store, sale) == (0, 1, 2)


def test_is_xlsx_by_name():
    assert ps._is_xlsx(b"", "tabela_precos_johndrop.xlsx")
    assert not ps._is_xlsx(b"Custo;Loja;Venda\n", "tabela.csv")
