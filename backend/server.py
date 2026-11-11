"""FastAPI server for TotyShop Automation."""
import html as html_module
import json
import os
import secrets
from typing import List, Optional
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, APIRouter, UploadFile, File, HTTPException, Body, Query, Request
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from dotenv import load_dotenv
from pydantic import BaseModel
from urllib.parse import urlencode

ROOT_DIR = Path(__file__).parent
PROJECT_ROOT = ROOT_DIR.parent

# A prévia original mantém as variáveis protegidas na raiz do projeto.  O
# arquivo local de backend continua sendo aceito apenas para chaves ausentes;
# variáveis injetadas pelo host e valores da raiz sempre têm prioridade.
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(ROOT_DIR / ".env", override=False)

from db import db, init_indexes
from models import (
    TitleCleanRequest, TitleCleanResponse, PriceLookupResponse,
    JohnDropCreds, RobotJobConfig, RobotStatusResponse, DashboardStats,
)
from title_cleaner import clean_title
import pricing_service
import bling_service
import robot_service
import johndrop_bot
import shopee_bot
from llm_cleaner import llm_clean_title
import bling_enrichment
import bulk_enrichment
from social_service import router as social_router
from social_ad_service import router as social_ad_router
from social_scheduler import router as social_scheduler_router, start_scheduler
from pinterest_service import router as pinterest_router
from social_onboarding import router as social_onboarding_router
from youtube_service import router as youtube_router
from enrichment_tracker import router as enrichment_tracker_router
from enrich_worker import router as enrich_worker_router, start_worker as start_enrich_worker
from image_proxy import router as image_proxy_router
from diag_service import router as diag_router
import stock_sync
import stock_sync_bot
import johndrop_export
import review_service
import admin_service
import category_mapping
import category_mapping_bot


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
logger = logging.getLogger(__name__)


app = FastAPI(title="TotyShop Automation")
api = APIRouter(prefix="/api")


@app.on_event("shutdown")
async def _shutdown_local_db() -> None:
    try:
        from db import stop_local_persistence
        stop_local_persistence()
    except Exception as e:
        logger.warning(f"banco local (shutdown): {e}")


@app.on_event("startup")
async def _startup() -> None:
    try:
        from db import start_local_persistence, local_db_info
        start_local_persistence()
        if local_db_info()["local_mode"]:
            logger.info("Banco local (sem MongoDB): %s", local_db_info())
    except Exception as e:
        logger.warning(f"banco local: {e}")
    try:
        await init_indexes()
    except Exception as e:
        logger.warning(f"index init: {e}")
    # Auto-install Chromium if missing (container may be reset between deploys).
    # Uses dynamic version detection — works with any playwright version.
    try:
        import subprocess
        import sys
        if not johndrop_bot._find_chromium_binary():
            logger.warning("Chromium ausente — instalando em background...")
            subprocess.Popen(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                env=johndrop_bot.pw_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            logger.info(f"Chromium pronto: {johndrop_bot._find_chromium_binary()}")
    except Exception as e:
        logger.warning(f"chromium auto-install skipped: {e}")
    # Boot the social ad scheduler (peak-hour publisher)
    try:
        start_scheduler()
        logger.info("Social ad scheduler started")
    except Exception as e:
        logger.warning(f"scheduler start failed: {e}")
    # Boot the enrich worker (watches JohnDrop→Bling sync)
    try:
        start_enrich_worker()
        logger.info("Enrich worker started")
    except Exception as e:
        logger.warning(f"enrich worker start failed: {e}")
    async def _load_prices_bg() -> None:
        import asyncio as _sleep
        await _sleep.sleep(2)
        try:
            logger.info("Carregando tabela de preços sozinha...")
            res = await pricing_service.ensure_table()
            if res.get("imported"):
                logger.info("Tabela de preços carregada: %s linhas (%s)", res["imported"], res.get("source") or "arquivo")
            elif res.get("skipped"):
                logger.info("Tabela de preços já estava pronta")
            else:
                logger.warning("Tabela de preços não entrou: %s", (res.get("errors") or ["sem arquivo"])[:3])
        except Exception as e:
            logger.warning("tabela de preços no startup: %s", e)

    import asyncio as _aio
    _aio.create_task(_load_prices_bg())
    _aio.create_task(_stock_schedule_loop())


@api.get("/")
async def root() -> dict:
    return {"app": "TotyShop Automation", "version": "0.1"}


@api.get("/system/chromium-status")
async def system_chromium_status() -> dict:
    """Returns whether Playwright Chromium is installed & ready. Used by the UI
    to disable the 'Iniciar Robô (REAL)' button when the browser isn't ready."""
    return johndrop_bot.chromium_status()


@api.get("/system/ready")
async def system_ready() -> dict:
    """What the robot actually needs before it can cadastre."""
    jd = await johndrop_bot.get_johndrop_credentials()
    bling = await bling_service.status()
    try:
        pricing_rows = await db.pricing.count_documents({})
    except Exception:
        pricing_rows = 0
    cr = johndrop_bot.chromium_status()
    johndrop_ok = bool(jd and jd.get("username"))
    bling_ok = bool(bling.get("connected"))
    chromium_ok = bool(cr.get("installed"))
    table_ok = pricing_rows > 0
    return {
        "johndrop": johndrop_ok,
        "johndrop_username": (jd or {}).get("username") or "",
        "bling": bling_ok,
        "pricing_rows": pricing_rows,
        "chromium": chromium_ok,
        "chromium_path": cr.get("path") or "",
        "can_test": johndrop_ok,
        "can_cadastre": johndrop_ok and table_ok and chromium_ok,
        "can_enrich": bling_ok,
        "can_stock": johndrop_ok and bling_ok and chromium_ok,
    }


@api.post("/system/install-chromium")
async def system_install_chromium() -> dict:
    """Trigger a background Chromium install. Idempotent — returns immediately."""
    import subprocess
    import sys
    status = johndrop_bot.chromium_status()
    if status["installed"]:
        return {"ok": True, "already_installed": True, "path": status["path"]}
    subprocess.Popen(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        env=johndrop_bot.pw_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {"ok": True, "installing": True}


# ---------- Title cleaner ----------
@api.post("/titles/clean", response_model=TitleCleanResponse)
async def clean_title_endpoint(payload: TitleCleanRequest) -> TitleCleanResponse:
    result = clean_title(payload.raw_title, preferred_code=payload.sku)
    if payload.use_llm_fallback:
        try:
            llm = await llm_clean_title(payload.raw_title, code_hint=result.get("code_used") or payload.sku)
            result["cleaned"] = llm
            result["length"] = len(llm)
            result["method"] = "llm"
        except Exception as e:
            logger.warning(f"LLM fallback falhou: {e}")
    return TitleCleanResponse(**result)


class BatchTitleRequest(BaseModel):
    items: list[TitleCleanRequest]


@api.post("/titles/clean/batch")
async def clean_titles_batch(payload: BatchTitleRequest) -> dict:
    out = []
    for item in payload.items:
        result = clean_title(item.raw_title, preferred_code=item.sku)
        out.append(result)
    return {"items": out}


# ---------- Pricing ----------
@api.post("/pricing/upload")
async def upload_pricing(file: UploadFile = File(...)) -> dict:
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(413, "Arquivo muito grande (>50MB)")
    res = await pricing_service.import_table(content, filename=file.filename or "")
    return res


@api.post("/pricing/load")
async def load_pricing() -> dict:
    """Baixa a planilha TotyShop (Google) ou lê Desktop/Downloads/data."""
    return await pricing_service.load_now()


@api.get("/pricing/lookup", response_model=PriceLookupResponse)
async def lookup_price_endpoint(cost: float = Query(...)) -> PriceLookupResponse:
    return await pricing_service.lookup_price(cost)


@api.get("/pricing/stats")
async def pricing_stats() -> dict:
    return await pricing_service.stats()


def _public_base(request: Request, origin: str | None = None) -> str:
    return bling_service.public_base_url(request, origin)


def _redirect_settings(app_base: str, **params: str) -> RedirectResponse:
    qs = urlencode({k: v for k, v in params.items() if v is not None})
    target = f"{app_base}/configuracoes"
    if qs:
        target = f"{target}?{qs}"
    return RedirectResponse(target)


# Short-lived tickets so the user can paste a small URL in a real Chrome tab.
# The Arena preview iframe cannot open pop-ups or navigate the parent window.
_OAUTH_TICKETS: dict[str, dict] = {}
_OAUTH_TICKET_TTL = timedelta(minutes=15)


def _purge_oauth_tickets() -> None:
    cutoff = datetime.now(timezone.utc) - _OAUTH_TICKET_TTL
    dead = [k for k, v in _OAUTH_TICKETS.items() if v.get("created_at", cutoff) < cutoff]
    for k in dead:
        _OAUTH_TICKETS.pop(k, None)


def _conectar_bling_html(authorize_url: str, copy_url: str, expired: bool = False) -> str:
    auth_js = json.dumps(authorize_url or "")
    copy_esc = html_module.escape(copy_url or "")
    title = "Link expirado" if expired else "Conectar Bling"
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title} — TotyShop</title>
<style>
  body {{ margin:0; font-family: system-ui, sans-serif; background:#FFF7ED; color:#1c1917; }}
  .box {{ max-width:640px; margin:48px auto; background:#fff; border:1px solid #FDBA74; border-radius:8px; padding:28px; }}
  h1 {{ margin:0 0 12px; color:#C2410C; font-size:22px; }}
  p {{ line-height:1.45; }}
  code {{ display:block; word-break:break-all; background:#FFF7ED; padding:12px; border:1px solid #FDBA74; margin:12px 0; }}
  button {{ background:#EA580C; color:#fff; border:0; padding:12px 18px; font-size:16px; cursor:pointer; border-radius:4px; }}
</style>
</head>
<body>
<div class="box">
  <h1 id="title">{'Este link expirou' if expired else 'Abrindo o login do Bling…'}</h1>
  <p id="help">{'Volte no TotyShop, clique em Conectar Bling e copie o novo endereço.' if expired else 'Se esta página não redirecionar, copie o endereço abaixo e cole numa nova guia do Chrome (o + no topo).'}</p>
  <code id="url">{copy_esc}</code>
  <button type="button" id="go">{'Voltar' if expired else 'Copiar endereço'}</button>
</div>
<script>
const AUTH = {auth_js};
const EXPIRED = {json.dumps(expired)};
const inIframe = window.top !== window.self;
const urlEl = document.getElementById('url');
const go = document.getElementById('go');
function copyUrl() {{
  const text = urlEl.textContent || '';
  if (navigator.clipboard && navigator.clipboard.writeText) {{
    navigator.clipboard.writeText(text);
  }} else {{
    const r = document.createRange(); r.selectNodeContents(urlEl);
    const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
    try {{ document.execCommand('copy'); }} catch (e) {{}}
  }}
}}
if (!EXPIRED && !inIframe && AUTH) {{
  window.location.replace(AUTH);
}} else if (!EXPIRED && inIframe) {{
  document.getElementById('title').textContent = 'A Arena não abre o Bling neste quadro';
  document.getElementById('help').textContent = 'Clique no + no topo do Chrome (Nova guia), cole este endereço e aperte Enter:';
}}
go.addEventListener('click', function () {{
  if (EXPIRED) {{ history.back(); return; }}
  copyUrl();
  go.textContent = 'Copiado — cole na nova guia';
}});
urlEl.addEventListener('click', function () {{
  const r = document.createRange(); r.selectNodeContents(urlEl);
  const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
  copyUrl();
}});
</script>
</body>
</html>"""


# ---------- Bling OAuth ----------
@api.get("/bling/authorize-url")
async def bling_authorize_url(
    request: Request,
    next: str = "/configuracoes",
    origin: str | None = None,
) -> dict:
    base = _public_base(request, origin)
    url = await bling_service.build_authorize_url(next_path=next, app_base=base)
    _purge_oauth_tickets()
    ticket = secrets.token_urlsafe(8)
    _OAUTH_TICKETS[ticket] = {"url": url, "created_at": datetime.now(timezone.utc)}
    open_url = f"{base}/conectar-bling/{ticket}"
    return {
        "url": url,
        "open_url": open_url,
        "redirect_uri": bling_service.redirect_uri(base),
    }


@api.get("/bling/oauth-config")
async def bling_oauth_config(request: Request, origin: str | None = None) -> dict:
    return await bling_service.oauth_config(_public_base(request, origin))


@api.get("/bling/callback")
async def bling_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    state_data: dict = {}
    if state:
        try:
            state_data = bling_service.parse_state(state)
        except HTTPException:
            state_data = {}
    app_base = (
        (state_data.get("redirect_uri") or "").rsplit("/api/bling/callback", 1)[0]
        or _public_base(request)
    )
    if error:
        return _redirect_settings(app_base, bling_error=error)
    if not code or not state:
        return _redirect_settings(app_base, bling_error="missing_code")
    try:
        if not state_data:
            state_data = bling_service.parse_state(state)
        callback_uri = state_data.get("redirect_uri") or bling_service.redirect_uri(app_base)
        tokens = await bling_service.exchange_code(code, callback_uri=callback_uri)
        await bling_service.save_tokens(tokens)
        next_path = state_data.get("next") or "/configuracoes"
        if not str(next_path).startswith("/"):
            next_path = "/configuracoes"
        return RedirectResponse(f"{app_base}{next_path}?bling=connected")
    except HTTPException as he:
        detail = he.detail if isinstance(he.detail, str) else str(he.detail)
        return _redirect_settings(app_base, bling_error=bling_service.friendly_oauth_error(detail))
    except Exception as e:
        return _redirect_settings(app_base, bling_error=bling_service.friendly_oauth_error(str(e)))


@api.get("/bling/status")
async def bling_status() -> dict:
    return await bling_service.status()


@api.get("/bling/ping")
async def bling_ping() -> dict:
    return await bling_service.ping()


@api.post("/bling/disconnect")
async def bling_disconnect_ep() -> dict:
    await bling_service.disconnect()
    return {"ok": True}


class BlingAppCreds(BaseModel):
    client_id: str
    client_secret: str = ""


@api.post("/settings/bling-app")
async def set_bling_app_creds(creds: BlingAppCreds) -> dict:
    return await bling_service.save_bling_app_creds(creds.client_id, creds.client_secret)


@api.get("/bling/products")
async def bling_list_products(pagina: int = 1, limite: int = 20) -> dict:
    resp = await bling_service.bling_request("GET", "/produtos", params={"pagina": pagina, "limite": limite})
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, resp.text)
    return resp.json()


@api.get("/bling/categories")
async def bling_categories() -> dict:
    resp = await bling_service.bling_request("GET", "/categorias/produtos")
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, resp.text)
    return resp.json()


# ---------- Settings ----------
@api.post("/settings/johndrop")
async def set_johndrop_creds(creds: JohnDropCreds) -> dict:
    await johndrop_bot.save_johndrop_credentials(creds.username, creds.password)
    return {"ok": True}


@api.get("/settings/johndrop")
async def get_johndrop_creds_status() -> dict:
    creds = await johndrop_bot.get_johndrop_credentials()
    if not creds:
        return {"configured": False}
    return {"configured": True, "username": creds.get("username")}


# ---------- Brand settings (item 4) ----------
class BrandSettings(BaseModel):
    mode: str  # "fabricante" | "loja"
    store_name: str  # nome da loja (o "____" preenchido pelo usuário)


@api.get("/settings/brand")
async def get_brand_settings() -> dict:
    doc = await db.settings.find_one({"key": "brand_settings"}, {"_id": 0})
    val = (doc or {}).get("value") or {}
    return {
        "mode": val.get("mode", "fabricante"),
        "store_name": val.get("store_name", ""),
    }


@api.post("/settings/brand")
async def save_brand_settings(req: BrandSettings) -> dict:
    if req.mode not in ("fabricante", "loja"):
        raise HTTPException(400, "modo inválido (use 'fabricante' ou 'loja')")
    await db.settings.update_one(
        {"key": "brand_settings"},
        {"$set": {"key": "brand_settings", "value": {
            "mode": req.mode,
            "store_name": req.store_name.strip(),
        }}},
        upsert=True,
    )
    return await get_brand_settings()


# ---------- Shopee Robot ----------
@api.get("/shopee/status", response_model=RobotStatusResponse)
async def shopee_status() -> RobotStatusResponse:
    return RobotStatusResponse(**shopee_bot.shopee_robot.to_dict())


@api.post("/shopee/start", response_model=RobotStatusResponse)
async def shopee_start(cfg: RobotJobConfig = Body(default_factory=RobotJobConfig)) -> RobotStatusResponse:
    if shopee_bot.shopee_robot.state == "running":
        raise HTTPException(400, "Robô Shopee já está em execução")
    await shopee_bot.start_bot(max_products=cfg.max_products, dry_run=cfg.dry_run)
    return RobotStatusResponse(**shopee_bot.shopee_robot.to_dict())


@api.post("/shopee/stop")
async def shopee_stop() -> dict:
    await shopee_bot.stop_bot()
    return {"ok": True}


@api.get("/shopee/creds")
async def shopee_creds_status() -> dict:
    return await shopee_bot.get_shopee_credentials_status()


# ---------- Bling Enrichment ----------
class EnrichRequest(BaseModel):
    sku: str
    raw_title: str
    raw_description: str = ""
    johndrop_id: Optional[str] = None
    cost: Optional[float] = None
    images: Optional[list] = None


@api.post("/bling/enrich")
async def bling_enrich_endpoint(payload: EnrichRequest) -> dict:
    """Manually trigger enrichment for a SKU."""
    st = await bling_service.status()
    if not st.get("connected"):
        raise HTTPException(400, "Conecte o Bling em Configurações antes de enriquecer")
    return await bling_enrichment.enrich_product_by_sku(
        payload.sku, payload.raw_title, payload.raw_description,
        johndrop_id=payload.johndrop_id, cost=payload.cost, images=payload.images,
        wait_for_product=False,
    )


class RawDescriptionRequest(BaseModel):
    sku: str
    raw_description: str
    raw_title: Optional[str] = ""


@api.post("/bling/raw-description")
async def save_raw_description(payload: RawDescriptionRequest) -> dict:
    """Allow the user to manually save a raw JohnDrop description for an
    existing Bling product, so bulk re-enrichment can re-create its variations.
    Especially useful for products imported before the persistence layer existed.
    """
    sku = payload.sku.strip()
    if not sku or len(payload.raw_description) < 30:
        raise HTTPException(400, "SKU e raw_description (>= 30 chars) obrigatórios")
    from datetime import datetime as _dt, timezone as _tz
    await db.product_raw.update_one(
        {"sku": sku},
        {"$set": {
            "sku": sku,
            "raw_title": payload.raw_title or "",
            "raw_description": payload.raw_description,
            "updated_at": _dt.now(_tz.utc).isoformat(),
            "source": "manual",
        }},
        upsert=True,
    )
    return {"ok": True, "sku": sku, "length": len(payload.raw_description)}


@api.get("/bling/raw-description/{sku}")
async def get_raw_description(sku: str) -> dict:
    doc = await db.product_raw.find_one({"sku": sku}, {"_id": 0})
    if not doc:
        return {"sku": sku, "exists": False}
    return {"sku": sku, "exists": True, **doc}


@api.delete("/bling/raw-description/{sku}")
async def delete_raw_description(sku: str) -> dict:
    """Remove a persisted raw_description (useful for cleaning up test data)."""
    r = await db.product_raw.delete_one({"sku": sku})
    return {"ok": True, "deleted": r.deleted_count}


@api.get("/bling/enrichment/logs")
async def bling_enrichment_logs(limit: int = 100) -> list:
    return await bling_enrichment.get_enrichment_logs(limit)


@api.get("/bling/enrichment/stats")
async def bling_enrichment_stats() -> dict:
    return await bling_enrichment.get_enrichment_stats()


# ---------- Bling Variations ----------
class VariationsRequest(BaseModel):
    sku: str
    variations: list[str]
    total_stock: int = 0
    image_urls: Optional[list[str]] = None


@api.post("/bling/variations")
async def bling_create_variations(payload: VariationsRequest) -> dict:
    """Manually create color/size variations on an existing Bling parent product.
    Distributes total_stock equally between children (Regra de Distribuição Balanceada).
    If image_urls is given, each child variation receives a copy of those images."""
    import bling_variations as bv
    if not payload.sku or not payload.variations:
        raise HTTPException(400, "Informe sku e lista de variações")
    result = await bv.find_and_create(
        payload.sku, payload.variations, payload.total_stock,
        parent_images=payload.image_urls,
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("reason", "falhou"))
    return result


class FixVariationsRequest(BaseModel):
    sku: Optional[str] = None
    product_id: Optional[int] = None


@api.post("/bling/variations/fix-existing")
async def bling_fix_existing_variations(payload: FixVariationsRequest) -> dict:
    """Enable cloneInfo=true and situacao=A on ALL existing child variations of a parent.
    Use this to retroactively fix variations that were created before the cloneInfo fix."""
    import bling_variations as bv
    pid = payload.product_id
    if not pid and payload.sku:
        resp = await bling_service.bling_request(
            "GET", "/produtos", params={"codigo": payload.sku, "limite": 5}
        )
        if resp.status_code >= 400:
            raise HTTPException(404, "produto não encontrado")
        items = (resp.json() or {}).get("data") or []
        for it in items:
            if (it.get("codigo") or "").strip().upper() == payload.sku.upper():
                pid = it.get("id")
                break
    if not pid:
        raise HTTPException(400, "Informe product_id OU sku válidos")
    return await bv.fix_existing_variations(pid)


# ---------- Bling Bulk Enrichment ----------
class BulkEnrichRequest(BaseModel):
    product_ids: Optional[list[int]] = None
    enrich_all_not_enriched: bool = False
    max_items: int = 500


@api.post("/bling/enrich-bulk")
async def bling_enrich_bulk(payload: BulkEnrichRequest) -> dict:
    """Start a background bulk enrichment job.

    Either pass `product_ids` directly OR set `enrich_all_not_enriched=true` to
    scan the Bling catalog and queue every non-enriched product (up to `max_items`).
    """
    ids = payload.product_ids or []
    if payload.enrich_all_not_enriched:
        scanned = await bulk_enrichment.collect_not_enriched_ids(max_items=payload.max_items)
        # Merge with explicit IDs (de-dup, preserve order)
        seen = set()
        merged: list[int] = []
        for i in list(ids) + scanned:
            if i not in seen:
                merged.append(i)
                seen.add(i)
        ids = merged
    if not ids:
        raise HTTPException(400, "Nenhum produto selecionado")
    result = await bulk_enrichment.start(ids)
    if not result.get("ok"):
        raise HTTPException(409, result.get("reason", "falha"))
    return result


@api.get("/bling/bulk-job")
async def bling_bulk_job_status() -> dict:
    return bulk_enrichment.job.to_dict()


@api.post("/bling/bulk-job/stop")
async def bling_bulk_job_stop() -> dict:
    return await bulk_enrichment.stop()


# ---------- Revisão em lote de produtos (itens 6 e 7) ----------

class ReviewEstimateRequest(BaseModel):
    max_items: Optional[int] = None


@api.post("/review/estimate")
async def review_estimate(payload: ReviewEstimateRequest = None) -> dict:
    """Estimativa de custo de IA ANTES de iniciar a revisão (item 7)."""
    req = payload or ReviewEstimateRequest()
    return await review_service.estimate_cost(req.max_items)


class ReviewStartRequest(BaseModel):
    product_ids: Optional[List[int]] = None
    max_items: int = 200


@api.post("/review/start")
async def review_start(payload: ReviewStartRequest) -> dict:
    if payload.product_ids:
        return await review_service.start(payload.product_ids)
    # Varre produtos não enriquecidos (mesmo critério do Enriquecer em Lote)
    scanned = await bulk_enrichment.collect_not_enriched_ids(max_items=payload.max_items)
    if not scanned:
        raise HTTPException(400, "Nenhum produto para revisar")
    return await review_service.start(scanned)


@api.get("/review/status")
async def review_status() -> dict:
    return review_service.status()


@api.post("/review/pause")
async def review_pause() -> dict:
    return await review_service.pause()


@api.post("/review/resume")
async def review_resume() -> dict:
    return await review_service.resume()


@api.post("/review/stop")
async def review_stop() -> dict:
    return await review_service.stop()


@api.post("/review/mark-delete")
async def review_mark_delete(payload: dict) -> dict:
    """Marca um produto para exclusão (item 6 — só exclui após aprovação)."""
    return await review_service.mark_for_delete(
        int(payload.get("product_id", 0)), payload.get("sku", ""),
        payload.get("name", ""), payload.get("reason", "estrutura de variação incorreta"),
    )


@api.post("/review/approve-deletes")
async def review_approve_deletes() -> dict:
    """Usuário aprova as exclusões pendentes (item 6)."""
    return await review_service.approve_deletions()


@api.post("/review/clear-delete-list")
async def review_clear_delete_list() -> dict:
    return await review_service.clear_delete_list()


@api.delete("/bling/products/{product_id}")
async def bling_delete_product(product_id: int) -> dict:
    """Exclui um produto no Bling (item 6 — só após aprovação na revisão)."""
    resp = await bling_service.bling_request("DELETE", f"/produtos/{product_id}")
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, resp.text[:500])
    return {"ok": True, "product_id": product_id}


# ---------- Dashboard ----------
@api.get("/dashboard/stats", response_model=DashboardStats)
async def dashboard_stats() -> DashboardStats:
    pricing_count = await db.pricing.count_documents({})
    bling = await bling_service.status()
    jd = await johndrop_bot.get_johndrop_credentials()
    shopee_creds = await shopee_bot.get_shopee_credentials_status()
    processed = await robot_service.count_logs_today()
    success = await robot_service.count_logs_today("success")
    failed = await robot_service.count_logs_today("error")
    return DashboardStats(
        pricing_rows=pricing_count,
        bling_connected=bling.get("connected", False),
        johndrop_configured=bool(jd),
        products_processed_today=processed,
        success_today=success,
        failed_today=failed,
        robot_state=robot_service.robot.state,
        shopee_configured=shopee_creds.get("configured", False),
        shopee_state=shopee_bot.shopee_robot.state,
    )


api.include_router(social_router)
api.include_router(social_ad_router)
api.include_router(social_scheduler_router)
api.include_router(pinterest_router)
api.include_router(social_onboarding_router)
api.include_router(youtube_router)
api.include_router(enrichment_tracker_router)
api.include_router(enrich_worker_router)
api.include_router(image_proxy_router)
api.include_router(diag_router)
admin_service.setup(app, api)
app.include_router(api)

class _AllowIframe(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        # Do not send X-Frame-Options: ALLOWALL — it is invalid and some
        # browsers treat it as DENY. CSP frame-ancestors is the allowlist.
        response.headers["Content-Security-Policy"] = "frame-ancestors *"
        if "x-frame-options" in response.headers:
            del response.headers["x-frame-options"]
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response


app.add_middleware(_AllowIframe)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=False,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
except Exception:
    pass

FRONTEND_BUILD = Path(os.environ.get("FRONTEND_BUILD") or (PROJECT_ROOT / "frontend" / "build"))
_STATIC_DIR = FRONTEND_BUILD / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="frontend-static")


def _index_html() -> FileResponse:
    index = FRONTEND_BUILD / "index.html"
    if not index.exists():
        raise HTTPException(404, "frontend não compilado")
    return FileResponse(
        index,
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "frame-ancestors *",
            "X-Frame-Options": "ALLOWALL",
        },
    )


@app.get("/conectar-bling/{ticket}")
async def conectar_bling_launch(request: Request, ticket: str, dl: int = 0):
    """Landing page for OAuth. Auto-redirects only outside the Arena iframe.
    ``dl=1`` sends the page as a file so Chrome's download bar can open it."""
    rec = _OAUTH_TICKETS.get(ticket)
    copy_url = str(request.base_url).rstrip("/") + f"/conectar-bling/{ticket}"
    origin = request.headers.get("x-forwarded-proto")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if origin and host:
        copy_url = f"{origin.split(',')[0].strip()}://{host.split(',')[0].strip()}/conectar-bling/{ticket}"
    headers = {}
    if dl:
        headers["Content-Disposition"] = 'attachment; filename="conectar-bling.html"'
    if not rec:
        return HTMLResponse(
            _conectar_bling_html("", copy_url, expired=True),
            status_code=404,
            headers=headers,
        )
    created = rec.get("created_at")
    if created and datetime.now(timezone.utc) - created > _OAUTH_TICKET_TTL:
        _OAUTH_TICKETS.pop(ticket, None)
        return HTMLResponse(
            _conectar_bling_html("", copy_url, expired=True),
            status_code=410,
            headers=headers,
        )
    return HTMLResponse(_conectar_bling_html(rec["url"], copy_url), headers=headers)


@app.get("/")
async def spa_root():
    return _index_html()


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    """Serve the React build so the app runs on a single public URL (no Emergent)."""
    if full_path.startswith("api/"):
        raise HTTPException(404, "Not Found")
    if not FRONTEND_BUILD.exists():
        raise HTTPException(404, "frontend não compilado")
    candidate = (FRONTEND_BUILD / full_path).resolve()
    build_root = FRONTEND_BUILD.resolve()
    if str(candidate).startswith(str(build_root)) and candidate.is_file():
        return FileResponse(candidate)
    return _index_html()
