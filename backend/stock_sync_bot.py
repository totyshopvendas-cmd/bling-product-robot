"""JohnDrop scraper for the Stock Sync module.

Two sources:
  1. /dashboard/product — "Meus produtos": SKU + total stock + price.
  2. Bell icon → "Ver todos Alertas": per-SKU updates (estoque, preço).

ISOLATED: this scraper does NOT touch the cadastro flow. It reuses ONLY the
credentials helper from `johndrop_bot.get_johndrop_credentials()`.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import List, Optional

from playwright.async_api import async_playwright

from johndrop_bot import get_johndrop_credentials
from robot_service import add_log

logger = logging.getLogger(__name__)

JOHNDROP_LOGIN_URL = "https://app.jonhdrop.com.br/login"
JOHNDROP_PRODUCTS_URL = "https://app.jonhdrop.com.br/dashboard/product"
JOHNDROP_ALERTS_URL_GUESS = "https://app.jonhdrop.com.br/dashboard/alert"


# ---------------------------------------------------------------- helpers --
def _parse_int(s: str | None) -> Optional[int]:
    if not s:
        return None
    m = re.search(r"-?\d+", s.replace(".", "").replace(",", ""))
    return int(m.group(0)) if m else None


def _parse_price(s: str | None) -> Optional[float]:
    if not s:
        return None
    # Brazil format: R$32,50 → 32.50
    txt = (s or "").replace("R$", "").replace(" ", "").strip()
    txt = txt.replace(".", "").replace(",", ".")
    try:
        return float(re.sub(r"[^0-9.]", "", txt) or 0) or None
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------- catalog ---
async def _scrape_catalog(page) -> List[dict]:
    """Walk through every page of /dashboard/product extracting SKU + estoque
    + preço from the table.

    Table columns (per user screenshot):
       Id | Canal | Integração | Img | Nome | Sku | Preço | Estoque | Data | Ações
    """
    items: List[dict] = []
    await page.goto(JOHNDROP_PRODUCTS_URL, wait_until="networkidle", timeout=60000)

    # Try to bump page-size to max (50 or 100) via the 'Mostrar' select
    try:
        selects = await page.query_selector_all("select")
        for sel in selects:
            opts = await sel.evaluate(
                "el => Array.from(el.options).map(o => o.value)"
            )
            if "100" in opts:
                await sel.select_option(value="100")
                await page.wait_for_load_state("networkidle", timeout=15000)
                break
            if "50" in opts:
                await sel.select_option(value="50")
                await page.wait_for_load_state("networkidle", timeout=15000)
                break
    except Exception:
        pass

    page_idx = 1
    seen_skus: set = set()
    while True:
        await page.wait_for_selector("table tbody tr", timeout=15000)
        rows = await page.query_selector_all("table tbody tr")
        added_this_page = 0
        for row in rows:
            try:
                cells = await row.query_selector_all("td")
                if len(cells) < 8:
                    continue
                # Sku cell — extract first non-empty line (the catalogo: badge is on a separate line)
                sku_text = (await cells[5].inner_text() or "").strip()
                sku = sku_text.split("\n")[0].strip()
                if not sku:
                    continue
                # Preço, Estoque
                preco_text = await cells[6].inner_text()
                est_text = await cells[7].inner_text()
                preco = _parse_price(preco_text)
                est = _parse_int(est_text)
                if sku in seen_skus:
                    continue
                seen_skus.add(sku)
                items.append({
                    "sku": sku,
                    "stock": est,
                    "price": preco,
                    "source": "catalog",
                })
                added_this_page += 1
            except Exception as e:
                logger.debug("row parse failed: %s", e)
                continue
        await add_log(
            "info",
            f"SyncEstoque: catálogo página {page_idx} → {added_this_page} SKUs (total {len(items)})",
        )
        # Try to go to next page — pagination footer
        nxt = await page.query_selector(
            "a[rel='next'], li.page-item:not(.disabled) a:has-text('Próximo'), "
            "li.page-item:not(.disabled) a:has-text('Next'), a.paginate_button.next:not(.disabled)"
        )
        if not nxt:
            break
        disabled = await nxt.evaluate("el => el.closest('.disabled') !== null || el.classList.contains('disabled')")
        if disabled:
            break
        try:
            await nxt.click()
            await page.wait_for_load_state("networkidle", timeout=15000)
            page_idx += 1
            if page_idx > 60:  # hard safety stop
                break
        except Exception:
            break
    return items


# ---------------------------------------------------------------- alerts ---
ALERT_SKU_RE = re.compile(r"SKU\s+([A-Z0-9][A-Z0-9\-\._/]+)", re.IGNORECASE)
ALERT_PRICE_RE = re.compile(
    r"R\$\s*([0-9.,]+)\s+para\s+R\$\s*([0-9.,]+)", re.IGNORECASE,
)


async def _scrape_alerts(page) -> List[dict]:
    """Scrape the 'Ver todos Alertas' page. Each card mentions a SKU and either
    'Reposição de estoque' or 'Preço atualizado'.

    For estoque, the alert itself doesn't carry the new total — it only flags
    that stock CHANGED. We emit `{sku, source='alert', stock=None}` which is
    later merged with the catalog scrape (catalog gives the actual total).
    For preço, we parse 'saiu de R$X para R$Y' → new = Y.
    """
    items: List[dict] = []
    # Strategy 1: try direct alert URL (common pattern)
    tried_urls = [
        JOHNDROP_ALERTS_URL_GUESS,
        "https://app.jonhdrop.com.br/dashboard/alerts",
        "https://app.jonhdrop.com.br/dashboard/notification",
        "https://app.jonhdrop.com.br/dashboard/notifications",
    ]
    opened = False
    for url in tried_urls:
        try:
            r = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            if r and r.status < 400:
                await page.wait_for_load_state("networkidle", timeout=10000)
                # Heuristic check: page should contain at least one of the alert words
                content = (await page.content() or "")[:5000].lower()
                if "alert" in content or "reposição" in content or "preço" in content:
                    opened = True
                    break
        except Exception:
            continue

    if not opened:
        # Strategy 2: click the bell icon → "Ver Alertas" → "Ver todos Alertas"
        try:
            await page.goto(JOHNDROP_PRODUCTS_URL, wait_until="networkidle", timeout=30000)
            bell = await page.query_selector(
                "i.fa-bell, svg.lucide-bell, .notification-bell, [aria-label*='Alerta'], a:has-text('Alertas')"
            )
            if bell:
                await bell.click()
                await page.wait_for_timeout(800)
                ver = await page.query_selector("a:has-text('Ver Alertas')")
                if ver:
                    await ver.click()
                    await page.wait_for_load_state("networkidle", timeout=15000)
                ver_all = await page.query_selector("a:has-text('Ver todos Alertas')")
                if ver_all:
                    await ver_all.click()
                    await page.wait_for_load_state("networkidle", timeout=15000)
                    opened = True
        except Exception as e:
            await add_log("warning", f"SyncEstoque: alertas via sino falhou: {str(e)[:120]}")

    if not opened:
        await add_log("warning", "SyncEstoque: não foi possível abrir página de alertas — pulando alertas")
        return items

    # Walk through pagination
    page_idx = 1
    while True:
        # Each alert is typically rendered as a card / list item. Try several selectors.
        cards = await page.query_selector_all(
            ".alert-item, .notification-item, .alert-card, li.list-group-item, .card-body"
        )
        if not cards:
            # Fallback: grab visible text and split by sections containing 'SKU'
            full = await page.evaluate("() => document.body.innerText")
            chunks = re.split(r"(?=Reposição de estoque|Preço atualizado)", full)
            cards_text = [c.strip() for c in chunks if c.strip() and ("SKU" in c.upper())]
        else:
            cards_text = []
            for c in cards:
                try:
                    txt = await c.inner_text()
                    if txt and "SKU" in txt.upper():
                        cards_text.append(txt)
                except Exception:
                    continue
        added = 0
        for txt in cards_text:
            mt = ALERT_SKU_RE.search(txt)
            if not mt:
                continue
            sku = mt.group(1).strip().rstrip(".,;)")
            entry = {"sku": sku, "source": "alert", "stock": None, "price": None}
            if "preço" in txt.lower() or "preco" in txt.lower():
                pm = ALERT_PRICE_RE.search(txt)
                if pm:
                    entry["price"] = _parse_price(pm.group(2))
            # Reposição de estoque → we mark it so the merge step keeps catalog stock
            items.append(entry)
            added += 1
        await add_log("info", f"SyncEstoque: alertas página {page_idx} → {added} entradas")
        if added == 0:
            break
        nxt = await page.query_selector(
            "a[rel='next'], li.page-item:not(.disabled) a:has-text('Próximo'), a.paginate_button.next:not(.disabled)"
        )
        if not nxt:
            break
        try:
            await nxt.click()
            await page.wait_for_load_state("networkidle", timeout=15000)
            page_idx += 1
            if page_idx > 30:
                break
        except Exception:
            break
    return items


# --------------------------------------------------------------- runner ----
async def collect_supplier_items() -> dict:
    """Open JohnDrop, login, scrape catalog + alerts. Returns
    {items: [...], catalog_count, alerts_count}."""
    creds = await get_johndrop_credentials()
    if not creds or not creds.get("username") or not creds.get("password"):
        raise RuntimeError("JohnDrop credentials not configured")

    items: List[dict] = []
    catalog_count = 0
    alerts_count = 0
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await add_log("info", "SyncEstoque: abrindo JohnDrop e fazendo login...")
            await page.goto(JOHNDROP_LOGIN_URL, wait_until="networkidle", timeout=60000)
            await page.fill('input[type="email"], input[name="email"]', creds["username"])
            await page.fill('input[type="password"], input[name="password"]', creds["password"])
            async with page.expect_navigation(wait_until="networkidle", timeout=60000):
                await page.click('button[type="submit"]')
            if "login" in page.url.lower():
                raise RuntimeError("Login JohnDrop falhou — credenciais inválidas")
            await add_log("success", "SyncEstoque: login JohnDrop OK")

            catalog = await _scrape_catalog(page)
            catalog_count = len(catalog)
            items.extend(catalog)

            alerts = await _scrape_alerts(page)
            alerts_count = len(alerts)
            items.extend(alerts)

        finally:
            try:
                await browser.close()
            except Exception:
                pass

    await add_log(
        "info",
        f"SyncEstoque: coletado catálogo={catalog_count} alertas={alerts_count}",
    )
    return {
        "items": items,
        "catalog_count": catalog_count,
        "alerts_count": alerts_count,
    }
