"""Testes das descrições geradas para a planilha Bling (Fase 1, sem IA)."""
from __future__ import annotations

import asyncio

import pytest

import dropper_descriptions as dd

DESC_VONDER = """TESOURA MULTIUSO VONDER - 25 CM
Indicada para cortes em geral. Possui lâmina em aço inox.
CONTEÚDO DA EMBALAGEM:
- 1 tesoura multiuso Vonder
ESPECIFICAÇÕES:
- Material da lâmina: aço inox
- Comprimento da lâmina: 100 mm
- Peso aproximado do produto: 108 g
- Código Vonder: 30.85.250.000
"""


def test_clean_lines_remove_html_e_vazias():
    linhas = dd._clean_lines("<p>Oi</p>\n\n  <b>Teste</b>  \n")
    assert "Oi" in linhas
    assert "Teste" in linhas
    assert all(ln.strip() for ln in linhas)


def test_fallback_short_ignora_titulo_repetido():
    curta = dd.fallback_short("TESOURA MULTIUSO VONDER - 25 CM", DESC_VONDER)
    assert "Indicada para cortes" in curta
    assert not curta.startswith("TESOURA MULTIUSO VONDER")


def test_fallback_short_corta_em_fim_de_frase():
    texto = ("Frase completa. " * 200).strip()
    curta = dd.fallback_short("Titulo", texto, max_chars=200)
    assert len(curta) <= 200
    assert curta.endswith(".")


def test_fallback_short_sem_descricao_devolve_vazio():
    assert dd.fallback_short("Titulo", "") == ""


def test_compose_complementary_coloca_o_hifen():
    out = dd.compose_complementary(["Feito em aço", "- Já com hífen"], ["- <b>Cor:</b> Azul"])
    linhas = out.split("<br>")
    assert linhas[0] == "- Feito em aço"
    assert linhas[1] == "- Já com hífen"
    assert dd.FICHA_HEADER in linhas


def test_compose_complementary_sem_ficha():
    out = dd.compose_complementary(["- um"], [])
    assert dd.FICHA_HEADER not in out
    assert out == "- um"


def test_compose_complementary_ignora_bullets_vazios():
    out = dd.compose_complementary(["- ok", "", None], [])
    assert out == "- ok"


def test_short_to_html():
    assert dd.short_to_html("a\n\nb") == "a<br><br>b"
    assert dd.short_to_html("") == ""


def test_fallback_complementary_usa_especificacoes():
    compl = dd.fallback_complementary(DESC_VONDER)
    assert "Material da lâmina" in compl
    assert compl.count("<br>") + 1 <= 8


def test_fallback_complementary_sem_bullets():
    assert dd.fallback_complementary("texto solto") == ""


def test_build_descriptions_sem_ia_usa_fornecedor():
    curta, compl, origem = asyncio.run(dd.build_descriptions("Tesoura", DESC_VONDER, use_ai=False))
    assert origem == "fornecedor"
    assert "Indicada" in curta
    assert "Material" in compl


def test_build_descriptions_sem_dados_fica_vazio():
    curta, compl, origem = asyncio.run(dd.build_descriptions("Titulo", "", use_ai=False))
    assert (curta, compl, origem) == ("", "", "vazio")


def test_build_descriptions_ia_quebrada_cai_para_fornecedor(monkeypatch):
    """Se a IA estourar, não pode levantar exceção nem inventar texto."""
    import bling_enrichment as be

    async def boom(*_a, **_k):
        raise RuntimeError("sem saldo")

    monkeypatch.setattr(be, "generate_short_description", boom)
    monkeypatch.setattr(be, "generate_bullet_points", boom)
    curta, compl, origem = asyncio.run(dd.build_descriptions("Tesoura", DESC_VONDER, use_ai=True))
    assert origem == "fornecedor"
    assert "Indicada" in curta


def test_build_descriptions_ia_ok(monkeypatch):
    import bling_enrichment as be

    async def fake_short(*_a, **_k):
        return "Parágrafo um.\n\nParágrafo dois."

    async def fake_bullets(*_a, **_k):
        return [f"- bullet {i}" for i in range(1, 9)]

    async def fake_attrs(*_a, **_k):
        return {"modelo": "TM-25", "material": "aço inox", "cor": "", "voltagem": "",
                "dimensoes": "", "palavras_chave": []}

    monkeypatch.setattr(be, "generate_short_description", fake_short)
    monkeypatch.setattr(be, "generate_bullet_points", fake_bullets)
    monkeypatch.setattr(be, "_extract_attributes", fake_attrs)

    curta, compl, origem = asyncio.run(dd.build_descriptions("Tesoura", DESC_VONDER, use_ai=True))
    assert origem == "ia"
    assert curta == "Parágrafo um.<br><br>Parágrafo dois."
    assert compl.count("- ") == 10          # 8 bullets + 2 da ficha
    assert dd.FICHA_HEADER in compl
