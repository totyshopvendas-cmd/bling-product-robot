"""A real Playwright failure must never be reported as mocked successes."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import johndrop_bot  # noqa: E402


def test_playwright_timeout_is_error_not_mock_success(monkeypatch):
    logs = []

    async def fake_credentials():
        return {"username": "user@example.test", "password": "not-used"}

    async def playwright_ready():
        return True

    async def fail_playwright(*args, **kwargs):
        raise TimeoutError("simulated 60 second timeout")

    async def fake_log(level, message, **extra):
        logs.append((level, message))

    async def forbidden_mock(*args, **kwargs):
        raise AssertionError("MOCKED fallback must not run")

    monkeypatch.setattr(johndrop_bot, "_get_credentials", fake_credentials)
    monkeypatch.setattr(johndrop_bot, "_playwright_available", playwright_ready)
    monkeypatch.setattr(johndrop_bot, "_run_playwright", fail_playwright)
    monkeypatch.setattr(johndrop_bot, "add_log", fake_log)
    monkeypatch.setattr(johndrop_bot, "_run_mock", forbidden_mock)

    asyncio.run(johndrop_bot.run_bot(max_products=5, dry_run=False))

    assert johndrop_bot.robot.state == "error"
    assert johndrop_bot.robot.processed == 0
    assert johndrop_bot.robot.success == 0
    assert any("sem fallback MOCKED" in message for _, message in logs)
