"""Dropper Playwright automation bot.

Mirrors the JohnDrop bot (johndrop_bot.py) but targets
https://dropper.dropping.com.br/ — login, catalog, product creation form and
submission. After a successful cadastro it schedules the Bling enrichment
(enrich_pending) so the same enrichment worker used by JohnDrop handles the
Dropper products too (same pipeline until Bling).

Designed to fail gracefully if Playwright/Chromium is not installed.
"""
import asyncio
import os
import re
import sys

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
from robot_service import robot, add_log
from db import db


# Dropper URLs — ajustar se o layout do site mudar.
DROPPER_LOGIN_URL = "https://dropper.dropping.com.br/login"
DROPPER_CATALOG_BASE_URL = "https://dropper.dropping.com.br/dashboard/catalog"
DROPPER_CATALOG_URL = f"{DROPPER_CATALOG_BASE_URL}?integration_filter=without_integration"
INTEGRATION_NAME = "TotyShop-Bling"
SKU_ALLOWED_RE = re.compile(r"[^A-Za-z0-9\\-]")
DELAY_BETWEEN_PRODUCTS_S = 25  # pausa após cadastrar com sucesso


async def _save_credentials(username: str, password: str):
    value = {"username": username, "password": password}
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
    doc = await db.settings.find_one({"key": "dropper_creds"}, {"_id": 0})
    value = (doc or {}).get("value") if doc else None
    if value and value.get("username"):
        return value
    try:
        import secrets_store
        stored = secrets_store.read("dropper")
        if stored and stored.get("username"):
            return stored
    except Exception:
        pass
    return None


def browsers_path() -> str:
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override:
        return override
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\\AppData\\Local")
        return os.path.join(local, "ms-playwright")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Caches/ms-playwright")
    return os.path.expanduser("~/.cache/ms-playwright")


def pw_env() -> dict:
    return dict(os.environ)


def _chromium_relative_paths() -> list:
    if os.name == "nt":
        return [
            os.path.join("chrome-headless-shell-win64", "chrome-headless-shell.exe"),
            os.path.join("chrome-win64", "chrome.exe"),
            os.path.join("chrome-win", "chrome.exe"),
        ]
    if sys.platform == "darwin":
        return [
            os.path.join("chrome-mac", "headless_shell"),
            os.path.join("chrome-mac", "Chromium.app", "Contents", "MacOS", "Chromium"),
        ]
    return [
        os.path.join("chrome-linux", "headless_shell"),
        os.path.join("chrome-linux", "chrome"),
    ]


_EXPECTED_VERSION_CACHE: dict = {"version": None, "checked": False}


def _expected_chromium_version() -> Optional[str]:
    if _EXPECTED_VERSION_CACHE["checked"]:
        return _EXPECTED_VERSION_CACHE["version"]
    import subprocess
    try:
        r = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium", "--dry-run"],
            env=pw_env(),
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
    version = _expected_chromium_version()
    if not version:
        return None
    base = browsers_path()
    relatives = _chromium_relative_paths()
    for folder in (f"chromium_headless_shell-{version}", f"chromium-{version}"):
        for rel in relatives:
            candidate = os.path.join(base, folder, rel)
            if os.path.isfile(candidate):
                return candidate
    return os.path.join(base, f"chromium_headless_shell-{version}", relatives[0])


def _find_chromium_binary() -> Optional[str]:
    expected = _expected_chromium_path()
    if expected and os.path.isfile(expected):
        return expected
    import glob
    base = browsers_path()
    for folder in ("chromium_headless_shell-*", "chromium-*"):
        for rel in _chromium_relative_paths():
            for m in sorted(glob.glob(os.path.join(base, folder, rel)), reverse=True):
                if os.path.isfile(m):
                    return m
    return None


async def _ensure_chromium_installed() -> bool:
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
            env=pw_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.wait(), timeout=300)
    except Exception as e:
        await add_log("error", f"Falha ao instalar Chromium: {e}")
        return False
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


async def _playwright_available() -> bool:
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return False
    if not await _ensure_chromium_installed():
        return False
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
        msg = str(e)
        await add_log("error", f"Bling enrichment background falhou para {sku}: {msg}")
        if "Budget has been exceeded" in msg or "budget" in msg.lower():
            robot.message = (
                "Saldo da IA esgotado. Recarregue sua chave e use 'Enriquecer em Lote' "
                "para reprocessar."
            )
            robot.llm_budget_exhausted = True


async def run_bot(max_products: int = 10, dry_run: bool = True):
    """Main bot loop. Set robot.state during execution."""
    robot.reset()
    robot.state = "running"
    robot.started_at = datetime.now(timezone.utc)
    await add_log("info", f"Robô Dropper iniciado (max={max_products}, dry_run={dry_run})")

    creds = await _get_credentials()
    if not creds:
        robot.state = "error"
        robot.message = "Credenciais Dropper não configuradas"
        robot.finished_at = datetime.now(timezone.utc)
        await add_log("error", "Credenciais Dropper ausentes. Configure em Configurações.")
        return

    if not await _playwright_available():
        robot.state = "error"
        robot.message = "Playwright/Chromium não instalado no servidor"
        robot.finished_at = datetime.now(timezone.utc)
        await add_log(
            "error",
            "Playwright não está instalado. Modo MOCKED ativo — gerando títulos sem cadastrar.",
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
    """MOCKED execution path when Playwright is unavailable."""
    samples = [
        ("Caneta Touch Screen Stylus Universal Para Tablet e Celular XLS B125 / A-P18", 21.99),
        ("(KA-1369 (4X AAA)) Carregador de Pilhas com LED + 04 Pilhas AAA Recarregáveis Kapbom KA-1369", 29.49),
        ("(KA-S079) Câmera Segurança Babá Eletrônica Wi-fi 360° Kapbom KA-S079", 64.99),
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


async def _login_dropper(page, username: str, password: str) -> bool:
    """Login on Dropper. Returns True on success."""
    await add_log("info", "Abrindo página de login Dropper...")
    await page.goto(DROPPER_LOGIN_URL, wait_until="networkidle", timeout=60000)
    await page.fill('input[type="email"], input[name="email"]', username)
    await page.fill('input[type="password"], input[name="password"]', password)
    async with page.expect_navigation(wait_until="networkidle", timeout=60000):
        await page.click('button[type="submit"]')
    if "login" in page.url.lower():
        await add_log("error", "Falha no login Dropper — verifique credenciais")
        return False
    await add_log("success", "Login Dropper OK")
    return True


async def _open_catalog(page) -> None:
    """Open catalog page, apply filter 'Todos que eu não cadastrei'."""
    last_err = None
    for attempt in range(3):
        try:
            await page.goto(DROPPER_CATALOG_BASE_URL, wait_until="domcontentloaded", timeout=60000)
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

    await add_log("info", "Acessando catálogo Dropper...")

    applied_via_ui = False
    try:
        selects = await page.query_selector_all("select")
        for sel in selects:
            options_text = await sel.evaluate(
                "el => Array.from(el.options).map(o => o.textContent).join('||')"
            )
            if options_text and ("não cadastrei" in options_text.lower() or "sem integração" in options_text.lower()):
                opt_value = await sel.evaluate(
                    "el => { const o = Array.from(el.options).find(x => { const t = (x.textContent || '').toLowerCase(); return t.includes('não cadastrei') || t.includes('sem integração'); }); return o ? o.value : null; }"
                )
                if opt_value is not None:
                    await sel.select_option(value=opt_value)
                    await add_log("info", "Filtro 'não cadastrei / sem integração' selecionado")
                    applied_via_ui = True
                break
        if applied_via_ui:
            search_btn = await page.query_selector(
                'button[type="submit"], button:has(i.fa-search), button:has(svg.lucide-search)'
            )
            if search_btn:
                await search_btn.click()
                await page.wait_for_load_state("networkidle", timeout=30000)
                await add_log("info", "Botão de busca clicado")
    except Exception as e:
        await add_log("warning", f"Falha ao aplicar filtro via UI ({str(e)[:120]}). Usando URL.")

    if not applied_via_ui:
        await page.goto(DROPPER_CATALOG_URL, wait_until="networkidle", timeout=60000)

    try:
        await page.wait_for_selector(CARD_SELECTOR, timeout=15000)
    except Exception:
        await add_log("warning", "Selector de produtos não apareceu em 15s — sem produtos pendentes ou layout mudou")


async def _select_integration(page) -> bool:
    """Click the TotyShop-Bling integration card on the product create page."""
    try:
        loc = page.locator(f'text="{INTEGRATION_NAME}"').first
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
    """Read SKU field, strip non-alphanumeric (except hyphen) AND descriptive suffixes."""
    sku_input = await page.query_selector('input[name="sku"], input#sku, input[placeholder*="Sku"], input[placeholder*="SKU"]')
    if not sku_input:
        return None
    current = (await sku_input.input_value() or "").strip()
    SUFFIX_RE = re.compile(
        r"(com\s*tampa|sem\s*tampa|com\s*tampa\.?|c/?tampa|s/?tampa|"
        r"com\s*alça|sem\s*alça|com\s*nf|sem\s*nf|"
        r"\bml\b|\bun\b|\bunid\b|\bpcs?\b|\bkit\b)",
        re.IGNORECASE,
    )
    cleaned_text = SUFFIX_RE.sub("", current).strip()
    cleaned = SKU_ALLOWED_RE.sub("", cleaned_text)
    cleaned = cleaned.strip("-")
    if cleaned and cleaned != current:
        await sku_input.fill(cleaned)
        await add_log("info", f"SKU limpo: '{current}' → '{cleaned}'")
    return cleaned or None


async def _fill_sale_price(page, sale_price: int) -> bool:
    """Fill the 'Preço de Venda' field."""
    value_str = str(sale_price)
    handle = await page.evaluate_handle(
        """
        () => {
            const norm = s => (s || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase().trim();
            const target = 'preco de venda';
            const isVisible = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) return false;
                const st = window.getComputedStyle(el);
                if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') return false;
                return true;
            };
            const candidates = [];
            for (const inp of document.querySelectorAll('input')) {
                if (norm(inp.placeholder).includes(target)) candidates.push(inp);
            }
            const labels = Array.from(document.querySelectorAll('label, span, div, td, th, p'))
                .filter(el => {
                    const t = norm(el.innerText || el.textContent || '');
                    return t === target || (t.includes(target) && t.length < 60);
                });
            const findInputNear = (el) => {
                const found = [];
                let sib = el.nextElementSibling;
                for (let i = 0; i < 4 && sib; i++) {
                    for (const inp of sib.querySelectorAll('input[type="text"], input[type="number"], input:not([type])')) {
                        const ph = norm(inp.placeholder), nm = norm(inp.name);
                        if (!ph.includes('custo') && !nm.includes('cost')) found.push(inp);
                    }
                    if (sib.tagName === 'INPUT') {
                        const ph = norm(sib.placeholder);
                        if (!ph.includes('custo')) found.push(sib);
                    }
                    sib = sib.nextElementSibling;
                }
                let parent = el.parentElement;
                for (let i = 0; i < 5 && parent; i++) {
                    for (const inp of parent.querySelectorAll('input[type="text"], input[type="number"], input:not([type])')) {
                        const ph = norm(inp.placeholder), nm = norm(inp.name);
                        if (!ph.includes('custo') && !nm.includes('cost')) found.push(inp);
                    }
                    parent = parent.parentElement;
                }
                return found;
            };
            for (const lbl of labels) candidates.push(...findInputNear(lbl));
            const seen = new Set();
            const unique = candidates.filter(c => !seen.has(c) && seen.add(c));
            const visible = unique.find(isVisible);
            return visible || unique[0] || null;
        }
        """
    )
    elem = handle.as_element() if handle else None
    if not elem:
        await add_log("error", "Input 'Preço de Venda' não localizado no DOM")
        return False
    try:
        is_visible = False
        try:
            is_visible = await elem.is_visible()
        except Exception:
            is_visible = False
        if not is_visible:
            await add_log("warning", "Campo 'Preço de Venda' está oculto — tentando expandir/scroll via JS")
            await page.evaluate(
                """el => {
                    el.scrollIntoView({block: 'center'});
                    let cur = el;
                    for (let i = 0; i < 6 && cur; i++) {
                        const s = window.getComputedStyle(cur);
                        if (s.display === 'none') cur.style.display = 'block';
                        if (s.visibility === 'hidden') cur.style.visibility = 'visible';
                        cur = cur.parentElement;
                    }
                    el.focus();
                }""",
                elem,
            )
            await page.wait_for_timeout(400)
        try:
            await elem.scroll_into_view_if_needed(timeout=5000)
        except Exception:
            pass
        try:
            await elem.click(click_count=3, timeout=5000)
        except Exception:
            await elem.focus()
        await page.keyboard.press("Backspace")
        for ch in value_str:
            await page.keyboard.type(ch, delay=40)
        await page.keyboard.press("Tab")
        try:
            final_value = await elem.input_value()
        except Exception:
            final_value = ""
        if not final_value:
            try:
                await page.evaluate(
                    """([el, v]) => {
                        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                        setter.call(el, v);
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        el.dispatchEvent(new Event('blur', {bubbles: true}));
                    }""",
                    [elem, value_str],
                )
                await page.wait_for_timeout(300)
                final_value = await elem.input_value()
            except Exception:
                pass
        if not final_value:
            await add_log("warning", f"Preço de Venda parece vazio após digitar ({value_str})")
            return False
        await add_log("info", f"Preço de Venda preenchido: '{final_value}' (esperado: {value_str})")
        return True
    except Exception as e:
        await add_log("error", f"Erro ao digitar preço: {e}")
        return False


async def _read_product_fields(page) -> Tuple[str, float]:
    """Read raw title and cost from Dropper product creation form."""
    raw_title = ""
    if await page.query_selector('input[name="name"], input#name, input[placeholder*="Nome"]'):
        raw_title = await page.input_value('input[name="name"], input#name, input[placeholder*="Nome"]')
    cost = 0.0
    cost_input = await page.query_selector('input[placeholder*="Custo"], input[name="cost"], input#cost')
    if cost_input:
        cost_text = (await cost_input.input_value() or "0").strip()
        cost = _parse_brl_number(cost_text)
    return raw_title, cost


async def _read_product_images(page, quiet: bool = False) -> list:
    """Extract all image URLs from Dropper product creation form."""
    try:
        try:
            await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight/2)")
            await page.wait_for_timeout(800)
            await page.evaluate("() => window.scrollTo(0, 0)")
            await page.wait_for_timeout(500)
        except Exception:
            pass

        urls = await page.evaluate(
            """
            () => {
                const out = new Set();
                const isProductLike = (src) => {
                    if (!src) return false;
                    if (src.startsWith('data:') || src.startsWith('blob:')) return false;
                    const low = src.toLowerCase();
                    if (low.includes('/logo') || low.includes('/icon') || low.includes('favicon')) return false;
                    if (low.includes('/avatar') || low.includes('/profile')) return false;
                    if (low.includes('placeholder') || low.includes('default')) return false;
                    if (low.endsWith('.svg')) return false;
                    return true;
                };
                for (const img of document.querySelectorAll('img')) {
                    const candidates = [
                        img.currentSrc, img.src,
                        img.getAttribute('data-src'),
                        img.getAttribute('data-original'),
                        img.getAttribute('data-lazy'),
                        img.getAttribute('data-image'),
                    ];
                    for (const s of candidates) {
                        if (s && isProductLike(s)) {
                            if (img.naturalWidth > 0 && img.naturalWidth < 80) break;
                            out.add(s);
                            break;
                        }
                    }
                }
                const containers = document.querySelectorAll(
                    '.image, .img, .photo, .picture, .thumbnail, .gallery img, .carousel img, ' +
                    '[class*="image"], [class*="img"], [class*="photo"], [class*="gallery"]'
                );
                for (const el of containers) {
                    const bg = window.getComputedStyle(el).backgroundImage;
                    const m = bg.match(/url\\(["']?(.+?)["']?\\)/);
                    if (m && isProductLike(m[1])) out.add(m[1]);
                }
                return Array.from(out);
            }
            """
        )
        result = [u for u in (urls or []) if u]
        img_exts = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")

        def _is_real_image(u: str) -> bool:
            low = u.lower()
            if "/dashboard/" in low or "/product/" in low:
                return False
            if "user.png" in low or "avatar" in low or "/logo" in low:
                return False
            base = low.split("?")[0].split("#")[0]
            return any(base.endswith(ext) for ext in img_exts)

        cleaned = [u for u in result if _is_real_image(u)]
        seen = set()
        final: list = []
        for u in cleaned:
            if u not in seen:
                seen.add(u)
                final.append(u)
        final = final[:15]
        if not quiet:
            await add_log(
                "info",
                f"Imagens extraídas do Dropper: {len(final)} (brutas={len(result)}, após limpeza={len(cleaned)})",
            )
        return final
    except Exception as e:
        try:
            await add_log("warning", f"Falha ao ler imagens: {e}")
        except Exception:
            pass
        return []


async def _read_product_description(page) -> str:
    """Read the raw product description from Dropper."""
    try:
        desc = await page.evaluate(
            """
            () => {
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
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except Exception:
        return 0.0


async def _submit_product(page, cleaned_title: str, sale_price: int, raw_title: str) -> bool:
    """Click the green 'Criar Produto' button and wait for the post-submit redirect."""
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
                f"SKU já cadastrado/duplicado no Dropper — pulando: {dup_text}",
                raw_title=raw_title,
                cleaned_title=cleaned_title,
            )
            try:
                await page.goto(DROPPER_CATALOG_URL, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(1500)
            except Exception:
                pass
            return False

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
    """Process the next pending product card. Returns True if a card was found and handled."""
    buttons = await page.query_selector_all(CARD_SELECTOR)
    if not buttons:
        await add_log("info", "Nenhum produto pendente encontrado nesta página")
        return False

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

    # 3. Clean SKU
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

    # 5. Clean title and fill
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
        # Extract Dropper product ID from the URL (.../createv2/<id>) BEFORE leaving the page
        dropper_id = None
        try:
            m = re.search(r"/createv2/(\d+)", page.url or "")
            if m:
                dropper_id = m.group(1)
        except Exception:
            dropper_id = None
        # ARQUITETURA: o robô Dropper SÓ cadastra. NÃO dispara enriquecimento
        # imediatamente — Dropper sincroniza com Bling de forma assíncrona.
        # Salvamos o SKU em `enrich_pending` e o worker detecta quando está completo.
        if sku:
            try:
                from db import db as _db
                from datetime import datetime, timezone
                await _db.enrich_pending.update_one(
                    {"sku": sku},
                    {"$set": {
                        "sku": sku,
                        "raw_title": cleaned["cleaned"],
                        "raw_description": raw_description,
                        "dropper_id": dropper_id,
                        "cost": cost,
                        "images": raw_images,
                        "source": "dropper",
                        "status": "pending",
                        "queued_at": datetime.now(timezone.utc).isoformat(),
                        "attempts": 0,
                    }},
                    upsert=True,
                )
                await add_log(
                    "info",
                    f"Produto {sku} cadastrado no Dropper. Aguardando sync→Bling. "
                    "Worker vai enriquecer automaticamente quando estiver completo.",
                )
            except Exception as e:
                await add_log("warning", f"Falhou ao agendar enriquecimento de {sku}: {e}")
    else:
        robot.failed += 1

    robot.processed += 1

    if submitted_ok and not dry_run:
        robot.current_product = f"⏸ Aguardando {DELAY_BETWEEN_PRODUCTS_S}s antes do próximo..."
        await add_log("info", f"Pausa de {DELAY_BETWEEN_PRODUCTS_S}s antes do próximo cadastro")
        await asyncio.sleep(DELAY_BETWEEN_PRODUCTS_S)

    await _open_catalog(page)
    await asyncio.sleep(1.0)
    return True


async def _run_playwright(username: str, password: str, max_products: int, dry_run: bool):
    """Playwright automation orchestrator against dropper.dropping.com.br"""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(viewport={"width": 1366, "height": 900})
        page = await context.new_page()

        try:
            if not await _login_dropper(page, username, password):
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
                if robot.llm_budget_exhausted:
                    await add_log(
                        "warning",
                        "🛑 Saldo da IA esgotado — robô parado para evitar gastos extras.",
                    )
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
                f"Robô Dropper finalizado. Processados={robot.processed} Sucesso={robot.success} Falhas={robot.failed}",
            )


async def start_bot(max_products: int = 10, dry_run: bool = True):
    if robot.state == "running":
        raise ValueError("Robô já está em execução")
    robot.task = asyncio.create_task(run_bot(max_products=max_products, dry_run=dry_run))


async def stop_bot():
    robot.stop_flag = True
    await add_log("warning", "Sinal de parada enviado ao robô Dropper")


async def save_dropper_credentials(username: str, password: str):
    await _save_credentials(username, password)


async def get_dropper_credentials():
    return await _get_credentials()
