"""Worker que detecta quando produtos da JohnDrop chegaram completos no Bling
e dispara o enriquecimento.

Fluxo:
  1. Robô JohnDrop cadastra produto → salva SKU em `enrich_pending` (status=pending)
  2. Produtos criados manualmente na integração TotyShop-Bling também são
     descobertos periodicamente na lista da JohnDrop e entram na mesma fila
  3. Worker busca pendentes. Quando o produto chega ao Bling com imagens,
     dispara enriquecimento e marca status=done
  4. Se não encontrou ou está sem imagens, marca attempts+=1 e tenta na próxima rodada
  5. Após 80 tentativas (~2h), marca como giveup
"""
import asyncio
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter

import bling_service
import bling_enrichment
from db import db
from robot_service import add_log


router = APIRouter(prefix="/enrich", tags=["enrich-pending-worker"])

POLL_INTERVAL_S = 90  # check pending queue every 90s
# A consulta leve à página "Meus Produtos" pega cadastros feitos manualmente
# na JohnDrop. Só considera a integração TotyShop-Bling e roda no máximo a cada
# cinco minutos, para não sobrecarregar a conta JohnDrop.
MANUAL_DISCOVERY_INTERVAL_S = 300
MANUAL_DISCOVERY_MAX_QUEUE = 10
MAX_ATTEMPTS = 80  # ~2h total wait (suficiente para JohnDrop concluir o sync)
MIN_IMAGES_REQUIRED = 1  # SOMENTE quando Bling tem ao menos 1 imagem
                          # (imagens chegam como "bagagem" do produto vindo da JohnDrop;
                          # sem imagens, o produto ainda está no ar — não enriquecemos)


_WORKER_STATE = {
    "running": False,
    "last_tick": None,
    "last_manual_discovery": None,
    "last_manual_discovery_count": 0,
    "task": None,
}
_NEXT_MANUAL_DISCOVERY_AT = 0.0


def _is_totyshop_bling_integration(item: dict) -> bool:
    """True only for a JohnDrop product linked to TotyShop-Bling.

    The filter is deliberately strict: it prevents the automatic monitor from
    touching products that came to Bling from another supplier or integration.
    """
    text = f"{item.get('canal') or ''} {item.get('integracao') or ''}"
    compact = re.sub(r"[^a-z0-9]+", "", text.casefold())
    return "totyshopbling" in compact


def _source_text_for_enrichment(item: dict, product: dict) -> tuple[str, str]:
    """Choose raw title/description for both robot and manual JohnDrop queues."""
    title = (item.get("raw_title") or product.get("nome") or "").strip()
    description = (
        item.get("raw_description")
        or product.get("descricao")
        or product.get("descricaoCurta")
        or ""
    ).strip()
    return title, description


async def _discover_manual_johndrop_products() -> int:
    """Queue untracked products manually created in JohnDrop for TotyShop-Bling.

    The JohnDrop bot already queues products it creates itself. This monitor
    covers the other path: the user creates a product directly in JohnDrop,
    selects TotyShop-Bling, and waits for its native sync to Bling.
    """
    try:
        import johndrop_bot
        import johndrop_export
        from playwright.async_api import async_playwright
    except Exception as exc:
        await add_log("warning", f"Monitor manual JohnDrop indisponível: {exc}")
        return 0

    creds = await johndrop_bot.get_johndrop_credentials()
    if not creds or not creds.get("username") or not creds.get("password"):
        return 0

    rows: list[dict] = []
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
                env=johndrop_bot.pw_env(),
            )
            try:
                context = await browser.new_context()
                page = await context.new_page()
                await johndrop_export._login(page, creds)
                await johndrop_export._goto(page, johndrop_export.PRODUCTS_URL, creds)
                # Read only the newest page. JohnDrop lists the recent products
                # first, and this keeps the monitor fast and non-invasive.
                try:
                    await page.evaluate(johndrop_export._SET_PAGE_SIZE_JS, "100")
                    await page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                await page.wait_for_selector("table tbody tr", timeout=15000)
                table = await page.evaluate(johndrop_export._READ_TABLE_JS)
                rows = johndrop_export.parse_list_rows(
                    table.get("headers") or [], table.get("rows") or [],
                )
            finally:
                await browser.close()
    except Exception as exc:
        await add_log("info", f"Monitor manual JohnDrop: não foi possível ler a lista ({str(exc)[:160]})")
        return 0

    queued = 0
    for item in rows:
        if queued >= MANUAL_DISCOVERY_MAX_QUEUE or not _is_totyshop_bling_integration(item):
            continue
        sku = (item.get("sku") or "").strip()
        if not sku:
            continue

        # Do not duplicate work done by the robot, an earlier monitor pass, or
        # an enrichment already concluded manually/em lote.
        tracked = await db.enrich_pending.find_one({"sku": sku}, {"_id": 1})
        completed = await db.bling_enrichment_logs.find_one(
            {"sku": sku, "status": "success"}, {"_id": 1},
        )
        if tracked or completed:
            continue

        await db.enrich_pending.update_one(
            {"sku": sku},
            {"$set": {
                "sku": sku,
                "raw_title": (item.get("nome") or "").strip(),
                # The original description is read from Bling once the native
                # JohnDrop sync completes; it is unavailable in the list view.
                "raw_description": "",
                "johndrop_id": item.get("jonhdrop_id") or None,
                "cost": None,
                "images": [],
                "source": "manual_johndrop",
                "status": "pending",
                "queued_at": datetime.now(timezone.utc).isoformat(),
                "attempts": 0,
            }},
            upsert=True,
        )
        queued += 1
        await add_log(
            "info",
            f"Produto manual JohnDrop detectado: {sku}. Aguardando sync→Bling para enriquecer.",
        )
    return queued


async def _find_in_bling(sku: str) -> Optional[dict]:
    try:
        r = await bling_service.bling_request(
            "GET", "/produtos", params={"codigo": sku, "limite": 1},
        )
        if r.status_code >= 400:
            return None
        items = (r.json() or {}).get("data") or []
        if not items:
            return None
        # Fetch full product
        pid = items[0].get("id")
        fr = await bling_service.bling_request("GET", f"/produtos/{pid}")
        if fr.status_code >= 400:
            return None
        return (fr.json() or {}).get("data") or None
    except Exception:
        return None


async def _is_ready_for_enrichment(product: dict) -> tuple[bool, str]:
    """REGRA: produto pronto para enriquecimento SOMENTE quando Bling já tem
    pelo menos 1 imagem.

    O JohnDrop manda o produto + imagens ("bagagem") em momentos diferentes — o
    metadado chega rápido mas as imagens demoram. Se enriquecermos antes das
    imagens pousarem, podemos atropelar o sync nativo (Bling sobrescreve campos
    durante o sync) e o produto fica sem imagens para sempre.

    Estoque NÃO é sinal confiável: ele às vezes chega antes das imagens.
    """
    imgs = (product.get("midia") or {}).get("imagens") or {}
    n_imgs = len(imgs.get("internas") or []) + len(imgs.get("externas") or [])
    if n_imgs >= MIN_IMAGES_REQUIRED:
        return True, f"images={n_imgs}"
    return False, "no_images"


async def _tick() -> None:
    """One pass through the pending queue."""
    global _NEXT_MANUAL_DISCOVERY_AT
    now_monotonic = time.monotonic()
    if now_monotonic >= _NEXT_MANUAL_DISCOVERY_AT:
        # Set the next slot before the browser work so a slow/failing JohnDrop
        # page never makes the 90-second worker repeatedly open new browsers.
        _NEXT_MANUAL_DISCOVERY_AT = now_monotonic + MANUAL_DISCOVERY_INTERVAL_S
        try:
            detected = await _discover_manual_johndrop_products()
            _WORKER_STATE["last_manual_discovery"] = datetime.now(timezone.utc).isoformat()
            _WORKER_STATE["last_manual_discovery_count"] = detected
        except Exception as exc:
            await add_log("warning", f"Monitor manual JohnDrop falhou: {str(exc)[:160]}")

    pending = await db.enrich_pending.find(
        {"status": "pending"}, {"_id": 0},
    ).sort("queued_at", 1).to_list(50)

    for item in pending:
        sku = item["sku"]
        product = await _find_in_bling(sku)
        if not product:
            # Not in Bling yet — increment attempts, will retry
            await db.enrich_pending.update_one(
                {"sku": sku},
                {"$set": {"last_check": datetime.now(timezone.utc).isoformat(),
                          "last_status": "not_in_bling"},
                 "$inc": {"attempts": 1}},
            )
            continue

        ready, reason = await _is_ready_for_enrichment(product)
        if not ready:
            await db.enrich_pending.update_one(
                {"sku": sku},
                {"$set": {"last_check": datetime.now(timezone.utc).isoformat(),
                          "last_status": "waiting_images",
                          "product_id": product.get("id")},
                 "$inc": {"attempts": 1}},
            )
            continue

        # Imagens chegaram (a "bagagem" pousou)! Mark as processing
        # and trigger full enrichment.
        await db.enrich_pending.update_one(
            {"sku": sku},
            {"$set": {"status": "processing",
                      "started_at": datetime.now(timezone.utc).isoformat(),
                      "ready_reason": reason}},
        )
        await add_log("info", f"Produto {sku} pronto no Bling ({reason}) — iniciando enriquecimento")

        try:
            # Manual JohnDrop registrations have no local raw record yet. The
            # native sync normally carries the original description into Bling,
            # so use it as the source for the same title/description/variation
            # analysis used by the robot-created products.
            raw_title, raw_description = _source_text_for_enrichment(item, product)
            result = await bling_enrichment.enrich_product_by_sku(
                sku,
                raw_title,
                raw_description,
                johndrop_id=item.get("johndrop_id"),
                cost=item.get("cost"),
                supplier_name=item.get("supplier_name"),
            )
            if result.get("ok"):
                await db.enrich_pending.update_one(
                    {"sku": sku},
                    {"$set": {
                        "status": "done",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "result": {
                            "product_id": result.get("product_id"),
                            "variations": result.get("variations_created", 0),
                        },
                    }},
                )
            else:
                await db.enrich_pending.update_one(
                    {"sku": sku},
                    {"$set": {
                        "status": "pending",  # retry next tick
                        "last_status": "enrich_failed",
                        "last_error": str(result.get("reason", "unknown")),
                        "last_check": datetime.now(timezone.utc).isoformat(),
                    }},
                )
        except Exception as e:
            await db.enrich_pending.update_one(
                {"sku": sku},
                {"$set": {
                    "status": "pending",
                    "last_status": "exception",
                    "last_error": str(e)[:200],
                    "last_check": datetime.now(timezone.utc).isoformat(),
                }},
            )

    # Mark items as giveup if too many attempts (avoid infinite retries)
    await db.enrich_pending.update_many(
        {"status": "pending", "attempts": {"$gte": MAX_ATTEMPTS}},
        {"$set": {"status": "giveup", "finished_at": datetime.now(timezone.utc).isoformat()}},
    )


async def _loop() -> None:
    _WORKER_STATE["running"] = True
    while _WORKER_STATE["running"]:
        try:
            await _tick()
        except Exception as e:
            await add_log("error", f"Worker enrich tick crashed: {e}")
        _WORKER_STATE["last_tick"] = datetime.now(timezone.utc).isoformat()
        await asyncio.sleep(POLL_INTERVAL_S)


def start_worker() -> None:
    if _WORKER_STATE.get("task") and not _WORKER_STATE["task"].done():
        return
    _WORKER_STATE["task"] = asyncio.create_task(_loop())


@router.get("/queue")
async def list_queue(status: Optional[str] = None, limit: int = 100) -> dict:
    q = {}
    if status:
        q["status"] = status
    cur = db.enrich_pending.find(q, {"_id": 0}).sort("queued_at", -1).limit(limit)
    items = await cur.to_list(limit)
    summary = {
        "pending": await db.enrich_pending.count_documents({"status": "pending"}),
        "processing": await db.enrich_pending.count_documents({"status": "processing"}),
        "done": await db.enrich_pending.count_documents({"status": "done"}),
        "giveup": await db.enrich_pending.count_documents({"status": "giveup"}),
    }
    return {
        "items": items,
        "summary": summary,
        "worker": {
            "running": bool(_WORKER_STATE.get("running")),
            "last_tick": _WORKER_STATE.get("last_tick"),
            "poll_interval_s": POLL_INTERVAL_S,
            "last_manual_discovery": _WORKER_STATE.get("last_manual_discovery"),
            "last_manual_discovery_count": _WORKER_STATE.get("last_manual_discovery_count", 0),
        },
    }


@router.post("/queue/{sku}/retry")
async def retry_item(sku: str) -> dict:
    r = await db.enrich_pending.update_one(
        {"sku": sku},
        {"$set": {"status": "pending", "attempts": 0, "last_error": None}},
    )
    if r.matched_count == 0:
        return {"ok": False, "reason": "sku não está na fila"}
    return {"ok": True}


@router.delete("/queue/{sku}")
async def remove_item(sku: str) -> dict:
    r = await db.enrich_pending.delete_one({"sku": sku})
    return {"ok": True, "deleted": r.deleted_count}


@router.post("/queue/tick-now")
async def trigger_tick() -> dict:
    """Force an immediate tick (useful for testing — don't wait 90s)."""
    asyncio.create_task(_tick())
    return {"ok": True, "triggered": True}
