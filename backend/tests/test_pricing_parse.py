"""Unit tests for pricing table parsers (CSV/Excel helpers)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pricing_service as ps  # noqa: E402


def test_to_cents_br_and_float():
    assert ps._to_cents("21,99") == 2199
    assert ps._to_cents(21.99) == 2199
    assert ps._to_cents("1,00") == 100
    assert ps._to_cents("R$ 1,00") == 100


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
    assert ps._is_xlsx(b"PK\x03\x04", "tabela_precos_johndrop.xlsx")
    assert not ps._is_xlsx(b"Custo;Loja;Venda\n", "tabela.csv")


def test_parse_simple_csv():
    raw = (
        "Custo do Catálogo;Preço da Loja;Preço de Venda\n"
        "21,99;50,50;5050\n"
        "22,00;51,00;5100\n"
    ).encode("utf-8")
    docs, errors = ps._parse_table(raw, "tabela.csv")
    assert errors == []
    assert len(docs) == 2
    by = {d["cost_cents"]: d for d in docs}
    assert by[2199]["sale_price_int"] == 5050
    assert by[2200]["store_price_brl"] == "51,00"


def test_forbidden_drive_path():
    from pathlib import Path
    assert ps._is_forbidden_path(Path(r"D:\Meu Drive\TOTYSHOP\tabela.xlsx"))
    assert not ps._is_forbidden_path(Path(r"C:\Users\limaa\Desktop\tabela.xlsx"))


def test_looks_like_html_login():
    assert ps._looks_like_html(b"<!DOCTYPE html><html><title>Sign in</title>")
    assert not ps._looks_like_html(b"Custo;Loja;Venda\n1;2;3\n")
