"""Cria produtos no Bling pela API v3, a partir do que foi lido no Dropper.

Por que API e não planilha: a planilha não diz *qual* campo está errado — o Bling
recusa o arquivo inteiro e devolve uma faixa vermelha sem motivo. A API responde
com o campo e a mensagem (ex.: ``{"error": {"fields": [{"field": "preco",
"message": "..."}]}}``), então dá para corrigir sem adivinhar.

Regras de ouro deste módulo:

* **Idempotente**: antes de criar, consulta ``GET /produtos?codigo=<SKU>``. Se o
  SKU já existe, não cria de novo — devolve ``skipped``.
* **Nada é inventado**: campo sem valor no Dropper simplesmente não vai no
  payload (o Bling aceita omitir; mandar ``""`` em NCM, por exemplo, é pior).
* **Fornecedor** precisa existir como contato. Procura pelo nome exato e, se não
  achar, cria com o CNPJ informado.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import bling_service
from bling_sheet_export import (
    DropperProduct,
    extract_supplier_code,
    extract_weight_kg,
    extract_dimensions,
    format_ncm,
    format_ean,
)

#: contato do fornecedor no Bling (nome exato do cadastro)
SUPPLIER_NAME = "DROPPING INTERMEDIACAO E CONSULTORIA DE VENDAS LTDA"
SUPPLIER_FANTASIA = "Dropper"
SUPPLIER_CNPJ = "66.871.221/0001-43"

#: produto simples, ativo
PRODUCT_TYPE = "P"      # P = produto
PRODUCT_FORMAT = "S"    # S = simples
PRODUCT_STATUS = "A"    # A = ativo


def _num(value: Optional[float]) -> Optional[float]:
    """Número limpo para JSON (None quando não existe)."""
    if value is None:
        return None
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _txt(value: Optional[str], limit: int = 4000) -> Optional[str]:
    value = (value or "").strip()
    return value[:limit] if value else None


def build_payload(
    product: DropperProduct,
    *,
    title: str,
    short_description: str = "",
    complementary_description: str = "",
    sale_price: Optional[float] = None,
    supplier_id: Optional[int] = None,
    category_id: Optional[int] = None,
    unit: Optional[str] = None,
    send_images: bool = False,
) -> Dict[str, Any]:
    """Monta o corpo do ``POST /produtos``.

    Só entram campos com valor real. ``nome``, ``preco``, ``tipo``, ``formato`` e
    ``situacao`` são os obrigatórios da API.
    """
    payload: Dict[str, Any] = {
        "nome": (title or product.title or "")[:120],
        "tipo": PRODUCT_TYPE,
        "formato": PRODUCT_FORMAT,
        "situacao": PRODUCT_STATUS,
    }

    if product.code:
        payload["codigo"] = product.code
    if sale_price:
        payload["preco"] = float(sale_price)
    if product.cost:
        payload["precoCusto"] = float(product.cost)

    unidade = (unit or product.unit or "UN").upper()[:6]
    payload["unidade"] = unidade

    peso = product.weight_kg if product.weight_kg is not None else extract_weight_kg(product.description)
    if peso and 0 < peso < 500:
        payload["pesoLiquido"] = _num(peso)
        payload["pesoBruto"] = _num(peso)

    dims = product.dimensions or extract_dimensions(product.description)
    for campo, chave in (("largura", "largura"), ("altura", "altura"), ("profundidade", "profundidade")):
        valor = _num(dims.get(campo)) if dims else None
        if valor and 0 < valor < 300:
            payload[chave] = valor

    ncm = format_ncm(product.ncm)
    tributacao: Dict[str, Any] = {"origem": 0}
    if ncm:
        tributacao["ncm"] = ncm.replace(".", "")
    payload["tributacao"] = tributacao

    ean = format_ean(product.ean)
    if ean:
        payload["gtin"] = ean

    if product.brand:
        payload["marca"] = product.brand[:50]

    if short_description:
        payload["descricaoCurta"] = _txt(short_description)
    if complementary_description:
        payload["descricaoComplementar"] = _txt(complementary_description, 8000)

    if supplier_id:
        # o vínculo com fornecedor é feito depois em /produtos/fornecedores;
        # aqui vai só a referência, que o Bling aceita ignorar se não usar
        payload["fornecedor"] = {"id": int(supplier_id)}
    if category_id:
        payload["categoria"] = {"id": int(category_id)}

    if send_images and product.images:
        payload["imagens"] = [{"link": url} for url in product.images[:10] if url]

    return payload


# --------------------------------------------------------------------------- #
# Consultas e criação
# --------------------------------------------------------------------------- #


async def find_product_by_code(code: str) -> Optional[dict]:
    """Produto existente com este SKU (ou None)."""
    if not code:
        return None
    resp = await bling_service.bling_request(
        "GET", "/produtos", params={"codigo": code, "limite": 5}
    )
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    for item in (data.get("data") or []):
        if str(item.get("codigo") or "").strip().lower() == code.strip().lower():
            return item
    return None


async def find_supplier(name: str = SUPPLIER_NAME) -> Optional[int]:
    """Id do contato do fornecedor, procurando pelo nome."""
    resp = await bling_service.bling_request(
        "GET", "/contatos", params={"pesquisa": name, "limite": 20}
    )
    if resp.status_code != 200:
        return None
    try:
        data = resp.json().get("data") or []
    except Exception:
        return None
    alvo = name.strip().lower()
    for item in data:
        if str(item.get("nome") or "").strip().lower() == alvo:
            return int(item.get("id"))
    # aceita o primeiro que comece igual (nome pode ter variação de grafia)
    for item in data:
        nome = str(item.get("nome") or "").strip().lower()
        if nome.startswith(alvo[:30]):
            return int(item.get("id"))
    return None


async def create_supplier(name: str = SUPPLIER_NAME, fantasia: str = SUPPLIER_FANTASIA,
                          cnpj: str = SUPPLIER_CNPJ) -> dict:
    """Cria o contato do fornecedor (usado só se não existir)."""
    payload = {
        "nome": name,
        "fantasia": fantasia,
        "tipo": "J",
        "numeroDocumento": "".join(ch for ch in cnpj if ch.isdigit()),
    }
    resp = await bling_service.bling_request("POST", "/contatos", json=payload)
    body = _safe_json(resp)
    return {"status": resp.status_code, "ok": 200 <= resp.status_code < 300,
            "body": body, "payload": payload}


async def ensure_supplier(name: str = SUPPLIER_NAME) -> dict:
    """Devolve ``{"id": int|None, "criado": bool, "detalhe": str}``."""
    existente = await find_supplier(name)
    if existente:
        return {"id": existente, "criado": False, "detalhe": "fornecedor já existia"}
    criado = await create_supplier(name)
    novo_id = None
    if criado["ok"]:
        body = criado["body"] or {}
        novo_id = (body.get("data") or {}).get("id")
    if novo_id:
        return {"id": int(novo_id), "criado": True, "detalhe": "fornecedor criado agora"}
    return {
        "id": None,
        "criado": False,
        "detalhe": (
            "não achei e não consegui criar o fornecedor: "
            f"HTTP {criado['status']} {(criado['body'] or '').__str__()[:300]}"
        ),
    }


def _safe_json(resp) -> Any:
    try:
        return resp.json()
    except Exception:
        try:
            return resp.text[:2000]
        except Exception:
            return None


def explain_error(status: int, body: Any) -> str:
    """Traduz a resposta do Bling em algo legível (campo -> mensagem)."""
    if isinstance(body, dict):
        erro = body.get("error")
        if isinstance(erro, dict):
            campos = erro.get("fields") or []
            partes = [
                f"{c.get('field')}: {c.get('message') or c.get('msg')}" for c in campos if isinstance(c, dict)
            ]
            if partes:
                return f"HTTP {status} — " + " | ".join(partes[:6])
            if erro.get("message"):
                return f"HTTP {status} — {erro['message']}"
            if erro.get("description"):
                return f"HTTP {status} — {erro['description']}"
        texto = body.get("message") or body.get("detail")
        if texto:
            return f"HTTP {status} — {texto}"
    return f"HTTP {status} — resposta sem detalhe: {str(body)[:300]}"


async def create_product(payload: Dict[str, Any]) -> dict:
    """Cria o produto. Devolve ``{ok, status, body, error, id}``."""
    resp = await bling_service.bling_request("POST", "/produtos", json=payload)
    body = _safe_json(resp)
    ok = 200 <= resp.status_code < 300
    novo_id = None
    if ok and isinstance(body, dict):
        novo_id = (body.get("data") or {}).get("id")
    return {
        "ok": ok,
        "status": resp.status_code,
        "body": body,
        "id": novo_id,
        "error": "" if ok else explain_error(resp.status_code, body),
    }


async def link_supplier(product_id: int, supplier_id: int, *,
                        codigo: Optional[str] = None,
                        descricao: Optional[str] = None,
                        preco_compra: Optional[float] = None) -> dict:
    """Vincula o fornecedor ao produto (``POST /produtos/fornecedores``)."""
    payload: Dict[str, Any] = {
        "produto": {"id": int(product_id)},
        "fornecedor": {"id": int(supplier_id)},
        "padrao": True,
    }
    if codigo:
        payload["codigo"] = codigo
    if descricao:
        payload["descricao"] = descricao[:120]
    if preco_compra:
        payload["precocompra"] = float(preco_compra)
    resp = await bling_service.bling_request("POST", "/produtos/fornecedores", json=payload)
    body = _safe_json(resp)
    ok = 200 <= resp.status_code < 300
    return {"ok": ok, "status": resp.status_code, "body": body,
            "error": "" if ok else explain_error(resp.status_code, body)}


async def ensure_product(
    product: DropperProduct,
    *,
    title: str,
    short_description: str = "",
    complementary_description: str = "",
    sale_price: Optional[float] = None,
    supplier_name: str = SUPPLIER_NAME,
    category_id: Optional[int] = None,
    send_images: bool = False,
) -> dict:
    """Cria o produto no Bling se ainda não existir. Nunca levanta exceção.

    Devolve um dicionário com tudo que aconteceu, para a tela mostrar cru:
    ``status`` (created | skipped | error), ``payload`` enviado, resposta,
    fornecedor e a mensagem de erro traduzida.
    """
    resultado: Dict[str, Any] = {
        "code": product.code,
        "title": title,
        "status": "error",
        "product_id": None,
        "error": "",
        "payload": None,
        "response": None,
        "supplier": None,
    }

    try:
        existente = await find_product_by_code(product.code)
        if existente:
            resultado.update(
                status="skipped",
                product_id=existente.get("id"),
                error=f"SKU {product.code} já existe no Bling (id {existente.get('id')}) — não criei de novo",
            )
            return resultado

        fornecedor = await ensure_supplier(supplier_name)
        resultado["supplier"] = fornecedor

        payload = build_payload(
            product,
            title=title,
            short_description=short_description,
            complementary_description=complementary_description,
            sale_price=sale_price,
            supplier_id=fornecedor.get("id"),
            category_id=category_id,
            send_images=send_images,
        )
        resultado["payload"] = payload

        criado = await create_product(payload)
        resultado["response"] = criado["body"]
        if not criado["ok"]:
            resultado["error"] = criado["error"]
            return resultado

        produto_id = criado["id"]
        resultado.update(status="created", product_id=produto_id)

        if fornecedor.get("id") and produto_id:
            vinculo = await link_supplier(
                int(produto_id),
                int(fornecedor["id"]),
                codigo=extract_supplier_code(product.description) or product.code,
                descricao=title,
                preco_compra=product.cost,
            )
            resultado["supplier_link"] = {
                "ok": vinculo["ok"],
                "status": vinculo["status"],
                "error": vinculo["error"],
            }
        return resultado
    except Exception as e:  # nunca derrubar o robô por causa de um produto
        resultado["error"] = f"exceção: {str(e)[:300]}"
        return resultado
