"""FastAPI server for TotyShop Automation."""
import os
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


@api.get("/")
async def root() -> dict:
    return {"app": "TotyShop Automation", "version": "0.1"}


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


app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
