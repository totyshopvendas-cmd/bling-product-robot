"""Exportar planilha — todos os produtos de "Meus Produtos" da JonhDrop.

MÓDULO ISOLADO (somente leitura): faz login na JonhDrop com as mesmas
credenciais do robô, varre /dashboard/product (todas as páginas), abre a página
de cada produto para pegar descrição, variações, imagens, peso/medidas e gera
um .xlsx em backend/exports/. NÃO altera nada na JonhDrop nem no Bling.

Fluxo:
  1. Login.
  2. Listagem: lê a tabela (Id | Canal | Integração | Img | Nome | Sku | Preço |
     Estoque | Data | Ações) mapeando colunas pelo cabeçalho. Produtos repetidos
     (mesmo SKU em várias integrações) viram 1 linha com canais agrupados.
  3. Detalhe (opcional): descobre o link do produto na linha (ou aprende o
     padrão da URL clicando no botão de Ações da 1ª linha), abre cada um e
     extrai todos os campos do formulário + descrição + imagens.
  4. Gera a planilha (também gera parcial se for interrompido ou der erro).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin

from db import db
from robot_service import add_log
import johndrop_export_sheet as sheet

logger = logging.getLogger(__name__)

BASE_URL = "https://app.jonhdrop.com.br"
LOGIN_URL = f"{BASE_URL}/login"
PRODUCTS_URL = f"{BASE_URL}/dashboard/product"
EXPORT_DIR = Path(__file__).parent / "exports"
MAX_LIST_PAGES = 200
DETAIL_DELAY_S = 1.0  # pausa entre páginas de detalhe (evita rate-limit)

STATE: Dict = {
    "running": False,
    "stop_requested": False,
    "phase": "idle",          # idle | login | list | details | writing | done | error | stopped
    "detailed": True,
    "started_at": None,
    "finished_at": None,
    "list_pages": 0,
    "total": 0,
    "done": 0,
    "detail_ok": 0,
    "detail_failed": 0,
    "current": None,
    "eta_seconds": None,
    "file": None,
    "error": None,
    "warnings": [],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _warn(msg: str) -> None:
    STATE["warnings"] = (STATE.get("warnings") or [])[-19:] + [msg]


def reset_state(detailed: bool) -> None:
    STATE.update({
        "running": True, "stop_requested": False, "phase": "login",
        "detailed": detailed, "started_at": _now(), "finished_at": None,
        "list_pages": 0, "total": 0, "done": 0, "detail_ok": 0,
        "detail_failed": 0, "current": None, "eta_seconds": None,
        "file": None, "error": None, "warnings": [],
    })


def get_status() -> dict:
    out = dict(STATE)
    out["files"] = list_exports()[:10]
    return out


def list_exports() -> List[dict]:
    if not EXPORT_DIR.exists():
        return []
    files = sorted(
        [p for p in EXPORT_DIR.iterdir() if p.suffix.lower() in (".xlsx", ".csv")],
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    return [
        {
            "name": p.name,
            "size": p.stat().st_size,
            "modified": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(),
        }
        for p in files
    ]


def resolve_export(name: Optional[str]) -> Optional[Path]:
    """Caminho seguro para download (sem path traversal)."""
    files = list_exports()
    if not files:
        return None
    if not name:
        return EXPORT_DIR / files[0]["name"]
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", name or ""):
        return None
    p = EXPORT_DIR / name
    return p if p.exists() and p.parent == EXPORT_DIR else None


# ------------------------------------------------------------- browser -----
async def _login(page, username: str, password: str) -> None:
    await page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
    await page.fill('input[type="email"], input[name="email"]', username)
    await page.fill('input[type="password"], input[name="password"]', password)
    async with page.expect_navigation(wait_until="networkidle", timeout=60000):
        await page.click('button[type="submit"]')
    if "login" in page.url.lower():
        raise RuntimeError("Login JonhDrop falhou — verifique as credenciais em Configurações")


async def _goto(page, url: str, creds: dict) -> None:
    """Navega; se a sessão caiu (redirecionou pro login), loga de novo."""
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    if "/login" in page.url.lower():
        await add_log("warning", "Exportar: sessão JonhDrop expirou — refazendo login")
        await _login(page, creds["username"], creds["password"])
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass


_SET_PAGE_SIZE_JS = """(val) => {
    for (const s of document.querySelectorAll('select')) {
        const opt = Array.from(s.options).find(o => o.value === val || o.textContent.trim() === val);
        if (opt) { s.value = opt.value; s.dispatchEvent(new Event('change', {bubbles: true})); return true; }
    }
    return false;
}"""

_READ_TABLE_JS = """() => {
    const table = document.querySelector('table');
    if (!table) return {headers: [], rows: []};
    const headers = Array.from(table.querySelectorAll('thead th')).map(th => (th.innerText || '').trim());
    const rows = [];
    for (const tr of table.querySelectorAll('tbody tr')) {
        const tds = Array.from(tr.querySelectorAll('td'));
        if (tds.length < 5) continue;
        const cells = tds.map(td => (td.innerText || '').trim());
        const imgs = tds.map(td => {
            const im = td.querySelector('img');
            return im ? (im.getAttribute('src') || im.getAttribute('data-src') || '') : '';
        });
        const links = Array.from(tr.querySelectorAll('a[href]'))
            .map(a => a.href)
            .filter(h => h && !h.startsWith('javascript') && !h.endsWith('#'));
        rows.push({cells, imgs, links});
    }
    return {headers, rows};
}"""


def _col_index(headers: List[str]) -> Dict[str, int]:
    """Mapeia colunas pelo cabeçalho; fallback para a ordem conhecida."""
    default = {"id": 0, "canal": 1, "integracao": 2, "img": 3, "nome": 4,
               "sku": 5, "preco": 6, "estoque": 7, "data": 8}
    if not headers:
        return default
    found: Dict[str, int] = {}
    for i, h in enumerate(headers):
        n = sheet.normalize_label(h)
        for key, kws in (
            ("id", ("id",)), ("canal", ("canal",)), ("integracao", ("integracao",)),
            ("img", ("img", "imagem", "foto")), ("nome", ("nome", "produto")),
            ("sku", ("sku", "codigo")), ("preco", ("preco", "valor")),
            ("estoque", ("estoque",)), ("data", ("data",)),
        ):
            if key not in found and n in kws:
                found[key] = i
    return found if ("sku" in found and "nome" in found) else default


def parse_list_rows(headers: List[str], rows: List[dict]) -> List[dict]:
    """Converte as linhas cruas da tabela em dicts de produto (puro/testável)."""
    idx = _col_index(headers)
    out = []
    for r in rows:
        cells = r.get("cells") or []

        def g(key):
            i = idx.get(key)
            return cells[i] if i is not None and i < len(cells) else ""

        sku_raw = g("sku")
        sku = sheet.clean_sku_cell(sku_raw)
        if not sku:
            continue
        img = ""
        i_img = idx.get("img")
        imgs = r.get("imgs") or []
        if i_img is not None and i_img < len(imgs):
            img = imgs[i_img]
        if not img:
            img = next((x for x in imgs if x), "")
        jd_id = re.sub(r"\D", "", g("id") or "")
        out.append({
            "jonhdrop_id": jd_id,
            "sku": sku,
            "sku_catalogo": sheet.catalog_sku_from_cell(sku_raw),
            "nome": g("nome"),
            "canal": g("canal"),
            "integracao": g("integracao"),
            "preco": sheet.parse_brl(g("preco")),
            "estoque": sheet.parse_int(g("estoque")),
            "data": g("data"),
            "imagem_principal": img,
            "links": r.get("links") or [],
        })
    return out


def merge_by_sku(items: List[dict]) -> List[dict]:
    """Mesmo SKU em várias integrações → 1 linha, canais/integrações agrupados."""
    merged: Dict[str, dict] = {}
    order: List[str] = []
    for it in items:
        key = it["sku"].upper()
        if key not in merged:
            merged[key] = dict(it)
            merged[key]["_canais"] = [it.get("canal")] if it.get("canal") else []
            merged[key]["_integracoes"] = [it.get("integracao")] if it.get("integracao") else []
            order.append(key)
            continue
        m = merged[key]
        if it.get("canal") and it["canal"] not in m["_canais"]:
            m["_canais"].append(it["canal"])
        if it.get("integracao") and it["integracao"] not in m["_integracoes"]:
            m["_integracoes"].append(it["integracao"])
        for k in ("links",):
            m[k] = list(dict.fromkeys((m.get(k) or []) + (it.get(k) or [])))
        for k in ("imagem_principal", "nome", "jonhdrop_id", "sku_catalogo"):
            if not m.get(k) and it.get(k):
                m[k] = it[k]
    result = []
    for key in order:
        m = merged.pop(key)
        m["canal"] = ", ".join(m.pop("_canais"))
        m["integracao"] = ", ".join(m.pop("_integracoes"))
        result.append(m)
    return result


def pick_detail_link(item: dict) -> Optional[str]:
    """Escolhe, entre os links da linha, o que parece ser a página do produto."""
    links = [l for l in (item.get("links") or []) if l.startswith(BASE_URL)]
    links = [l for l in links if "/createv2/" not in l and not l.rstrip("/").endswith("/dashboard/product")]
    jd = item.get("jonhdrop_id") or ""
    if jd:
        with_id = [l for l in links if re.search(rf"(?:/|=){re.escape(jd)}(?:/|$|\?|&)", l)]
        if with_id:
            # prefere edit/show
            with_id.sort(key=lambda l: (0 if re.search(r"edit|show|view|detal", l) else 1, len(l)))
            return with_id[0]
    prod = [l for l in links if "/product" in l]
    return prod[0] if prod else None


async def _scrape_list(page, creds: dict) -> List[dict]:
    await _goto(page, PRODUCTS_URL, creds)
    for size in ("100", "50", "25"):
        try:
            if await page.evaluate(_SET_PAGE_SIZE_JS, size):
                await page.wait_for_load_state("networkidle", timeout=15000)
                break
        except Exception:
            continue

    items: List[dict] = []
    seen_signatures: set = set()
    page_idx = 1
    while page_idx <= MAX_LIST_PAGES and not STATE["stop_requested"]:
        try:
            await page.wait_for_selector("table tbody tr", timeout=15000)
        except Exception:
            break
        data = await page.evaluate(_READ_TABLE_JS)
        parsed = parse_list_rows(data.get("headers") or [], data.get("rows") or [])
        new = 0
        for it in parsed:
            sig = (it["jonhdrop_id"], it["sku"], it["canal"], it["integracao"])
            if sig in seen_signatures:
                continue
            seen_signatures.add(sig)
            items.append(it)
            new += 1
        STATE["list_pages"] = page_idx
        STATE["total"] = len(items)
        await add_log("info", f"Exportar: listagem página {page_idx} → {new} produtos (total {len(items)})")
        if new == 0:
            break
        clicked = False
        for sel in (
            "li.page-item:not(.disabled) a:has-text('Próximo')",
            "li.page-item:not(.disabled) a:has-text('Next')",
            "a.paginate_button.next:not(.disabled)",
            "a[rel='next']:not(.disabled)",
            "button:has-text('Próxima'):not([disabled])",
            f"li.page-item a:has-text('{page_idx + 1}')",
        ):
            try:
                nxt = await page.query_selector(sel)
                if not nxt:
                    continue
                await nxt.click()
                await page.wait_for_load_state("networkidle", timeout=15000)
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            break
        page_idx += 1
    return items


async def _learn_detail_pattern(page, creds: dict, sample: dict) -> Optional[str]:
    """Quando a linha não tem <a href>, clica no botão de Ações da linha do
    produto `sample` e aprende o padrão da URL (ex.: /dashboard/product/edit/{id})."""
    jd = sample.get("jonhdrop_id")
    if not jd:
        return None
    try:
        await _goto(page, PRODUCTS_URL, creds)
        await page.wait_for_selector("table tbody tr", timeout=15000)
        before = page.url
        ok = await page.evaluate(
            """(jd) => {
                for (const tr of document.querySelectorAll('table tbody tr')) {
                    const first = tr.querySelector('td');
                    if (!first || (first.innerText || '').replace(/\\D/g, '') !== jd) continue;
                    const cands = Array.from(tr.querySelectorAll('a, button, [role="button"]'));
                    const score = (el) => {
                        const t = ((el.innerText || '') + ' ' + (el.title || '') + ' ' + (el.className || '') +
                                   ' ' + (el.innerHTML || '')).toLowerCase();
                        if (/excluir|delete|trash|remov/.test(t)) return -1;
                        if (/editar|edit|pencil|pen/.test(t)) return 3;
                        if (/ver|eye|show|detal/.test(t)) return 2;
                        return 1;
                    };
                    cands.sort((a, b) => score(b) - score(a));
                    const el = cands.find(c => score(c) > 0);
                    if (el) { el.click(); return true; }
                }
                return false;
            }""",
            jd,
        )
        if not ok:
            return None
        try:
            await page.wait_for_url(lambda u: u != before, timeout=15000)
        except Exception:
            pass
        url = page.url
        if url != before and jd in url:
            pattern = url.replace(jd, "{id}")
            await add_log("info", f"Exportar: padrão da página do produto aprendido → {pattern}")
            return pattern
    except Exception as e:
        logger.info("learn pattern failed: %s", e)
    return None


_READ_DETAIL_JS = """() => {
    const labelFor = (el) => {
        if (el.id) {
            const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
            if (l && l.innerText.trim()) return l.innerText.trim();
        }
        const wrap = el.closest('label');
        if (wrap && wrap.innerText.trim()) return wrap.innerText.trim();
        let p = el.parentElement;
        for (let i = 0; i < 3 && p; i++) {
            const ls = p.querySelectorAll('label');
            if (ls.length === 1 && ls[0].innerText.trim()) return ls[0].innerText.trim();
            p = p.parentElement;
        }
        return el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name || el.id || '';
    };
    const fields = [];
    for (const el of document.querySelectorAll('input, select, textarea')) {
        const type = (el.type || '').toLowerCase();
        if (['hidden', 'password', 'submit', 'button', 'file', 'search', 'reset', 'image'].includes(type)) continue;
        if ((type === 'checkbox' || type === 'radio') && !el.checked) continue;
        let value = '';
        if (el.tagName === 'SELECT') {
            value = Array.from(el.selectedOptions || []).map(o => o.textContent.trim()).join(', ');
        } else if (type === 'checkbox' || type === 'radio') {
            value = (el.value && el.value !== 'on') ? el.value : 'Sim';
        } else {
            value = el.value;
        }
        if (value == null || String(value).trim() === '') continue;
        fields.push({label: labelFor(el).slice(0, 150), name: el.name || el.id || '', value: String(value)});
    }
    for (const el of document.querySelectorAll('[contenteditable="true"]')) {
        const v = (el.innerText || '').trim();
        if (v) fields.push({label: labelFor(el) || 'Descrição', name: 'contenteditable', value: v});
    }
    // Páginas "somente leitura": pares dt/dd e tabelas de 2 colunas
    for (const dt of document.querySelectorAll('dt')) {
        const dd = dt.nextElementSibling;
        if (dd && dd.tagName === 'DD') fields.push({label: dt.innerText.trim(), name: '', value: dd.innerText.trim()});
    }
    for (const tr of document.querySelectorAll('table tr')) {
        const c = tr.querySelectorAll('th, td');
        if (c.length === 2 && c[0].innerText.trim() && c[1].innerText.trim())
            fields.push({label: c[0].innerText.trim(), name: '', value: c[1].innerText.trim()});
    }
    return fields;
}"""


async def _read_detail(page, url: str, creds: dict) -> dict:
    from johndrop_bot import _read_product_description, _read_product_images

    await _goto(page, url, creds)
    raw_fields = await page.evaluate(_READ_DETAIL_JS)
    mapped = sheet.map_detail_fields(raw_fields)
    desc = await _read_product_description(page)
    if not desc:
        # maior textarea/campo de descrição encontrado
        desc = max(
            (f["value"] for f in raw_fields if sheet.match_field(f.get("label"), f.get("name")) == "descricao"),
            key=len, default="",
        )
    images = await _read_product_images(page)
    return {"mapped": mapped, "raw_fields": raw_fields, "descricao": desc, "imagens": images}


def _apply_variations(p: dict) -> None:
    try:
        from bling_enrichment import _parse_variations, _parse_variation_quantities
        vs = _parse_variations(p.get("descricao") or "")
        qtys = _parse_variation_quantities(p.get("descricao") or "", vs) if vs else {}
    except Exception:
        vs, qtys = [], {}
    p["variacoes_lista"] = vs
    p["variacoes_qtd"] = qtys
    p["variacoes"] = ", ".join(vs)
    p["qtd_variacoes"] = len(vs)


async def _fallback_raw(p: dict) -> None:
    """Se a página não trouxe descrição, usa o texto salvo pelo robô (product_raw)."""
    if p.get("descricao"):
        return
    try:
        doc = await db.product_raw.find_one({"sku": p["sku"]}, {"_id": 0})
    except Exception:
        doc = None
    if doc:
        p["descricao"] = doc.get("raw_description") or ""
        if not p.get("imagens") and doc.get("images"):
            p["imagens"] = list(doc["images"])
        if not p.get("custo") and doc.get("cost"):
            p["custo"] = doc["cost"]


# --------------------------------------------------------------- runner ----
async def run_export(detailed: bool = True, max_products: Optional[int] = None) -> None:
    """Executa a exportação completa (chamar via asyncio.create_task)."""
    from johndrop_bot import get_johndrop_credentials, _ensure_chromium_installed

    products: List[dict] = []
    try:
        creds = await get_johndrop_credentials()
        if not creds or not creds.get("username") or not creds.get("password"):
            raise RuntimeError("Credenciais JonhDrop não configuradas (Configurações → JohnDrop)")

        try:
            await _ensure_chromium_installed()
        except Exception:
            pass
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                ctx = await browser.new_context()
                page = await ctx.new_page()
                STATE["phase"] = "login"
                await add_log("info", "Exportar: fazendo login na JonhDrop...")
                await _login(page, creds["username"], creds["password"])
                await add_log("success", "Exportar: login JonhDrop OK")

                STATE["phase"] = "list"
                rows = await _scrape_list(page, creds)
                products = merge_by_sku(rows)
                if max_products:
                    products = products[: int(max_products)]
                STATE["total"] = len(products)
                await add_log("info", f"Exportar: {len(products)} produtos únicos em Meus Produtos")

                if detailed and products and not STATE["stop_requested"]:
                    STATE["phase"] = "details"
                    pattern: Optional[str] = None
                    if not any(pick_detail_link(p) for p in products[:5]):
                        pattern = await _learn_detail_pattern(page, creds, products[0])
                        if not pattern:
                            _warn("Não achei o link da página de cada produto — planilha terá só os dados da listagem + textos salvos pelo robô.")
                    t0 = time.monotonic()
                    for i, p in enumerate(products):
                        if STATE["stop_requested"]:
                            break
                        STATE["current"] = f"{p['sku']} — {(p.get('nome') or '')[:50]}"
                        url = pick_detail_link(p) or (pattern.replace("{id}", p["jonhdrop_id"]) if pattern and p.get("jonhdrop_id") else None)
                        p["link"] = url or ""
                        if url:
                            try:
                                d = await _read_detail(page, url, creds)
                                for k, v in d["mapped"].items():
                                    if k in ("sku", "estoque", "preco") and p.get(k) not in (None, ""):
                                        continue  # listagem é a fonte oficial desses
                                    if k == "descricao":
                                        continue
                                    if k == "nome" and p.get("nome"):
                                        continue
                                    p[k] = v
                                p["descricao"] = d["descricao"]
                                p["imagens"] = d["imagens"]
                                if d["imagens"]:
                                    p["imagem_principal"] = d["imagens"][0]
                                p["campos_brutos"] = d["raw_fields"]
                                p["detalhe_status"] = "ok"
                                STATE["detail_ok"] += 1
                            except Exception as e:
                                p["detalhe_status"] = f"erro: {str(e)[:60]}"
                                STATE["detail_failed"] += 1
                                logger.info("detail %s failed: %s", p["sku"], e)
                        else:
                            p["detalhe_status"] = "sem link"
                            STATE["detail_failed"] += 1
                        await _fallback_raw(p)
                        STATE["done"] = i + 1
                        elapsed = time.monotonic() - t0
                        STATE["eta_seconds"] = int(elapsed / (i + 1) * (len(products) - i - 1))
                        if (i + 1) % 25 == 0:
                            await add_log("info", f"Exportar: detalhes {i + 1}/{len(products)}")
                        await asyncio.sleep(DETAIL_DELAY_S)
            finally:
                try:
                    await browser.close()
                except Exception:
                    pass

        STATE["phase"] = "stopped" if STATE["stop_requested"] else "writing"
    except Exception as e:
        logger.exception("johndrop export failed: %s", e)
        STATE["error"] = str(e)[:300]
        await add_log("error", f"Exportar: falhou — {str(e)[:200]}")
    finally:
        try:
            if products:
                for p in products:
                    if not detailed:
                        await _fallback_raw(p)
                    p.setdefault("imagens", [p["imagem_principal"]] if p.get("imagem_principal") else [])
                    if not p.get("imagem_principal") and p["imagens"]:
                        p["imagem_principal"] = p["imagens"][0]
                    p["qtd_imagens"] = len(p["imagens"])
                    p.setdefault("link", pick_detail_link(p) or "")
                    p.setdefault("detalhe_status", "não solicitado" if not detailed else "não processado")
                    _apply_variations(p)
                stamp = datetime.now().strftime("%Y%m%d_%H%M")
                partial = "_parcial" if (STATE["stop_requested"] or STATE["error"]) else ""
                path = EXPORT_DIR / f"jonhdrop_meus_produtos_{stamp}{partial}.xlsx"
                meta = {
                    "Gerado em": datetime.now().strftime("%d/%m/%Y %H:%M"),
                    "Origem": "JonhDrop — Meus Produtos",
                    "Modo": "Completo (com página de cada produto)" if detailed else "Básico (só listagem)",
                    "Detalhes OK": STATE["detail_ok"],
                    "Detalhes com falha": STATE["detail_failed"],
                    "Situação": "Parcial" if partial else "Completa",
                }
                final = sheet.build_workbook(products, path, meta)
                STATE["file"] = final.name
                await add_log("success", f"Exportar: planilha gerada → {final.name} ({len(products)} produtos)")
            elif not STATE["error"]:
                STATE["error"] = "Nenhum produto encontrado em Meus Produtos"
        except Exception as e:
            logger.exception("write workbook failed: %s", e)
            STATE["error"] = f"Falha ao gravar planilha: {str(e)[:200]}"
        STATE["phase"] = "error" if (STATE["error"] and not STATE["file"]) else ("stopped" if STATE["stop_requested"] else "done")
        STATE["running"] = False
        STATE["current"] = None
        STATE["eta_seconds"] = None
        STATE["finished_at"] = _now()
