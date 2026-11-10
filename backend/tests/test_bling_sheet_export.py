"""Testes do gerador de planilha de importação do Bling."""
from __future__ import annotations

import csv
import io

import pytest

from bling_sheet_export import (
    BLING_COLUMNS,
    DropperProduct,
    DropperVariation,
    build_csv_text,
    build_report,
    build_rows,
    extract_dimensions,
    extract_supplier_code,
    extract_weight_kg,
    format_decimal,
    format_ean,
    format_ncm,
    format_price,
    join_images,
    summarize,
    write_sheet,
)


# --------------------------------------------------------------------------- #
# Formato do arquivo
# --------------------------------------------------------------------------- #


def test_tem_59_colunas():
    assert len(BLING_COLUMNS) == 59


def test_colunas_na_ordem_do_bling():
    # Âncoras do arquivo modelo real do usuário (posições-chave).
    assert BLING_COLUMNS[0] == "ID"
    assert BLING_COLUMNS[1] == "Código"
    assert BLING_COLUMNS[2] == "Descrição"
    assert BLING_COLUMNS[4] == "NCM"
    assert BLING_COLUMNS[6] == "Preço"
    assert BLING_COLUMNS[11] == "Preço de custo"
    assert BLING_COLUMNS[17] == "Peso líquido (Kg)"
    assert BLING_COLUMNS[28] == "Produto Variação"
    assert BLING_COLUMNS[35] == "Código Pai"
    assert BLING_COLUMNS[41] == "Descrição Curta"
    assert BLING_COLUMNS[43] == "URL Imagens Externas"
    assert BLING_COLUMNS[-1] == "Informações Adicionais"


def test_csv_usa_ponto_e_virgula_e_bom(tmp_path):
    rows = build_rows(DropperProduct(code="X1", title="Produto Teste"))
    path = write_sheet(rows, tmp_path / "p.csv")
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM
    text = raw.decode("utf-8-sig")
    header = text.splitlines()[0]
    assert header.split(";")[0] == "ID"
    assert len(header.split(";")) == 59


# --------------------------------------------------------------------------- #
# Formatação
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw,esperado",
    [
        ("82130000", "8213.00.00"),
        ("8213.00.00", "8213.00.00"),
        ("8213 00 00", "8213.00.00"),
        ("8518.30.00", "8518.30.00"),
        ("", ""),
        (None, ""),
        ("123", ""),        # incompleto: nunca inventa
        ("123456789", ""),  # comprimento errado
    ],
)
def test_format_ncm(raw, esperado):
    assert format_ncm(raw) == esperado


@pytest.mark.parametrize(
    "raw,esperado",
    [
        ("2003990403811", "2003990403811"),  # 13 dígitos
        ("78912345", "78912345"),            # 8
        ("123", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_format_ean(raw, esperado):
    assert format_ean(raw) == esperado


@pytest.mark.parametrize(
    "valor,esperado",
    [
        (0.108, "0,108"),
        (1.5, "1,5"),
        (2.0, "2"),
        (None, ""),
        (0.0, "0"),
    ],
)
def test_format_decimal(valor, esperado):
    assert format_decimal(valor) == esperado


@pytest.mark.parametrize(
    "valor,esperado",
    [
        (52.0, "52"),
        (52.9, "52,9"),
        (190.44, "190,44"),
        (None, ""),
    ],
)
def test_format_price(valor, esperado):
    assert format_price(valor) == esperado


def test_join_images_remove_duplicadas_e_vazias():
    urls = ["http://a/1.jpg", "", "http://a/2.jpg", "http://a/1.jpg", None]
    assert join_images(urls) == "http://a/1.jpg,http://a/2.jpg"


# --------------------------------------------------------------------------- #
# Extração da descrição (camada 2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("- Peso aproximado do produto: 108 g", 0.108),
        ("Peso: 1,5 kg", 1.5),
        ("Peso líquido 300g", 0.3),
        ("Peso do produto: 250 gramas", 0.25),
        ("Peso: 2 quilos", 2.0),
        ("Sem informação de peso aqui", None),
        ("", None),
        ("Peso: 9999 g", None),  # fora da faixa de sanidade -> ignorado
    ],
)
def test_extract_weight_kg(texto, esperado):
    assert extract_weight_kg(texto) == esperado


def test_extract_weight_ignora_medidas_que_nao_sao_peso():
    texto = "Comprimento da lâmina: 100 mm\nComprimento total: 250 mm"
    assert extract_weight_kg(texto) is None


def test_extract_dimensions_so_aceita_embalagem():
    com_embalagem = "Dimensões da embalagem: largura: 14 cm, altura: 6 cm, profundidade: 20 cm"
    got = extract_dimensions(com_embalagem)
    assert got == {"largura": 14.0, "altura": 6.0, "profundidade": 20.0}

    sem_embalagem = "Comprimento da lâmina: 100 mm"
    assert extract_dimensions(sem_embalagem) == {}


def test_extract_dimensions_converte_mm_para_cm():
    got = extract_dimensions("Largura da embalagem: 140 mm")
    assert got["largura"] == 14.0


def test_extract_supplier_code():
    assert extract_supplier_code("Código Vonder: 30.85.250.000") == "30.85.250.000"
    assert extract_supplier_code("Código do fornecedor: KA-1369") == "KA-1369"
    assert extract_supplier_code("nada aqui") is None


# --------------------------------------------------------------------------- #
# Montagem das linhas
# --------------------------------------------------------------------------- #


def _produto_tesoura() -> DropperProduct:
    """Reproduz o produto real do print enviado pelo usuário."""
    return DropperProduct(
        code="ProdDPF2589",
        title="Kit 12 Tesouras Multiuso 25 cm Lâmina Inox Vonder",
        cost=190.44,
        ncm="82130000",
        ean="2003990403811",
        brand="VONDER",
        category="FERAMENTAS MANUAIS",
        unit="KIT",
        supplier="Dropping",
        stock=7,
        description=(
            "TESOURA MULTIUSO VONDER - 25 CM\n"
            "- Material da lâmina: aço inox\n"
            "- Comprimento total: 250 mm\n"
            "- Peso aproximado do produto: 108 g\n"
            "- Código Vonder: 30.85.250.000\n"
        ),
        images=["http://img/1.jpg", "http://img/2.jpg"],
    )


def test_produto_simples_gera_uma_linha():
    rows = build_rows(
        _produto_tesoura(),
        cleaned_title="Tesoura Multiuso 25 cm Lâmina Inox",
        short_description="<p>curta</p>",
        complementary_description="<p>complementar</p>",
        sale_price=285.0,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.kind == "simple"

    v = row.values
    assert v["Código"] == "ProdDPF2589"
    assert v["Descrição"] == "Tesoura Multiuso 25 cm Lâmina Inox"
    assert v["NCM"] == "8213.00.00"
    assert v["GTIN/EAN"] == "2003990403811"
    assert v["Marca"] == "VONDER"
    assert v["Categoria do produto"] == "FERAMENTAS MANUAIS"
    assert v["Unidade"] == "KIT"
    assert v["Estoque"] == "7"
    assert v["Preço"] == "285"
    assert v["Preço de custo"] == "190,44"
    assert v["Preço de Compra"] == "190,44"
    # peso veio da descrição
    assert v["Peso líquido (Kg)"] == "0,108"
    assert row.sources["Peso líquido (Kg)"] == "descricao"
    # dimensões NÃO entram (só havia medida da lâmina)
    assert v["Largura do produto"] == ""
    assert v["Altura do Produto"] == ""
    # imagens
    assert v["URL Imagens Externas"] == "http://img/1.jpg,http://img/2.jpg"
    # padrões fixos
    assert v["Situação"] == "Ativo"
    assert v["Tipo Produção"] == "Terceiros"
    assert v["Condição do Produto"] == "NOVO"
    assert v["Origem"] == "0"


def test_valores_preenchidos_tem_a_ordem_correta_no_csv():
    rows = build_rows(_produto_tesoura(), sale_price=285.0)
    text = build_csv_text(rows)
    parsed = list(csv.reader(io.StringIO(text), delimiter=";"))
    assert parsed[0] == list(BLING_COLUMNS)
    data = parsed[1]
    assert len(data) == 59
    assert data[1] == "ProdDPF2589"      # Código
    assert data[4] == "8213.00.00"       # NCM
    assert data[6] == "285"              # Preço
    assert data[11] == "190,44"          # Preço de custo
    assert data[17] == "0,108"           # Peso líquido
    assert data[38] == "VONDER"          # Marca
    assert data[-2] == "FERAMENTAS MANUAIS"  # Categoria


def test_ncm_ausente_fica_vazio_e_aparece_no_relatorio():
    produto = DropperProduct(code="X1", title="Produto sem NCM")
    rows = build_rows(produto, sale_price=10.0)
    assert rows[0].values["NCM"] == ""
    relatorio = build_report(rows, product_title="Produto sem NCM")
    assert "NCM" in relatorio
    assert "preencher manualmente" in relatorio


def test_produto_com_variacoes_gera_pai_e_filhas():
    produto = DropperProduct(
        code="HM677-3",
        title="Mini Ventilador HM677-3",
        ncm="84145910",
        brand="Generico",
        category="Casa, Cozinha e Lazer>>Ventiladores Portáteis",
        unit="UN",
        cost=17.49,
        images=["http://img/pai.jpg", "http://img/amarela.jpg", "http://img/rosa.jpg"],
        variations=[
            DropperVariation(name="Amarelo", code="HM677-3-AM"),
            DropperVariation(name="Rosa Coelho", code="HM677-3-RSCO"),
        ],
    )
    rows = build_rows(
        produto,
        cleaned_title="Mini Ventilador Portatil",
        sale_price=52.0,
        color_images={
            "Amarelo": ["http://img/amarela.jpg"],
            "Rosa Coelho": ["http://img/rosa.jpg"],
        },
    )
    assert len(rows) == 3
    pai, amarelo, rosa = rows

    assert pai.kind == "parent"
    assert pai.values["Código"] == "HM677-3"
    assert pai.values["Código Pai"] == ""
    assert pai.values["Produto Variação"] == ""
    assert "pai.jpg" in pai.values["URL Imagens Externas"]
    assert "amarela.jpg" in pai.values["URL Imagens Externas"]

    assert amarelo.kind == "child"
    assert amarelo.values["Código"] == "HM677-3-AM"
    assert amarelo.values["Descrição"] == "Cor:Amarelo"
    assert amarelo.values["Produto Variação"] == "Variação"
    assert amarelo.values["Código Pai"] == "HM677-3"
    assert amarelo.values["Clonar dados do pai"] == "SIM"
    assert amarelo.values["URL Imagens Externas"] == "http://img/amarela.jpg"
    assert amarelo.sources["URL Imagens Externas"] == "IA"

    assert rosa.values["Código"] == "HM677-3-RSCO"
    assert rosa.values["Descrição"] == "Cor:Rosa Coelho"
    assert rosa.values["URL Imagens Externas"] == "http://img/rosa.jpg"


def test_variacao_sem_imagem_propria_nao_fica_vazia():
    produto = DropperProduct(
        code="ABC-1",
        title="Produto ABC",
        variations=[DropperVariation(name="Azul", code="ABC-1-AZ")],
    )
    rows = build_rows(produto, sale_price=10.0)
    filho = rows[1]
    assert filho.values["URL Imagens Externas"] == ""


def test_codigo_da_variacao_e_gerado_se_faltar():
    produto = DropperProduct(
        code="XYZ-9",
        title="Produto XYZ",
        variations=[DropperVariation(name="Verde")],
    )
    rows = build_rows(produto, sale_price=5.0)
    assert rows[1].values["Código"] == "XYZ-9-VERDE"


def test_fornecedor_customizado():
    rows = build_rows(_produto_tesoura(), supplier="DROPPING", sale_price=1.0)
    assert rows[0].values["Fornecedor"] == "DROPPING"


def test_summarize_conta_linhas_e_alertas():
    simples = build_rows(_produto_tesoura(), sale_price=285.0)
    com_var = build_rows(
        DropperProduct(
            code="P1",
            title="Com variacao",
            variations=[DropperVariation(name="A", code="P1-A")],
        ),
        sale_price=10.0,
    )
    info = summarize(simples + com_var)
    assert info["linhas"] == 3
    assert info["variacoes"] == 1
    assert info["sem_ncm"] == 1  # a segunda família não tem NCM


def test_relatorio_marca_origem_de_cada_campo():
    rows = build_rows(_produto_tesoura(), sale_price=285.0)
    relatorio = build_report(rows, product_title="Kit 12 Tesouras")
    assert "Kit 12 Tesouras" in relatorio
    assert "Peso líquido (Kg)" in relatorio
    assert "(descricao)" in relatorio
    assert "(tabela)" in relatorio
