"""Testes do robô Dropper na versão "ler e gerar planilha".

Nada de navegador aqui: as partes testadas são as que decidem o conteúdo da
planilha (id da URL, prévia, arquivo gerado e o modo MOCKED, que exercita o
pipeline inteiro sem Playwright).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import bling_sheet_export as sheet
import dropper_bot


def test_product_id_from_url():
    assert dropper_bot._product_id_from_url(
        "https://dropper.dropping.com.br/cliente/produtos/cmw4wjuow0001kx08h711yzib"
    ) == "cmw4wjuow0001kx08h711yzib"
    assert dropper_bot._product_id_from_url(
        "https://dropper.dropping.com.br/cliente/produtos/abc-123?x=1"
    ) == "abc-123"
    assert dropper_bot._product_id_from_url("") is None
    assert dropper_bot._product_id_from_url("https://dropper.dropping.com.br/login") is None


def test_product_url_monta_a_url_do_produto():
    url = dropper_bot.DROPPER_PRODUCT_URL.format(pid="X1")
    assert url == "https://dropper.dropping.com.br/cliente/produtos/X1"
    assert dropper_bot._product_id_from_url(url) == "X1"


def test_preview_line_resume_o_essencial():
    product = dropper_bot.dropper_reader.parse_product_text(
        dropper_bot._mock_text("ProdDPF2589", "Tesoura Vonder")
    )
    rows = sheet.build_rows(product, cleaned_title="Tesoura Vonder", sale_price=285.0)
    linha = dropper_bot._preview_line(product, rows, "ia")
    assert "ProdDPF2589" in linha
    assert "8213.00.00" in linha
    assert "0,108" in linha
    assert "desc: ia" in linha


def test_mock_text_e_lido_pelo_parser():
    """O texto de exemplo do modo MOCKED precisa ter todos os campos reais."""
    product = dropper_bot.dropper_reader.parse_product_text(
        dropper_bot._mock_text("ProdDPF2589", "Kit 12 Tesouras Vonder")
    )
    assert product.code == "ProdDPF2589"
    assert product.title == "Kit 12 Tesouras Vonder"
    assert product.cost == 190.44
    assert product.ncm == "82130000"
    assert product.ean == "2003990403811"
    assert product.brand == "VONDER"
    assert product.unit == "KIT"
    assert product.stock == 7


def test_write_output_gera_csv_e_relatorio(tmp_path, monkeypatch):
    monkeypatch.setattr(dropper_bot, "EXPORT_DIR", tmp_path)
    dropper_bot.STATE.update({"file": None, "rows": 0, "products": 0})

    product = dropper_bot.dropper_reader.parse_product_text(
        dropper_bot._mock_text("ProdDPF1", "Tesoura de Teste"),
        images=["https://exemplo/1.jpg"],
    )
    rows = sheet.build_rows(product, cleaned_title="Tesoura de Teste", sale_price=99.9)
    reports = [sheet.build_report(rows, product_title="Tesoura de Teste")]

    path = asyncio.run(dropper_bot._write_output(rows, reports, dry_run=False))
    assert path is not None and path.is_file()
    assert path.name.startswith("planilha_bling_")
    assert "TESTE" not in path.name

    header = path.read_text(encoding="utf-8-sig").splitlines()[0]
    assert header.split(";") == list(sheet.BLING_COLUMNS)

    report = path.with_name(path.stem + "_revisao.txt")
    assert report.is_file()
    assert "RELATÓRIO DE REVISÃO" in report.read_text(encoding="utf-8")
    assert "Tesoura de Teste" in report.read_text(encoding="utf-8")
    assert dropper_bot.STATE["file"] == path.name


def test_write_output_modo_teste_marca_o_arquivo(tmp_path, monkeypatch):
    monkeypatch.setattr(dropper_bot, "EXPORT_DIR", tmp_path)
    product = dropper_bot.dropper_reader.parse_product_text(
        dropper_bot._mock_text("ProdDPF2", "Produto Teste")
    )
    rows = sheet.build_rows(product, cleaned_title="Produto Teste", sale_price=10.0)
    path = asyncio.run(dropper_bot._write_output(rows, ["[teste]"], dry_run=True))
    assert path.name.startswith("planilha_bling_TESTE_")


def test_write_output_sem_linhas_nao_grava(tmp_path, monkeypatch):
    monkeypatch.setattr(dropper_bot, "EXPORT_DIR", tmp_path)
    path = asyncio.run(dropper_bot._write_output([], [], dry_run=False))
    assert path is None
    assert list(tmp_path.iterdir()) == []


def test_sheet_status_sem_arquivo(tmp_path, monkeypatch):
    monkeypatch.setattr(dropper_bot, "EXPORT_DIR", tmp_path)
    status = dropper_bot.sheet_status()
    assert status["file"] is None
    assert dropper_bot.latest_sheet() is None


def test_sheet_status_pega_a_mais_recente(tmp_path, monkeypatch):
    monkeypatch.setattr(dropper_bot, "EXPORT_DIR", tmp_path)
    antigo = tmp_path / "planilha_bling_20260101-0000.csv"
    antigo.write_text("a", encoding="utf-8")
    novo = tmp_path / "planilha_bling_20260202-0000.csv"
    novo.write_text("b", encoding="utf-8")
    (tmp_path / "planilha_bling_20260202-0000_revisao.txt").write_text("r", encoding="utf-8")

    assert dropper_bot.latest_sheet().name == novo.name
    status = dropper_bot.sheet_status()
    assert status["file"] == novo.name
    assert status["report"] == "planilha_bling_20260202-0000_revisao.txt"
    assert status["is_test"] is False


def test_sheet_status_marca_arquivo_de_teste(tmp_path, monkeypatch):
    monkeypatch.setattr(dropper_bot, "EXPORT_DIR", tmp_path)
    (tmp_path / "planilha_bling_TESTE_20260202-0000.csv").write_text("b", encoding="utf-8")
    assert dropper_bot.sheet_status()["is_test"] is True


def test_resolve_sheet_nao_sai_da_pasta(tmp_path, monkeypatch):
    monkeypatch.setattr(dropper_bot, "EXPORT_DIR", tmp_path)
    (tmp_path / "planilha_bling_x.csv").write_text("x", encoding="utf-8")
    assert dropper_bot.resolve_sheet("planilha_bling_x.csv").name == "planilha_bling_x.csv"
    assert dropper_bot.resolve_sheet("../../etc/passwd") is None
    assert dropper_bot.resolve_sheet("nao-existe.csv") is None


def test_mock_gera_planilha_completa(tmp_path, monkeypatch):
    """Modo sem navegador: precisa produzir arquivo com cabeçalho válido."""
    monkeypatch.setattr(dropper_bot, "EXPORT_DIR", tmp_path)
    dropper_bot.robot.reset()

    asyncio.run(dropper_bot._run_mock(1, dry_run=True))

    arquivos = list(tmp_path.glob("planilha_bling_*.csv"))
    assert len(arquivos) == 1
    linhas = arquivos[0].read_text(encoding="utf-8-sig").splitlines()
    assert linhas[0].split(";") == list(sheet.BLING_COLUMNS)
    assert len(linhas) == 2                     # cabeçalho + 1 produto
    assert "ProdDPF2589" in linhas[1]
    assert dropper_bot.robot.state == "idle"
    assert dropper_bot.robot.success == 1


def test_read_variations_nao_inventa():
    """Leitura de variação está desligada: devolver lista vazia é o contrato."""
    assert asyncio.run(dropper_bot._read_variations(None)) == []


class _FakePage:
    """Página falsa: devolve o texto e as imagens que o teste mandar."""

    def __init__(self, text: str, images=None):
        self.text = text
        self.images = images or []
        self.visited: list = []

    async def goto(self, url, **_kw):
        self.visited.append(url)

    async def wait_for_timeout(self, _ms):
        return None

    async def inner_text(self, _sel):
        return self.text

    async def evaluate(self, js):
        if "querySelectorAll('img')" in js:
            return list(self.images)
        return None


def test_funcoes_do_fluxo_existem():
    """Trava contra erro de nome (função usada e não definida) no fluxo."""
    for nome in (
        "_login_dropper",
        "_open_products_page",
        "_collect_product_urls",
        "_read_page_text",
        "_read_product_images",
        "_read_variations",
        "_read_one_product",
        "_write_output",
        "_run_playwright",
        "diagnostico_dropper",
        "sheet_status",
        "resolve_sheet",
    ):
        assert callable(getattr(dropper_bot, nome, None)), f"faltou {nome}"


def test_read_one_product_ponta_a_ponta_com_pagina_falsa():
    """Lê um produto completo (texto + imagens) sem navegador de verdade."""
    texto = dropper_bot._mock_text("ProdDPF777", "Tesoura Profissional Vonder")
    page = _FakePage(texto, images=[f"https://cdn.exemplo/t{i}.jpg" for i in range(1, 4)])

    resultado = asyncio.run(
        dropper_bot._read_one_product(
            page, "https://dropper.dropping.com.br/cliente/produtos/abc777", use_ai=False
        )
    )
    assert resultado is not None
    rows, origem, product, limpo = resultado

    assert page.visited, "deveria abrir a URL do produto"
    assert product.code == "ProdDPF777"
    assert origem == "fornecedor"
    v = rows[0].values
    assert v["Código"] == "ProdDPF777"
    assert v["NCM"] == "8213.00.00"
    assert v["Peso líquido (Kg)"] == "0,108"
    assert v["URL Imagens Externas"].count("https://cdn.exemplo") == 3
    assert v["Descrição Curta"], "descrição curta não pode ficar vazia"
    assert len(rows) == 1


def test_read_one_product_sem_titulo_falha_sem_explodir():
    page = _FakePage("texto qualquer sem os campos do produto")
    assert asyncio.run(
        dropper_bot._read_one_product(page, "https://dropper/cliente/produtos/xyz", use_ai=False)
    ) is None


# --------------------------------------------------------------------------- #
# Histórico: não repetir produto entre execuções
# --------------------------------------------------------------------------- #

URL = "https://dropper.dropping.com.br/cliente/produtos/{}"


def test_historico_vazio_quando_nao_existe(tmp_path, monkeypatch):
    monkeypatch.setattr(dropper_bot, "EXPORT_DIR", tmp_path)
    assert dropper_bot.load_history() == {}


def test_historico_corrompido_nao_explode(tmp_path, monkeypatch):
    monkeypatch.setattr(dropper_bot, "EXPORT_DIR", tmp_path)
    (tmp_path / dropper_bot.HISTORY_NAME).write_text("{lixo", encoding="utf-8")
    assert dropper_bot.load_history() == {}


def test_record_e_salvar_historico(tmp_path, monkeypatch):
    monkeypatch.setattr(dropper_bot, "EXPORT_DIR", tmp_path)
    hist = dropper_bot.record_history({}, [("abc123", "ProdDPF1", "Tesoura")])
    dropper_bot.save_history(hist)

    lido = dropper_bot.load_history()
    assert "id:abc123" in lido
    assert "cod:ProdDPF1" in lido
    assert lido["cod:ProdDPF1"]["title"] == "Tesoura"


def test_filter_new_urls_separa_novos_dos_ja_lidos(tmp_path, monkeypatch):
    monkeypatch.setattr(dropper_bot, "EXPORT_DIR", tmp_path)
    hist = dropper_bot.record_history({}, [("abc123", "ProdDPF1", "Tesoura")])
    novos, ja = dropper_bot.filter_new_urls(
        [URL.format("abc123"), URL.format("novo999")], hist
    )
    assert novos == [URL.format("novo999")]
    assert ja == [URL.format("abc123")]


def test_filter_new_urls_remove_repetido_na_mesma_lista():
    novos, ja = dropper_bot.filter_new_urls(
        [URL.format("x1"), URL.format("x1"), URL.format("x2")], {}
    )
    assert novos == [URL.format("x1"), URL.format("x2")]
    assert ja == []


def test_filter_new_urls_por_codigo_quando_o_id_muda(tmp_path, monkeypatch):
    """Mesmo SKU vindo de outro id também é pulado."""
    monkeypatch.setattr(dropper_bot, "EXPORT_DIR", tmp_path)
    hist = {"cod:ProdDPF1": {"pid": "antigo"}}
    novos, ja = dropper_bot.filter_new_urls([URL.format("id-novo")], hist)
    assert novos == [URL.format("id-novo")]      # id diferente e desconhecido -> continua novo
    assert dropper_bot._is_known("", "ProdDPF1", hist) is True


def test_reset_history_esquece_tudo(tmp_path, monkeypatch):
    monkeypatch.setattr(dropper_bot, "EXPORT_DIR", tmp_path)
    dropper_bot.save_history(dropper_bot.record_history({}, [("a1", "SKU1", "A"), ("a2", "SKU2", "B")]))
    removidos = dropper_bot.reset_history()
    assert removidos >= 2
    assert dropper_bot.load_history() == {}
    assert dropper_bot.reset_history() == 0      # idempotente


def test_historico_nao_mistura_com_planilha(tmp_path, monkeypatch):
    """O arquivo de histórico não pode aparecer como planilha na tela."""
    monkeypatch.setattr(dropper_bot, "EXPORT_DIR", tmp_path)
    dropper_bot.save_history({"id:x": {}})
    assert dropper_bot.latest_sheet() is None
