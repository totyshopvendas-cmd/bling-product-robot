"""Social Ad Scheduler.

Background worker that periodically scans `social_ad_scheduled` for drafts
whose publish_at <= now and triggers the publish flow. Single in-process loop
started by FastAPI on startup.

Recommended approach (per user choice): in-process asyncio task — simpler than
external cron and works as long as backend is up. Persistent state in MongoDB
ensures we don't double-publish across restarts (we mark `status=publishing`
before posting).
"""
import asyncio
import uuid
import os
from datetime import datetime, timezone
from typing import Optional, List
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import db
from robot_service import add_log
from social_service import get_meta_token_and_ids


router = APIRouter(prefix="/social", tags=["social-scheduler"])

# Brazil time = UTC-3 (no DST since 2019)
BR_TZ = ZoneInfo("America/Sao_Paulo")

# Default peak times for Brazilian audience
DEFAULT_PEAK_HOURS = [12, 18, 21]


# ---------------------------------------------------------------- helpers

def _now_br() -> datetime:
    return datetime.now(BR_TZ)


def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


async def _publish_draft(draft_id: str, image_url: str, caption: str) -> dict:
    """Re-implement the publish-to-meta flow here (avoid HTTP self-call)."""
    creds = await get_meta_token_and_ids()
    if not creds or not creds.get("token"):
        return {"ok": False, "error": "Credenciais Meta ausentes"}

    token = creds["token"]
    page_id = creds.get("facebook_page_id")
    ig_id = creds.get("instagram_business_id")

    result: dict = {"instagram": None, "facebook": None}
    async with httpx.AsyncClient(timeout=60) as cx:
        if ig_id:
            try:
                r1 = await cx.post(
                    f"https://graph.facebook.com/v23.0/{ig_id}/media",
                    params={"access_token": token, "image_url": image_url, "caption": caption},
                )
                if r1.status_code >= 400:
                    result["instagram"] = {"ok": False, "error": r1.json().get("error", {}).get("message", r1.text[:200])}
                else:
                    cid = r1.json().get("id")
                    r2 = await cx.post(
                        f"https://graph.facebook.com/v23.0/{ig_id}/media_publish",
                        params={"access_token": token, "creation_id": cid},
                    )
                    if r2.status_code >= 400:
                        result["instagram"] = {"ok": False, "error": r2.json().get("error", {}).get("message", r2.text[:200])}
                    else:
                        result["instagram"] = {"ok": True, "post_id": r2.json().get("id")}
            except Exception as e:
                result["instagram"] = {"ok": False, "error": str(e)}
        else:
            result["instagram"] = {"ok": False, "error": "IG Business ID não configurado"}

        if page_id:
            try:
                rf = await cx.post(
                    f"https://graph.facebook.com/v23.0/{page_id}/photos",
                    params={"access_token": token, "url": image_url, "caption": caption},
                )
                if rf.status_code >= 400:
                    result["facebook"] = {"ok": False, "error": rf.json().get("error", {}).get("message", rf.text[:200])}
                else:
                    body = rf.json()
                    result["facebook"] = {"ok": True, "post_id": body.get("post_id") or body.get("id")}
            except Exception as e:
                result["facebook"] = {"ok": False, "error": str(e)}
        else:
            result["facebook"] = {"ok": False, "error": "Facebook Page ID não configurado"}

    any_ok = (result["instagram"] or {}).get("ok") or (result["facebook"] or {}).get("ok")
    result["ok"] = bool(any_ok)
    return result


# ---------------------------------------------------------------- API


class ScheduleRequest(BaseModel):
    draft_id: str
    publish_at_iso: Optional[str] = None  # explicit UTC ISO time
    # OR: next available peak slot today/tomorrow if no time given


class BulkScheduleRequest(BaseModel):
    draft_ids: List[str]
    peak_hours: Optional[List[int]] = None  # default [12, 18, 21]
    days_ahead: int = 0  # 0 = today (skipping past slots), 1 = today + tomorrow, etc.


def _next_peak_slots(peak_hours: List[int], days_ahead: int = 0) -> List[datetime]:
    """Return upcoming BR-time slots: today's remaining peaks + N additional days.
    Used to spread bulk-scheduled posts evenly across peak hours."""
    now = _now_br()
    slots: List[datetime] = []
    base = now.replace(minute=0, second=0, microsecond=0)
    for d in range(days_ahead + 1):
        day = base + timedelta(days=d)
        for h in sorted(peak_hours):
            t = day.replace(hour=h)
            if t > now:
                slots.append(t)
    return slots


from datetime import timedelta


@router.post("/ad/schedule")
async def schedule_ad(payload: ScheduleRequest) -> dict:
    """Schedule a single draft for publishing at publish_at_iso (or next peak)."""
    draft = await db.social_ad_drafts.find_one({"id": payload.draft_id})
    if not draft:
        raise HTTPException(404, "draft não encontrado")

    if payload.publish_at_iso:
        try:
            publish_at = datetime.fromisoformat(payload.publish_at_iso.replace("Z", "+00:00"))
            if publish_at.tzinfo is None:
                publish_at = publish_at.replace(tzinfo=BR_TZ)
        except Exception:
            raise HTTPException(400, "publish_at_iso inválido (use ISO 8601 com timezone)")
    else:
        slots = _next_peak_slots(DEFAULT_PEAK_HOURS, days_ahead=1)
        if not slots:
            raise HTTPException(500, "nenhum slot futuro disponível")
        publish_at = slots[0]

    item_id = uuid.uuid4().hex[:16]
    doc = {
        "id": item_id,
        "draft_id": payload.draft_id,
        "publish_at_utc": _utc_iso(publish_at),
        "publish_at_local": publish_at.isoformat(),
        "status": "pending",
        "created_at": _utc_iso(_now_br()),
        "attempts": 0,
    }
    await db.social_ad_scheduled.insert_one(doc)
    await add_log("info", f"Anúncio agendado: draft={payload.draft_id} → {publish_at.isoformat()}")
    return {"ok": True, "id": item_id, "publish_at": publish_at.isoformat()}


@router.post("/ad/schedule/bulk")
async def schedule_bulk(payload: BulkScheduleRequest) -> dict:
    """Schedule many drafts spread across upcoming peak slots.

    Example: 6 drafts + peak_hours=[12,18,21] + days_ahead=1 → 3 slots today + 3 tomorrow."""
    if not payload.draft_ids:
        raise HTTPException(400, "draft_ids vazio")
    peaks = payload.peak_hours or DEFAULT_PEAK_HOURS
    slots = _next_peak_slots(peaks, days_ahead=payload.days_ahead)
    if not slots:
        # extend to tomorrow if no slots remain today
        slots = _next_peak_slots(peaks, days_ahead=max(payload.days_ahead, 1))
    if not slots:
        raise HTTPException(500, "sem slots futuros")

    scheduled: List[dict] = []
    docs: List[dict] = []
    for i, did in enumerate(payload.draft_ids):
        slot = slots[i % len(slots)]
        # If we ran out of slots, push subsequent ones to next day
        if i >= len(slots):
            slot = slot + timedelta(days=(i // len(slots)))
        item_id = uuid.uuid4().hex[:16]
        docs.append({
            "id": item_id,
            "draft_id": did,
            "publish_at_utc": _utc_iso(slot),
            "publish_at_local": slot.isoformat(),
            "status": "pending",
            "created_at": _utc_iso(_now_br()),
            "attempts": 0,
        })
        scheduled.append({"id": item_id, "draft_id": did, "publish_at": slot.isoformat()})
    if docs:
        await db.social_ad_scheduled.insert_many(docs)
    await add_log("success", f"Agendamento em lote: {len(docs)} anúncios em slots de pico")
    return {"ok": True, "scheduled": scheduled, "total": len(docs)}


@router.get("/ad/schedule")
async def list_scheduled(status: Optional[str] = None, limit: int = 100) -> dict:
    q: dict = {}
    if status:
        q["status"] = status
    cur = db.social_ad_scheduled.find(q, {"_id": 0}).sort("publish_at_utc", 1).limit(limit)
    items = await cur.to_list(limit)
    # Hydrate with draft preview info
    for it in items:
        draft = await db.social_ad_drafts.find_one(
            {"id": it.get("draft_id")},
            {"_id": 0, "headline": 1, "image_url": 1, "product_name": 1},
        )
        if draft:
            it["preview"] = draft
    return {"items": items}


@router.delete("/ad/schedule/{item_id}")
async def cancel_scheduled(item_id: str) -> dict:
    r = await db.social_ad_scheduled.update_one(
        {"id": item_id, "status": "pending"},
        {"$set": {"status": "cancelled", "cancelled_at": _utc_iso(_now_br())}},
    )
    if r.matched_count == 0:
        raise HTTPException(404, "não encontrado ou já processado")
    return {"ok": True}


# ---------------------------------------------------------------- worker

_WORKER_STATE = {"running": False, "last_tick": None, "task": None}


async def _scheduler_tick() -> None:
    """Check for due items and publish them. Idempotent: marks status=publishing
    before posting; on failure increments attempts (max 3, then mark failed)."""
    now_utc = datetime.now(timezone.utc).isoformat()
    due = await db.social_ad_scheduled.find(
        {"status": "pending", "publish_at_utc": {"$lte": now_utc}},
        {"_id": 0},
    ).to_list(20)
    for item in due:
        item_id = item["id"]
        # Atomic claim — only one tick processes a given item
        claim = await db.social_ad_scheduled.update_one(
            {"id": item_id, "status": "pending"},
            {"$set": {"status": "publishing", "started_at": now_utc}},
        )
        if claim.matched_count == 0:
            continue
        draft = await db.social_ad_drafts.find_one({"id": item["draft_id"]})
        if not draft:
            await db.social_ad_scheduled.update_one(
                {"id": item_id},
                {"$set": {"status": "failed", "error": "draft removido"}},
            )
            continue
        # Build absolute URL if needed
        image_url = draft.get("image_url") or ""
        if not image_url.startswith("http"):
            from social_ad_service import _public_asset_url
            asset_id = draft.get("asset_id")
            base = os.environ.get("APP_BASE_URL", "").rstrip("/")
            image_url = f"{base}/api/social/ad/asset/{asset_id}.png" if (base and asset_id) else image_url
        caption = draft.get("caption") or ""

        result = await _publish_draft(item["draft_id"], image_url, caption)
        attempts = item.get("attempts", 0) + 1
        if result.get("ok"):
            await db.social_ad_scheduled.update_one(
                {"id": item_id},
                {"$set": {"status": "published", "result": result, "published_at": _utc_iso(_now_br()), "attempts": attempts}},
            )
            await db.social_ad_drafts.update_one(
                {"id": item["draft_id"]},
                {"$set": {"status": "published", "publish_result": result, "published_at": _utc_iso(_now_br())}},
            )
            await add_log("success", f"Agendado publicado: item={item_id}")
        else:
            if attempts >= 3:
                final = "failed"
            else:
                final = "pending"  # retry next tick
            await db.social_ad_scheduled.update_one(
                {"id": item_id},
                {"$set": {"status": final, "last_error": result, "attempts": attempts}},
            )
            await add_log("warning", f"Agendado falhou (tentativa {attempts}/3): item={item_id}")


async def _scheduler_loop() -> None:
    _WORKER_STATE["running"] = True
    while _WORKER_STATE["running"]:
        try:
            await _scheduler_tick()
        except Exception as e:
            await add_log("error", f"Scheduler tick crashed: {e}")
        _WORKER_STATE["last_tick"] = datetime.now(timezone.utc).isoformat()
        await asyncio.sleep(60)


def start_scheduler() -> None:
    """Called from FastAPI startup. Idempotent."""
    if _WORKER_STATE.get("task") and not _WORKER_STATE["task"].done():
        return
    _WORKER_STATE["task"] = asyncio.create_task(_scheduler_loop())


@router.get("/ad/scheduler/status")
async def scheduler_status() -> dict:
    return {
        "running": bool(_WORKER_STATE.get("running")),
        "last_tick": _WORKER_STATE.get("last_tick"),
        "now_br": _now_br().isoformat(),
        "default_peaks_br": DEFAULT_PEAK_HOURS,
    }
