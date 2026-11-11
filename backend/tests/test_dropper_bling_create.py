"""Testes do criador de produtos no Bling via API v3.

Nenhuma chamada real: `bling_service.bling_request` é substituído por um dublê
que responde como o Bling. O foco é o contrato do payload e o tratamento dos
erros (que é a razão de existir deste caminho).
"""
from __future__ import annotations

import asyncio

import pytest

import dropper_bling_create as creator
from bling_sheet_export import DropperProduct

PRODUTO = DropperProduct(
    code="ProdDPF2589",
    title="Kit 12 Tesouras Multiuso 25 cm Lâmina Inox Vonder",
    cost=190.44,
    ncm="82130000",
    ean="2003990403811",
    brand="VONDER",
    category="FERAMENTAS MANUAIS",
    unit="KIT",
    stock=7,
    description="Peso aproximado do produto: 108 g\nCódigo Vonder: 30.85.250.000",
)


class FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    @property
    def text(self):
        return str(self._body)


def make_fake(rotas):
    """``rotas``: lista de (método, prefixo_do_path, FakeResp)."""
    chamadas = []

    async def fake(method, path, params=None, json=None):
        chamadas.append({"method": method, "path": path, "params": params, "json": json})
        for m, prefixo, resp in rotas:
            if m == method and path.startswith(prefixo):
                return resp
        return FakeResp(404, {"error": {"message": "rota não simulada"}})

    return fake, chamadas


# --------------------------------------------------------------------------- #
# Payload
# --------------------------------------------------------------------------- #

def test_payload_tem_os_obrigatorios():
    p = creator.build_payload(PRODUTO, title="Tesoura Vonder", sale_price=285.0)
    assert p["nome"] == "Tesoura Vonder"
    assert p["tipo"] == "P"
    assert p["formato"] == "S"
    assert p["situacao"] == "A"
    assert p["preco"] == 285.0


def test_payload_leva_codigo_e_custos():
    p = creator.build_payload(PRODUTO, title="Tesoura", sale_price=285.0)
    assert p["codigo"] == "ProdDPF2589"
    assert p["precoCusto"] == 190.44


def test_payload_leva_ncm_fiscal_sem_pontuacao():
    p = creator.build_payload(PRODUTO, title="Tesoura", sale_price=285.0)
    assert p["tributacao"]["ncm"] == "82130000"
    assert p["tributacao"]["origem"] == 0


def test_payload_sem_ncm_nao_inventa():
    produto = DropperProduct(code="X1", title="Produto sem NCM")
    p = creator.build_payload(produto, title="Produto sem NCM", sale_price=10.0)
    assert "ncm" not in p["tributacao"]


def test_payload_leva_gtin_marca_unidade():
    p = creator.build_payload(PRODUTO, title="Tesoura", sale_price=285.0)
    assert p["gtin"] == "2003990403811"
    assert p["marca"] == "VONDER"
    assert p["unidade"] == "KIT"


def test_payload_leva_peso_da_descricao():
    p = creator.build_payload(PRODUTO, title="Tesoura", sale_price=285.0)
    assert p["pesoLiquido"] == 0.108
    assert p["pesoBruto"] == 0.108


def test_payload_sem_medidas_nao_inventa_dimensao():
    p = creator.build_payload(PRODUTO, title="Tesoura", sale_price=285.0)
    for campo in ("largura", "altura", "profundidade"):
        assert campo not in p


def test_payload_omite_campos_vazios():
    produto = DropperProduct(code="X9", title="Simples")
    p = creator.build_payload(produto, title="Simples", sale_price=10.0)
    assert "gtin" not in p
    assert "marca" not in p
    assert "descricaoCurta" not in p


def test_payload_titulo_longe_de_120_caracteres():
    produto = DropperProduct(code="X2", title="t" * 300)
    p = creator.build_payload(produto, title="t" * 300, sale_price=1.0)
    assert len(p["nome"]) == 120


def test_payload_nao_manda_imagem_por_padrao():
    produto = DropperProduct(code="X3", title="Com fotos", images=["https://x/1.jpg"])
    p = creator.build_payload(produto, title="Com fotos", sale_price=5.0)
    assert "imagens" not in p


def test_payload_manda_imagem_quando_pedido():
    produto = DropperProduct(code="X4", title="Com fotos", images=["https://x/1.jpg", "https://x/2.jpg"])
    p = creator.build_payload(produto, title="Com fotos", sale_price=5.0, send_images=True)
    assert p["imagens"] == [{"link": "https://x/1.jpg"}, {"link": "https://x/2.jpg"}]


def test_payload_com_fornecedor_e_categoria():
    p = creator.build_payload(
        PRODUTO, title="Tesoura", sale_price=285.0, supplier_id=123, category_id=456
    )
    assert p["fornecedor"] == {"id": 123}
    assert p["categoria"] == {"id": 456}


# --------------------------------------------------------------------------- #
# Erros
# --------------------------------------------------------------------------- #

def test_explica_erro_por_campo():
    corpo = {"error": {"fields": [{"field": "preco", "message": "Preço inválido"}]}}
    texto = creator.explain_error(400, corpo)
    assert "preco" in texto and "Preço inválido" in texto


def test_explica_erro_generico():
    assert "HTTP 401" in creator.explain_error(401, {"error": {"message": "token expirado"}})


def test_explica_erro_sem_detalhe():
    assert "HTTP 500" in creator.explain_error(500, None)


# --------------------------------------------------------------------------- #
# Consultas
# --------------------------------------------------------------------------- #

def test_find_product_por_codigo_exato(monkeypatch):
    fake, _ = make_fake([("GET", "/produtos", FakeResp(200, {"data": [
        {"id": 1, "codigo": "OUTRO"},
        {"id": 77, "codigo": "ProdDPF2589"},
    ]}))])
    monkeypatch.setattr(creator.bling_service, "bling_request", fake)
    assert asyncio.run(creator.find_product_by_code("ProdDPF2589"))["id"] == 77


def test_find_product_sem_codigo_devolve_none(monkeypatch):
    fake, chamadas = make_fake([])
    monkeypatch.setattr(creator.bling_service, "bling_request", fake)
    assert asyncio.run(creator.find_product_by_code("")) is None
    assert chamadas == []


def test_find_supplier_pelo_nome(monkeypatch):
    fake, _ = make_fake([("GET", "/contatos", FakeResp(200, {"data": [
        {"id": 9, "nome": "DROPPING INTERMEDIACAO E CONSULTORIA DE VENDAS LTDA"},
    ]}))])
    monkeypatch.setattr(creator.bling_service, "bling_request", fake)
    assert asyncio.run(creator.find_supplier()) == 9


def test_ensure_supplier_nao_cria_duplicado(monkeypatch):
    fake, chamadas = make_fake([("GET", "/contatos", FakeResp(200, {"data": [
        {"id": 9, "nome": creator.SUPPLIER_NAME},
    ]}))])
    monkeypatch.setattr(creator.bling_service, "bling_request", fake)
    r = asyncio.run(creator.ensure_supplier())
    assert r["id"] == 9 and r["criado"] is False
    assert not any(c["method"] == "POST" for c in chamadas)


def test_ensure_supplier_cria_quando_falta(monkeypatch):
    fake, chamadas = make_fake([
        ("GET", "/contatos", FakeResp(200, {"data": []})),
        ("POST", "/contatos", FakeResp(201, {"data": {"id": 55}})),
    ])
    monkeypatch.setattr(creator.bling_service, "bling_request", fake)
    r = asyncio.run(creator.ensure_supplier())
    assert r["id"] == 55 and r["criado"] is True
    post = [c for c in chamadas if c["method"] == "POST"][0]
    assert post["json"]["tipo"] == "J"
    assert post["json"]["numeroDocumento"].isdigit()


# --------------------------------------------------------------------------- #
# ensure_product (o fluxo do piloto)
# --------------------------------------------------------------------------- #

def test_ensure_product_pula_quando_sku_existe(monkeypatch):
    fake, chamadas = make_fake([("GET", "/produtos", FakeResp(200, {"data": [
        {"id": 77, "codigo": "ProdDPF2589"},
    ]}))])
    monkeypatch.setattr(creator.bling_service, "bling_request", fake)

    r = asyncio.run(creator.ensure_product(PRODUTO, title="Tesoura", sale_price=285.0))
    assert r["status"] == "skipped"
    assert r["product_id"] == 77
    assert "já existe" in r["error"]
    assert not any(c["method"] == "POST" for c in chamadas), "não pode criar duplicado"


def test_ensure_product_cria_e_vincula_fornecedor(monkeypatch):
    fake, chamadas = make_fake([
        ("GET", "/produtos", FakeResp(200, {"data": []})),
        ("GET", "/contatos", FakeResp(200, {"data": [{"id": 9, "nome": creator.SUPPLIER_NAME}]})),
        ("POST", "/produtos/fornecedores", FakeResp(201, {"data": {"id": 1}})),
        ("POST", "/produtos", FakeResp(201, {"data": {"id": 555}})),
    ])
    monkeypatch.setattr(creator.bling_service, "bling_request", fake)

    r = asyncio.run(creator.ensure_product(
        PRODUTO, title="Tesoura Vonder", sale_price=285.0,
        short_description="Descrição curta", complementary_description="- bullet",
    ))
    assert r["status"] == "created"
    assert r["product_id"] == 555
    assert r["error"] == ""
    assert r["supplier_link"]["ok"] is True

    post_produto = [c for c in chamadas if c["method"] == "POST" and c["path"] == "/produtos"][0]
    enviado = post_produto["json"]
    assert enviado["codigo"] == "ProdDPF2589"
    assert enviado["preco"] == 285.0
    assert enviado["descricaoCurta"] == "Descrição curta"
    assert enviado["fornecedor"] == {"id": 9}


def test_ensure_product_reporta_erro_do_bling(monkeypatch):
    fake, _ = make_fake([
        ("GET", "/produtos", FakeResp(200, {"data": []})),
        ("GET", "/contatos", FakeResp(200, {"data": [{"id": 9, "nome": creator.SUPPLIER_NAME}]})),
        ("POST", "/produtos", FakeResp(400, {"error": {"fields": [
            {"field": "codigo", "message": "Código já utilizado"}
        ]}})),
    ])
    monkeypatch.setattr(creator.bling_service, "bling_request", fake)

    r = asyncio.run(creator.ensure_product(PRODUTO, title="Tesoura", sale_price=285.0))
    assert r["status"] == "error"
    assert "codigo" in r["error"] and "já utilizado" in r["error"]
    assert r["payload"]["codigo"] == "ProdDPF2589"


def test_ensure_product_nunca_levanta_excecao(monkeypatch):
    async def explode(*_a, **_k):
        raise RuntimeError("rede caiu")

    monkeypatch.setattr(creator.bling_service, "bling_request", explode)
    r = asyncio.run(creator.ensure_product(PRODUTO, title="Tesoura", sale_price=285.0))
    assert r["status"] == "error"
    assert "rede caiu" in r["error"]
