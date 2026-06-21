"""Worker que detecta quando produtos da JohnDrop chegaram completos no Bling
e dispara o enriquecimento.

Fluxo:
  1. Robô JohnDrop cadastra produto → salva SKU em `enrich_pending` (status=pending)
  2. Worker roda a cada 90s, busca pendentes
  3. Para cada pendente: procura no Bling. Se encontra e produto tem imagens
     (sync da JohnDrop concluiu), dispara enriquecimento e marca status=done
  4. Se não encontrou ou está sem imagens, marca attempts+=1 e tenta na próxima rodada
  5. Após 30 tentativas (~45min), marca como giveup
"""
import asyncio
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter

import bling_service
import bling_enrichment
from db import db
from robot_service import add_log


router = APIRouter(prefix="/enrich", tags=["enrich-pending-worker"])

POLL_INTERVAL_S = 90  # check pending queue every 90s
MAX_ATTEMPTS = 80  # ~2h total wait (suficiente para JohnDrop concluir o sync)
MIN_IMAGES_REQUIRED = 1  # SOMENTE quando Bling tem ao menos 1 imagem
                          # (imagens chegam como "bagagem" do produto vindo da JohnDrop;
                          # sem imagens, o produto ainda está no ar — não enriquecemos)


_WORKER_STATE = {"running": False, "last_tick": None, "task": None}


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
            result = await bling_enrichment.enrich_product_by_sku(
                sku,
                item.get("raw_title") or "",
                item.get("raw_description") or "",
                johndrop_id=item.get("johndrop_id"),
                cost=item.get("cost"),
                images=item.get("images"),
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
