"""Regression coverage for the original end-to-end enrichment contract."""
import asyncio
import time

import bulk_enrichment
import enrich_worker
import bling_enrichment
from bling_enrichment import _completion_issues, _is_johndrop_supplier
from db import db


def _complete_product():
    bullets = "<br>".join(f"- Fato técnico {n}" for n in range(1, 7))
    return {
        "descricaoCurta": "Descrição curta completa e organizada para o produto.",
        "descricaoComplementar": bullets,
        "marca": "TotyShop",
        "condicao": 1,
        "tipoProducao": "T",
        "gtin": "",
        "gtinEmbalagem": "",
        "categoria": {"id": 123},
        "midia": {"imagens": {"internas": [{"link": "https://image.example/1.jpg"}]}},
    }


def test_bulk_marks_product_enriched_only_when_original_fields_are_complete():
    product = _complete_product()

    assert bulk_enrichment._completion_gaps(product) == []
    assert bulk_enrichment._is_enriched(product) is True

    product["categoria"] = {}
    product["tipoProducao"] = "P"
    product["descricaoComplementar"] = "<br>".join(f"- Fato {n}" for n in range(1, 6))

    assert bulk_enrichment._completion_gaps(product) == [
        "6 ou 8 bullets",
        "produção terceiros",
        "categoria",
    ]
    assert bulk_enrichment._is_enriched(product) is False


def test_bulk_screen_requires_verified_complete_contract(monkeypatch):
    product_id = int(time.time_ns() % 1_000_000_000)

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {"data": [
                {"id": product_id, "codigo": "SKU-COMPLETE"},
                {"id": product_id + 1, "codigo": "SKU-COMPLETE-AZ"},
            ]}

    async def fake_request(method, path, **_kwargs):
        assert method == "GET"
        assert path == "/produtos"
        return Response()

    async def fake_full(pid):
        if pid == product_id + 1:
            return {
                "id": pid,
                "codigo": "SKU-COMPLETE-AZ",
                "nome": "Produto completo Azul",
                "produtoPai": {"id": product_id},
            }
        product = _complete_product()
        product.update({"id": product_id, "codigo": "SKU-COMPLETE", "nome": "Produto completo"})
        return product

    async def run():
        monkeypatch.setattr(bulk_enrichment.bling_service, "bling_request", fake_request)
        monkeypatch.setattr(bulk_enrichment, "_fetch_full", fake_full)
        await db.bling_enriched_cache.insert_one({
            "product_id": product_id,
            "completion_contract": bling_enrichment.ENRICHMENT_CONTRACT_VERSION,
        })
        verified = await bulk_enrichment.list_products_with_status(limite=1)
        await db.bling_enriched_cache.update_one(
            {"product_id": product_id}, {"$unset": {"completion_contract": ""}},
        )
        legacy = await bulk_enrichment.list_products_with_status(limite=1)
        await db.bling_enriched_cache.delete_one({"product_id": product_id})
        return verified, legacy

    verified, legacy = asyncio.run(run())
    assert len(verified["items"]) == 1  # variation children are not bulk targets
    assert verified["items"][0]["enriched"] is True
    assert verified["items"][0]["completion_gaps"] == []
    assert legacy["items"][0]["enriched"] is False
    assert "fornecedor/variações não verificados" in legacy["items"][0]["completion_gaps"]


def test_legacy_success_log_is_never_shown_as_current_completion():
    sku = f"LEGACY-COMPLETE-{time.time_ns()}"

    async def run():
        before = await bling_enrichment.get_enrichment_stats()
        await db.bling_enrichment_logs.insert_one({
            "sku": sku,
            "status": "success",
            "message": "resultado legado",
            "created_at": "9999-01-01T00:00:00+00:00",
        })
        logs = await bling_enrichment.get_enrichment_logs(limit=10)
        stats = await bling_enrichment.get_enrichment_stats()
        await db.bling_enrichment_logs.delete_many({"sku": sku})
        return [row for row in logs if row.get("sku") == sku], before, stats

    logs, before, stats = asyncio.run(run())
    assert logs[0]["status"] == "needs_review"
    assert "Verificação completa pendente" in logs[0]["message"]
    assert stats["success"] == before["success"]
    assert stats["needs_review"] == before["needs_review"] + 1


def test_trimpro_code_never_uses_johndrop_observation_override():
    assert _is_johndrop_supplier("JONH VARIEDADES") is True
    assert _is_johndrop_supplier("TRIMPRO") is False


def test_existing_variation_children_count_as_ready_on_reenrichment():
    import bling_variations

    result = asyncio.run(bling_variations.create_variations(
        123,
        {
            "nome": "Produto",
            "codigo": "SKU-123",
            "formato": "V",
            "variacoes": [
                {"variacao": {"nome": "Cor:Azul"}},
                {"variacao": {"nome": "Cor:Verde"}},
            ],
        },
        ["Azul", "Verde"],
        total_stock=1,
    ))

    assert result["created"] == 0
    assert result["skipped"] == 2
    assert result["ready"] == 2


def test_existing_bling_category_satisfies_completion_contract():
    assert _completion_issues(
        category_id=None,
        current={
            "categoria": {"id": 42},
            "midia": {"imagens": {"internas": [{"link": "https://image.example/1.jpg"}]}},
        },
        supplier_ok=True,
        supplier_reason="",
        expected_variations=2,
        ready_variations=2,
    ) == []


def test_completion_issues_require_supplier_category_and_all_explicit_variations():
    assert _completion_issues(
        category_id=None,
        current={"categoria": {}},
        supplier_ok=False,
        supplier_reason="Fornecedor TRIMPRO não foi encontrado no Bling.",
        expected_variations=3,
        ready_variations=1,
    ) == [
        "categoria existente não encontrada",
        "imagem não sincronizada",
        "Fornecedor TRIMPRO não foi encontrado no Bling.",
        "variações incompletas (1/3)",
    ]


def test_supplier_link_is_reported_instead_of_silently_claiming_success(monkeypatch):
    class Response:
        status_code = 200
        text = ""

    async def fake_brand(_title, _description):
        return "Generico"

    async def fake_supplier(_name):
        return None

    async def fake_request(method, path, **_kwargs):
        assert method == "PATCH"
        assert path == "/produtos/123"
        return Response()

    monkeypatch.setattr(bling_enrichment, "_resolve_brand", fake_brand)
    monkeypatch.setattr(bling_enrichment, "_find_supplier_id", fake_supplier)
    monkeypatch.setattr(bling_enrichment.bling_service, "bling_request", fake_request)

    result = asyncio.run(bling_enrichment.update_bling_product(
        123,
        {"nome": "Produto TrimPro"},
        {"descricaoCurta": "Descrição completa do produto."},
        johndrop_id="TP-123",
        cost=12.5,
        supplier_name="TRIMPRO",
    ))

    assert result["ok"] is True
    assert result["supplier_ok"] is False
    assert "TRIMPRO" in result["supplier_reason"]


def test_bulk_reenrichment_recovers_trimpro_source_before_persisting(monkeypatch):
    """A nonempty Bling description must not overwrite persisted TrimPro data."""
    sku = f"TRIMPRO-RAW-{time.time_ns()}"
    captured = {}

    async def run():
        await db.product_raw.insert_one({
            "sku": sku,
            "raw_title": "Título original TrimPro",
            "raw_description": "Descrição original TrimPro suficientemente longa para recuperação.",
            "johndrop_id": "TP-ORIGINAL-77",
            "cost": 23.9,
            "images": ["https://image.example/trimpro.jpg"],
            "supplier_name": "TRIMPRO",
        })

        async def fake_find(_sku, **_kwargs):
            return {"id": 901, "nome": "Produto Bling"}

        async def fake_full(_pid):
            return {
                "id": 901,
                "nome": "Produto Bling",
                "codigo": sku,
                "categoria": {"id": 88},
                "midia": {"imagens": {"internas": [{"link": "https://image.example/bling.jpg"}]}},
            }

        async def fake_short(*_args):
            return "Descrição curta profissional, clara e adequada para o produto anunciado."

        async def fake_bullets(*_args):
            return [f"- Especificação técnica verificável número {n}." for n in range(1, 7)]

        async def fake_category(*_args):
            return 88

        async def fake_update(*_args, **kwargs):
            captured.update(kwargs)
            return {"ok": True, "supplier_ok": True, "supplier_reason": ""}

        async def noop(*_args, **_kwargs):
            return None

        monkeypatch.setattr(bling_enrichment, "find_bling_product_by_sku", fake_find)
        monkeypatch.setattr(bling_enrichment, "_fetch_bling_product_full", fake_full)
        monkeypatch.setattr(bling_enrichment, "generate_short_description", fake_short)
        monkeypatch.setattr(bling_enrichment, "generate_bullet_points", fake_bullets)
        monkeypatch.setattr(bling_enrichment, "pick_or_create_category", fake_category)
        monkeypatch.setattr(bling_enrichment, "update_bling_product", fake_update)
        monkeypatch.setattr(bling_enrichment, "_upsert_enriched_cache", noop)

        result = await bling_enrichment.enrich_product_by_sku(
            sku,
            "Título mostrado pelo Bling",
            "Descrição presente no Bling e longa o suficiente para não usar fallback.",
            wait_for_product=False,
        )
        saved = await db.product_raw.find_one({"sku": sku})
        await db.product_raw.delete_one({"sku": sku})
        return result, saved

    result, saved = asyncio.run(run())
    assert result["ok"] is True
    assert captured["supplier_name"] == "TRIMPRO"
    assert captured["johndrop_id"] == "TP-ORIGINAL-77"
    assert captured["cost"] == 23.9
    assert saved["supplier_name"] == "TRIMPRO"


def test_trimpro_queue_marks_missing_required_data_for_review(monkeypatch):
    """TrimPro uses the same non-success state as the original JohnDrop flow."""
    sku = f"TRIMPRO-REVIEW-{time.time_ns()}"

    async def run():
        await db.enrich_pending.insert_one({
            "sku": sku,
            "raw_title": "Mouse Pad Ergonômico",
            "raw_description": "Mouse pad ergonômico com apoio para pulso e base antiderrapante.",
            "supplier_code": "TP-TEST",
            "supplier_name": "TRIMPRO",
            "source": "trimpro_bot",
            "status": "pending",
            "queued_at": "2026-01-01T00:00:00+00:00",
            "attempts": 0,
        })

        async def fake_find(_sku):
            return {
                "id": 456,
                "nome": "Mouse Pad Ergonômico",
                "midia": {"imagens": {"internas": [{"link": "https://image.example/1.jpg"}]}},
            }

        async def fake_enrich(*_args, **_kwargs):
            return {
                "ok": False,
                "needs_review": True,
                "product_id": 456,
                "reason": "Revisão necessária: Fornecedor TRIMPRO não foi encontrado no Bling.",
            }

        monkeypatch.setattr(enrich_worker, "_find_in_bling", fake_find)
        monkeypatch.setattr(enrich_worker.bling_enrichment, "enrich_product_by_sku", fake_enrich)
        monkeypatch.setattr(enrich_worker, "_NEXT_MANUAL_DISCOVERY_AT", float("inf"))
        await enrich_worker._tick()
        item = await db.enrich_pending.find_one({"sku": sku})
        await db.enrich_pending.delete_one({"sku": sku})
        return item

    item = asyncio.run(run())
    assert item["status"] == "needs_review"
    assert "Fornecedor TRIMPRO" in item["last_error"]


def test_retry_moves_needs_review_item_back_to_safe_pending_state():
    sku = f"RETRY-REVIEW-{time.time_ns()}"

    async def run():
        await db.enrich_pending.insert_one({
            "sku": sku,
            "status": "needs_review",
            "attempts": 4,
            "completion_issues": ["fornecedor não vinculado"],
            "last_error": "Revisão necessária",
        })
        result = await enrich_worker.retry_item(sku)
        item = await db.enrich_pending.find_one({"sku": sku})
        await db.enrich_pending.delete_one({"sku": sku})
        return result, item

    result, item = asyncio.run(run())
    assert result == {"ok": True}
    assert item["status"] == "pending"
    assert item["attempts"] == 0
    assert "completion_issues" not in item
