"""Safety coverage for the isolated supplier-integration DOM diagnostic."""
import asyncio
import inspect

import pytest

import integration_diagnostic
import johndrop_bot
import server
import trimpro_bot


class SnapshotPage:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def evaluate(self, script):
        self.calls.append(script)
        return self.payload


def test_snapshot_reads_only_the_page_dom_and_returns_control_metadata():
    page = SnapshotPage({
        "page_path": "/dashboard/product/createv2/123",
        "total_visible_controls": 1,
        "controls": [{
            "position": 1,
            "id": "integration-bling",
            "name": "integration",
            "value": "bling",
            "label": "TotyShop-Bling",
        }],
    })

    result = asyncio.run(integration_diagnostic.snapshot_integration_controls(page))

    assert result["page_path"] == "/dashboard/product/createv2/123"
    assert result["total_visible_controls"] == 1
    assert result["controls"][0]["id"] == "integration-bling"
    assert page.calls == [integration_diagnostic._INTEGRATION_CONTROL_SNAPSHOT]


def test_snapshot_probe_has_no_click_fill_or_submit_operation():
    script = integration_diagnostic._INTEGRATION_CONTROL_SNAPSHOT

    assert ".click(" not in script
    assert ".fill(" not in script
    assert ".submit(" not in script
    assert "input[type=\"radio\"]" in script
    assert "data_attributes" in script
    assert "totyshop_bling_hint" in script


@pytest.mark.parametrize(
    ("bot_module", "state_name", "credential_name", "supplier"),
    [
        (johndrop_bot, "robot", "_get_credentials", "JohnDrop"),
        (trimpro_bot, "trimpro_robot", "_get_credentials", "TrimPro"),
    ],
)
def test_diagnostic_stops_before_browser_when_credentials_are_missing(
    monkeypatch, bot_module, state_name, credential_name, supplier
):
    async def no_credentials():
        return None

    state = getattr(bot_module, state_name)
    previous_state, previous_task = state.state, state.task
    state.state, state.task = "idle", None
    monkeypatch.setattr(bot_module, credential_name, no_credentials)
    try:
        result = asyncio.run(bot_module.diagnose_integration_controls())
    finally:
        state.state, state.task = previous_state, previous_task

    assert result == {"ok": False, "detail": f"Credenciais {supplier} não configuradas"}


@pytest.mark.parametrize("bot_module", [johndrop_bot, trimpro_bot])
def test_diagnostic_never_calls_product_processing_or_integration_selection(bot_module):
    source = inspect.getsource(bot_module.diagnose_integration_controls)

    assert "snapshot_integration_controls(page)" in source
    assert "page.goto(create_url" in source
    assert "_process_one_product" not in source
    assert "_select_integration(page)" not in source
    assert "select_option" not in source
    assert "_remember_processed" not in source


def test_johndrop_diagnostic_endpoint_delegates_without_starting_robot(monkeypatch):
    expected = {"ok": True, "supplier": "johndrop", "snapshot": {"controls": []}}

    async def diagnostic():
        return expected

    monkeypatch.setattr(server.johndrop_bot, "diagnose_integration_controls", diagnostic)

    assert asyncio.run(server.johndrop_integration_diagnostic()) == expected


def test_trimpro_diagnostic_endpoint_delegates_without_starting_robot(monkeypatch):
    expected = {"ok": True, "supplier": "trimpro", "snapshot": {"controls": []}}

    async def diagnostic():
        return expected

    monkeypatch.setattr(server.trimpro_bot, "diagnose_integration_controls", diagnostic)

    assert asyncio.run(server.trimpro_integration_diagnostic()) == expected
