"""FastAPI server for TotyShop Automation."""
import os
from typing import Optional
import logging
from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI, APIRouter, UploadFile, File, HTTPException, Body, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from starlette.middleware.cors import CORSMiddleware
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
import category_mapping
import category_mapping_bot


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
logger = logging.getLogger(__name__)


app = FastAPI(title="TotyShop Automation")
api = APIRouter(prefix="/api")


@app.on_event("startup")
async def _startup() -> None:
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


@api.get("/")
async def root() -> dict:
    return {"app": "TotyShop Automation", "version": "0.1"}


@api.get("/system/chromium-status")
async def system_chromium_status() -> dict:
    """Returns whether Playwright Chromium is installed & ready. Used by the UI
    to disable the 'Iniciar Robô (REAL)' button when the browser isn't ready."""
    return johndrop_bot.chromium_status()


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
    res = await pricing_service.import_csv(content)
    return res


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


# ---------- Bling OAuth ----------
@api.get("/bling/authorize-url")
async def bling_authorize_url(
    request: Request,
    next: str = "/configuracoes",
    origin: str | None = None,
) -> dict:
    base = _public_base(request, origin)
    url = await bling_service.build_authorize_url(next_path=next, app_base=base)
    return {"url": url, "redirect_uri": bling_service.redirect_uri(base)}


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
        return _redirect_settings(app_base, bling_error=detail[:180])
    except Exception as e:
        return _redirect_settings(app_base, bling_error=str(e)[:100])


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


# ---------- Robot ----------
@api.post("/robot/start", response_model=RobotStatusResponse)
async def robot_start(cfg: RobotJobConfig = Body(default_factory=RobotJobConfig)) -> RobotStatusResponse:
    if robot_service.robot.state == "running":
        raise HTTPException(400, "Robô já está em execução")
    await johndrop_bot.start_bot(max_products=cfg.max_products, dry_run=cfg.dry_run)
    return RobotStatusResponse(**robot_service.robot.to_dict())


@api.post("/robot/stop")
async def robot_stop() -> dict:
    await johndrop_bot.stop_bot()
    return {"ok": True}


@api.get("/robot/status", response_model=RobotStatusResponse)
async def robot_status() -> RobotStatusResponse:
    return RobotStatusResponse(**robot_service.robot.to_dict())


@api.get("/robot/logs")
async def robot_logs(limit: int = 100, bot: Optional[str] = None) -> list:
    logs = await robot_service.get_logs(limit)
    if bot:
        logs = [log for log in logs if (log.get("bot") or "johndrop") == bot]
    return logs


@api.post("/robot/logs/clear")
async def robot_logs_clear() -> dict:
    await robot_service.clear_logs()
    return {"ok": True}


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
    return await bling_enrichment.enrich_product_by_sku(
        payload.sku, payload.raw_title, payload.raw_description,
        johndrop_id=payload.johndrop_id, cost=payload.cost, images=payload.images,
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
@api.get("/bling/products-with-status")
async def bling_products_with_status(
    pagina: int = 1, limite: int = 50, filtro: str = "all", busca: str = ""
) -> dict:
    """Paginated Bling product list tagged as enriched / not enriched.
    filtro = 'all' | 'enriched' | 'not_enriched'."""
    return await bulk_enrichment.list_products_with_status(pagina, limite, filtro, busca)


@api.get("/bling/recent-skus")
async def bling_recent_skus(limit: int = 50) -> dict:
    """Return the last N SKUs registered locally by the JohnDrop bot, joined
    with their current Bling state. Used by the bulk-enrich UI to show a
    quick "últimos N" tab so the user can re-enrich recent products without
    paginating through the whole Bling catalog."""
    return await bulk_enrichment.list_recent_skus(limit=limit)


@api.get("/bling/needs-llm-retry")
async def bling_needs_llm_retry() -> dict:
    """Lista SKUs que foram enriquecidos parcialmente (falha no LLM). Usado
    para reprocessar em lote quando o saldo da Universal Key voltar."""
    from db import db as _db
    skus = []
    async for doc in _db.enrich_pending.find(
        {"needs_llm_retry": True},
        {"_id": 0, "sku": 1, "llm_last_error": 1, "product_id": 1},
    ):
        skus.append(doc)
    return {"total": len(skus), "items": skus}


@api.post("/bling/retry-llm-failed")
async def bling_retry_llm_failed() -> dict:
    """Re-enriquece TODOS os SKUs marcados como needs_llm_retry. Usar quando
    o saldo da Universal Key voltar."""
    from db import db as _db
    ids = []
    async for doc in _db.enrich_pending.find(
        {"needs_llm_retry": True}, {"product_id": 1, "_id": 0}
    ):
        if doc.get("product_id"):
            ids.append(doc["product_id"])
    if not ids:
        return {"ok": True, "message": "Nenhum SKU pendente de retry LLM", "count": 0}
    job = await bulk_enrichment.start(ids)
    # Limpa a flag depois (o job fica em background — flag será revalidada
    # naturalmente ao próximo enrichment se falhar de novo)
    await _db.enrich_pending.update_many(
        {"needs_llm_retry": True},
        {"$unset": {"needs_llm_retry": "", "llm_last_error": ""}},
    )
    return {"ok": True, "count": len(ids), "job_id": job.get("job_id")}


# ----- Stock Sync (JohnDrop → Bling) ---------------------------------------
_STOCK_SYNC_STATE = {"running": False, "started_at": None, "last_summary": None}


async def _stock_sync_task() -> None:
    try:
        scrape = await stock_sync_bot.collect_supplier_items()
        items = scrape.get("items") or []
        summary = await stock_sync.run_sync(items)
        summary["catalog_count"] = scrape.get("catalog_count")
        summary["alerts_count"] = scrape.get("alerts_count")
        _STOCK_SYNC_STATE["last_summary"] = summary
    except Exception as e:
        logger.exception("stock_sync task failed: %s", e)
        _STOCK_SYNC_STATE["last_summary"] = {"error": str(e)[:200]}
    finally:
        _STOCK_SYNC_STATE["running"] = False


@api.post("/stock-sync/run")
async def stock_sync_run() -> dict:
    """Trigger a manual stock-sync run (JohnDrop catalog + alerts → Bling).
    Returns immediately; consult /stock-sync/status for progress."""
    if _STOCK_SYNC_STATE["running"]:
        return {"ok": False, "running": True, "message": "Sync já em execução"}
    import asyncio as _aio
    _STOCK_SYNC_STATE["running"] = True
    _STOCK_SYNC_STATE["started_at"] = datetime.now(timezone.utc).isoformat()
    _aio.create_task(_stock_sync_task())
    return {"ok": True, "running": True, "started_at": _STOCK_SYNC_STATE["started_at"]}


@api.get("/stock-sync/status")
async def stock_sync_status() -> dict:
    last = await stock_sync.get_last_run()
    return {
        "running": _STOCK_SYNC_STATE["running"],
        "started_at": _STOCK_SYNC_STATE["started_at"],
        "in_memory_summary": _STOCK_SYNC_STATE["last_summary"],
        "last_run": last,
    }


# ----- Category Mapping (Bling → Marketplaces multiloja) -------------------
_CATMAP_STATE = {"running": False, "started_at": None}


class CatMapRunRequest(BaseModel):
    bling_user: str
    bling_pass: str


async def _catmap_task(bling_user: str, bling_pass: str) -> None:
    try:
        trees = await category_mapping_bot.scan_marketplace_trees(bling_user, bling_pass)
        bling_cats = await category_mapping._get_bling_categories_from_api()
        await category_mapping.generate_suggestions(trees, bling_cats)
    except Exception as e:
        logger.exception("catmap task failed: %s", e)
        await db.category_mapping_runs.update_one(
            {"name": "main"}, {"$set": {"status": "error", "error": str(e)[:300]}},
        )
    finally:
        _CATMAP_STATE["running"] = False


@api.post("/category-mapping/scan")
async def category_mapping_scan(req: CatMapRunRequest) -> dict:
    """Dispara scan das árvores de marketplace + geração de sugestões IA."""
    if _CATMAP_STATE["running"]:
        return {"ok": False, "running": True, "message": "Scan já em execução"}
    import asyncio as _aio
    _CATMAP_STATE["running"] = True
    _CATMAP_STATE["started_at"] = datetime.now(timezone.utc).isoformat()
    _aio.create_task(_catmap_task(req.bling_user, req.bling_pass))
    return {"ok": True, "running": True, "started_at": _CATMAP_STATE["started_at"]}


@api.get("/category-mapping/status")
async def category_mapping_status() -> dict:
    run = await category_mapping.get_run_status()
    return {
        "running": _CATMAP_STATE["running"],
        "started_at": _CATMAP_STATE["started_at"],
        "run": run,
    }


@api.get("/category-mapping/marketplaces")
async def category_mapping_marketplaces() -> dict:
    """Lista marketplaces já escaneados (fonte para o filtro na UI)."""
    items = await category_mapping.list_marketplaces()
    return {"total": len(items), "items": items}


@api.get("/category-mapping/lojas")
async def category_mapping_lojas() -> dict:
    """Lista marketplaces conectados via API Bling (não usa Playwright)."""
    try:
        items = await category_mapping.list_bling_lojas()
        return {"total": len(items), "items": items}
    except Exception as e:
        logger.exception("list_bling_lojas failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Bling API: {e}")


@api.get("/category-mapping/gaps")
async def category_mapping_gaps() -> dict:
    """Categorias Bling sem vínculo em cada loja conectada (via API)."""
    try:
        return await category_mapping.list_gaps()
    except Exception as e:
        logger.exception("list_gaps failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Bling API: {e}")


@api.get("/category-mapping/previews")
async def category_mapping_previews(
    marketplace: Optional[str] = None, limit: int = 500,
) -> dict:
    items = await category_mapping.list_previews(marketplace, limit)
    return {"total": len(items), "items": items}


class ApprovePreviewRequest(BaseModel):
    bling_category_id: int
    marketplace: str
    new_suggestion_id: Optional[str] = None
    approved: bool = True


@api.post("/category-mapping/approve")
async def category_mapping_approve(req: ApprovePreviewRequest) -> dict:
    return await category_mapping.approve_preview(
        req.bling_category_id, req.marketplace,
        req.new_suggestion_id, req.approved,
    )


# ----- Auto-sync novas categorias (P0) --------------------------------------
_AUTOSYNC_STATE = {"running": False, "started_at": None, "last_summary": None}


class CatMapAutoSyncRequest(BaseModel):
    bling_user: str
    bling_pass: str
    apply: bool = True


async def _autosync_task(bling_user: str, bling_pass: str, apply: bool) -> None:
    try:
        summary = await category_mapping.sync_new_categories(
            bling_user, bling_pass, apply=apply,
        )
        _AUTOSYNC_STATE["last_summary"] = summary
    except Exception as e:
        logger.exception("autosync failed: %s", e)
        await db.category_mapping_runs.update_one(
            {"name": "auto_sync"},
            {"$set": {"status": "error", "error": str(e)[:300]}},
        )
    finally:
        _AUTOSYNC_STATE["running"] = False


@api.get("/category-mapping/new-count")
async def category_mapping_new_count() -> dict:
    """Retorna a contagem de categorias Bling sem mapeamento (pendentes)."""
    try:
        n = await category_mapping.count_pending_new()
        return {"pending": n}
    except Exception as e:
        logger.exception("new-count failed: %s", e)
        return {"pending": 0, "error": str(e)[:200]}


@api.post("/category-mapping/sync-new")
async def category_mapping_sync_new(req: CatMapAutoSyncRequest) -> dict:
    """Detecta categorias Bling novas e mapeia+aplica automaticamente."""
    if _AUTOSYNC_STATE["running"]:
        return {"ok": False, "running": True, "message": "Auto-sync já em execução"}
    import asyncio as _aio
    _AUTOSYNC_STATE["running"] = True
    _AUTOSYNC_STATE["started_at"] = datetime.now(timezone.utc).isoformat()
    _aio.create_task(_autosync_task(req.bling_user, req.bling_pass, req.apply))
    return {"ok": True, "running": True, "started_at": _AUTOSYNC_STATE["started_at"]}


@api.get("/category-mapping/sync-new/status")
async def category_mapping_sync_new_status() -> dict:
    run = await category_mapping.get_auto_sync_status()
    return {
        "running": _AUTOSYNC_STATE["running"],
        "started_at": _AUTOSYNC_STATE["started_at"],
        "last_summary": _AUTOSYNC_STATE["last_summary"],
        "run": run,
    }


# ----- API-based sync (não usa Playwright) ----------------------------------
_APISYNC_STATE = {"running": False, "started_at": None, "last_summary": None}


class ApiSyncRequest(BaseModel):
    include_subcategorias: bool = True
    dry_run: bool = False


async def _apisync_task(include_subcategorias: bool, dry_run: bool) -> None:
    try:
        summary = await category_mapping.sync_via_api(
            include_subcategorias=include_subcategorias, dry_run=dry_run,
        )
        _APISYNC_STATE["last_summary"] = summary
    except Exception as e:
        logger.exception("api-sync failed: %s", e)
        await db.category_mapping_runs.update_one(
            {"name": "api_sync"},
            {"$set": {"status": "error", "error": str(e)[:300]}},
        )
    finally:
        _APISYNC_STATE["running"] = False


@api.post("/category-mapping/sync-api")
async def category_mapping_sync_api(req: ApiSyncRequest) -> dict:
    """Sincroniza via API oficial Bling (sem Playwright, sem credenciais web)."""
    if _APISYNC_STATE["running"]:
        return {"ok": False, "running": True, "message": "API-sync já em execução"}
    import asyncio as _aio
    _APISYNC_STATE["running"] = True
    _APISYNC_STATE["started_at"] = datetime.now(timezone.utc).isoformat()
    _aio.create_task(_apisync_task(req.include_subcategorias, req.dry_run))
    return {"ok": True, "running": True, "started_at": _APISYNC_STATE["started_at"]}


@api.get("/category-mapping/sync-api/status")
async def category_mapping_sync_api_status() -> dict:
    run = await category_mapping.get_api_sync_status()
    return {
        "running": _APISYNC_STATE["running"],
        "started_at": _APISYNC_STATE["started_at"],
        "last_summary": _APISYNC_STATE["last_summary"],
        "run": run,
    }


class LojaAliasRequest(BaseModel):
    loja_id: int
    alias: str


@api.put("/category-mapping/lojas/alias")
async def category_mapping_loja_alias(req: LojaAliasRequest) -> dict:
    return await category_mapping.set_loja_alias(req.loja_id, req.alias)


class KnownLojaRequest(BaseModel):
    loja_id: int
    name: str
    sample_code: str = ""


@api.post("/category-mapping/lojas/known")
async def category_mapping_add_known_loja(req: KnownLojaRequest) -> dict:
    """Registra manualmente uma loja Bling (útil para lojas sem vínculos ainda)."""
    return await category_mapping.add_known_loja(req.loja_id, req.name, req.sample_code)


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
app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
except Exception:
    pass

FRONTEND_BUILD = Path(os.environ.get("FRONTEND_BUILD") or (PROJECT_ROOT / "frontend" / "build"))


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
    index = FRONTEND_BUILD / "index.html"
    if index.exists():
        return FileResponse(index)
    raise HTTPException(404, "frontend não compilado")
