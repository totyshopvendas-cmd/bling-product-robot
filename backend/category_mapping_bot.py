"""Playwright scraper para árvores de categorias dos marketplaces no Bling.

Abre o modal 'Vincular categorias multiloja' e extrai a lista completa
de categorias disponíveis em cada marketplace conectado."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List

from playwright.async_api import async_playwright

from db import db
from johndrop_bot import get_johndrop_credentials
from robot_service import add_log

logger = logging.getLogger(__name__)

BLING_LOGIN_URL = "https://www.bling.com.br/login.php"
BLING_CATEGORIES_URL = "https://www.bling.com.br/categorias.produtos.php"


async def scan_marketplace_trees(bling_user: str, bling_pass: str) -> dict:
    """Login no Bling → abre categorias.produtos.php → clica em 'Vincular
    categorias multiloja' → para cada 'Loja Virtual' do dropdown, extrai
    as opções da 'Selecione a categoria...' e persiste.
    """
    trees: dict = {}
    started = datetime.now(timezone.utc).isoformat()
    await db.category_mapping_runs.update_one(
        {"name": "main"},
        {"$set": {"status": "scanning", "started_at": started, "trees_scanned": 0}},
        upsert=True,
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await add_log("info", "MapCategorias: login no Bling...")
            await page.goto(BLING_LOGIN_URL, wait_until="networkidle", timeout=60000)
            # O login do Bling costuma ter 2 passos (usuário → senha)
            await page.fill('input[type="text"], input[name="username"]', bling_user)
            btn = await page.query_selector('button:has-text("Próximo"), button:has-text("Entrar")')
            if btn:
                await btn.click()
            await page.wait_for_timeout(2000)
            await page.fill('input[type="password"], input[name="password"]', bling_pass)
            await page.click('button[type="submit"], button:has-text("Entrar")')
            await page.wait_for_load_state("networkidle", timeout=30000)
            if "login" in page.url.lower():
                raise RuntimeError("Login Bling falhou (credenciais inválidas ou 2FA)")
            await add_log("success", "MapCategorias: login Bling OK")

            await page.goto(BLING_CATEGORIES_URL, wait_until="networkidle", timeout=45000)
            await page.wait_for_timeout(1500)

            # Marca a primeira categoria pra habilitar o menu
            first_cb = await page.query_selector('table tbody input[type="checkbox"]')
            if first_cb:
                await first_cb.check()
                await page.wait_for_timeout(500)

            # Clica em "Vincular categorias multiloja"
            link_btn = await page.query_selector('a:has-text("Vincular categorias multiloja")')
            if not link_btn:
                raise RuntimeError("Botão 'Vincular categorias multiloja' não encontrado")
            await link_btn.click()
            await page.wait_for_selector('text=Lista de categorias', timeout=15000)
            await page.wait_for_timeout(800)

            # Abre o dropdown "Loja Virtual" e lê as opções
            marketplaces: List[str] = []
            # Lê marketplaces do <select>
            marketplaces = await page.evaluate(
                """() => {
                    const sels = Array.from(document.querySelectorAll('select'));
                    for (const s of sels) {
                        const opts = Array.from(s.options).map(o => o.textContent.trim());
                        if (opts.some(o => o.toLowerCase().includes('shop') || o.toLowerCase().includes('amazon') || o.toLowerCase().includes('facebook'))) {
                            return opts.filter(o => o && !o.toLowerCase().includes('selecione'));
                        }
                    }
                    return [];
                }"""
            )
            await add_log("info", f"MapCategorias: {len(marketplaces)} marketplaces detectados: {marketplaces}")

            # Para cada marketplace, seleciona no dropdown e lê as categorias
            for mkt in marketplaces:
                try:
                    # Seleciona o marketplace
                    await page.evaluate(
                        """(txt) => {
                            const sels = Array.from(document.querySelectorAll('select'));
                            for (const s of sels) {
                                const opt = Array.from(s.options).find(o => o.textContent.trim() === txt);
                                if (opt) {
                                    s.value = opt.value;
                                    s.dispatchEvent(new Event('change', {bubbles: true}));
                                    return true;
                                }
                            }
                            return false;
                        }""",
                        mkt,
                    )
                    await page.wait_for_timeout(2500)
                    # Lê as opções da categoria (2º select)
                    cats = await page.evaluate(
                        """() => {
                            const sels = Array.from(document.querySelectorAll('select'));
                            // O segundo select relevante (ou o último) contém categorias
                            for (let i = sels.length - 1; i >= 0; i--) {
                                const s = sels[i];
                                const opts = Array.from(s.options);
                                if (opts.length > 10) {
                                    return opts
                                        .filter(o => o.value && o.value !== '')
                                        .map(o => ({ id: o.value, name: o.textContent.trim() }));
                                }
                            }
                            return [];
                        }"""
                    )
                    trees[mkt] = cats
                    await add_log("info", f"MapCategorias: {mkt} → {len(cats)} categorias")
                    await db.category_mapping_runs.update_one(
                        {"name": "main"},
                        {"$set": {"trees_scanned": len(trees)}},
                    )
                except Exception as e:
                    logger.warning("Falha lendo %s: %s", mkt, e)
                    trees[mkt] = []

            # Salva árvores no Mongo
            await db.category_mapping_trees.delete_many({})
            for mkt, cats in trees.items():
                await db.category_mapping_trees.insert_one({
                    "marketplace": mkt,
                    "categories": cats,
                    "count": len(cats),
                    "scanned_at": datetime.now(timezone.utc).isoformat(),
                })
        finally:
            try:
                await browser.close()
            except Exception:
                pass

    await add_log(
        "success",
        f"MapCategorias: scan concluído. Marketplaces: "
        f"{ {k: len(v) for k, v in trees.items()} }",
    )
    return trees
