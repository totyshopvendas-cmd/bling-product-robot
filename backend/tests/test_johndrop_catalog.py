"""Regression tests for JohnDrop catalog fallback when the filter UI times out."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import johndrop_bot  # noqa: E402


class FakeSelect:
    async def evaluate(self, script):
        if "join('||')" in script:
            return "Todos||Todos que eu não cadastrei"
        return "without-integration"

    async def select_option(self, value):
        assert value == "without-integration"


class FakeButton:
    async def click(self):
        return None


class FakePage:
    def __init__(self):
        self.visited = []
        self.card_waits = 0

    async def goto(self, url, **kwargs):
        self.visited.append(url)

    async def query_selector_all(self, selector):
        return [FakeSelect()]

    async def query_selector(self, selector):
        return FakeButton()

    async def wait_for_timeout(self, delay):
        return None

    async def wait_for_selector(self, selector, timeout):
        self.card_waits += 1
        if self.card_waits == 1:
            raise TimeoutError("UI filter returned no cards")
        return object()



def test_catalog_falls_back_to_direct_unregistered_url(monkeypatch):
    logs = []

    async def fake_log(level, message, **extra):
        logs.append((level, message))

    monkeypatch.setattr(johndrop_bot, "add_log", fake_log)
    page = FakePage()
    asyncio.run(johndrop_bot._open_catalog(page))

    assert page.visited[0] == johndrop_bot.JOHNDROP_CATALOG_BASE_URL
    assert page.visited[-1] == johndrop_bot.JOHNDROP_CATALOG_URL
    assert any("tentando endereço direto" in message.lower() for _, message in logs)
    assert page.card_waits == 2
