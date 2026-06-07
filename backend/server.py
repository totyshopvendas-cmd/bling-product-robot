"""FastAPI server for TotyShop Automation."""
import os
from typing import Optional
import logging
from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI, APIRouter, UploadFile, File, HTTPException, Body, Query
from fastapi.responses import RedirectResponse
from starlette.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import BaseModel

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

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
from llm_cleaner import llm_clean_title
import bling_enrichment
import bulk_enrichment
from social_service import router as social_router
from social_ad_service import router as social_ad_router
from social_scheduler import router as social_scheduler_router, start_scheduler
from pinterest_service import router as pinterest_router
from social_onboarding import router as social_onboarding_router
from diag_service import router as diag_router


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
                env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": "/pw-browsers"},
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
        env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": "/pw-browsers"},
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


# ---------- Bling OAuth ----------
@api.get("/bling/authorize-url")
async def bling_authorize_url(next: str = "/configuracoes") -> dict:
    return {"url": bling_service.build_authorize_url(next)}


@api.get("/bling/callback")
async def bling_callback(
    code: str | None = None, state: str | None = None, error: str | None = None
) -> RedirectResponse:
    app_base = os.environ["APP_BASE_URL"]
    if error:
        return RedirectResponse(f"{app_base}/configuracoes?bling_error={error}")
    if not code or not state:
        return RedirectResponse(f"{app_base}/configuracoes?bling_error=missing_code")
    try:
        state_data = bling_service.parse_state(state)
        tokens = await bling_service.exchange_code(code)
        await bling_service.save_tokens(tokens)
        next_path = state_data.get("next", "/configuracoes")
        return RedirectResponse(f"{app_base}{next_path}?bling=connected")
    except HTTPException as he:
        return RedirectResponse(f"{app_base}/configuracoes?bling_error={he.detail}")
    except Exception as e:
        return RedirectResponse(f"{app_base}/configuracoes?bling_error={str(e)[:100]}")


@api.get("/bling/status")
async def bling_status() -> dict:
    return await bling_service.status()


@api.post("/bling/disconnect")
async def bling_disconnect_ep() -> dict:
    await bling_service.disconnect()
    return {"ok": True}


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
async def robot_logs(limit: int = 100) -> list:
    return await robot_service.get_logs(limit)


@api.post("/robot/logs/clear")
async def robot_logs_clear() -> dict:
    await robot_service.clear_logs()
    return {"ok": True}


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
    )


api.include_router(social_router)
api.include_router(social_ad_router)
api.include_router(social_scheduler_router)
api.include_router(pinterest_router)
api.include_router(social_onboarding_router)
api.include_router(diag_router)
app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
