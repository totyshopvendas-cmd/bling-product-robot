"""Description completion contract shared by JohnDrop and TrimPro."""
import asyncio
import time

import pytest

import enrich_worker
from bling_enrichment import _description_contract_error, _normalise_generated_bullets
from db import db


def test_complementary_description_accepts_six_distinct_factual_bullets():
    raw = """- Material: silicone
- Formato ergonômico
- Base antiderrapante
- Compatível com notebook
- Fácil de limpar
- Uso doméstico"""

    bullets = _normalise_generated_bullets(raw)

    assert len(bullets) == 6
    assert all(line.startswith("- ") for line in bullets)
    assert _description_contract_error("Descrição do produto suficientemente completa.", bullets) is None


def test_complementary_description_rejects_incomplete_or_repeated_reply():
    five_bullets = "\n".join(f"- Item técnico {n}" for n in range(1, 6))
    repeated = "\n".join(["- Material resistente"] * 6)

    with pytest.raises(ValueError, match="6 ou 8 bullets"):
        _normalise_generated_bullets(five_bullets)
    with pytest.raises(ValueError, match="6 ou 8 bullets"):
        _normalise_generated_bullets(repeated)


def test_description_contract_requires_a_real_short_description():
    bullets = _normalise_generated_bullets("\n".join(f"- Fato técnico {n}" for n in range(1, 7)))

    assert _description_contract_error("curta", bullets) == "A IA não gerou uma descrição curta completa."


def test_trimpro_waits_for_ai_instead_of_being_marked_done(monkeypatch):
    """The same protection applies to the TrimPro queue and is retryable."""
    sku = f"TRIMPRO-DESC-{time.time_ns()}"

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
                "needs_ai_retry": True,
                "product_id": 456,
                "reason": "Descrição aguardando IA: chave não configurada",
            }

        monkeypatch.setattr(enrich_worker, "_find_in_bling", fake_find)
        monkeypatch.setattr(enrich_worker.bling_enrichment, "enrich_product_by_sku", fake_enrich)
        monkeypatch.setattr(enrich_worker, "_NEXT_MANUAL_DISCOVERY_AT", float("inf"))
        await enrich_worker._tick()
        item = await db.enrich_pending.find_one({"sku": sku})
        await db.enrich_pending.delete_one({"sku": sku})
        return item

    item = asyncio.run(run())
    assert item["status"] == "waiting_ai"
    assert item["needs_llm_retry"] is True
    assert "Descrição aguardando IA" in item["last_error"]


def test_queue_reports_waiting_ai_separately_from_completed_items():
    sku = f"DESC-WAIT-{time.time_ns()}"

    async def run():
        await db.enrich_pending.insert_one({"sku": sku, "status": "waiting_ai"})
        queue = await enrich_worker.list_queue()
        await db.enrich_pending.delete_one({"sku": sku})
        return queue

    queue = asyncio.run(run())
    assert queue["summary"]["waiting_ai"] >= 1
