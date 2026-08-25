"""Shopee Playwright automation bot (via JohnDrop integration).

This bot runs SEPARATELY from johndrop_bot.py and targets the
"TotyShop-Shopee" integration card on app.jonhdrop.com.br. It reuses
credentials stored for JohnDrop, but uses its own state, logs, and
Shopee-specific category selection.

It is designed to fail gracefully if Playwright/Chromium is not installed
and never modifies johndrop_bot.py behavior.
"""
import asyncio
import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional, Tuple

from title_cleaner import clean_title
from pricing_service import lookup_price
from robot_service import add_log
from db import db
from shopee_category_llm import pick_shopee_category

# Reuse JohnDrop Chromium helpers (these are stateless utility functions).
from johndrop_bot import (
    _ensure_chromium_installed,
    _find_chromium_binary,
    _playwright_available,
    browsers_path,
    pw_env,
    _login_johndrop,
    _open_catalog,
    _clean_sku_field,
    _fill_sale_price,
    _read_product_fields,
    _read_product_images,
    _read_product_description,
    _parse_brl_number,
    JOHNDROP_LOGIN_URL,
    JOHNDROP_CATALOG_URL,
    JOHNDROP_CATALOG_BASE_URL,
)

# Own constants
SHOPEE_INTEGRATION_NAME = "TotyShop-Shopee"
DELAY_BETWEEN_PRODUCTS_S = 25  # same conservative delay as JohnDrop

# Shopee reaproveita produtos JÁ cadastrados no TotyShop-Bling, então o filtro
# do catálogo é o oposto do robô JohnDrop ("que eu cadastrei", não "que eu NÃO
# cadastrei"). O texto é casado de forma tolerante a acentos/maiúsculas.
SHOPEE_CATALOG_FILTER_TEXT = "cadastrei"
SHOPEE_CATALOG_EXCLUDE_TEXT = "não cadastrei"


# ---------------------------------------------------------------------------
# State (separate from JohnDrop robot)
# ---------------------------------------------------------------------------
class ShopeeRobotState:
    def __init__(self):
        self.state = "idle"  # idle | running | paused | error
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


shopee_robot = ShopeeRobotState()


async def _get_credentials():
    """Reuse JohnDrop credentials for now (same app.jonhdrop.com.br login)."""
    from johndrop_bot import _get_credentials as _jd_get_credentials
    return await _jd_get_credentials()


async def _log(level: str, message: str, **extra):
    """Persist a log entry tagged as shopee so the UI can filter it."""
    await add_log(level, message, bot="shopee", **extra)


# ---------------------------------------------------------------------------
# Persistent memory — evita reprocessar o mesmo produto entre execuções
# ---------------------------------------------------------------------------
async def _load_processed_keys() -> set:
    """Chaves (href e SKU) de produtos que a Shopee já processou em runs anteriores."""
    keys: set = set()
    try:
        cur = db.shopee_processed.find({}, {"_id": 0, "key": 1})
        async for doc in cur:
            k = doc.get("key")
            if k:
                keys.add(k)
    except Exception as e:
        await _log("warning", f"Não consegui carregar histórico Shopee: {e}")
    return keys


async def _remember_processed(keys: list, status: str = "done"):
    """Grava as chaves processadas para não repetir na próxima execução."""
    now = datetime.now(timezone.utc).isoformat()
    for key in keys:
        if not key:
            continue
        try:
            await db.shopee_processed.update_one(
                {"key": key},
                {"$set": {"key": key, "status": status, "updated_at": now}},
                upsert=True,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Catalog (Shopee usa filtro "que eu cadastrei", oposto do robô JohnDrop)
# ---------------------------------------------------------------------------
async def _open_catalog_shopee(page) -> None:
    """Abre o catálogo aplicando o filtro dos produtos JÁ cadastrados.

    O robô JohnDrop usa "Todos que eu NÃO cadastrei" para achar produtos novos.
    A Shopee é o contrário: reaproveita os produtos que já foram cadastrados no
    TotyShop-Bling, para manter título, SKU e preço idênticos.
    """
    last_err = None
    for attempt in range(3):
        try:
            await page.goto(JOHNDROP_CATALOG_BASE_URL, wait_until="domcontentloaded", timeout=60000)
            last_err = None
            break
        except Exception as e:
            last_err = e
            msg = str(e)
            if "ERR_ABORTED" in msg or "ERR_TIMED_OUT" in msg:
                await _log("info", f"Catálogo: nav aborted, retry {attempt + 1}/3")
                await page.wait_for_timeout(2500)
                continue
            raise
    if last_err:
        raise last_err

    await _log("info", "Acessando catálogo (produtos já cadastrados)...")

    applied = False
    try:
        selects = await page.query_selector_all("select")
        for sel in selects:
            options_text = await sel.evaluate(
                "el => Array.from(el.options).map(o => o.textContent).join('||')"
            )
            if not options_text or "cadastrei" not in options_text.lower():
                continue
            opt_value = await sel.evaluate(
                """el => {
                    const norm = s => (s || '').toLowerCase();
                    const o = Array.from(el.options).find(x => {
                        const t = norm(x.textContent);
                        return t.includes('cadastrei') && !t.includes('não cadastrei') && !t.includes('nao cadastrei');
                    });
                    return o ? o.value : null;
                }"""
            )
            if opt_value is not None:
                await sel.select_option(value=opt_value)
                await _log("info", "Filtro 'Todos que eu cadastrei' selecionado")
                applied = True
            break

        if applied:
            search_btn = await page.query_selector(
                'button[type="submit"], button:has(i.fa-search), button:has(svg.lucide-search)'
            )
            if search_btn:
                await search_btn.click()
                await page.wait_for_load_state("networkidle", timeout=30000)
                await _log("info", "Botão de busca (lupa) clicado")
    except Exception as e:
        await _log("warning", f"Falha ao aplicar filtro Shopee ({str(e)[:120]}). Usando catálogo padrão.")

    if not applied:
        await _log("warning", "Filtro 'que eu cadastrei' não encontrado — listando catálogo completo")
        try:
            await page.goto(JOHNDROP_CATALOG_BASE_URL, wait_until="networkidle", timeout=60000)
        except Exception:
            pass

    try:
        await page.wait_for_selector(CARD_SELECTOR, timeout=15000)
    except Exception:
        await _log("warning", "Nenhum card de produto apareceu em 15s")


# ---------------------------------------------------------------------------
# Integration selection
# ---------------------------------------------------------------------------
async def _select_shopee_integration(page) -> bool:
    """Click the TotyShop-Shopee integration card on the product create page."""
    try:
        loc = page.locator(f'text="{SHOPEE_INTEGRATION_NAME}"').first
        if await loc.count() > 0:
            await loc.click()
            await page.wait_for_timeout(500)
            return True
    except Exception:
        pass

    try:
        clicked = await page.evaluate(
            f"""
            () => {{
                const els = Array.from(document.querySelectorAll('div, label, button, span'));
                const target = els.find(e =>
                    (e.innerText || '').trim() === '{SHOPEE_INTEGRATION_NAME}'
                );
                if (target) {{
                    (target.closest('label') || target).click();
                    return true;
                }}
                return false;
            }}
            """
        )
        if clicked:
            await page.wait_for_timeout(500)
            return True
    except Exception:
        pass

    await _log("warning", f"Não encontrei o botão da integração '{SHOPEE_INTEGRATION_NAME}'")
    return False


# ---------------------------------------------------------------------------
# Category selection (Shopee-specific)
# ---------------------------------------------------------------------------
async def _select_shopee_category(page, raw_title: str) -> bool:
    """Select a Shopee-compatible category from the dropdown.

    Strategy:
    1. Try to open a category dropdown/select already rendered on the page.
    2. Use an LLM/keyword heuristic to pick the most generic matching option,
       preferring broad subcategories (e.g. "Acessórios" > "Celulares" > "Outros").
    3. If no AI is available, fall back to a keyword map of common product groups.

    Returns True if a category was selected.
    """
    # Collect candidate option texts from any visible category-related dropdown
    candidates = []
    try:
        selects = await page.query_selector_all("select")
        for sel in selects:
            label = await sel.evaluate(
                """el => {
                    const id = el.id || el.name || '';
                    const lbl = document.querySelector(`label[for="${id}"]`);
                    return (lbl?.innerText || el.getAttribute('placeholder') || id || '').toLowerCase();
                }"""
            )
            if label and ("categoria" in label or "shopee" in label or "marketplace" in label):
                opts = await sel.evaluate(
                    "el => Array.from(el.options).map(o => ({ value: o.value, text: o.textContent.trim() }))"
                )
                candidates.append({"element": sel, "options": opts})
    except Exception as e:
        await _log("warning", f"Erro ao listar categorias: {e}")

    if not candidates:
        # Some UIs use custom dropdowns (divs). Try a generic click + list scan.
        try:
            clicked = await page.evaluate(
                """() => {
                    const labels = Array.from(document.querySelectorAll('label, div, span'));
                    const el = labels.find(x => (x.innerText || '').toLowerCase().includes('categoria'));
                    if (el) { el.click(); return true; }
                    return false;
                }"""
            )
            if clicked:
                await page.wait_for_timeout(800)
                # Read whatever list items appeared
                items = await page.evaluate(
                    """() => Array.from(document.querySelectorAll('li, .dropdown-item, [role="option"]'))
                              .map(x => x.innerText.trim()).filter(Boolean)"""
                )
                if items:
                    candidates.append({"element": None, "options": [{"value": i, "text": t} for i, t in enumerate(items)]})
        except Exception:
            pass

    if not candidates:
        await _log("warning", "Campo/navegação de categoria da Shopee não encontrada")
        return False

    # Use the first category control found (UI usually has one main category path)
    control = candidates[0]
    options = control["options"]
    if len(options) <= 1:
        await _log("warning", "Dropdown de categoria está vazio ou só tem placeholder")
        return False

    # Try LLM first; fall back to local heuristic
    best = await pick_shopee_category(raw_title, options)
    if best:
        await _log("info", f"Categoria sugerida pela IA: {best['text']}", category=best["text"])
    else:
        best = _pick_category(raw_title, options)
        if best:
            await _log("info", f"Categoria sugerida localmente: {best['text']}", category=best["text"])
    if not best:
        await _log("warning", f"Não consegui sugerir categoria para: {raw_title}")
        return False

    try:
        if control["element"]:
            await control["element"].select_option(value=best["value"])
        else:
            # Custom dropdown: click the item whose text matches
            txt = best["text"].replace('"', '\\"')
            await page.click(f'li:has-text("{txt}")')
        await page.wait_for_timeout(600)
        await _log("info", f"Categoria Shopee selecionada: {best['text']}", category=best["text"])
        return True
    except Exception as e:
        await _log("warning", f"Falha ao selecionar categoria '{best.get('text')}': {e}")
        return False


def _pick_category(raw_title: str, options: list) -> Optional[dict]:
    """Pick the best Shopee category option from a dropdown list.

    Prefers generic/broad subcategories to avoid filling many specific attributes.
    """
    title_lower = raw_title.lower()

    # Product group keywords -> preferred category keyword (broad)
    keyword_map = [
        (("celular", "smartphone", "phone"), "celulares"),
        (("fone", "headphone", "earphone", "auricular", "bluetooth"), "fones"),
        (("cabo", "carregador", "fonte", "adaptador", "usb"), "acessórios"),
        (("suporte", "garra", "aranha"), "suportes"),
        (("relogio", "smartwatch", "pulseira"), "relógios"),
        (("capa", "case", "pelicula"), "acessórios"),
        (("camera", "cameras", "seguranca"), "câmeras"),
        (("caneta", "stylus", "touch"), "acessórios"),
        (("pilha", "bateria", "carregador de pilha"), "pilhas"),
    ]

    target_keyword = None
    for words, cat in keyword_map:
        if any(w in title_lower for w in words):
            target_keyword = cat
            break

    # Score options: exact broad match gets highest score; generic subcategories
    # like "Outros" / "Acessórios" get bonus.
    def score(opt: dict) -> int:
        text = opt.get("text", "").lower()
        s = 0
        if target_keyword and target_keyword in text:
            s += 10
        # Generic fallback preferences
        if "outros" in text or "outras" in text:
            s += 5
        if "acessórios" in text:
            s += 4
        # Penalize very specific leaf categories (long text with > 2 separators)
        depth = text.count(">") + text.count("-") + text.count("/")
        if depth > 2:
            s -= 3
        # Prefer options that are not placeholder
        if any(p in text for p in ("selecione", "escolha", "placeholder")):
            s -= 100
        return s

    # Skip first option if it looks like a placeholder
    scored = [(score(o), o) for o in options if o.get("text", "").strip()]
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored and scored[0][0] > -50:
        return scored[0][1]
    return None


# ---------------------------------------------------------------------------
# Product submission (reuse JohnDrop submit but log under shopee)
# ---------------------------------------------------------------------------
async def _submit_shopee_product(page, cleaned_title: str, sale_price: int, raw_title: str) -> bool:
    """Click the green 'Criar Produto' button and detect duplicate SKU / validation errors."""
    try:
        clicked = False
        for sel in [
            'button:has-text("Criar Produto")',
            'button.btn-success:has-text("Criar")',
            'button[type="submit"]:has-text("Criar")',
        ]:
            btn = await page.query_selector(sel)
            if btn:
                await btn.scroll_into_view_if_needed()
                await btn.click()
                clicked = True
                break
        if not clicked:
            await _log("error", "Botão 'Criar Produto' não encontrado", raw_title=raw_title)
            return False

        # Short-circuit duplicate SKU / validation error
        await page.wait_for_timeout(2500)
        try:
            dup_text = await page.evaluate(
                """() => {
                    const sels = ['.toast', '.alert-danger', '.swal2-popup', '.notyf__message',
                                  '[role="alert"]', '.error-message', '.invalid-feedback'];
                    for (const s of sels) {
                        const els = document.querySelectorAll(s);
                        for (const el of els) {
                            const t = (el.innerText || '').trim();
                            if (!t) continue;
                            if (/sku|c[óo]digo|j[áa]\\s+(cadastrad|exist|registrad)|duplicad|conflito/i.test(t)) {
                                return t.slice(0, 240);
                            }
                        }
                    }
                    return null;
                }"""
            )
        except Exception:
            dup_text = None

        if dup_text:
            await _log(
                "warning",
                f"SKU já cadastrado/duplicado — pulando: {dup_text}",
                raw_title=raw_title,
                cleaned_title=cleaned_title,
            )
            try:
                await page.goto(JOHNDROP_CATALOG_BASE_URL, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(1500)
            except Exception:
                pass
            return False

        # Wait redirect away from /createv2
        try:
            await page.wait_for_function(
                "() => !window.location.pathname.includes('createv2')",
                timeout=45000,
            )
        except Exception:
            pass
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass
        await page.wait_for_timeout(1500)

        await _log(
            "success",
            f"Cadastrado na Shopee: {cleaned_title}",
            raw_title=raw_title,
            cleaned_title=cleaned_title,
            sale_price=sale_price,
        )
        return True
    except Exception as e:
        await _log("error", f"Falha ao submeter na Shopee: {e}", raw_title=raw_title)
        return False


# ---------------------------------------------------------------------------
# Process one product
# ---------------------------------------------------------------------------
CARD_SELECTOR = (
    'a[href*="/dashboard/product/createv2/"], '
    'a:has-text("Cadastrar Produto"), '
    'button:has-text("Cadastrar Produto")'
)


async def _process_one_shopee_product(page, dry_run: bool, seen_skus: set) -> bool:
    """Process the next pending product card for Shopee."""
    buttons = await page.query_selector_all(CARD_SELECTOR)
    if not buttons:
        await _log("info", "Nenhum produto pendente encontrado nesta página")
        return False

    chosen = None
    chosen_href = ""
    for btn in buttons:
        try:
            href = await btn.get_attribute("href") or ""
        except Exception:
            href = ""
        if href and href in seen_skus:
            continue
        chosen = btn
        chosen_href = href
        if href:
            seen_skus.add(href)
        break

    if chosen is None:
        await _log("info", "Não há mais produtos NOVOS nesta página")
        return False

    try:
        async with page.expect_navigation(wait_until="networkidle", timeout=60000):
            await chosen.click()
    except Exception as e:
        await _log("error", f"Erro ao abrir produto: {e}")
        shopee_robot.failed += 1
        shopee_robot.processed += 1
        return True

    # 1. Select Shopee integration
    await _select_shopee_integration(page)

    # 2. Read title / cost / description / images
    raw_title, cost = await _read_product_fields(page)
    shopee_robot.current_product = raw_title[:60]
    raw_description = await _read_product_description(page)
    raw_images = await _read_product_images(page)

    # 3. Clean SKU
    sku = await _clean_sku_field(page)
    if sku:
        seen_skus.add(sku)

    # 4. Lookup price
    price = await lookup_price(cost)
    if not price.found:
        await _log("error", f"Preço não encontrado para custo {cost}", raw_title=raw_title)
        shopee_robot.failed += 1
        shopee_robot.processed += 1
        await _open_catalog_shopee(page)
        return True

    # 5. Clean title and fill name
    cleaned = clean_title(raw_title, preferred_code=sku)
    await page.fill('input[name="name"], input#name', cleaned["cleaned"])

    # 6. Fill sale price
    price_ok = await _fill_sale_price(page, price.sale_price_int)
    if not price_ok:
        await _log("error", "Não foi possível preencher o campo 'Preço de Venda'", raw_title=raw_title)
        shopee_robot.failed += 1
        shopee_robot.processed += 1
        await _open_catalog_shopee(page)
        return True

    # 7. Shopee-specific: select category
    category_ok = await _select_shopee_category(page, raw_title)
    if not category_ok:
        await _log(
            "warning",
            "Categoria Shopee não selecionada — tentando cadastrar mesmo assim",
            raw_title=raw_title,
        )

    # 8. Submit (or dry-run)
    submitted_ok = False
    if dry_run:
        await _log(
            "info",
            f"[DRY-RUN] Pronto: {cleaned['cleaned']} | SKU: {sku} | Preço: {price.sale_price_int}",
            raw_title=raw_title,
            cleaned_title=cleaned["cleaned"],
            sale_price=price.sale_price_int,
        )
        shopee_robot.success += 1
        submitted_ok = True
    elif await _submit_shopee_product(page, cleaned["cleaned"], price.sale_price_int, raw_title):
        shopee_robot.success += 1
        submitted_ok = True
        johndrop_id = None
        try:
            m = re.search(r"/createv2/(\d+)", page.url or "")
            if m:
                johndrop_id = m.group(1)
        except Exception:
            johndrop_id = None
        if sku:
            try:
                await db.enrich_pending.update_one(
                    {"sku": sku},
                    {"$set": {
                        "sku": sku,
                        "raw_title": cleaned["cleaned"],
                        "raw_description": raw_description,
                        "johndrop_id": johndrop_id,
                        "cost": cost,
                        "images": raw_images,
                        "status": "pending",
                        "queued_at": datetime.now(timezone.utc).isoformat(),
                        "attempts": 0,
                    }},
                    upsert=True,
                )
                await _log(
                    "info",
                    f"Produto {sku} cadastrado na Shopee. Aguardando sync→Bling. Worker vai enriquecer automaticamente.",
                )
            except Exception as e:
                await _log("warning", f"Falhou ao agendar enriquecimento de {sku}: {e}")
    else:
        shopee_robot.failed += 1

    shopee_robot.processed += 1

    # Memória persistente: marca href e SKU para não repetir nas próximas execuções
    if submitted_ok and not dry_run:
        await _remember_processed([chosen_href, sku])

    if submitted_ok and not dry_run:
        shopee_robot.current_product = f"⏸ Aguardando {DELAY_BETWEEN_PRODUCTS_S}s antes do próximo..."
        await _log("info", f"Pausa de {DELAY_BETWEEN_PRODUCTS_S}s antes do próximo cadastro")
        await asyncio.sleep(DELAY_BETWEEN_PRODUCTS_S)

    await _open_catalog_shopee(page)
    await asyncio.sleep(1.0)
    return True


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
async def run_bot(max_products: int = 10, dry_run: bool = True):
    """Main Shopee bot loop."""
    shopee_robot.reset()
    shopee_robot.state = "running"
    shopee_robot.started_at = datetime.now(timezone.utc)
    await _log("info", f"Robô Shopee iniciado (max={max_products}, dry_run={dry_run})")

    creds = await _get_credentials()
    if not creds:
        shopee_robot.state = "error"
        shopee_robot.message = "Credenciais JohnDrop não configuradas"
        shopee_robot.finished_at = datetime.now(timezone.utc)
        await _log("error", "Credenciais ausentes. Configure em Configurações.")
        return

    if not await _playwright_available():
        shopee_robot.state = "error"
        shopee_robot.message = "Playwright/Chromium não instalado no servidor"
        shopee_robot.finished_at = datetime.now(timezone.utc)
        await _log("error", "Playwright não está instalado.")
        return

    try:
        await _run_playwright(creds["username"], creds["password"], max_products, dry_run)
    except Exception as e:
        await _log("error", f"Playwright falhou ({str(e)[:160]})")
        shopee_robot.state = "error"
    finally:
        shopee_robot.finished_at = datetime.now(timezone.utc)
        if shopee_robot.state == "running":
            shopee_robot.state = "idle"


async def _run_playwright(username: str, password: str, max_products: int, dry_run: bool):
    """Playwright orchestrator for Shopee."""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(viewport={"width": 1366, "height": 900})
        page = await context.new_page()

        try:
            if not await _login_johndrop(page, username, password):
                shopee_robot.state = "error"
                shopee_robot.message = "Login falhou"
                return

            await _open_catalog_shopee(page)

            # Carrega histórico para não reprocessar produtos de execuções anteriores
            seen_skus: set = await _load_processed_keys()
            if seen_skus:
                await _log("info", f"{len(seen_skus)} produtos já processados anteriormente serão pulados")
            processed_count = 0
            while processed_count < max_products:
                if shopee_robot.stop_flag:
                    await _log("warning", "Robô Shopee interrompido")
                    break
                if not await _process_one_shopee_product(page, dry_run, seen_skus):
                    break
                processed_count += 1
        finally:
            await browser.close()
            if shopee_robot.state == "running":
                shopee_robot.state = "idle"
            await _log(
                "info",
                f"Robô Shopee finalizado. Processados={shopee_robot.processed} Sucesso={shopee_robot.success} Falhas={shopee_robot.failed}",
            )


async def start_bot(max_products: int = 10, dry_run: bool = True):
    if shopee_robot.state == "running":
        raise ValueError("Robô Shopee já está em execução")
    shopee_robot.task = asyncio.create_task(run_bot(max_products=max_products, dry_run=dry_run))


async def stop_bot():
    shopee_robot.stop_flag = True
    await _log("warning", "Sinal de parada enviado ao robô Shopee")


async def get_shopee_credentials_status():
    creds = await _get_credentials()
    if not creds:
        return {"configured": False}
    return {"configured": True, "username": creds.get("username")}
