"""TrimPro Playwright automation bot.

Runs in background, navigates TrimPro catalog, processes pending products,
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
from submission_confirmation import confirm_supplier_submission
from robot_service import RobotState, add_log as _base_add_log
from db import db


trimpro_robot = RobotState()


async def _trimpro_log(level: str, message: str, **extra) -> None:
    """Persist TrimPro messages separately from the JohnDrop log stream."""
    extra.setdefault("bot", "trimpro")
    await _base_add_log(level, message, **extra)


TRIMPRO_LOGIN_URL = "https://app.trimpro.com.br/login"
TRIMPRO_CATALOG_BASE_URL = "https://app.trimpro.com.br/dashboard/catalog"
TRIMPRO_CATALOG_URL = f"{TRIMPRO_CATALOG_BASE_URL}?integration_filter=without_integration"
INTEGRATION_NAME = "TotyShop-Bling"
SKU_ALLOWED_RE = re.compile(r"[^A-Za-z0-9\-]")
DELAY_BETWEEN_PRODUCTS_S = 25  # pause após cadastrar com sucesso (evita rate-limit do TrimPro)


async def _save_credentials(username: str, password: str):
    value = {"username": username, "password": password}
    await db.settings.update_one(
        {"key": "trimpro_creds"},
        {"$set": {"key": "trimpro_creds", "value": value}},
        upsert=True,
    )
    try:
        import secrets_store
        secrets_store.write("trimpro", value)
    except Exception:
        pass


async def _get_credentials():
    doc = await db.settings.find_one({"key": "trimpro_creds"}, {"_id": 0})
    value = (doc or {}).get("value") if doc else None
    if value and value.get("username"):
        return value
    try:
        import secrets_store
        stored = secrets_store.read("trimpro")
        if stored and stored.get("username"):
            return stored
    except Exception:
        pass
    return None


_EXPECTED_VERSION_CACHE: dict = {"version": None, "checked": False}


def browsers_path() -> str:
    """Directory where Playwright keeps its browsers on this platform."""
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if override:
        return override
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
        return os.path.join(local, "ms-playwright")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Caches/ms-playwright")
    return os.path.expanduser("~/.cache/ms-playwright")


def pw_env() -> dict:
    """Environment for `playwright` subprocesses, preserving the platform default."""
    return dict(os.environ)


def _chromium_relative_paths() -> list:
    """Binary paths inside a chromium package, relative to its version folder."""
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


def _expected_chromium_version() -> Optional[str]:
    """Run `playwright install chromium --dry-run` and parse the version Playwright expects.
    Uses sys.executable (the SAME python the backend is running under) to avoid version
    mismatch between /usr/local/bin/python and the venv python."""
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
    """Return the chromium binary path the current Playwright version expects."""
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
    # Not installed yet — report the canonical location so logs stay useful.
    return os.path.join(base, f"chromium_headless_shell-{version}", relatives[0])


def _find_chromium_binary() -> Optional[str]:
    """Locate a usable chromium binary. Preference order:
       1. The EXACT path the current Playwright version expects (ground truth).
       2. Glob fallback for any installed version (covers edge cases)."""
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
    """Block until a chromium binary matching the CURRENT Playwright version exists.
    If the installed version mismatches what Playwright expects, reinstalls."""
    import sys
    expected = _expected_chromium_path()
    if expected and os.path.isfile(expected):
        return True
    if expected:
        await _trimpro_log(
            "warning",
            f"Chromium ausente em {expected} (Playwright atualizado). Instalando agora (~109 MB)...",
        )
    else:
        await _trimpro_log("warning", "Chromium ausente. Instalando agora (~109 MB)...")
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "playwright", "install", "chromium",
            env=pw_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.wait(), timeout=300)
    except Exception as e:
        await _trimpro_log("error", f"Falha ao instalar Chromium: {e}")
        return False
    # Invalidate cache so we re-read the expected version
    _EXPECTED_VERSION_CACHE["checked"] = False
    _EXPECTED_VERSION_CACHE["version"] = None
    expected = _expected_chromium_path()
    if expected and os.path.isfile(expected):
        await _trimpro_log("success", "Chromium instalado com sucesso, retomando...")
        return True
    if _find_chromium_binary():
        await _trimpro_log("success", "Chromium detectado (via fallback), retomando...")
        return True
    await _trimpro_log("error", "Chromium não encontrado após tentativa de instalação")
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
        "browsers_path": browsers_path(),
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
    # Probe: try a quick launch+close. Never claim a simulated cadastro when it fails.
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            await browser.close()
        return True
    except Exception as e:
        await _trimpro_log("warning", f"Playwright indisponível. ({str(e)[:120]})")
        return False


async def _safe_enrich_bling(sku: str, cleaned_title: str, raw_description: str,
                              trimpro_id: Optional[str] = None, cost: Optional[float] = None,
                              images: Optional[list] = None) -> None:
    """Run Bling enrichment in background — never raises, just logs.
    If the LLM budget is exhausted, flips a flag so the main loop stops queueing more work."""
    try:
        import bling_enrichment
        await bling_enrichment.enrich_product_by_sku(
            sku, cleaned_title, raw_description,
            johndrop_id=trimpro_id, cost=cost, images=images,
            supplier_name="TRIMPRO",
        )
    except Exception as e:
        msg = str(e)
        await _trimpro_log("error", f"Bling enrichment background falhou para {sku}: {msg}")
        if "Budget has been exceeded" in msg or "budget" in msg.lower():
            trimpro_robot.message = (
                "Saldo da Universal Key esgotado. Recarregue em "
                "Profile → Universal Key → Add Balance e use 'Enriquecer em Lote' para reprocessar."
            )
            trimpro_robot.llm_budget_exhausted = True


async def run_bot(max_products: int = 10, dry_run: bool = True):
    """Main bot loop. Set trimpro_robot.state during execution."""
    trimpro_robot.reset()
    trimpro_robot.state = "running"
    trimpro_robot.started_at = datetime.now(timezone.utc)
    await _trimpro_log("info", f"Robô iniciado (max={max_products}, dry_run={dry_run})")

    creds = await _get_credentials()
    if not creds:
        trimpro_robot.state = "error"
        trimpro_robot.message = "Credenciais TrimPro não configuradas"
        trimpro_robot.finished_at = datetime.now(timezone.utc)
        await _trimpro_log("error", "Credenciais TrimPro ausentes. Configure em Configurações.")
        return

    if not await _playwright_available():
        trimpro_robot.state = "error"
        trimpro_robot.message = "Playwright/Chromium não está pronto; nenhum produto foi cadastrado"
        trimpro_robot.finished_at = datetime.now(timezone.utc)
        await _trimpro_log(
            "error",
            "Playwright/Chromium não está pronto. Execução encerrada sem simular cadastro."
        )
        return

    try:
        await _run_playwright(creds["username"], creds["password"], max_products, dry_run)
    except Exception as e:
        trimpro_robot.state = "error"
        trimpro_robot.message = "Falha no navegador; nenhum cadastro adicional foi confirmado"
        await _trimpro_log("error", f"Playwright falhou: {str(e)[:160]}")
    finally:
        trimpro_robot.finished_at = datetime.now(timezone.utc)
        if trimpro_robot.state == "running":
            trimpro_robot.state = "idle"


CARD_SELECTOR = (
    'a[href*="/dashboard/product/createv2/"], '
    'a:has-text("Cadastrar Produto"), '
    'button:has-text("Cadastrar Produto")'
)


async def _login_trimpro(page, username: str, password: str) -> bool:
    """Login on TrimPro. Returns True on success."""
    await _trimpro_log("info", "Abrindo página de login TrimPro...")
    await page.goto(TRIMPRO_LOGIN_URL, wait_until="networkidle", timeout=60000)
    await page.fill('input[type="email"], input[name="email"]', username)
    await page.fill('input[type="password"], input[name="password"]', password)
    async with page.expect_navigation(wait_until="networkidle", timeout=60000):
        await page.click('button[type="submit"]')
    if "login" in page.url.lower():
        await _trimpro_log("error", "Falha no login TrimPro — verifique credenciais")
        return False
    await _trimpro_log("success", "Login TrimPro OK")
    return True


async def _open_catalog(page) -> None:
    """Open catalog page, apply filter 'Todos que eu não cadastrei' via dropdown + search button.
    Resilient against ERR_ABORTED (page redirects still in flight)."""
    last_err = None
    for attempt in range(3):
        try:
            await page.goto(TRIMPRO_CATALOG_BASE_URL, wait_until="domcontentloaded", timeout=60000)
            last_err = None
            break
        except Exception as e:
            last_err = e
            msg = str(e)
            if "ERR_ABORTED" in msg or "ERR_TIMED_OUT" in msg:
                await _trimpro_log("info", f"Catálogo: nav aborted, retry {attempt + 1}/3")
                await page.wait_for_timeout(2500)
                continue
            raise
    if last_err:
        raise last_err

    await _trimpro_log("info", "Acessando Publicar Catálogo...")

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
                    await _trimpro_log("info", "Filtro 'Todos que eu não cadastrei' selecionado")
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
                await _trimpro_log("info", "Botão de busca (lupa) clicado")
    except Exception as e:
        await _trimpro_log("warning", f"Falha ao aplicar filtro via UI ({str(e)[:120]}). Usando URL.")

    if not applied_via_ui:
        await page.goto(TRIMPRO_CATALOG_URL, wait_until="networkidle", timeout=60000)

    # Wait for product cards to render
    try:
        await page.wait_for_selector(CARD_SELECTOR, timeout=15000)
    except Exception:
        await _trimpro_log("warning", "Selector de produtos não apareceu em 15s — sem produtos pendentes ou layout mudou")
        try:
            snippet = await page.evaluate(
                "() => document.querySelector('.content-page, main, body')?.innerText?.slice(0, 600) || ''"
            )
            if snippet:
                await _trimpro_log("info", f"Página: {snippet[:400]}")
        except Exception:
            pass


async def _select_integration(page) -> bool:
    """Select TotyShop-Bling and require a verified selected state before submit."""
    result = await select_required_integration(page, INTEGRATION_NAME)
    if result["selected"]:
        await _trimpro_log("info", f"Integração '{INTEGRATION_NAME}' confirmada")
        return True
    await _trimpro_log(
        "error",
        f"Integração '{INTEGRATION_NAME}' não confirmada: {result['detail']}",
    )
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
        await _trimpro_log("info", f"SKU limpo: '{current}' → '{cleaned}'")
    return cleaned or None


async def _fill_sale_price(page, sale_price: int) -> bool:
    """Fill the 'Preço de Venda' field by locating the input next to that label
    and typing the digits via keyboard so currency masks/React handlers fire."""
    value_str = str(sale_price)

    # Find ALL candidate inputs first, then prefer visible ones.
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

            // 1. Inputs by placeholder
            for (const inp of document.querySelectorAll('input')) {
                if (norm(inp.placeholder).includes(target)) candidates.push(inp);
            }

            // 2. Labels/spans/divs whose text equals "Preço de Venda" → find nearby input
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

            // De-dup + prefer VISIBLE candidates first
            const seen = new Set();
            const unique = candidates.filter(c => !seen.has(c) && seen.add(c));
            const visible = unique.find(isVisible);
            return visible || unique[0] || null;
        }
        """
    )
    elem = handle.as_element() if handle else None
    if not elem:
        await _trimpro_log("error", "Input 'Preço de Venda' não localizado no DOM")
        return False

    try:
        # Try to scroll/focus — if not visible, fall back to JS-based focus + value injection
        is_visible = False
        try:
            is_visible = await elem.is_visible()
        except Exception:
            is_visible = False

        if not is_visible:
            await _trimpro_log("warning", "Campo 'Preço de Venda' está oculto — tentando expandir/scroll via JS")
            # Try to force scroll + remove hidden styles via JS
            await page.evaluate(
                """el => {
                    el.scrollIntoView({block: 'center'});
                    // Remove hidden ancestors if any
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

        # Now try scroll_into_view with shorter timeout
        try:
            await elem.scroll_into_view_if_needed(timeout=5000)
        except Exception:
            pass  # already tried via JS

        # Try click; if it fails, dispatch keyboard events directly
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
            # Last-resort: set value via JS + fire input event
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
            await _trimpro_log("warning", f"Preço de Venda parece vazio após digitar ({value_str})")
            return False
        await _trimpro_log("info", f"Preço de Venda preenchido: '{final_value}' (esperado: {value_str})")
        return True
    except Exception as e:
        await _trimpro_log("error", f"Erro ao digitar preço: {e}")
        return False


_TRIMPRO_NAME_INPUT_SELECTOR = 'input[name="name"], input#name, input[placeholder*="Nome"]'


def _choose_trimpro_source_title(candidates: list, field_value: str = "") -> str:
    """Pick the real supplier title shown between the NOME header and its input.

    TrimPro puts the source title in visible text above the blank ``Nome`` field;
    unlike JohnDrop it is not the input's initial value.  The browser-side
    collector returns nearest candidates first, but this pure gate keeps labels
    such as ``NOME`` and ``Nome (máx. 60 caracteres)`` out of the result.
    """
    ignored = {
        "nome", "name", "informações da categoria", "informacoes da categoria",
        "informações descritivas", "informacoes descritivas",
    }
    for entry in candidates or []:
        text = entry.get("text", "") if isinstance(entry, dict) else str(entry or "")
        text = re.sub(r"\s+", " ", text).strip(" -:\t\n")
        lowered = text.casefold()
        if not text or lowered in ignored:
            continue
        if "máx" in lowered or "max" in lowered or "carac" in lowered:
            continue
        if len(text) < 3 or len(text) > 180:
            continue
        # Form labels are usually just one short token. A source title must be
        # meaningful text, e.g. "Mouse Pad Ergonômico com Apoio...".
        if len(text.split()) == 1 and len(text) < 12:
            continue
        return text
    return (field_value or "").strip()


async def _read_trimpro_source_title(page, name_input) -> str:
    """Read the product title displayed immediately below TrimPro's NOME card."""
    candidates = []
    try:
        candidates = await page.evaluate(
            """
            () => {
                const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
                const visible = el => {
                    const r = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && style.display !== 'none' &&
                        style.visibility !== 'hidden' && style.opacity !== '0';
                };
                const input = document.querySelector('input[name="name"], input#name, input[placeholder*="Nome"]');
                if (!input || !visible(input)) return [];

                const inputBox = input.getBoundingClientRect();
                const elements = Array.from(document.querySelectorAll('div, p, span, strong, b, small, label, h1, h2, h3, h4'));
                const sectionHeader = elements.find(el => visible(el) && clean(el.innerText || el.textContent).toLowerCase() === 'nome');
                const headerBottom = sectionHeader ? sectionHeader.getBoundingClientRect().bottom : inputBox.top - 180;
                const minTop = Math.max(0, headerBottom - 8);
                const out = [];
                const seen = new Set();

                for (const el of elements) {
                    if (!visible(el)) continue;
                    const box = el.getBoundingClientRect();
                    // The source is visually located between the NOME heading
                    // and the Nome input, in the same column (as in TrimPro).
                    if (box.top < minTop || box.bottom > inputBox.top + 4) continue;
                    if (box.right < inputBox.left - 30 || box.left > inputBox.right + 30) continue;
                    const directText = Array.from(el.childNodes)
                        .filter(node => node.nodeType === Node.TEXT_NODE)
                        .map(node => node.textContent || '').join(' ');
                    const text = clean(directText || (el.children.length === 0 ? el.innerText || el.textContent : ''));
                    const key = text.toLowerCase();
                    if (!text || seen.has(key)) continue;
                    seen.add(key);
                    out.push({ text, distance: Math.max(0, inputBox.top - box.bottom) });
                }
                return out.sort((a, b) => a.distance - b.distance);
            }
            """
        ) or []
    except Exception:
        candidates = []

    current_value = ""
    try:
        current_value = (await name_input.input_value() or "").strip()
    except Exception:
        pass
    return _choose_trimpro_source_title(candidates, current_value)


def _is_poor_trimpro_title(title: str) -> bool:
    """Flag only titles too vague to be useful in a marketplace listing."""
    cleaned = (title or "").strip()
    generic_words = {"produto", "item", "modelo", "novo", "novidade", "acessorio", "acessório"}
    words = [word.casefold() for word in re.findall(r"[A-Za-zÀ-ÿ0-9-]+", cleaned)]
    meaningful = [word for word in words if word not in generic_words]
    return len(cleaned) < 12 or len(meaningful) < 2


def _professional_title_from_description(description: str, preferred_code: Optional[str]) -> Optional[dict]:
    """Use a factual descriptive line when TrimPro's displayed name is too vague.

    This makes a stronger title without inventing product facts or consuming an
    AI credit. The normal title-cleaning rules and 60-character maximum still
    apply to the replacement.
    """
    plain = re.sub(r"<[^>]+>", " ", description or "")
    plain = plain.replace("&nbsp;", " ")
    skip_prefixes = (
        "descrição", "descricao", "característica", "caracteristica", "informação",
        "informacao", "material", "cor", "tamanho", "medida", "peso", "sku",
    )
    for line in re.split(r"[\r\n.!?]+", plain):
        line = re.sub(r"^[\s•\-–:]+", "", line).strip()
        if len(line) < 12 or line.casefold().startswith(skip_prefixes):
            continue
        candidate = clean_title(line, preferred_code=preferred_code)
        if not _is_poor_trimpro_title(candidate["cleaned"]):
            candidate["method"] = "description_fallback"
            return candidate
    return None


def _build_trimpro_title(raw_title: str, raw_description: str, preferred_code: Optional[str]) -> dict:
    """Clean the supplier title, with a factual professional fallback if needed."""
    cleaned = clean_title(raw_title, preferred_code=preferred_code)
    if not _is_poor_trimpro_title(cleaned["cleaned"]):
        return cleaned
    improved = _professional_title_from_description(raw_description, preferred_code)
    if improved:
        improved["raw"] = raw_title
        return improved
    return cleaned


async def _fill_trimpro_name(page, title: str) -> bool:
    """Fill and verify TrimPro's editable Nome field."""
    name_input = await page.query_selector(_TRIMPRO_NAME_INPUT_SELECTOR)
    if not name_input:
        await _trimpro_log("error", "Campo 'Nome' da TrimPro não localizado")
        return False
    try:
        await name_input.scroll_into_view_if_needed()
        await name_input.fill(title)
        await page.wait_for_timeout(150)
        final_value = (await name_input.input_value() or "").strip()
        if final_value != title:
            await _trimpro_log(
                "warning",
                f"Campo 'Nome' não confirmou o título preenchido: '{final_value}'",
            )
            return False
        return True
    except Exception as exc:
        await _trimpro_log("error", f"Falha ao preencher o campo 'Nome': {exc}")
        return False


async def _read_product_fields(page) -> Tuple[str, float]:
    """Read TrimPro's source title below NOME and its product cost."""
    name_input = await page.query_selector(_TRIMPRO_NAME_INPUT_SELECTOR)
    raw_title = await _read_trimpro_source_title(page, name_input) if name_input else ""
    cost = 0.0
    cost_input = await page.query_selector('input[placeholder*="Custo"], input[name="cost"], input#cost')
    if cost_input:
        cost_text = (await cost_input.input_value() or "0").strip()
        cost = _parse_brl_number(cost_text)
    return raw_title, cost


async def _read_product_images(page, quiet: bool = False) -> list:
    """Extract all image URLs from TrimPro product creation form.
    Handles: <img src>, <img data-src> (lazy-load), CSS background-image, and
    product carousels. Forces a brief scroll to trigger lazy-load."""
    try:
        # Scroll to force lazy-loaded images to render
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
                    if (low.endsWith('.svg')) return false;  // skip vector icons
                    return true;
                };

                // 1. <img> tags — also check data-src, data-original, data-lazy
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
                            // skip tiny images (icons)
                            if (img.naturalWidth > 0 && img.naturalWidth < 80) break;
                            out.add(s);
                            break;
                        }
                    }
                }
                // 2. CSS background-image on common containers
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

        # LIMPEZA MÍNIMA — mantém TODAS as fotos do produto:
        # 1. Remove só o lixo puro (HTML, avatar, logo).
        # 2. Junta URLs dos dois CDNs do TrimPro (uploads folder +
        #    meucatalogofacil.com) — cada um pode ter fotos DIFERENTES do
        #    mesmo produto.
        # 3. Dedupe apenas por URL exato (não por CDN, nem por nome de arquivo,
        #    porque URLs diferentes = fotos diferentes).
        img_exts = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")

        def _is_real_image(u: str) -> bool:
            low = u.lower()
            if "/dashboard/" in low or "/product/" in low:
                return False
            if "user.png" in low or "avatar" in low or "/logo" in low:
                return False
            if "bling" in low and "icon" in low:  # placeholder do Bling
                return False
            base = low.split("?")[0].split("#")[0]
            return any(base.endswith(ext) for ext in img_exts)

        cleaned = [u for u in result if _is_real_image(u)]

        # Dedupe por URL exato — NÃO por CDN, para não perder fotos
        seen = set()
        final: list = []
        for u in cleaned:
            if u not in seen:
                seen.add(u)
                final.append(u)
        final = final[:15]  # limite razoável de fotos por produto

        try:
            if quiet:
                return final
            await _trimpro_log(
                "info",
                f"Imagens extraídas do TrimPro: {len(final)} "
                f"(brutas={len(result)}, após limpeza={len(cleaned)})",
            )
        except Exception:
            pass
        return final
    except Exception as e:
        try:
            await _trimpro_log("warning", f"Falha ao ler imagens: {e}")
        except Exception:
            pass
        return []


async def _read_product_description(page) -> str:
    """Read the raw product description from TrimPro (textarea or contenteditable)."""
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
            await _trimpro_log("error", "Botão 'Criar Produto' não encontrado", raw_title=raw_title)
            return False

        # A click is not enough: reject a form that remains on /createv2
        # without either a redirect or an explicit supplier success message.
        await page.wait_for_timeout(2500)
        confirmation = await confirm_supplier_submission(page)
        if not confirmation["confirmed"]:
            await _trimpro_log(
                "error",
                f"Cadastro TrimPro não confirmado: {confirmation['detail']}",
                raw_title=raw_title,
                cleaned_title=cleaned_title,
            )
            return False
        await page.wait_for_timeout(1500)

        await _trimpro_log(
            "success",
            f"Cadastrado: {cleaned_title}",
            raw_title=raw_title,
            cleaned_title=cleaned_title,
            sale_price=sale_price,
        )
        return True
    except Exception as e:
        await _trimpro_log("error", f"Falha ao submeter: {e}", raw_title=raw_title)
        return False


async def _process_one_product(page, dry_run: bool, seen_skus: set) -> bool:
    """Process the next pending product card. Returns True if a card was found and handled.
    Tracks already-processed cards (by href) so dry-run doesn't revisit same product."""
    buttons = await page.query_selector_all(CARD_SELECTOR)
    if not buttons:
        await _trimpro_log("info", "Nenhum produto pendente encontrado nesta página")
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
        await _trimpro_log("info", "Não há mais produtos NOVOS nesta página")
        return False

    try:
        async with page.expect_navigation(wait_until="networkidle", timeout=60000):
            await chosen.click()
    except Exception as e:
        await _trimpro_log("error", f"Erro ao abrir produto: {e}")
        trimpro_robot.failed += 1
        trimpro_robot.processed += 1
        return True

    # 1. Select the TotyShop-Bling integration.  Do not create a supplier
    # product when the page did not actually select it: it would never sync to
    # Bling and the UI would incorrectly report a successful cadastro.
    if not await _select_integration(page):
        trimpro_robot.failed += 1
        trimpro_robot.processed += 1
        trimpro_robot.message = f"Integração {INTEGRATION_NAME} não foi confirmada"
        await _open_catalog(page)
        return True

    # 2. Read TrimPro's source title shown below the NOME heading + cost.
    # Unlike JohnDrop, TrimPro leaves the editable Nome input blank.
    raw_title, cost = await _read_product_fields(page)
    if not raw_title:
        await _trimpro_log(
            "error",
            "Título original abaixo de 'NOME' não localizado — produto não foi cadastrado para evitar nome vazio.",
        )
        trimpro_robot.failed += 1
        trimpro_robot.processed += 1
        await _open_catalog(page)
        return True
    trimpro_robot.current_product = raw_title[:60]

    # 2b. Read raw description (used later for Bling enrichment and only as a
    # factual fallback if the title displayed by the supplier is too poor).
    raw_description = await _read_product_description(page)
    raw_images = await _read_product_images(page)

    # Keep TrimPro's source ID BEFORE submitting. The site redirects away from
    # /createv2/<id> after save, which otherwise loses the supplier code.
    trimpro_id = None
    try:
        match = re.search(r"/createv2/(\d+)", page.url or "")
        if match:
            trimpro_id = match.group(1)
    except Exception:
        pass

    # 3. Clean SKU (only letters, digits, hyphen)
    sku = await _clean_sku_field(page)
    if sku:
        seen_skus.add(sku)

    # 4. Lookup sale price
    price = await lookup_price(cost)
    if not price.found:
        await _trimpro_log("error", f"Preço não encontrado para custo {cost}", raw_title=raw_title)
        trimpro_robot.failed += 1
        trimpro_robot.processed += 1
        await _open_catalog(page)
        return True

    # 5. Clean the visible source title and fill TrimPro's Nome input. The
    # shared cleaner keeps the 60-character rule; only poor source titles use
    # a factual phrase from the product description as a professional fallback.
    cleaned = _build_trimpro_title(raw_title, raw_description, preferred_code=sku)
    if cleaned.get("method") == "description_fallback":
        await _trimpro_log(
            "info",
            f"Título TrimPro muito curto/genérico; usado contexto da descrição: '{cleaned['cleaned']}'",
            raw_title=raw_title,
            cleaned_title=cleaned["cleaned"],
        )
    elif _is_poor_trimpro_title(cleaned["cleaned"]):
        await _trimpro_log(
            "warning",
            f"Título TrimPro curto/genérico sem descrição útil; mantido após limpeza: '{cleaned['cleaned']}'",
            raw_title=raw_title,
            cleaned_title=cleaned["cleaned"],
        )
    if not await _fill_trimpro_name(page, cleaned["cleaned"]):
        trimpro_robot.failed += 1
        trimpro_robot.processed += 1
        await _open_catalog(page)
        return True

    # 6. Fill sale price
    price_ok = await _fill_sale_price(page, price.sale_price_int)
    if not price_ok:
        await _trimpro_log("error", "Não foi possível preencher o campo 'Preço de Venda'", raw_title=raw_title)
        trimpro_robot.failed += 1
        trimpro_robot.processed += 1
        await _open_catalog(page)
        return True

    # 7. Submit (or just log if dry-run)
    submitted_ok = False
    if dry_run:
        await _trimpro_log(
            "info",
            f"[DRY-RUN] Pronto: {cleaned['cleaned']} | SKU: {sku} | Preço: {price.sale_price_int}",
            raw_title=raw_title,
            cleaned_title=cleaned["cleaned"],
            sale_price=price.sale_price_int,
        )
        trimpro_robot.success += 1
        submitted_ok = True
    elif await _submit_product(page, cleaned["cleaned"], price.sale_price_int, raw_title):
        trimpro_robot.success += 1
        submitted_ok = True
        # ARQUITETURA: o robô TrimPro SÓ cadastra. NÃO dispara enriquecimento
        # imediatamente — TrimPro sincroniza com Bling de forma assíncrona
        # (5-15 min) e durante esse período o produto fica sem imagens/estoque.
        # Em vez disso, salvamos o SKU em `enrich_pending` e um worker periódico
        # detecta quando o produto está completo no Bling e dispara o enriquecimento.
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
                        "supplier_code": trimpro_id,
                        "supplier_name": "TRIMPRO",
                        "source": "trimpro_bot",
                        "cost": cost,
                        "images": raw_images,
                        "status": "pending",
                        "queued_at": datetime.now(timezone.utc).isoformat(),
                        "attempts": 0,
                    }},
                    upsert=True,
                )
                await _trimpro_log(
                    "info",
                    f"Produto {sku} cadastrado na TrimPro. Aguardando sync→Bling. "
                    "Worker vai enriquecer automaticamente quando estiver completo.",
                )
            except Exception as e:
                await _trimpro_log("warning", f"Falhou ao agendar enriquecimento de {sku}: {e}")
    else:
        trimpro_robot.failed += 1

    trimpro_robot.processed += 1

    # Pause after a real successful cadastro to give TrimPro time to settle
    if submitted_ok and not dry_run:
        trimpro_robot.current_product = f"⏸ Aguardando {DELAY_BETWEEN_PRODUCTS_S}s antes do próximo..."
        await _trimpro_log("info", f"Pausa de {DELAY_BETWEEN_PRODUCTS_S}s antes do próximo cadastro")
        await asyncio.sleep(DELAY_BETWEEN_PRODUCTS_S)

    # Always re-apply the filter via dropdown + search button so cadastered products
    # are removed from the list. Just navigating via URL is NOT enough.
    await _open_catalog(page)
    await asyncio.sleep(1.0)
    return True


async def _run_playwright(username: str, password: str, max_products: int, dry_run: bool):
    """Playwright automation orchestrator against app.trimpro.com.br"""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(viewport={"width": 1366, "height": 900})
        page = await context.new_page()

        try:
            if not await _login_trimpro(page, username, password):
                trimpro_robot.state = "error"
                trimpro_robot.message = "Login falhou"
                return

            await _open_catalog(page)

            seen_skus: set = set()
            processed_count = 0
            while processed_count < max_products:
                if trimpro_robot.stop_flag:
                    await _trimpro_log("warning", "Robô interrompido")
                    break
                if trimpro_robot.llm_budget_exhausted:
                    await _trimpro_log(
                        "warning",
                        "🛑 Saldo da Universal Key esgotado — robô parado para evitar gastos extras. "
                        "Recarregue em Profile → Universal Key e use 'Enriquecer em Lote' para reprocessar.",
                    )
                    break
                if not await _process_one_product(page, dry_run, seen_skus):
                    break
                processed_count += 1
        finally:
            await browser.close()
            if trimpro_robot.state == "running":
                trimpro_robot.state = "idle"
            await _trimpro_log(
                "info",
                f"Robô finalizado. Processados={trimpro_robot.processed} Sucesso={trimpro_robot.success} Falhas={trimpro_robot.failed}",
            )


async def start_bot(max_products: int = 10, dry_run: bool = True):
    if trimpro_robot.state == "running":
        raise ValueError("Robô já está em execução")
    trimpro_robot.task = asyncio.create_task(run_bot(max_products=max_products, dry_run=dry_run))


async def stop_bot():
    trimpro_robot.stop_flag = True
    await _trimpro_log("warning", "Sinal de parada enviado ao robô")


# expose helper used from API
async def save_trimpro_credentials(username: str, password: str):
    await _save_credentials(username, password)


async def get_trimpro_credentials():
    return await _get_credentials()
