"""Unit coverage for the isolated TrimPro robot flow."""
import asyncio
import time

import enrich_worker
import robot_service
import trimpro_bot
from db import db


def test_trimpro_has_its_own_runtime_state_and_provider_settings():
    """Starting/stopping TrimPro must never reuse JohnDrop's robot state."""
    assert trimpro_bot.trimpro_robot is not robot_service.robot
    assert trimpro_bot.TRIMPRO_LOGIN_URL == "https://app.trimpro.com.br/login"
    assert trimpro_bot.TRIMPRO_CATALOG_BASE_URL == "https://app.trimpro.com.br/dashboard/catalog"
    assert trimpro_bot.INTEGRATION_NAME == "TotyShop-Bling"


def test_trimpro_reads_visible_title_below_nome_not_blank_input():
    source = trimpro_bot._choose_trimpro_source_title([
        {"text": "NOME", "distance": 90},
        {"text": "Mouse Pad Ergonômico com Apoio para Pulso Personalizado", "distance": 60},
        {"text": "Nome * (máx 60 carac.)", "distance": 10},
    ])
    assert source == "Mouse Pad Ergonômico com Apoio para Pulso Personalizado"


def test_trimpro_reads_source_text_instead_of_the_empty_nome_input():
    class EmptyNameInput:
        async def input_value(self):
            return ""

    class TrimProCreatePage:
        async def query_selector(self, selector):
            if selector == trimpro_bot._TRIMPRO_NAME_INPUT_SELECTOR:
                return EmptyNameInput()
            return None

        async def evaluate(self, _script):
            return [
                {"text": "NOME", "distance": 90},
                {"text": "Mouse Pad Ergonômico com Apoio para Pulso Personalizado", "distance": 60},
                {"text": "Nome * (máx 60 carac.)", "distance": 10},
            ]

    raw_title, cost = asyncio.run(trimpro_bot._read_product_fields(TrimProCreatePage()))
    assert raw_title == "Mouse Pad Ergonômico com Apoio para Pulso Personalizado"
    assert cost == 0.0


def test_trimpro_poor_title_uses_a_factual_professional_description_phrase():
    result = trimpro_bot._build_trimpro_title(
        "Produto",
        "Mouse Pad Ergonômico com Apoio para Pulso Personalizado. Material resistente.",
        preferred_code=None,
    )
    assert result["method"] == "description_fallback"
    assert "Mouse Pad" in result["cleaned"]
    assert len(result["cleaned"]) <= 60


def test_trimpro_log_is_tagged_for_its_own_log_stream(monkeypatch):
    captured = {}

    async def fake_add_log(level, message, **extra):
        captured.update(level=level, message=message, **extra)

    monkeypatch.setattr(trimpro_bot, "_base_add_log", fake_add_log)
    asyncio.run(trimpro_bot._trimpro_log("info", "teste TrimPro"))

    assert captured == {
        "level": "info",
        "message": "teste TrimPro",
        "bot": "trimpro",
    }


def test_worker_forwards_trimpro_supplier_code_and_name(monkeypatch):
    """The shared enrichment rules must receive TRIMPRO + its source code."""
    sku = f"TRIMPRO-TEST-{time.time_ns()}"
    captured = {}

    async def run():
        await db.enrich_pending.insert_one({
            "sku": sku,
            "raw_title": "Produto TrimPro",
            "raw_description": "Descrição original suficientemente longa para enriquecer.",
            "supplier_code": "TP-209544",
            "supplier_name": "TRIMPRO",
            "source": "trimpro_bot",
            "status": "pending",
            "queued_at": "2026-01-01T00:00:00+00:00",
            "attempts": 0,
        })

        async def fake_find(_sku):
            return {
                "id": 987,
                "nome": "Produto TrimPro",
                "midia": {"imagens": {"internas": [{"link": "https://image.example/1.jpg"}]}},
            }

        async def fake_enrich(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return {"ok": True, "product_id": 987, "variations_created": 0}

        monkeypatch.setattr(enrich_worker, "_find_in_bling", fake_find)
        monkeypatch.setattr(enrich_worker.bling_enrichment, "enrich_product_by_sku", fake_enrich)
        monkeypatch.setattr(enrich_worker, "_NEXT_MANUAL_DISCOVERY_AT", float("inf"))
        await enrich_worker._tick()

        item = await db.enrich_pending.find_one({"sku": sku})
        await db.enrich_pending.delete_one({"sku": sku})
        return item

    item = asyncio.run(run())
    assert captured["args"][0] == sku
    assert captured["kwargs"]["johndrop_id"] == "TP-209544"
    assert captured["kwargs"]["supplier_name"] == "TRIMPRO"
    assert item["status"] == "done"
