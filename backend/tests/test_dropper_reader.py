"""Testes do leitor da página de produto do Dropper.

A fixture reproduz o TEXTO da página real enviada pelo usuário (print da
tesoura Vonder). Se o Dropper mudar o layout, é aqui que quebra primeiro —
e o teste mostra qual campo deixou de ser encontrado.
"""
from __future__ import annotations

import pytest

from dropper_reader import parse_product_text, parse_variations

# Texto exatamente como aparece na página (valores do print do usuário)
PAGINA_TESOURA = """Produtos
ProdDPF2589
FERAMENTAS MANUAIS · Classe Bronze
Disponível
Kit 12 Tesouras Multiuso 25 cm Lâmina Inox Vonder
SEU CUSTO DE COMPRA
R$ 190,44
ESTOQUE
7
Informações do produto
Enviado por
Dropping
Marca
VONDER
Categoria
FERAMENTAS MANUAIS
Material
Bronze
Unidade
KIT
EAN
2003990403811
NCM
82130000
Onde você vende esse produto
Mercado Livre
TikTok Shop
Shopee
Anunciar
DESCRIÇÃO
TESOURA MULTIUSO VONDER - 25 CM
Indicada para cortes em geral. Possui lâmina em aço inox, que oferece maior durabilidade, e cabo plástico com parte interna emborrachada.
CONTEÚDO DA EMBALAGEM:
- 1 tesoura multiuso Vonder
ESPECIFICAÇÕES:
- Material da lâmina: aço inox
- Material do cabo: plástico com área interna emborrachada
- Comprimento da lâmina: 100 mm
- Comprimento total: 250 mm
- Peso aproximado do produto: 108 g
- Código Vonder: 30.85.250.000
- Garantia legal: 90 dias
Produto original VONDER, adequado para uso doméstico, escritório, artesanato e trabalhos gerais.
"""

URL_TESOURA = "https://dropper.dropping.com.br/cliente/produtos/cmw4wjuow0001kx08h711yzib"
IMAGENS = [f"http://cdn/img{i}.jpg" for i in range(1, 7)]


def test_le_codigo_do_produto():
    p = parse_product_text(PAGINA_TESOURA, URL_TESOURA)
    assert p.code == "ProdDPF2589"


def test_le_titulo_completo():
    p = parse_product_text(PAGINA_TESOURA, URL_TESOURA)
    assert p.title == "Kit 12 Tesouras Multiuso 25 cm Lâmina Inox Vonder"


def test_le_custo_em_reais():
    p = parse_product_text(PAGINA_TESOURA, URL_TESOURA)
    assert p.cost == 190.44


def test_le_estoque():
    p = parse_product_text(PAGINA_TESOURA, URL_TESOURA)
    assert p.stock == 7


def test_le_bloco_de_informacoes():
    p = parse_product_text(PAGINA_TESOURA, URL_TESOURA)
    assert p.brand == "VONDER"
    assert p.category == "FERAMENTAS MANUAIS"
    assert p.unit == "KIT"
    assert p.ean == "2003990403811"
    assert p.ncm == "82130000"
    assert p.supplier == "Dropping"


def test_le_descricao_completa():
    p = parse_product_text(PAGINA_TESOURA, URL_TESOURA)
    assert "TESOURA MULTIUSO VONDER" in p.description
    assert "Peso aproximado do produto: 108 g" in p.description
    assert "Material do cabo" in p.description


def test_guarda_imagens_recebidas():
    p = parse_product_text(PAGINA_TESOURA, URL_TESOURA, images=IMAGENS)
    assert p.images == IMAGENS


def test_nao_confunde_categoria_com_titulo():
    p = parse_product_text(PAGINA_TESOURA, URL_TESOURA)
    assert "FERAMENTAS" not in p.title
    assert "Disponível" != p.title


def test_nao_usa_bloco_de_venda_como_titulo():
    p = parse_product_text(PAGINA_TESOURA, URL_TESOURA)
    assert "Anunciar" not in p.title
    assert "Mercado Livre" not in p.title


def test_pagina_sem_dados_nao_explode():
    p = parse_product_text("", url="")
    assert p.code == ""
    assert p.title == ""
    assert p.cost is None
    assert p.ncm is None
    assert p.description == ""


def test_usa_id_da_url_quando_sem_codigo():
    p = parse_product_text("apenas texto", url="https://dropper/d/cliente/produtos/abc123xyz")
    assert p.code == "abc123xyz"


def test_ncm_em_outro_layout_mesma_linha():
    texto = "ProdDPF1\nProduto Qualquer Legal\nNCM: 8518.30.00\nEAN: 7891234567890\n"
    p = parse_product_text(texto)
    assert p.ncm == "8518.30.00"
    assert p.ean == "7891234567890"


def test_custo_com_milhar():
    texto = "ProdDPF2\nProduto Caro Teste\nR$ 1.234,56\n"
    p = parse_product_text(texto)
    assert p.cost == 1234.56


def test_peso_e_extraido_da_descricao_no_fluxo_completo():
    """Integração leitor -> planilha: o peso sai da descrição."""
    from bling_sheet_export import build_rows

    p = parse_product_text(PAGINA_TESOURA, URL_TESOURA, images=IMAGENS)
    rows = build_rows(p, cleaned_title="Tesoura Multiuso", sale_price=285.0)
    v = rows[0].values
    assert v["Peso líquido (Kg)"] == "0,108"
    assert v["NCM"] == "8213.00.00"
    assert v["Código"] == "ProdDPF2589"
    assert v["Marca"] == "VONDER"


# --------------------------------------------------------------------------- #
# Variações
# --------------------------------------------------------------------------- #


def test_parse_variations():
    blocks = [
        {"name": "Amarelo", "code": "HM677-3-AM", "images": ["http://a/am.jpg"]},
        {"name": "", "code": "ignorar"},
        {"name": "Rosa", "images": ["http://a/rs.jpg"]},
    ]
    got = parse_variations(blocks)
    assert len(got) == 2
    assert got[0].name == "Amarelo"
    assert got[0].code == "HM677-3-AM"
    assert got[1].name == "Rosa"
    assert got[1].code is None


def test_parse_variations_lista_vazia():
    assert parse_variations([]) == []
    assert parse_variations(None) == []
