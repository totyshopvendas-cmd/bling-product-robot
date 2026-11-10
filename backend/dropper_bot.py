"""Robô Dropper — LÊ os produtos no Dropper e gera a planilha do Bling.

Mudança de objetivo (v3): o robô **não cadastra mais nada no Dropper** e **não
usa a API do Bling**. Ele percorre o catálogo do Dropper, abre a página de cada
produto (``/cliente/produtos/<id>``), lê tudo que existe lá (título, descrição,
imagens, código, custo, NCM, EAN, marca, categoria, unidade, estoque) e escreve
uma planilha ``.csv`` no formato de importação do Bling — 59 colunas, ponto e
vírgula, UTF-8 com BOM.

Fluxo:

1. login no Dropper;
2. colher as URLs do catálogo (``/cliente/produtos``);
3. para cada produto: abrir a página, ler o texto, normalizar
   (:mod:`dropper_reader`), buscar o preço de venda na tabela de preços
   (custo -> venda) e gerar as descrições (:mod:`dropper_descriptions`);
4. montar as linhas (:func:`bling_sheet_export.build_rows`) e gravar o arquivo
   com um relatório de revisão do lado.

Regra de ouro: **nada é inventado**. Campo que não vem do Dropper fica vazio e
aparece no relatório; NCM/CEST/EAN nunca são chutados.
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
from pathlib import Path
from typing import List, Optional, Tuple

from title_cleaner import clean_title
from pricing_service import lookup_price
from robot_service import robot, add_log
from db import db

import bling_sheet_export as sheet
import dropper_reader
import dropper_descriptions


# URLs do Dropper — ajustar se o site mudar de layout.
DROPPER_BASE_URL = "https://dropper.dropping.com.br"
DROPPER_LOGIN_URL = f"{DROPPER_BASE_URL}/login"
DROPPER_PRODUCTS_URL = f"{DROPPER_BASE_URL}/cliente/produtos"
DROPPER_PRODUCT_URL = f"{DROPPER_BASE_URL}/cliente/produtos/{{pid}}"

#: pasta dos arquivos gerados (mesma pasta da planilha do JohnDrop)
EXPORT_DIR = Path(__file__).parent / "exports"

#: pausa entre um produto e outro (segundos) — não irritar o site
DELAY_BETWEEN_PRODUCTS_S = float(os.environ.get("DROPPER_PAGE_DELAY", "1.0"))

#: navegador visível (foi o que funcionou); DROPPER_HEADLESS=1 esconde a janela
_HEADLESS = os.environ.get("DROPPER_HEADLESS", "0").strip() in ("1", "true", "True")

#: estado da geração atual (independente do robô JohnDrop)
STATE: dict = {
    "file": None,       # nome do arquivo gerado
    "test_file": False, # veio de um modo teste?
    "rows": 0,
    "products": 0,
    "failing": [],      # produtos que não entraram na planilha
    "pulados": [],      # pulados de propósito (sem código, exige reserva)
}


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


# --------------------------------------------------------------------------- #
# Login e catálogo
# --------------------------------------------------------------------------- #


async def _login_dropper(page, username: str, password: str) -> bool:
    """Login no Dropper. Devolve True quando sai da tela de /login."""
    await add_log("info", "Abrindo página de login Dropper...")
    try:
        await page.goto(DROPPER_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        await add_log("error", f"Não abriu a página de login: {str(e)[:160]}")
        return False
    await page.wait_for_timeout(1500)
    try:
        await page.fill('input[type="email"], input[name="email"]', username)
        await page.fill('input[type="password"], input[name="password"]', password)
    except Exception as e:
        await add_log("error", f"Campos de login não encontrados: {str(e)[:160]}")
        return False
    try:
        await page.click('button[type="submit"]')
    except Exception:
        try:
            await page.keyboard.press("Enter")
        except Exception:
            await add_log("error", "Não consegui enviar o formulário de login")
            return False

    # SPA: espera a URL sair de /login (sem expect_navigation, que dá timeout)
    for _ in range(40):
        if "login" not in (page.url or "").lower():
            await add_log("success", "Login Dropper OK")
            return True
        await page.wait_for_timeout(1000)
    await add_log("error", "Falha no login Dropper — a página continuou em /login")
    return False


async def _open_products_page(page) -> None:
    """Abre a lista de produtos do Dropper."""
    for attempt in range(3):
        try:
            await page.goto(DROPPER_PRODUCTS_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2500)
            return
        except Exception as e:
            if attempt == 2:
                await add_log("warning", f"Catálogo do Dropper com problema: {str(e)[:140]}")
            else:
                await page.wait_for_timeout(2000)


def _product_id_from_url(url: str) -> Optional[str]:
    """``.../cliente/produtos/abc123`` -> ``abc123``."""
    if not url:
        return None
    m = re.search(r"/cliente/produtos/([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else None


async def _collect_product_urls(page, max_products: int) -> List[str]:
    """Colhe as URLs dos produtos na lista (rolando até parar de aparecer).

    O ID do produto está na URL; assim abrimos cada produto direto, sem clicar
    em card (clicar dava erro de navegação em SPA).
    """
    found: List[str] = []
    seen: set = set()
    stagnant = 0

    for _ in range(80):
        if robot.stop_flag:
            break
        try:
            hrefs = await page.evaluate(
                """() => Array.from(document.querySelectorAll('a[href]'))
                        .map(a => a.getAttribute('href') || '')
                        .filter(h => h.includes('/cliente/produtos/'))"""
            )
        except Exception:
            hrefs = []
        for href in hrefs or []:
            pid = _product_id_from_url(href)
            if pid and pid not in seen:
                seen.add(pid)
                found.append(DROPPER_PRODUCT_URL.format(pid=pid))
        if max_products and len(found) >= max_products:
            break
        before = len(found)
        try:
            await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1400)
        except Exception:
            break
        stagnant = stagnant + 1 if len(found) == before else 0
        if stagnant >= 3:
            break

    if max_products:
        found = found[:max_products]
    await add_log("info", f"Produtos encontrados no catálogo: {len(found)}")
    return found


async def _read_page_text(page) -> str:
    """Texto visível da página do produto (é dele que tudo é lido)."""
    try:
        await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(700)
        await page.evaluate("() => window.scrollTo(0, 0)")
        await page.wait_for_timeout(300)
    except Exception:
        pass
    try:
        return await page.inner_text("body")
    except Exception:
        try:
            return await page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            return ""


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


async def _read_variations(page) -> list:
    """Variações (cor/tamanho) da página — ainda NÃO lidas nesta versão.

    O seletor de variações do Dropper não foi mapeado (não apareceu em nenhum
    print), e chutar aqui geraria linha de variação errada na planilha. Fica
    desligado; quando o usuário abrir um produto com variação, mapeamos.
    """
    return []


# --------------------------------------------------------------------------- #
# Configuração do arquivo (fornecedor e categoria)
# --------------------------------------------------------------------------- #

#: guardado em backend/.secrets junto com as demais credenciais
CONFIG_NAME = "dropper_export"


def load_export_config() -> dict:
    """Fornecedor e categoria usados na planilha.

    O fornecedor precisa ser o nome EXATO do cadastro no Bling: com "DROPPING"
    solto o import recusa a linha ("fornecedor inválido").
    """
    cfg = {
        "fornecedor": sheet.DEFAULT_SUPPLIER,
        "usar_categoria": sheet.DEFAULT_USE_CATEGORY,
    }
    try:
        import secrets_store

        stored = secrets_store.read(CONFIG_NAME) or {}
        if stored.get("fornecedor"):
            cfg["fornecedor"] = str(stored["fornecedor"]).strip()
        if "usar_categoria" in stored:
            cfg["usar_categoria"] = bool(stored["usar_categoria"])
    except Exception:
        pass
    return cfg


def save_export_config(fornecedor: str, usar_categoria: bool) -> dict:
    import secrets_store

    cfg = {
        "fornecedor": (fornecedor or sheet.DEFAULT_SUPPLIER).strip(),
        "usar_categoria": bool(usar_categoria),
    }
    secrets_store.write(CONFIG_NAME, cfg)
    return cfg


# --------------------------------------------------------------------------- #
# Histórico — quais produtos já entraram em alguma planilha
# --------------------------------------------------------------------------- #

#: arquivo (dentro de EXPORT_DIR) com os produtos já processados
HISTORY_NAME = "dropper_lidos.json"


def _history_path() -> Path:
    return EXPORT_DIR / HISTORY_NAME


def load_history() -> dict:
    """Lê o histórico. Nunca levanta exceção: histórico corrompido vira vazio."""
    path = _history_path()
    if not path.is_file():
        return {}
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_history(history: dict) -> None:
    import json

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _history_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_history_path())


def reset_history() -> int:
    """Apaga o histórico (o robô volta a ler todos). Devolve quantos foram esquecidos."""
    history = load_history()
    try:
        _history_path().unlink()
    except FileNotFoundError:
        pass
    return len(history)


def _is_known(pid: str, code: str, history: dict) -> bool:
    """O produto já foi para alguma planilha antes? (pelo id da URL ou pelo código)"""
    if pid and f"id:{pid}" in history:
        return True
    if code and f"cod:{code}" in history:
        return True
    return False


def filter_new_urls(urls: List[str], history: dict) -> Tuple[List[str], List[str]]:
    """Separa ``(novos, ja_lidos)`` a partir das URLs do catálogo.

    Usa o id que está na URL do produto. Produtos que já apareceram em alguma
    planilha ficam de fora — é isso que evita repetir produto entre execuções.
    """
    novos: List[str] = []
    ja: List[str] = []
    vistos: set = set()
    for url in urls:
        pid = _product_id_from_url(url) or ""
        if pid and pid in vistos:
            continue
        if pid:
            vistos.add(pid)
        (ja if _is_known(pid, "", history) else novos).append(url)
    return novos, ja


def record_history(history: dict, entries: list) -> dict:
    """Marca no histórico os produtos que entraram na planilha.

    ``entries`` é uma lista de ``(pid, code, title)``. Grava o id e o código
    (``cod:``) para não repetir o mesmo SKU vindo de outro id.
    """
    now = datetime.now(timezone.utc).isoformat()
    for pid, code, title in entries:
        if pid:
            history[f"id:{pid}"] = {"code": code or "", "title": (title or "")[:120], "lido_em": now}
        if code:
            history[f"cod:{code}"] = {"pid": pid or "", "title": (title or "")[:120], "lido_em": now}
    return history


# --------------------------------------------------------------------------- #
# Um produto
# --------------------------------------------------------------------------- #


async def _read_one_product(page, url: str, use_ai: bool):
    """Lê um produto. Devolve ``(rows, origem_desc, produto, limpo)`` ou None."""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1500)
    except Exception as e:
        await add_log("error", f"Não abriu o produto: {str(e)[:140]}")
        return None

    text = await _read_page_text(page)
    if not text or len(text) < 40:
        await add_log("error", "Página do produto sem texto legível")
        return None

    # Produto que exige reserva/pagamento de saldo NÃO entra na planilha.
    bloqueio = dropper_reader.page_is_blocked(text)
    if bloqueio:
        await add_log(
            "warning",
            f"Produto pulado: a página pede '{bloqueio}' — não está liberado para anúncio",
        )
        STATE.setdefault("pulados", []).append(_product_id_from_url(url) or url)
        return None

    images = await _read_product_images(page, quiet=True)
    product = dropper_reader.parse_product_text(text, url, images=images)
    if not product.code:
        # sem "ProdDPFxxxx" não há SKU: o id da URL é um cuid ilegível
        await add_log(
            "warning",
            f"Produto sem código (ProdDPF...) — pulado para não virar SKU ilegível no Bling: {url}",
        )
        STATE.setdefault("pulados", []).append(_product_id_from_url(url) or url)
        return None
    if not product.title:
        await add_log("error", f"Título não encontrado no produto {product.code}")
        return None

    variations = await _read_variations(page)
    if variations:
        product.variations = dropper_reader.parse_variations(variations)

    sale_price = None
    if product.cost is None:
        await add_log("warning", f"{product.code}: custo não encontrado no Dropper")
    else:
        try:
            price = await lookup_price(product.cost)
            if price.found:
                sale_price = price.sale_price_int
            else:
                await add_log(
                    "warning",
                    f"{product.code}: custo R$ {product.cost:.2f} fora da tabela de preços",
                )
        except Exception as e:
            await add_log("warning", f"{product.code}: falha ao consultar preço ({str(e)[:100]})")

    cleaned = clean_title(product.title, preferred_code=product.code)
    curta, compl, origem = await dropper_descriptions.build_descriptions(
        cleaned.get("cleaned") or product.title, product.description, use_ai=use_ai
    )

    cfg = load_export_config()
    rows = sheet.build_rows(
        product,
        cleaned_title=cleaned.get("cleaned"),
        short_description=curta,
        complementary_description=compl,
        sale_price=sale_price,
        supplier=cfg["fornecedor"],
        use_category=cfg["usar_categoria"],
    )
    return rows, origem, product, cleaned.get("cleaned") or product.title


def _preview_line(product, rows, origem: str) -> str:
    """Resumo curto de um produto, para o log do modo teste."""
    v = rows[0].values
    peso = v.get("Peso líquido (Kg)") or "-"
    preco = v.get("Preço") or "-"
    ncm = v.get("NCM") or "SEM NCM"
    imgs = len([u for u in (v.get("URL Imagens Externas") or "").split(",") if u.strip()])
    return (
        f"{v.get('Código', '')} | NCM {ncm} | custo {v.get('Preço de custo') or '-'} "
        f"→ venda {preco} | peso {peso} kg | {imgs} img | desc: {origem}"
    )


async def _write_output(rows, reports: List[str], dry_run: bool) -> Optional[Path]:
    """Grava o CSV + o relatório de revisão. Devolve o caminho do CSV."""
    if not rows:
        await add_log("warning", "Nenhuma linha para gravar — planilha não gerada")
        return None
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    prefix = "planilha_bling_TESTE" if dry_run else "planilha_bling"
    path = EXPORT_DIR / f"{prefix}_{stamp}.csv"

    path = sheet.write_sheet(rows, path)

    stats = sheet.summarize(rows)
    header = [
        "RELATÓRIO DE REVISÃO — planilha de importação Bling",
        f"Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "Origem dos dados: produtos lidos no Dropper (nada foi inventado)",
        "",
        f"Linhas: {stats['linhas']}  (produtos: {stats['produtos']}, variações: {stats['variacoes']})",
        f"Sem NCM: {stats['sem_ncm']} · Sem preço: {stats['sem_preco']} · Sem imagem: {stats['sem_imagem']}",
        "",
        "=" * 70,
        "",
    ]
    report_path = path.with_name(path.stem + "_revisao.txt")
    report_path.write_text("\n".join(header) + "\n\n".join(reports), encoding="utf-8")

    STATE["file"] = path.name
    STATE["test_file"] = dry_run
    STATE["rows"] = stats["linhas"]
    STATE["products"] = stats["produtos"]
    return path


def _mock_text(code: str, title: str) -> str:
    """Texto de exemplo no formato da página real (para o modo sem navegador)."""
    return (
        f"{code}\nFERAMENTAS MANUAIS · Classe Bronze\nDisponível\n{title}\n"
        "SEU CUSTO DE COMPRA\nR$ 190,44\nESTOQUE\n7\n"
        "Enviado por\nDropping\nMarca\nVONDER\nCategoria\nFERAMENTAS MANUAIS\n"
        "Unidade\nKIT\nEAN\n2003990403811\nNCM\n82130000\n"
        "DESCRIÇÃO\nTESOURA MULTIUSO VONDER\n"
        "Indicada para cortes em geral.\n- Material da lâmina: aço inox\n"
        "- Comprimento total: 250 mm\n- Peso aproximado do produto: 108 g\n"
        "- Código Vonder: 30.85.250.000\n"
    )


async def _run_mock(max_products: int, dry_run: bool = True):
    """Modo sem navegador: exercita o mesmo pipeline com um produto de exemplo."""
    samples = [
        ("ProdDPF2589", "Kit 12 Tesouras Multiuso 25 cm Lâmina Inox Vonder"),
        ("ProdDPF2590", "Jogo de Chaves de Fenda 6 Peças Vonder"),
    ]
    rows_all: List[sheet.SheetRow] = []
    reports: List[str] = []
    for code, title in (samples[:max_products] if max_products else samples):
        if robot.stop_flag:
            break
        robot.current_product = title[:60]
        product = dropper_reader.parse_product_text(
            _mock_text(code, title), url="", images=[f"https://exemplo/{code}-{i}.jpg" for i in range(1, 4)]
        )
        sale = None
        if product.cost is not None:
            try:
                price = await lookup_price(product.cost)
                sale = price.sale_price_int if price.found else None
            except Exception:
                sale = None
        cleaned = clean_title(product.title, preferred_code=product.code)
        curta, compl, origem = await dropper_descriptions.build_descriptions(
            cleaned.get("cleaned") or product.title, product.description, use_ai=False
        )
        rows = sheet.build_rows(
            product,
            cleaned_title=cleaned.get("cleaned"),
            short_description=curta,
            complementary_description=compl,
            sale_price=sale,
        )
        rows_all.extend(rows)
        reports.append(sheet.build_report(rows, product_title=f"[EXEMPLO] {cleaned.get('cleaned')}"))
        robot.processed += 1
        robot.success += 1
        await add_log("info", "[MOCKED] " + _preview_line(product, rows, origem), raw_title=title)
        await asyncio.sleep(0.3)

    await _write_output(rows_all, reports, dry_run=True)
    robot.state = "idle"


# --------------------------------------------------------------------------- #
# Orquestração
# --------------------------------------------------------------------------- #


async def run_bot(max_products: int = 10, dry_run: bool = True):
    """Lê os produtos no Dropper e gera a planilha do Bling."""
    robot.reset()
    robot.state = "running"
    robot.started_at = datetime.now(timezone.utc)
    STATE.update({"file": None, "test_file": False, "rows": 0, "products": 0,
                  "failing": [], "pulados": []})
    await add_log(
        "info",
        f"Robô Dropper iniciado — LER produtos e gerar planilha Bling "
        f"(produtos={'todos' if not max_products else max_products}, "
        f"modo={'TESTE' if dry_run else 'GERAR ARQUIVO'})",
    )

    creds = await _get_credentials()
    if not creds:
        robot.state = "error"
        robot.message = "Credenciais Dropper não configuradas"
        robot.finished_at = datetime.now(timezone.utc)
        await add_log("error", "Credenciais Dropper ausentes. Configure em Configurações.")
        return

    if not await _playwright_available():
        robot.message = "Playwright/Chromium indisponível — gerando com dados de exemplo"
        await add_log("warning", "Playwright indisponível — rodando em modo MOCKED (dados de exemplo)")
        await _run_mock(max_products, dry_run=True)
        robot.finished_at = datetime.now(timezone.utc)
        return

    try:
        await _run_playwright(creds["username"], creds["password"], max_products, dry_run)
    except Exception as e:
        robot.state = "error"
        robot.message = f"Falha na leitura do Dropper: {str(e)[:160]}"
        await add_log("error", f"Playwright falhou: {str(e)[:200]}")
    finally:
        robot.finished_at = datetime.now(timezone.utc)
        if robot.state == "running":
            robot.state = "idle"


async def _run_playwright(username: str, password: str, max_products: int, dry_run: bool):
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=_HEADLESS,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(viewport={"width": 1366, "height": 900})
        page = await context.new_page()

        rows_all: List[sheet.SheetRow] = []
        reports: List[str] = []
        use_ai = True
        ai_misses = 0

        try:
            if not await _login_dropper(page, username, password):
                robot.state = "error"
                robot.message = "Login falhou"
                return

            await _open_products_page(page)
            # Pede mais URLs do que o limite para compensar os já processados
            history = load_history()
            urls = await _collect_product_urls(page, 0 if not max_products else max_products + len(history))
            if not urls:
                robot.message = "Nenhum produto encontrado no catálogo do Dropper"
                await add_log("error", "Nenhum produto encontrado em /cliente/produtos")
                return

            urls, ja_lidos = filter_new_urls(urls, history)
            if ja_lidos:
                await add_log(
                    "info",
                    f"{len(ja_lidos)} produto(s) já entraram em planilha antes — serão pulados "
                    f"(total no histórico: {len(history)} registros)",
                )
            if max_products:
                urls = urls[:max_products]
            if not urls:
                robot.message = (
                    "Todos os produtos do catálogo já foram processados. "
                    "Use 'Zerar histórico' se quiser refazer."
                )
                await add_log("success", "Nada novo para ler — todos os produtos já estão no histórico")
                return
            await add_log("info", f"Produtos NOVOS nesta execução: {len(urls)}")
            processados: list = []

            for index, url in enumerate(urls, start=1):
                if robot.stop_flag:
                    await add_log("warning", "Robô interrompido pelo usuário")
                    break
                pid = _product_id_from_url(url) or ""
                robot.current_product = f"[{index}/{len(urls)}] {pid}"
                result = await _read_one_product(page, url, use_ai)
                robot.processed += 1
                if result is None:
                    robot.failed += 1
                    STATE["failing"].append(pid)
                    continue

                rows, origem, product, limpo = result
                processados.append((pid, product.code, limpo))
                rows_all.extend(rows)
                reports.append(sheet.build_report(rows, product_title=limpo))
                robot.success += 1

                if origem != "ia":
                    ai_misses += 1
                    if use_ai and ai_misses >= 2:
                        use_ai = False
                        await add_log(
                            "warning",
                            "IA de texto indisponível (chave/saldo). Seguindo com a descrição "
                            "do fornecedor — o relatório marca esses produtos.",
                        )
                else:
                    ai_misses = 0

                await add_log("info", f"[{index}/{len(urls)}] {_preview_line(product, rows, origem)}")
                await asyncio.sleep(DELAY_BETWEEN_PRODUCTS_S)

            path = await _write_output(rows_all, reports, dry_run)
            if path and not dry_run:
                # Só grava o histórico quando o arquivo oficial existe de verdade:
                # se algo quebrar antes, os produtos não são "perdidos".
                save_history(record_history(load_history(), processados))
                await add_log(
                    "info",
                    f"Histórico atualizado: {len(load_history())} registros "
                    f"({len(processados)} produto(s) desta execução)",
                )
            elif path:
                await add_log(
                    "info",
                    "MODO TESTE: o histórico não é gravado — os mesmos produtos "
                    "continuam disponíveis na execução oficial.",
                )
            if path:
                stats = sheet.summarize(rows_all)
                robot.message = (
                    f"Planilha gerada: {path.name} — {stats['produtos']} produtos, "
                    f"{stats['linhas']} linhas"
                )
                await add_log(
                    "success",
                    f"Planilha {'de TESTE ' if dry_run else ''}gerada → {path.name} "
                    f"({stats['produtos']} produtos, {stats['linhas']} linhas)",
                )
                await add_log(
                    "info",
                    f"Alertas: sem NCM {stats['sem_ncm']} · sem preço {stats['sem_preco']} · "
                    f"sem imagem {stats['sem_imagem']} · arquivo em {EXPORT_DIR}",
                )
                if STATE.get("pulados"):
                    await add_log(
                        "warning",
                        f"{len(STATE['pulados'])} produto(s) pulado(s) de propósito "
                        f"(sem código ou exigindo reserva/saldo) — não entraram na planilha",
                    )
                await add_log("info", f"Relatório de revisão: {path.stem}_revisao.txt")
        finally:
            try:
                await browser.close()
            except Exception:
                pass
            if robot.state == "running":
                robot.state = "idle"
            await add_log(
                "info",
                f"Robô Dropper finalizado. Lidos={robot.processed} "
                f"OK={robot.success} Falhas={robot.failed}",
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


# --------------------------------------------------------------------------- #
# Arquivos gerados (para a tela do robô)
# --------------------------------------------------------------------------- #


def _sheet_files() -> List[Path]:
    """Planilhas geradas, mais nova primeiro."""
    if not EXPORT_DIR.exists():
        return []
    # ordena por data de modificação e, no empate, pelo nome (que já tem o
    # carimbo AAAAmmdd-HHMM) — assim a "mais recente" é sempre a esperada
    return sorted(
        (p for p in EXPORT_DIR.glob("planilha_bling_*.csv")),
        key=lambda p: (p.stat().st_mtime, p.name),
        reverse=True,
    )


def latest_sheet() -> Optional[Path]:
    files = _sheet_files()
    return files[0] if files else None


def resolve_sheet(name: Optional[str] = None) -> Optional[Path]:
    """Acha a planilha pelo nome (ou a mais recente), sem sair da pasta."""
    if not name:
        return latest_sheet()
    safe = os.path.basename(name)
    path = EXPORT_DIR / safe
    return path if path.is_file() else None


def sheet_status() -> dict:
    """Estado da geração para a tela: arquivo, contagens, relatório."""
    path = latest_sheet()
    if not path:
        return {"file": None, "report": None, "size": 0, "modified": None,
                "products": 0, "rows": 0, "historico": len(load_history())}
    report = path.with_name(path.stem + "_revisao.txt")
    return {
        "file": path.name,
        "report": report.name if report.is_file() else None,
        "size": path.stat().st_size,
        "modified": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        "products": STATE.get("products") or 0,
        "rows": STATE.get("rows") or 0,
        "is_test": path.name.startswith("planilha_bling_TESTE"),
        "historico": len(load_history()),
    }


# --------------------------------------------------------------------------- #
# Diagnóstico (uma execução, para conferir a leitura no site real)
# --------------------------------------------------------------------------- #

_DIAG_JS = """
() => {
    const anchors = Array.from(document.querySelectorAll('a[href]'));
    const hrefs = anchors.map(a => a.getAttribute('href') || '');
    const produtos = hrefs.filter(h => h.includes('/cliente/produtos/'));
    return {
        total_links: anchors.length,
        links_produto: produtos.length,
        amostra_produto: produtos.slice(0, 8),
        amostra_links: hrefs.filter(h => h && h !== '#').slice(0, 25),
    };
}
"""


async def diagnostico_dropper(max_chars: int = 3500) -> str:
    """Abre o catálogo e o primeiro produto e devolve um relatório em texto.

    Serve para conferir, numa única execução, se o robô está achando as URLs
    dos produtos e se o texto lido da página do produto tem todos os campos.
    """
    creds = await _get_credentials()
    if not creds:
        return "Credenciais do Dropper não configuradas (Configurações)."
    if not await _playwright_available():
        return "Playwright/Chromium indisponível no servidor."

    from playwright.async_api import async_playwright

    linhas: List[str] = ["DIAGNÓSTICO DO ROBÔ DROPPER", "=" * 40]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=_HEADLESS, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(viewport={"width": 1366, "height": 900})
        page = await context.new_page()
        try:
            if not await _login_dropper(page, creds["username"], creds["password"]):
                linhas.append("FALHA NO LOGIN — a página continuou em /login.")
                return "\n".join(linhas)

            await _open_products_page(page)
            linhas.append(f"Catálogo aberto em: {page.url}")
            try:
                info = await page.evaluate(_DIAG_JS)
            except Exception as e:
                info = {}
                linhas.append(f"Falha ao inspecionar a página: {str(e)[:120]}")
            linhas.append(f"Links na página: {info.get('total_links', 0)}")
            linhas.append(f"Links de produto (/cliente/produtos/): {info.get('links_produto', 0)}")
            amostra = info.get("amostra_produto") or []
            if amostra:
                linhas.append("Exemplos de link de produto:")
                linhas.extend(f"  - {a}" for a in amostra)
            else:
                linhas.append("Nenhum link de produto. Primeiros links da página:")
                linhas.extend(f"  - {a}" for a in (info.get("amostra_links") or [])[:15])

            urls = await _collect_product_urls(page, 1)
            if not urls:
                linhas.append("")
                linhas.append("NÃO ENCONTREI PRODUTO PARA ABRIR — me mande este relatório.")
                return "\n".join(linhas)

            linhas.append("")
            linhas.append(f"ABRINDO PRODUTO: {urls[0]}")
            try:
                await page.goto(urls[0], wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(2000)
            except Exception as e:
                linhas.append(f"Falha ao abrir o produto: {str(e)[:140]}")
                return "\n".join(linhas)

            texto = await _read_page_text(page)
            imagens = await _read_product_images(page, quiet=True)
            produto = dropper_reader.parse_product_text(texto, urls[0], images=imagens)
            linhas.append(f"Imagens encontradas: {len(imagens)}")
            linhas.append("")
            linhas.append("CAMPOS INTERPRETADOS PELO ROBÔ")
            linhas.append(f"  código:    {produto.code!r}")
            linhas.append(f"  título:    {produto.title!r}")
            linhas.append(f"  custo:     {produto.cost!r}")
            linhas.append(f"  estoque:   {produto.stock!r}")
            linhas.append(f"  marca:     {produto.brand!r}")
            linhas.append(f"  categoria: {produto.category!r}")
            linhas.append(f"  unidade:   {produto.unit!r}")
            linhas.append(f"  EAN:       {produto.ean!r}")
            linhas.append(f"  NCM:       {produto.ncm!r}")
            linhas.append(f"  fornecedor:{produto.supplier!r}")
            linhas.append(f"  descrição: {len(produto.description)} caracteres")
            linhas.append("")
            linhas.append("TEXTO BRUTO DA PÁGINA (para conferência)")
            linhas.append("-" * 40)
            linhas.append((texto or "")[:max_chars])
            return "\n".join(linhas)
        except Exception as e:
            linhas.append(f"Erro no diagnóstico: {str(e)[:200]}")
            return "\n".join(linhas)
        finally:
            try:
                await browser.close()
            except Exception:
                pass
