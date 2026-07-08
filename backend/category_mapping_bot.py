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


async def _bling_login(page, bling_user: str, bling_pass: str) -> None:
    await page.goto(BLING_LOGIN_URL, wait_until="networkidle", timeout=60000)
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


async def apply_mappings_for_categories(
    bling_user: str, bling_pass: str, bling_category_ids: List[int],
) -> int:
    """Aplica mapeamentos aprovados via Playwright para as categorias dadas.

    Para cada categoria:
      1. Filtra checkbox pela linha da categoria (busca por texto/descrição).
      2. Marca só ela, abre 'Vincular categorias multiloja'.
      3. Para cada marketplace, seleciona no dropdown → seleciona a sugestão
         no 2º dropdown → clica 'Adicionar'/'Vincular'.
      4. Salva/fecha modal.
      5. Marca preview como applied=True no Mongo.
    """
    if not bling_category_ids:
        return 0

    # Carrega previews aprovados e não aplicados para essas categorias
    previews = []
    async for p in db.category_mapping_previews.find({
        "bling_category_id": {"$in": bling_category_ids},
        "approved": True,
        "applied": {"$ne": True},
        "suggestion_id": {"$ne": None},
    }, {"_id": 0}):
        previews.append(p)
    if not previews:
        await add_log("info", "ApplyMap: nada a aplicar (0 previews aprovados)")
        return 0

    # Agrupa por categoria
    by_cat: dict = {}
    for p in previews:
        by_cat.setdefault(p["bling_category_id"], []).append(p)

    applied_count = 0
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            ctx = await browser.new_context()
            page = await ctx.new_page()
            await add_log("info", "ApplyMap: login Bling...")
            await _bling_login(page, bling_user, bling_pass)
            await add_log("success", "ApplyMap: login OK")

            for cat_id, cat_previews in by_cat.items():
                cat_name = cat_previews[0]["bling_category_name"]
                try:
                    await page.goto(BLING_CATEGORIES_URL, wait_until="networkidle", timeout=45000)
                    await page.wait_for_timeout(1200)

                    # Localiza a linha pela descrição e marca só o checkbox dela
                    row_ok = await page.evaluate(
                        """(name) => {
                            const rows = Array.from(document.querySelectorAll('table tbody tr'));
                            // desmarca todos
                            document.querySelectorAll('table tbody input[type=checkbox]:checked')
                                .forEach(cb => { cb.checked = false; cb.dispatchEvent(new Event('change', {bubbles: true})); });
                            for (const tr of rows) {
                                if ((tr.innerText || '').trim().includes(name)) {
                                    const cb = tr.querySelector('input[type=checkbox]');
                                    if (cb) { cb.checked = true; cb.dispatchEvent(new Event('change', {bubbles: true})); return true; }
                                }
                            }
                            return false;
                        }""",
                        cat_name,
                    )
                    if not row_ok:
                        await add_log("warning", f"ApplyMap: linha '{cat_name}' não achada")
                        continue
                    await page.wait_for_timeout(400)

                    link_btn = await page.query_selector('a:has-text("Vincular categorias multiloja")')
                    if not link_btn:
                        await add_log("warning", "ApplyMap: botão 'Vincular' indisponível")
                        continue
                    await link_btn.click()
                    await page.wait_for_selector('text=Lista de categorias', timeout=15000)
                    await page.wait_for_timeout(800)

                    # Para cada marketplace/sugestão aprovada, seleciona e adiciona
                    for prv in cat_previews:
                        mkt = prv["marketplace"]
                        sug_id = str(prv.get("suggestion_id") or "")
                        sug_name = prv.get("suggestion_name") or ""
                        if not sug_id:
                            continue
                        try:
                            # Seleciona marketplace no 1º select
                            await page.evaluate(
                                """(txt) => {
                                    const sels = Array.from(document.querySelectorAll('select'));
                                    for (const s of sels) {
                                        const opt = Array.from(s.options).find(o => o.textContent.trim() === txt);
                                        if (opt) { s.value = opt.value; s.dispatchEvent(new Event('change', {bubbles: true})); return true; }
                                    }
                                    return false;
                                }""",
                                mkt,
                            )
                            await page.wait_for_timeout(1500)
                            # Seleciona a categoria pelo id ou nome no 2º select
                            picked = await page.evaluate(
                                """(args) => {
                                    const { id, name } = args;
                                    const sels = Array.from(document.querySelectorAll('select'));
                                    // encontra o select com muitas opções (árvore de categorias)
                                    for (let i = sels.length - 1; i >= 0; i--) {
                                        const s = sels[i];
                                        if (s.options.length < 5) continue;
                                        let opt = Array.from(s.options).find(o => String(o.value) === String(id));
                                        if (!opt) opt = Array.from(s.options).find(o => o.textContent.trim() === name);
                                        if (opt) { s.value = opt.value; s.dispatchEvent(new Event('change', {bubbles: true})); return true; }
                                    }
                                    return false;
                                }""",
                                {"id": sug_id, "name": sug_name},
                            )
                            if not picked:
                                await add_log("warning", f"ApplyMap: opção não achada {mkt}/{sug_name}")
                                continue
                            await page.wait_for_timeout(500)
                            # Clica em Adicionar/Vincular (variação no botão)
                            add_btn = await page.query_selector(
                                'button:has-text("Adicionar"), button:has-text("Vincular"), button:has-text("Incluir")'
                            )
                            if add_btn:
                                await add_btn.click()
                                await page.wait_for_timeout(600)
                            # Marca applied no Mongo (idempotente)
                            await db.category_mapping_previews.update_one(
                                {"bling_category_id": cat_id, "marketplace": mkt},
                                {"$set": {"applied": True,
                                          "applied_at": datetime.now(timezone.utc).isoformat()}},
                            )
                            applied_count += 1
                        except Exception as e:
                            logger.warning("apply failed %s/%s: %s", cat_name, mkt, e)

                    # Salva modal
                    save_btn = await page.query_selector(
                        'button:has-text("Salvar"), button:has-text("Concluir")'
                    )
                    if save_btn:
                        await save_btn.click()
                        await page.wait_for_timeout(1500)
                    else:
                        # Se não tem "Salvar", fecha modal
                        close_btn = await page.query_selector(
                            'button:has-text("Fechar"), [aria-label="Close"]'
                        )
                        if close_btn:
                            await close_btn.click()
                            await page.wait_for_timeout(500)
                    await add_log("info", f"ApplyMap: '{cat_name}' processada ({len(cat_previews)} pares)")
                except Exception as e:
                    logger.exception("apply cat %s failed: %s", cat_name, e)
                    await add_log("error", f"ApplyMap '{cat_name}': {e}")
        finally:
            try:
                await browser.close()
            except Exception:
                pass

    await add_log("success", f"ApplyMap: {applied_count} vínculos aplicados")
    return applied_count
