"""JohnDrop Playwright automation bot.

Runs in background, navigates JohnDrop catalog, processes pending products,
applies title cleaning + price lookup, and submits the creation form.

Designed to fail gracefully if Playwright/Chromium is not installed.
"""
import asyncio
import os

# Use shared browser cache if container has one (Emergent provides /pw-browsers)
if os.path.isdir("/pw-browsers") and not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/pw-browsers"

from datetime import datetime, timezone
from typing import Optional, Tuple

from title_cleaner import clean_title
from pricing_service import lookup_price
from robot_service import robot, add_log
from db import db


JOHNDROP_LOGIN_URL = "https://app.jonhdrop.com.br/login"
JOHNDROP_CATALOG_URL = "https://app.jonhdrop.com.br/dashboard/catalog?integration_filter=without_integration"


async def _save_credentials(username: str, password: str):
    await db.settings.update_one(
        {"key": "johndrop_creds"},
        {"$set": {"key": "johndrop_creds", "value": {"username": username, "password": password}}},
        upsert=True,
    )


async def _get_credentials():
    doc = await db.settings.find_one({"key": "johndrop_creds"}, {"_id": 0})
    if not doc:
        return None
    return doc.get("value")


async def _playwright_available() -> bool:
    """Verify both the python package AND the chromium binary are usable."""
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return False
    # Probe: try a quick launch+close. If it fails, fall back to mock.
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            await browser.close()
        return True
    except Exception as e:
        # Log to robot logs so the user sees why
        await add_log("warning", f"Playwright indisponível — modo MOCKED ativo. ({str(e)[:120]})")
        return False


async def run_bot(max_products: int = 10, dry_run: bool = True):
    """Main bot loop. Set robot.state during execution."""
    robot.reset()
    robot.state = "running"
    robot.started_at = datetime.now(timezone.utc)
    await add_log("info", f"Robô iniciado (max={max_products}, dry_run={dry_run})")

    creds = await _get_credentials()
    if not creds:
        robot.state = "error"
        robot.message = "Credenciais JohnDrop não configuradas"
        robot.finished_at = datetime.now(timezone.utc)
        await add_log("error", "Credenciais JohnDrop ausentes. Configure em Configurações.")
        return

    if not await _playwright_available():
        robot.state = "error"
        robot.message = "Playwright/Chromium não instalado no servidor"
        robot.finished_at = datetime.now(timezone.utc)
        await add_log(
            "error",
            "Playwright não está instalado. Modo MOCKED ativo — gerando títulos sem cadastrar."
        )
        await _run_mock(max_products)
        return

    try:
        await _run_playwright(creds["username"], creds["password"], max_products, dry_run)
    except Exception as e:
        await add_log("error", f"Playwright falhou ({str(e)[:160]}) — caindo para modo MOCKED")
        try:
            await _run_mock(max_products)
        except Exception as e2:
            robot.state = "error"
            robot.message = str(e2)
            await add_log("error", f"Erro no fallback MOCKED: {e2}")
    finally:
        robot.finished_at = datetime.now(timezone.utc)
        if robot.state == "running":
            robot.state = "idle"


async def _run_mock(max_products: int):
    """MOCKED execution path when Playwright is unavailable.
    Picks sample raw titles from a static list to demonstrate cleaning + pricing."""
    samples = [
        ("Caneta Touch Screen Stylus Universal Para Tablet e Celular XLS B125 / A-P18", 21.99),
        ("(KA-1369 (4X AAA)) Carregador de Pilhas com LED + 04 Pilhas AAA Recarregáveis Kapbom KA-1369", 29.49),
        ("(KA-S079) Câmera Segurança Babá Eletrônica Wi-fi 360° Kapbom KA-S079", 64.99),
        ("(KA-1100) Adaptador USB Receptor Wireless para Rede WiFi Sem Fio PC Notebook Kapbom KA-1100", 7.49),
        ("(EL-1931) Caneta Peeling Ultrassônico E Ionização Portátil Anti Cravos E Acne Eletromex EL-1931", 21.99),
    ]
    for i, (raw, cost) in enumerate(samples[:max_products]):
        if robot.stop_flag:
            await add_log("warning", "Robô interrompido pelo usuário")
            break
        robot.current_product = raw[:60]
        robot.processed += 1
        result = clean_title(raw)
        price = await lookup_price(cost)
        if not price.found:
            robot.failed += 1
            await add_log("error", f"Preço não encontrado para custo {cost}", raw_title=raw)
            continue
        await add_log(
            "success",
            f"[MOCKED] Pronto para cadastrar: {result['cleaned']} | Preço: {price.sale_price_int}",
            raw_title=raw,
            cleaned_title=result["cleaned"],
            sale_price=price.sale_price_int,
        )
        robot.success += 1
        await asyncio.sleep(0.5)
    robot.state = "idle"


CARD_SELECTOR = (
    'a[href*="/dashboard/product/createv2/"], '
    'a:has-text("Cadastrar Produto"), '
    'button:has-text("Cadastrar Produto")'
)


async def _login_johndrop(page, username: str, password: str) -> bool:
    """Login on JohnDrop. Returns True on success."""
    await add_log("info", "Abrindo página de login JohnDrop...")
    await page.goto(JOHNDROP_LOGIN_URL, wait_until="networkidle", timeout=60000)
    await page.fill('input[type="email"], input[name="email"]', username)
    await page.fill('input[type="password"], input[name="password"]', password)
    async with page.expect_navigation(wait_until="networkidle", timeout=60000):
        await page.click('button[type="submit"]')
    if "login" in page.url.lower():
        await add_log("error", "Falha no login JohnDrop — verifique credenciais")
        return False
    await add_log("success", "Login JohnDrop OK")
    return True


async def _open_catalog(page) -> None:
    """Open catalog page and wait for product cards. Logs debug info if missing."""
    await page.goto(JOHNDROP_CATALOG_URL, wait_until="networkidle", timeout=60000)
    await add_log("info", "Acessando catálogo (não cadastrados)...")
    try:
        await page.wait_for_selector(CARD_SELECTOR, timeout=15000)
    except Exception:
        await add_log("warning", "Selector de produtos não apareceu em 15s — sem produtos pendentes ou layout mudou")
        try:
            snippet = await page.evaluate(
                "() => document.querySelector('.content-page, main, body')?.innerText?.slice(0, 600) || ''"
            )
            if snippet:
                await add_log("info", f"Página: {snippet[:400]}")
        except Exception:
            pass


async def _read_product_fields(page) -> Tuple[str, float]:
    """Read raw title and cost from JohnDrop product creation form."""
    raw_title = ""
    if await page.query_selector('input[name="name"], input#name, input[placeholder*="Nome"]'):
        raw_title = await page.input_value('input[name="name"], input#name, input[placeholder*="Nome"]')
    cost = 0.0
    cost_input = await page.query_selector('input[placeholder*="Custo"], input[name="cost"], input#cost')
    if cost_input:
        cost_text = (await cost_input.input_value() or "0").strip()
        cost = _parse_brl_number(cost_text)
    return raw_title, cost


def _parse_brl_number(text: str) -> float:
    """Parse a price string handling both BR ('21,99' or '1.234,56') and EN ('21.99') formats."""
    if not text:
        return 0.0
    text = text.strip().replace("R$", "").replace(" ", "")
    if "," in text:
        # Brazilian format: 1.234,56 -> 1234.56
        text = text.replace(".", "").replace(",", ".")
    # else: assume already in English/decimal format with '.'
    try:
        return float(text)
    except Exception:
        return 0.0


async def _submit_product(page, cleaned_title: str, sale_price: int, raw_title: str) -> bool:
    """Click 'Criar Produto' and wait for completion. Returns True on success."""
    try:
        await page.click('button:has-text("Criar Produto")')
        await page.wait_for_load_state("networkidle", timeout=60000)
        await add_log(
            "success",
            f"Cadastrado: {cleaned_title}",
            raw_title=raw_title,
            cleaned_title=cleaned_title,
            sale_price=sale_price,
        )
        return True
    except Exception as e:
        await add_log("error", f"Falha ao submeter: {e}", raw_title=raw_title)
        return False


async def _process_one_product(page, dry_run: bool) -> bool:
    """Process the next pending product card. Returns True if a card was found and handled."""
    buttons = await page.query_selector_all(CARD_SELECTOR)
    if not buttons:
        await add_log("info", "Nenhum produto pendente encontrado nesta página")
        return False

    try:
        async with page.expect_navigation(wait_until="networkidle", timeout=60000):
            await buttons[0].click()
    except Exception as e:
        await add_log("error", f"Erro ao abrir produto: {e}")
        robot.failed += 1
        robot.processed += 1
        return True

    raw_title, cost = await _read_product_fields(page)
    robot.current_product = raw_title[:60]
    cleaned = clean_title(raw_title)
    price = await lookup_price(cost)

    if not price.found:
        await add_log("error", f"Preço não encontrado para custo {cost}", raw_title=raw_title)
        robot.failed += 1
        robot.processed += 1
        await page.goto(JOHNDROP_CATALOG_URL, wait_until="networkidle")
        return True

    await page.fill('input[name="name"], input#name', cleaned["cleaned"])
    await page.fill('input[placeholder*="Preço de Venda"], input[name*="price"]', str(price.sale_price_int))

    if dry_run:
        await add_log(
            "info",
            f"[DRY-RUN] Pronto: {cleaned['cleaned']} | Preço: {price.sale_price_int}",
            raw_title=raw_title,
            cleaned_title=cleaned["cleaned"],
            sale_price=price.sale_price_int,
        )
        robot.success += 1
    elif await _submit_product(page, cleaned["cleaned"], price.sale_price_int, raw_title):
        robot.success += 1
    else:
        robot.failed += 1

    robot.processed += 1
    await page.goto(JOHNDROP_CATALOG_URL, wait_until="networkidle")
    await asyncio.sleep(1.5)
    return True


async def _run_playwright(username: str, password: str, max_products: int, dry_run: bool):
    """Playwright automation orchestrator against app.jonhdrop.com.br"""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(viewport={"width": 1366, "height": 900})
        page = await context.new_page()

        try:
            if not await _login_johndrop(page, username, password):
                robot.state = "error"
                robot.message = "Login falhou"
                return

            await _open_catalog(page)

            processed_count = 0
            while processed_count < max_products:
                if robot.stop_flag:
                    await add_log("warning", "Robô interrompido")
                    break
                if not await _process_one_product(page, dry_run):
                    break
                processed_count += 1
        finally:
            await browser.close()
            if robot.state == "running":
                robot.state = "idle"
            await add_log(
                "info",
                f"Robô finalizado. Processados={robot.processed} Sucesso={robot.success} Falhas={robot.failed}",
            )


async def start_bot(max_products: int = 10, dry_run: bool = True):
    if robot.state == "running":
        raise ValueError("Robô já está em execução")
    robot.task = asyncio.create_task(run_bot(max_products=max_products, dry_run=dry_run))


async def stop_bot():
    robot.stop_flag = True
    await add_log("warning", "Sinal de parada enviado ao robô")


# expose helper used from API
async def save_johndrop_credentials(username: str, password: str):
    await _save_credentials(username, password)


async def get_johndrop_credentials():
    return await _get_credentials()
