"""Unit tests for category_mapping auto-sync helpers.

Uses an in-memory fake Mongo collection to test the deduplication /
new-detection logic without touching a real DB or Bling API.
"""
import sys
import os
import asyncio
from unittest.mock import patch, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import category_mapping as cm  # noqa: E402


class FakeCursor:
    def __init__(self, items):
        self._items = items

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._items):
            raise StopAsyncIteration
        v = self._items[self._i]
        self._i += 1
        return v

    def limit(self, n):
        return FakeCursor(self._items[:n])


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = docs or []
        self.inserted = []
        self.updated = []

    def find(self, q=None, proj=None):
        return FakeCursor(self.docs)

    async def find_one(self, q, proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                return d
        return None

    async def insert_one(self, doc):
        self.inserted.append(doc)
        self.docs.append(doc)

    async def update_one(self, q, update, upsert=False):
        self.updated.append((q, update))

    async def delete_many(self, q):
        self.docs = []


def test_match_score_shared_tokens():
    assert cm._match_score("Relógio Digital", "Relógio Esportivo") > 0
    assert cm._match_score("XYZ ABC", "Alpha Beta") == 0.0


def test_get_new_bling_categories_filters_known():
    async def _run():
        previews = FakeCollection([{"bling_category_id": 1}, {"bling_category_id": 2}])
        with patch.object(cm, "db") as fake_db, \
             patch.object(cm, "_get_bling_categories_from_api", new=AsyncMock(
                 return_value=[
                     {"id": 1, "descricao": "Cat A"},
                     {"id": 2, "descricao": "Cat B"},
                     {"id": 3, "descricao": "Cat C NOVA"},
                 ])):
            fake_db.category_mapping_previews = previews
            out = await cm.get_new_bling_categories()
        assert len(out) == 1
        assert out[0]["id"] == 3

    asyncio.run(_run())


def test_map_single_category_creates_previews_per_marketplace():
    async def _run():
        previews = FakeCollection([])
        trees = {
            "Amazon": [{"id": "a1", "name": "Relógios"}, {"id": "a2", "name": "Bolsas"}],
            "Shopee": [{"id": "s1", "name": "Relógios"}, {"id": "s2", "name": "Bolsas"}],
        }
        with patch.object(cm, "db") as fake_db, \
             patch.object(cm, "_llm_pick_best_match", new=AsyncMock(
                 return_value={"id": "x1", "name": "Relógios", "confidence": 0.9, "reason": "llm"})):
            fake_db.category_mapping_previews = previews
            n = await cm.map_single_category(
                {"id": 42, "descricao": "Relógio Digital"}, trees, auto_approve=True,
            )
        assert n == 2  # um por marketplace
        assert len(previews.inserted) == 2
        # todos aprovados e não aplicados ainda
        assert all(p["approved"] is True for p in previews.inserted)
        assert all(p["applied"] is False for p in previews.inserted)
        assert all(p.get("auto_synced") is True for p in previews.inserted)

    asyncio.run(_run())


def test_map_single_category_skips_existing():
    """Idempotente: já existe preview para (categoria, marketplace) → não duplica."""
    async def _run():
        existing = [{"bling_category_id": 42, "marketplace": "Amazon"}]
        previews = FakeCollection(existing)
        trees = {
            "Amazon": [{"id": "a1", "name": "Relógios"}],
            "Shopee": [{"id": "s1", "name": "Relógios"}],
        }
        with patch.object(cm, "db") as fake_db, \
             patch.object(cm, "_llm_pick_best_match", new=AsyncMock(
                 return_value={"id": "x1", "name": "Relógios", "confidence": 0.9})):
            fake_db.category_mapping_previews = previews
            n = await cm.map_single_category(
                {"id": 42, "descricao": "Relógio"}, trees,
            )
        # Só Shopee é novo
        assert n == 1
        assert previews.inserted[0]["marketplace"] == "Shopee"

    asyncio.run(_run())


def test_map_single_category_no_suggestion_not_approved():
    """Se LLM não retornou id, o preview fica sem approved (não aplicar às cegas)."""
    async def _run():
        previews = FakeCollection([])
        trees = {"Amazon": [{"id": "a1", "name": "Coisas"}]}
        with patch.object(cm, "db") as fake_db, \
             patch.object(cm, "_llm_pick_best_match", new=AsyncMock(return_value=None)):
            fake_db.category_mapping_previews = previews
            n = await cm.map_single_category(
                {"id": 99, "descricao": "Categoria Estranha"}, trees, auto_approve=True,
            )
        assert n == 1
        assert previews.inserted[0]["approved"] is False
        assert previews.inserted[0]["suggestion_id"] is None
