"""Dropper Playwright automation bot.

The Dropper bot mirrors the business flow of the JohnDrop bot while keeping
its own credentials, state, selectors and logs. It only navigates/submits the
Dropper catalog. After a successful supplier-side registration it queues the
SKU as waiting for Dropper -> Bling native synchronization; it never writes to
Bling and never forwards supplier image URLs to Bling.
"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from typing import Optional, Tuple

from db import db
from pricing_service import lookup_price
from robot_service import add_log
from title_cleaner import clean_title
from johndrop_bot import (
    _ensure_chromium_installed,
    _find_chromium_binary,
    _playwright_available,
    browsers_path,
    pw_env,
    _parse_brl_number,
)

DROPPER_BASE_URL = "https://dropper.dropping.com.br"
DROPPER_LOGIN_URL = f"{DROPPER_BASE_URL}/login"
DROPPER_CATALOG_CANDIDATES = (
    f"{DROPPER_BASE_URL}/catalogo",
    f"{DROPPER_BASE_URL}/catalog",
    f"{DROPPER_BASE_URL}/produtos",
    f"{DROPPER_BASE_URL}/dashboard/catalog",
    DROPPER_BASE_URL,
)
DROPPER_INTEGRATION_NAME = "TotyShop-Bling"
DROPPER_SUPPLIER_NAME = (os.environ.get("DROPPER_SUPPLIER_NAME") or "DROPPER").strip()
DELAY_BETWEEN_PRODUCTS_S = 25


class DropperRobotState:
    def __init__(self):
        self.state = "idle"
        self.current_product: Optional[str] = None
        self.processed = 0
        self.success = 0
        self.failed = 0
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None
        self.message: Optional[str] = None
        self.task: Optional[asyncio.Task] = None
        self.stop_flag = False

    def reset(self):
        self.current_product = None
        self.processed = 0
        self.success = 0
        self.failed = 0
        self.started_at = None
        self.finished_at = None
        self.message = None
        self.stop_flag = False

    def to_dict(self):
        return {
            "state": self.state,
            "current_product": self.current_product,
            "processed": self.processed,
            "success": self.success,
            "failed": self.failed,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "message": self.message,
        }


dropper_robot = DropperRobotState()


async def _log(level: str, message: str, **extra):
    await add_log(level, message, bot="dropper", **extra)


async def _save_credentials(username: str, password: str):
    value = {"username": username.strip(), "password": password}
    await db.settings.update_one(
        {"key": "dropper_creds"},
        {"$set": {"key": "dropper_creds", "value": value}},
        upsert=True,
    )
    try:
        import secrets_store
        secrets_store.write("dropper", value)
    except Exception:
        pass


async def _get_credentials():
    try:
        doc = await db.settings.find_one({"key": "dropper_creds"}, {"_id": 0})
        value = (doc or {}).get("value") if doc else None
        if value and value.get("username"):
            return value
    except Exception:
        pass
    try:
        import secrets_store
        value = secrets_store.read("dropper")
        if value and value.get("username"):
            return value
    except Exception:
        pass
    return None


async def _login(page, username: str, password: str) -> bool:
    await _log("info", "Abrindo login do Dropper…")
    await page.goto(DROPPER_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
    await page.fill('input[type="email"], input[placeholder*="mail" i], input[name="email"]', username)
    await page.fill('input[type="password"], input[placeholder*="senha" i], input[name="password"]', password)
    await page.click('button[type="submit"]:has-text("Entrar"), button[type="submit"]')
    try:
        await page.wait_for_load_state("networkidle", timeout=30000)
    except Exception:
        await page.wait_for_timeout(3000)
    if "/login" in (page.url or "").lower():
        await _log("error", "Falha no login Dropper — verifique e-mail e senha")
        return False
    await _log("success", "Login Dropper OK")
    return True


async def _open_catalog(page) -> None:
    last_error = None
    for url in DROPPER_CATALOG_CANDIDATES:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(1200)
            text = (await page.locator("body").inner_text(timeout=5000)).lower()
            if any(x in text for x in ("produto", "catálogo", "catalogo", "preço", "estoque")):
                await _log("info", f"Catálogo Dropper aberto: {page.url}")
                return
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise RuntimeError("Não encontrei o catálogo do Dropper")


async def _product_links(page) -> list[str]:
    """Collect product links without scraping or forwarding image URLs."""
    links = await page.evaluate(
        """() => Array.from(document.querySelectorAll('a[href]'))
          .map(a => ({href: a.href, text: (a.innerText || a.textContent || '').trim()}))
          .filter(x => x.text && /produto|catalog|item|detalhe/i.test(x.href + ' ' + x.text))
          .map(x => x.href)"""
    )
    out, seen = [], set()
    for link in links or []:
        if link not in seen and "/login" not in link:
            seen.add(link)
            out.append(link)
    return out[:100]


async def _text_from_selectors(page, selectors: list[str]) -> str:
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if await loc.count():
                value = await loc.input_value() if await loc.is_editable() else await loc.inner_text()
                if value and value.strip():
                    return value.strip()
        except Exception:
            continue
    return ""


async def _read_product(page) -> Tuple[str, float, str, Optional[str]]:
    title = await _text_from_selectors(page, [
        "h1", "h2", "[data-testid*='title' i]", "[class*='title' i]",
        "input[name*='nome' i]", "input[name*='name' i]",
    ])
    sku = await _text_from_selectors(page, [
        "[data-testid*='sku' i]", "[class*='sku' i]", "input[name*='sku' i]",
    ])
    description = await _text_from_selectors(page, [
        "[data-testid*='description' i]", "[class*='description' i]",
        "[class*='descricao' i]", "textarea[name*='desc' i]",
    ])
    body = (await page.locator("body").inner_text()).strip()
    if not title:
        title = next((x.strip() for x in body.splitlines() if 8 < len(x.strip()) < 180), "Produto Dropper")
    if not description:
        description = body[:12000]
    if not sku:
        match = re.search(r"(?:SKU|c[oó]digo)\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9_-]{2,})", body, re.I)
        sku = match.group(1) if match else ""
    price_text = await _text_from_selectors(page, [
        "[data-testid*='cost' i]", "[data-testid*='price' i]", "[class*='price' i]",
        "[class*='preco' i]", "input[name*='custo' i]", "input[name*='price' i]",
    ])
    if not price_text:
        match = re.search(r"(?:custo|pre[cç]o|price)\s*[:R$]*\s*([\d.,]+)", body, re.I)
        price_text = match.group(1) if match else "0"
    return title, _parse_brl_number(price_text), description, sku or None


async def _select_bling_integration(page) -> bool:
    try:
        loc = page.get_by_text(DROPPER_INTEGRATION_NAME, exact=True).first
        if await loc.count():
            await loc.click()
            await page.wait_for_timeout(500)
            return True
    except Exception:
        pass
    return False


async def _submit_product(page, cleaned_title: str, sale_price: int) -> bool:
    """Submit only on Dropper; this function has no Bling/API dependency."""
    await _select_bling_integration(page)
    # Some Dropper flows expose an editable publishing form; others carry the
    # supplier values forward automatically. Fill only fields that exist.
    for selector in (
        'input[name*="nome" i]', 'input[name*="name" i]',
        'input[name*="titulo" i]', 'input[name*="title" i]',
    ):
        try:
            field = page.locator(selector).first
            if await field.count() and await field.is_visible() and await field.is_editable():
                await field.fill(cleaned_title)
                break
        except Exception:
            continue
    for selector in (
        'input[name*="preco" i]', 'input[name*="price" i]',
        'input[name*="venda" i]', 'input[type="number"]',
    ):
        try:
            field = page.locator(selector).first
            if await field.count() and await field.is_visible() and await field.is_editable():
                await field.fill(f"{sale_price / 100:.2f}")
                break
        except Exception:
            continue
    selectors = [
        'button:has-text("Cadastrar")', 'button:has-text("Adicionar")',
        'button:has-text("Importar")', 'button:has-text("Integrar")',
        'button:has-text("Publicar")', 'button[type="submit"]',
    ]
    for selector in selectors:
        try:
            btn = page.locator(selector).last
            if await btn.count() and await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(2500)
                return True
        except Exception:
            continue
    await _log("error", f"Botão de cadastro não encontrado para {cleaned_title}")
    return False


async def _queue_pending(sku: str, title: str, description: str, cost: float, supplier_id: Optional[str]):
    now = datetime.now(timezone.utc).isoformat()
    await db.enrich_pending.update_one(
        {"sku": sku},
        {"$set": {
            "sku": sku,
            "raw_title": title,
            "raw_description": description,
            "supplier": "dropper",
            "supplier_name": DROPPER_SUPPLIER_NAME,
            "supplier_id": supplier_id,
            "cost": cost,
            "status": "pending",
            "queued_at": now,
            "attempts": 0,
        }},
        upsert=True,
    )


async def _process_one(page, dry_run: bool, link: str) -> bool:
    await page.goto(link, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(1000)
    raw_title, cost, raw_description, detected_sku = await _read_product(page)
    dropper_id = re.search(r"/([A-Za-z0-9_-]+)(?:[/?#]|$)", link)
    supplier_id = detected_sku or (dropper_id.group(1) if dropper_id else None)
    cleaned = clean_title(raw_title, preferred_code=detected_sku)
    sku = detected_sku or cleaned.get("code_used") or ""
    if not sku:
        await _log("warning", f"SKU não encontrado: {raw_title[:80]}")
        return False
    price = await lookup_price(cost)
    if not price.found:
        await _log("error", f"Preço não encontrado para custo {cost}", raw_title=raw_title)
        return False
    dropper_robot.current_product = raw_title[:80]
    if dry_run:
        await _log("info", f"[DRY-RUN] Pronto: {cleaned['cleaned']} | SKU: {sku} | Preço: {price.sale_price_int}", bot="dropper")
        return True
    if not await _submit_product(page, cleaned["cleaned"], price.sale_price_int):
        return False
    await _queue_pending(sku, cleaned["cleaned"], raw_description, cost, supplier_id)
    await _log("success", f"Dropper cadastrado: {cleaned['cleaned']} — aguardando sync→Bling", raw_title=raw_title, cleaned_title=cleaned["cleaned"], sale_price=price.sale_price_int)
    return True


async def _run_mock(max_products: int):
    samples = [
        ("Organizador Multiuso Dropper", 19.90),
        ("Suporte Universal para Celular", 24.90),
        ("Luminária LED USB", 29.90),
    ]
    for raw, cost in samples[:max_products]:
        result = clean_title(raw)
        price = await lookup_price(cost)
        dropper_robot.processed += 1
        if price.found:
            dropper_robot.success += 1
            await _log("info", f"[MOCKED] Pronto para cadastrar: {result['cleaned']} | Preço: {price.sale_price_int}")
        else:
            dropper_robot.failed += 1
        await asyncio.sleep(0.1)


async def run_bot(max_products: int = 10, dry_run: bool = True):
    dropper_robot.reset()
    dropper_robot.state = "running"
    dropper_robot.started_at = datetime.now(timezone.utc)
    await _log("info", f"Robô Dropper iniciado (max={max_products}, dry_run={dry_run})")
    creds = await _get_credentials()
    if not creds:
        dropper_robot.state = "error"
        dropper_robot.message = "Credenciais Dropper não configuradas"
        await _log("error", "Credenciais Dropper ausentes. Configure em Configurações.")
        return
    try:
        if not await _playwright_available():
            dropper_robot.message = "Playwright/Chromium indisponível"
            await _run_mock(max_products)
            return
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = await browser.new_page()
            if not await _login(page, creds["username"], creds["password"]):
                dropper_robot.state = "error"
                dropper_robot.message = "Falha no login Dropper"
                await browser.close()
                return
            await _open_catalog(page)
            links = await _product_links(page)
            if not links:
                raise RuntimeError("Nenhum produto encontrado no catálogo Dropper")
            for link in links[:max_products]:
                if dropper_robot.stop_flag:
                    break
                dropper_robot.processed += 1
                try:
                    if await _process_one(page, dry_run, link):
                        dropper_robot.success += 1
                    else:
                        dropper_robot.failed += 1
                except Exception as exc:
                    dropper_robot.failed += 1
                    await _log("error", f"Falha no produto Dropper: {str(exc)[:180]}")
                if not dry_run:
                    await asyncio.sleep(DELAY_BETWEEN_PRODUCTS_S)
            await browser.close()
    except Exception as exc:
        dropper_robot.state = "error"
        dropper_robot.message = str(exc)[:240]
        await _log("error", f"Robô Dropper falhou: {dropper_robot.message}")
    finally:
        dropper_robot.finished_at = datetime.now(timezone.utc)
        if dropper_robot.state == "running":
            dropper_robot.state = "idle"


async def start_bot(max_products: int = 10, dry_run: bool = True):
    if dropper_robot.state == "running":
        return
    dropper_robot.task = asyncio.create_task(run_bot(max_products, dry_run))


async def stop_bot():
    dropper_robot.stop_flag = True
    if dropper_robot.state == "running":
        dropper_robot.message = "Parada solicitada — finalizando produto atual"


async def save_dropper_credentials(username: str, password: str):
    await _save_credentials(username, password)


async def get_dropper_credentials():
    return await _get_credentials()


def chromium_status() -> dict:
    expected = None
    found = _find_chromium_binary()
    return {"installed": bool(found), "path": found, "expected": expected, "browsers_path": browsers_path()}


__all__ = [
    "dropper_robot", "start_bot", "stop_bot", "run_bot", "save_dropper_credentials",
    "get_dropper_credentials", "chromium_status", "pw_env", "_ensure_chromium_installed",
]
