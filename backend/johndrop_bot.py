"""JohnDrop Playwright automation bot.

Runs in background, navigates JohnDrop catalog, processes pending products,
applies title cleaning + price lookup, and submits the creation form.

Designed to fail gracefully if Playwright/Chromium is not installed.
"""
import asyncio
import os
import re

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
JOHNDROP_CATALOG_BASE_URL = "https://app.jonhdrop.com.br/dashboard/catalog"
JOHNDROP_CATALOG_URL = f"{JOHNDROP_CATALOG_BASE_URL}?integration_filter=without_integration"
INTEGRATION_NAME = "TotyShop-Bling"
SKU_ALLOWED_RE = re.compile(r"[^A-Za-z0-9\-]")
DELAY_BETWEEN_PRODUCTS_S = 15  # pause após cadastrar com sucesso (evita rate-limit do JohnDrop)


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


_EXPECTED_VERSION_CACHE: dict = {"version": None, "checked": False}


def _expected_chromium_version() -> Optional[str]:
    """Run `playwright install chromium --dry-run` and parse the version Playwright expects.
    Uses sys.executable (the SAME python the backend is running under) to avoid version
    mismatch between /usr/local/bin/python and the venv python."""
    if _EXPECTED_VERSION_CACHE["checked"]:
        return _EXPECTED_VERSION_CACHE["version"]
    import subprocess
    import sys
    try:
        r = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium", "--dry-run"],
            env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": "/pw-browsers"},
            capture_output=True, text=True, timeout=20,
        )
        for line in (r.stdout or "").splitlines():
            m = re.search(r"playwright chromium v(\d+)", line)
            if m:
                _EXPECTED_VERSION_CACHE["version"] = m.group(1)
                _EXPECTED_VERSION_CACHE["checked"] = True
                return m.group(1)
    except Exception:
        pass
    _EXPECTED_VERSION_CACHE["checked"] = True
    return None


def _expected_chromium_path() -> Optional[str]:
    """Return the exact headless_shell path the current Playwright version expects."""
    version = _expected_chromium_version()
    if not version:
        return None
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/pw-browsers")
    return os.path.join(base, f"chromium_headless_shell-{version}", "chrome-linux", "headless_shell")


def _find_chromium_binary() -> Optional[str]:
    """Locate a usable chromium binary. Preference order:
       1. The EXACT path the current Playwright version expects (ground truth).
       2. Glob fallback for any installed version (covers edge cases)."""
    expected = _expected_chromium_path()
    if expected and os.path.isfile(expected):
        return expected
    import glob
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/pw-browsers")
    patterns = [
        os.path.join(base, "chromium_headless_shell-*/chrome-linux/headless_shell"),
        os.path.join(base, "chromium-*/chrome-linux/headless_shell"),
        os.path.join(base, "chromium-*/chrome-linux/chrome"),
    ]
    for pat in patterns:
        for m in glob.glob(pat):
            if os.path.isfile(m):
                return m
    return None


async def _ensure_chromium_installed() -> bool:
    """Block until a chromium binary matching the CURRENT Playwright version exists.
    If the installed version mismatches what Playwright expects, reinstalls."""
    import sys
    expected = _expected_chromium_path()
    if expected and os.path.isfile(expected):
        return True
    if expected:
        await add_log(
            "warning",
            f"Chromium ausente em {expected} (Playwright atualizado). Instalando agora (~109 MB)...",
        )
    else:
        await add_log("warning", "Chromium ausente. Instalando agora (~109 MB)...")
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "playwright", "install", "chromium",
            env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": "/pw-browsers"},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.wait(), timeout=300)
    except Exception as e:
        await add_log("error", f"Falha ao instalar Chromium: {e}")
        return False
    # Invalidate cache so we re-read the expected version
    _EXPECTED_VERSION_CACHE["checked"] = False
    _EXPECTED_VERSION_CACHE["version"] = None
    expected = _expected_chromium_path()
    if expected and os.path.isfile(expected):
        await add_log("success", "Chromium instalado com sucesso, retomando...")
        return True
    if _find_chromium_binary():
        await add_log("success", "Chromium detectado (via fallback), retomando...")
        return True
    await add_log("error", "Chromium não encontrado após tentativa de instalação")
    return False


def chromium_status() -> dict:
    """Public helper used by /api/system/chromium-status endpoint."""
    expected = _expected_chromium_path()
    found = _find_chromium_binary()
    return {
        "installed": bool(found) and (expected is None or os.path.isfile(expected)),
        "path": found,
        "expected": expected,
        "matches_expected": bool(expected) and bool(found) and (found == expected),
        "browsers_path": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/pw-browsers"),
    }


async def _playwright_available() -> bool:
    """Verify both the python package AND the chromium binary are usable."""
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return False
    # Ensure chromium is installed (will install if missing — blocks until ready)
    if not await _ensure_chromium_installed():
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
        await add_log("warning", f"Playwright indisponível — modo MOCKED ativo. ({str(e)[:120]})")
        return False


async def _safe_enrich_bling(sku: str, cleaned_title: str, raw_description: str,
                              johndrop_id: Optional[str] = None, cost: Optional[float] = None,
                              images: Optional[list] = None) -> None:
    """Run Bling enrichment in background — never raises, just logs."""
    try:
        import bling_enrichment
        await bling_enrichment.enrich_product_by_sku(
            sku, cleaned_title, raw_description,
            johndrop_id=johndrop_id, cost=cost, images=images,
        )
    except Exception as e:
        await add_log("error", f"Bling enrichment background falhou para {sku}: {e}")


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
    """Open catalog page, apply filter 'Todos que eu não cadastrei' via dropdown + search button.
    Resilient against ERR_ABORTED (page redirects still in flight)."""
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
                await add_log("info", f"Catálogo: nav aborted, retry {attempt + 1}/3")
                await page.wait_for_timeout(2500)
                continue
            raise
    if last_err:
        raise last_err

    await add_log("info", "Acessando Publicar Catálogo...")

    # Try explicit dropdown flow: select "Todos que eu não cadastrei" + click search
    applied_via_ui = False
    try:
        # The integration filter <select> usually has options like
        # "Todos", "Todos que eu cadastrei", "Todos que eu não cadastrei"
        selects = await page.query_selector_all("select")
        for sel in selects:
            options_text = await sel.evaluate(
                "el => Array.from(el.options).map(o => o.textContent).join('||')"
            )
            if options_text and "não cadastrei" in options_text.lower():
                # Select option matching "não cadastrei" (case-insensitive)
                opt_value = await sel.evaluate(
                    "el => { const o = Array.from(el.options).find(x => x.textContent.toLowerCase().includes('não cadastrei')); return o ? o.value : null; }"
                )
                if opt_value is not None:
                    await sel.select_option(value=opt_value)
                    await add_log("info", "Filtro 'Todos que eu não cadastrei' selecionado")
                    applied_via_ui = True
                break
        if applied_via_ui:
            # Click the blue magnifying glass search button (submit form)
            search_btn = await page.query_selector(
                'button[type="submit"], button:has(i.fa-search), button:has(svg.lucide-search)'
            )
            if search_btn:
                await search_btn.click()
                await page.wait_for_load_state("networkidle", timeout=30000)
                await add_log("info", "Botão de busca (lupa) clicado")
    except Exception as e:
        await add_log("warning", f"Falha ao aplicar filtro via UI ({str(e)[:120]}). Usando URL.")

    if not applied_via_ui:
        await page.goto(JOHNDROP_CATALOG_URL, wait_until="networkidle", timeout=60000)

    # Wait for product cards to render
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


async def _select_integration(page) -> bool:
    """Click the TotyShop-Bling integration card on the product create page."""
    try:
        # Strategy 1: click by visible text 'TotyShop-Bling'
        loc = page.locator(f'text="{INTEGRATION_NAME}"').first
        if await loc.count() > 0:
            # Click the parent card (avoid clicking just the label text)
            await loc.click()
            await page.wait_for_timeout(500)
            return True
    except Exception:
        pass

    try:
        # Strategy 2: click any element whose innerText contains the integration name
        clicked = await page.evaluate(
            f"""
            () => {{
                const els = Array.from(document.querySelectorAll('div, label, button, span'));
                const target = els.find(e =>
                    (e.innerText || '').trim() === '{INTEGRATION_NAME}'
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

    await add_log("warning", f"Não encontrei o botão da integração '{INTEGRATION_NAME}'")
    return False


async def _clean_sku_field(page) -> Optional[str]:
    """Read SKU field, strip non-alphanumeric (except hyphen) AND descriptive suffixes.
    Returns cleaned sku."""
    sku_input = await page.query_selector('input[name="sku"], input#sku, input[placeholder*="Sku"], input[placeholder*="SKU"]')
    if not sku_input:
        return None
    current = (await sku_input.input_value() or "").strip()
    # 1. Remove descriptive suffixes BEFORE stripping special chars
    SUFFIX_RE = re.compile(
        r"(com\s*tampa|sem\s*tampa|com\s*tampa\.?|c/?tampa|s/?tampa|"
        r"com\s*alça|sem\s*alça|com\s*nf|sem\s*nf|"
        r"\bml\b|\bun\b|\bunid\b|\bpc?s?\b|\bkit\b)",
        re.IGNORECASE,
    )
    cleaned_text = SUFFIX_RE.sub("", current).strip()
    # 2. Keep only letters, digits, hyphen
    cleaned = SKU_ALLOWED_RE.sub("", cleaned_text)
    # 3. Trim trailing hyphens
    cleaned = cleaned.strip("-")
    if cleaned and cleaned != current:
        await sku_input.fill(cleaned)
        await add_log("info", f"SKU limpo: '{current}' → '{cleaned}'")
    return cleaned or None


async def _fill_sale_price(page, sale_price: int) -> bool:
    """Fill the 'Preço de Venda' field by locating the input next to that label
    and typing the digits via keyboard so currency masks/React handlers fire."""
    value_str = str(sale_price)

    # Find the actual input element by walking the DOM around the "Preço de Venda" text
    handle = await page.evaluate_handle(
        """
        () => {
            const norm = s => (s || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase().trim();
            const target = 'preco de venda';

            // 1. Direct match: input with placeholder containing the text
            const phMatch = Array.from(document.querySelectorAll('input')).find(
                i => norm(i.placeholder).includes(target)
            );
            if (phMatch) return phMatch;

            // 2. Find element whose text is "Preço de Venda" then locate input around it
            const all = Array.from(document.querySelectorAll('label, span, div, td, th, p'));
            const labelEl = all.find(el => {
                const t = norm(el.innerText || el.textContent || '');
                return t === target || (t.includes(target) && t.length < 60);
            });
            if (!labelEl) return null;

            // Look at siblings first, then walk up
            const findInput = (el) => {
                if (!el) return null;
                if (el.tagName === 'INPUT') {
                    const ph = norm(el.placeholder);
                    if (!ph.includes('custo')) return el;
                }
                const ins = el.querySelectorAll
                    ? el.querySelectorAll('input[type="text"], input[type="number"], input:not([type])')
                    : [];
                for (const inp of ins) {
                    const ph = norm(inp.placeholder);
                    const nm = norm(inp.name);
                    if (ph.includes('custo') || nm.includes('cost')) continue;
                    return inp;
                }
                return null;
            };

            // Try next siblings of the label
            let sib = labelEl.nextElementSibling;
            for (let i = 0; i < 4 && sib; i++) {
                const inp = findInput(sib);
                if (inp) return inp;
                sib = sib.nextElementSibling;
            }
            // Walk up
            let parent = labelEl.parentElement;
            for (let i = 0; i < 5 && parent; i++) {
                const inp = findInput(parent);
                if (inp) return inp;
                parent = parent.parentElement;
            }
            return null;
        }
        """
    )
    elem = handle.as_element() if handle else None
    if not elem:
        await add_log("error", "Input 'Preço de Venda' não localizado no DOM")
        return False

    try:
        await elem.scroll_into_view_if_needed()
        await elem.click(click_count=3)  # triple-click to select existing content
        await page.keyboard.press("Backspace")
        # Type digits one by one — triggers currency masks / React onChange
        for ch in value_str:
            await page.keyboard.type(ch, delay=40)
        await page.keyboard.press("Tab")  # blur to apply mask

        # Verify the value was written
        try:
            final_value = await elem.input_value()
        except Exception:
            final_value = ""
        if not final_value:
            await add_log("warning", f"Preço de Venda parece vazio após digitar ({value_str})")
            return False
        await add_log("info", f"Preço de Venda preenchido: '{final_value}' (esperado: {value_str})")
        return True
    except Exception as e:
        await add_log("error", f"Erro ao digitar preço: {e}")
        return False


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


async def _read_product_images(page) -> list:
    """Extract all image URLs from JohnDrop product creation form (IMAGES section)."""
    try:
        urls = await page.evaluate(
            """
            () => {
                const imgs = Array.from(document.querySelectorAll('img'));
                const urls = [];
                for (const img of imgs) {
                    const src = img.src || '';
                    // Heuristic: only product images (skip logos, icons, avatars)
                    if (!src) continue;
                    if (src.includes('logo') || src.includes('avatar') || src.includes('icon')) continue;
                    if (src.startsWith('data:')) continue;
                    // Bling/JohnDrop product images are usually .jpg/.jpeg/.png/.webp
                    if (!/\\.(jpe?g|png|webp)(\\?|$)/i.test(src)) continue;
                    if (img.naturalWidth > 0 && img.naturalWidth < 80) continue;
                    if (!urls.includes(src)) urls.push(src);
                }
                return urls;
            }
            """
        )
        return [u for u in (urls or []) if u][:12]  # cap at 12 images
    except Exception:
        return []


async def _read_product_description(page) -> str:
    """Read the raw product description from JohnDrop (textarea or contenteditable)."""
    try:
        desc = await page.evaluate(
            """
            () => {
                // Try common selectors for the description
                const selectors = [
                    'textarea[name="description"]',
                    'textarea[name="descricao"]',
                    'textarea#description',
                    'textarea[placeholder*="Descrição"]',
                    'textarea[placeholder*="descricao"]',
                    '[contenteditable="true"]',
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el) {
                        const val = el.value || el.innerText || '';
                        if (val.trim().length > 20) return val.trim();
                    }
                }
                // Find the section labelled "DESCRIÇÃO DO PRODUTO" and grab nearby textarea
                const headers = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,label,div'));
                const header = headers.find(h => (h.innerText || '').toUpperCase().includes('DESCRIÇÃO DO PRODUTO') || (h.innerText || '').toUpperCase().includes('DESCRICAO'));
                if (header) {
                    let p = header.parentElement;
                    for (let i = 0; i < 4 && p; i++) {
                        const ta = p.querySelector('textarea, [contenteditable="true"]');
                        if (ta) return (ta.value || ta.innerText || '').trim();
                        p = p.parentElement;
                    }
                }
                return '';
            }
            """
        )
        return desc or ""
    except Exception:
        return ""


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
    """Click the green 'Criar Produto' button and wait for the post-submit redirect to settle."""
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
            await add_log("error", "Botão 'Criar Produto' não encontrado", raw_title=raw_title)
            return False

        # Quick check (3s) for JohnDrop's duplicate-SKU / validation error toast/banner
        # so we can short-circuit instead of waiting 45s for a redirect that won't happen.
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
            await add_log(
                "warning",
                f"SKU já cadastrado/duplicado no JohnDrop — pulando: {dup_text}",
                raw_title=raw_title,
                cleaned_title=cleaned_title,
            )
            # Try to navigate back to the listing so the bot can continue with next item
            try:
                await page.goto(JOHNDROP_CATALOG_URL, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(1500)
            except Exception:
                pass
            return False

        # Wait for the redirect away from /createv2 to settle (avoids ERR_ABORTED on next goto)
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


async def _process_one_product(page, dry_run: bool, seen_skus: set) -> bool:
    """Process the next pending product card. Returns True if a card was found and handled.
    Tracks already-processed cards (by href) so dry-run doesn't revisit same product."""
    buttons = await page.query_selector_all(CARD_SELECTOR)
    if not buttons:
        await add_log("info", "Nenhum produto pendente encontrado nesta página")
        return False

    # Pick the first card whose target URL (createv2/<id>) hasn't been processed yet
    chosen = None
    for btn in buttons:
        try:
            href = await btn.get_attribute("href") or ""
        except Exception:
            href = ""
        if href and href in seen_skus:
            continue
        chosen = btn
        if href:
            seen_skus.add(href)
        break

    if chosen is None:
        await add_log("info", "Não há mais produtos NOVOS nesta página")
        return False

    try:
        async with page.expect_navigation(wait_until="networkidle", timeout=60000):
            await chosen.click()
    except Exception as e:
        await add_log("error", f"Erro ao abrir produto: {e}")
        robot.failed += 1
        robot.processed += 1
        return True

    # 1. Select the TotyShop-Bling integration
    await _select_integration(page)

    # 2. Read raw title + cost
    raw_title, cost = await _read_product_fields(page)
    robot.current_product = raw_title[:60]

    # 2b. Read raw description (used later for Bling enrichment)
    raw_description = await _read_product_description(page)
    raw_images = await _read_product_images(page)

    # 3. Clean SKU (only letters, digits, hyphen)
    sku = await _clean_sku_field(page)
    if sku:
        seen_skus.add(sku)

    # 4. Lookup sale price
    price = await lookup_price(cost)
    if not price.found:
        await add_log("error", f"Preço não encontrado para custo {cost}", raw_title=raw_title)
        robot.failed += 1
        robot.processed += 1
        await _open_catalog(page)
        return True

    # 5. Clean title and fill (use the cleaned SKU as preferred code if present)
    cleaned = clean_title(raw_title, preferred_code=sku)
    await page.fill('input[name="name"], input#name', cleaned["cleaned"])

    # 6. Fill sale price
    price_ok = await _fill_sale_price(page, price.sale_price_int)
    if not price_ok:
        await add_log("error", "Não foi possível preencher o campo 'Preço de Venda'", raw_title=raw_title)
        robot.failed += 1
        robot.processed += 1
        await _open_catalog(page)
        return True

    # 7. Submit (or just log if dry-run)
    submitted_ok = False
    if dry_run:
        await add_log(
            "info",
            f"[DRY-RUN] Pronto: {cleaned['cleaned']} | SKU: {sku} | Preço: {price.sale_price_int}",
            raw_title=raw_title,
            cleaned_title=cleaned["cleaned"],
            sale_price=price.sale_price_int,
        )
        robot.success += 1
        submitted_ok = True
    elif await _submit_product(page, cleaned["cleaned"], price.sale_price_int, raw_title):
        robot.success += 1
        submitted_ok = True
        # Extract JohnDrop product ID from the URL (.../createv2/<id>) BEFORE leaving the page
        johndrop_id = None
        try:
            m = re.search(r"/createv2/(\d+)", page.url or "")
            if m:
                johndrop_id = m.group(1)
        except Exception:
            johndrop_id = None
        # Fire-and-forget Bling enrichment (does NOT block the JohnDrop loop)
        if sku:
            asyncio.create_task(
                _safe_enrich_bling(sku, cleaned["cleaned"], raw_description,
                                   johndrop_id=johndrop_id, cost=cost, images=raw_images)
            )
    else:
        robot.failed += 1

    robot.processed += 1

    # Pause after a real successful cadastro to give JohnDrop time to settle
    if submitted_ok and not dry_run:
        robot.current_product = f"⏸ Aguardando {DELAY_BETWEEN_PRODUCTS_S}s antes do próximo..."
        await add_log("info", f"Pausa de {DELAY_BETWEEN_PRODUCTS_S}s antes do próximo cadastro")
        await asyncio.sleep(DELAY_BETWEEN_PRODUCTS_S)

    # Always re-apply the filter via dropdown + search button so cadastered products
    # are removed from the list. Just navigating via URL is NOT enough.
    await _open_catalog(page)
    await asyncio.sleep(1.0)
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

            seen_skus: set = set()
            processed_count = 0
            while processed_count < max_products:
                if robot.stop_flag:
                    await add_log("warning", "Robô interrompido")
                    break
                if not await _process_one_product(page, dry_run, seen_skus):
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
