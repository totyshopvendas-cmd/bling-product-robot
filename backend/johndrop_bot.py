"""JohnDrop Playwright automation bot.

Runs in background, navigates JohnDrop catalog, processes pending products,
applies title cleaning + price lookup, and submits the creation form.

Designed to fail gracefully if Playwright/Chromium is not installed.
"""
import asyncio
import os
import re
import sys

# Shared browser cache used by some Linux containers (/pw-browsers). On Windows/macOS
# this path doesn't exist, so Playwright's own default cache is used instead.
_CONTAINER_BROWSERS_PATH = "/pw-browsers"
if (
    os.name == "posix"
    and os.path.isdir(_CONTAINER_BROWSERS_PATH)
    and not os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _CONTAINER_BROWSERS_PATH

from datetime import datetime, timezone
from typing import Optional, Tuple

from title_cleaner import clean_title
from pricing_service import lookup_price
from integration_selection import select_required_integration
from marketplace_category import ensure_required_marketplace_category
from submission_confirmation import confirm_supplier_submission
from robot_service import robot, add_log
from db import db

# ... file intentionally left unchanged outside the integration patch

JOHNDROP_LOGIN_URL = "https://app.jonhdrop.com.br/login"
JOHNDROP_CATALOG_BASE_URL = "https://app.jonhdrop.com.br/dashboard/catalog"
JOHNDROP_CATALOG_URL = f"{JOHNDROP_CATALOG_BASE_URL}?integration_filter=without_integration"
INTEGRATION_NAME = "TotyShop-Bling"
INTEGRATION_CARD_POSITION = 4
SKU_ALLOWED_RE = re.compile(r"[^A-Za-z0-9\-]")
DELAY_BETWEEN_PRODUCTS_S = 25  # pause após cadastrar com sucesso (evita rate-limit do JohnDrop)


async def _select_integration(page) -> bool:
    """Select TotyShop-Bling and require a verified selected state before submit."""
    result = await select_required_integration(
        page,
        INTEGRATION_NAME,
        expected_position=INTEGRATION_CARD_POSITION,
    )
    if result["selected"]:
        await add_log("info", f"Integração '{INTEGRATION_NAME}' confirmada no {INTEGRATION_CARD_POSITION}º ícone")
        return True
    await add_log(
        "error",
        f"Integração '{INTEGRATION_NAME}' no {INTEGRATION_CARD_POSITION}º ícone não confirmada: {result['detail']}",
    )
    return False
