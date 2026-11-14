"""Regression coverage for current Playwright browser-cache layouts."""
from pathlib import Path

import pytest

import johndrop_bot
import trimpro_bot


# Since Playwright 1.58, Linux x64 downloads Chrome for Testing into these
# folders.  The old ``chrome-linux`` layout remains supported by the bots too.
_CURRENT_LINUX_LAYOUTS = (
    Path("chromium_headless_shell-1255")
    / "chrome-headless-shell-linux64"
    / "chrome-headless-shell",
    Path("chromium-1255") / "chrome-linux64" / "chrome",
)


@pytest.mark.parametrize("bot", [johndrop_bot, trimpro_bot])
@pytest.mark.parametrize("relative_binary", _CURRENT_LINUX_LAYOUTS)
def test_detects_current_playwright_linux_browser_layout(
    tmp_path, monkeypatch, bot, relative_binary
):
    """A completed modern Playwright download must immediately be considered ready."""
    cache = tmp_path / "ms-playwright"
    binary = cache / relative_binary
    binary.parent.mkdir(parents=True)
    binary.touch()

    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(cache))
    monkeypatch.setattr(bot, "_expected_chromium_version", lambda: "1255")

    assert bot._find_chromium_binary() == str(binary)
    assert bot.chromium_status()["installed"] is True
    assert bot.chromium_status()["path"] == str(binary)
