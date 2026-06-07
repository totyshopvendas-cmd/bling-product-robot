"""Unit tests for the new _wait_for_johndrop_sync helper.

These tests stub out bling_service.bling_request so we don't hit the real API
and don't depend on Bling rate limits.
"""
import asyncio
import importlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def enrichment_module(monkeypatch):
    """Reload bling_enrichment with bling_service stubbed."""
    # Stub bling_service before import
    fake_bling = types.ModuleType("bling_service")
    fake_bling.bling_request = AsyncMock()
    monkeypatch.setitem(sys.modules, "bling_service", fake_bling)
    # Stub robot_service.add_log (lightweight)
    fake_robot = types.ModuleType("robot_service")
    fake_robot.add_log = AsyncMock()
    monkeypatch.setitem(sys.modules, "robot_service", fake_robot)
    # Stub db
    fake_db_mod = types.ModuleType("db")
    fake_db_mod.db = MagicMock()
    monkeypatch.setitem(sys.modules, "db", fake_db_mod)

    if "bling_enrichment" in sys.modules:
        del sys.modules["bling_enrichment"]
    mod = importlib.import_module("bling_enrichment")
    return mod, fake_bling, fake_robot


def _response(status: int, body: dict):
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=body)
    return r


def test_wait_returns_immediately_when_stock_already_present(enrichment_module):
    """First read shows estoque>0 → no polling, returns at once."""
    mod, fake_bling, _ = enrichment_module

    async def fake_req(method, path, **kw):
        if path.startswith("/produtos/"):
            return _response(200, {"data": {"id": 1, "estoque": {"saldoVirtualTotal": 5}, "midia": {"imagens": {"internas": []}}}})
        if path == "/estoques/saldos":
            return _response(200, {"data": [{"saldoVirtualTotal": 5}]})
        return _response(404, {})

    fake_bling.bling_request.side_effect = fake_req

    async def go():
        return await mod._wait_for_johndrop_sync(1, "TEST", max_attempts=3, delay_s=0.01)

    result = asyncio.run(go())
    assert result and result.get("id") == 1


def test_wait_returns_immediately_when_images_already_present(enrichment_module):
    """First read shows images>0 → no polling."""
    mod, fake_bling, _ = enrichment_module

    async def fake_req(method, path, **kw):
        if path.startswith("/produtos/"):
            return _response(200, {"data": {"id": 2, "midia": {"imagens": {"internas": [{"link": "u"}]}}}})
        if path == "/estoques/saldos":
            return _response(200, {"data": [{"saldoVirtualTotal": 0}]})
        return _response(404, {})

    fake_bling.bling_request.side_effect = fake_req

    async def go():
        return await mod._wait_for_johndrop_sync(2, "TEST", max_attempts=2, delay_s=0.01)

    result = asyncio.run(go())
    assert result and result.get("id") == 2


def test_wait_polls_until_sync_arrives(enrichment_module):
    """First N reads show empty, then stock arrives → loop returns after the arrival."""
    mod, fake_bling, _ = enrichment_module

    call_state = {"saldo_calls": 0}

    async def fake_req(method, path, **kw):
        if path.startswith("/produtos/"):
            return _response(200, {"data": {"id": 3, "midia": {"imagens": {"internas": []}}}})
        if path == "/estoques/saldos":
            call_state["saldo_calls"] += 1
            # Sync arrives on the 3rd call
            saldo = 7 if call_state["saldo_calls"] >= 3 else 0
            return _response(200, {"data": [{"saldoVirtualTotal": saldo}]})
        return _response(404, {})

    fake_bling.bling_request.side_effect = fake_req

    async def go():
        return await mod._wait_for_johndrop_sync(3, "TEST", max_attempts=5, delay_s=0.01)

    result = asyncio.run(go())
    assert result and result.get("id") == 3
    assert call_state["saldo_calls"] >= 3


def test_wait_times_out_gracefully(enrichment_module):
    """When sync never arrives within max_attempts, returns whatever full doc it has + warns."""
    mod, fake_bling, _ = enrichment_module

    async def fake_req(method, path, **kw):
        if path.startswith("/produtos/"):
            return _response(200, {"data": {"id": 4, "midia": {"imagens": {}}}})
        if path == "/estoques/saldos":
            return _response(200, {"data": [{"saldoVirtualTotal": 0}]})
        return _response(404, {})

    fake_bling.bling_request.side_effect = fake_req

    async def go():
        return await mod._wait_for_johndrop_sync(4, "TEST", max_attempts=3, delay_s=0.01)

    result = asyncio.run(go())
    # Returns the last full doc (id=4); doesn't crash
    assert result and result.get("id") == 4
