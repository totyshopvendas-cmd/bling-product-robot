"""Pinterest API v5 integration (image pins only).

User flow:
  1. User creates a Pinterest Business App at developers.pinterest.com
     and generates an Access Token (Sandbox or Production).
  2. User pastes the token + (optionally) the board name in TotyShop Settings.
  3. When publishing an ad, we call POST /v5/pins with the public image URL +
     title + description + board_id.

Notes:
- We DO NOT implement full OAuth here. Pinterest's "Generate Access Token"
  feature in the developer console produces a long-lived token directly,
  which is simpler and matches the user's existing manual-token flow with
  Meta. If full OAuth is added later, drop in a /pinterest/callback route.
- Tokens are stored encrypted in MongoDB (same Fernet helper as social_service).
"""
import os
import httpx
from typing import Optional, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import db
from robot_service import add_log
from social_service import _enc, _dec  # reuse Fernet helpers


router = APIRouter(prefix="/social", tags=["social-pinterest"])

PINTEREST_API = "https://api.pinterest.com/v5"


class PinterestCredentials(BaseModel):
    access_token: str
    default_board_id: Optional[str] = None


@router.post("/pinterest/credentials")
async def save_pinterest_credentials(payload: PinterestCredentials) -> dict:
    """Save Pinterest access token (encrypted) + optional default board."""
    doc = {
        "provider": "pinterest",
        "access_token_enc": _enc(payload.access_token.strip()),
        "default_board_id": (payload.default_board_id or "").strip() or None,
    }
    await db.social_credentials.update_one(
        {"provider": "pinterest"}, {"$set": doc}, upsert=True,
    )
    await add_log("info", "Credenciais Pinterest salvas com sucesso")
    return {"ok": True}


@router.get("/pinterest/credentials")
async def get_pinterest_credentials() -> dict:
    doc = await db.social_credentials.find_one({"provider": "pinterest"})
    if not doc:
        return {"configured": False}
    return {
        "configured": True,
        "access_token_masked": "••••••••" if doc.get("access_token_enc") else None,
        "default_board_id": doc.get("default_board_id"),
    }


async def _get_pinterest_token() -> Optional[str]:
    doc = await db.social_credentials.find_one({"provider": "pinterest"})
    if not doc:
        return None
    return _dec(doc.get("access_token_enc"))


@router.post("/pinterest/test")
async def test_pinterest_connection() -> dict:
    """Verify token by calling /v5/user_account."""
    token = await _get_pinterest_token()
    if not token:
        raise HTTPException(400, "Credenciais Pinterest não configuradas")
    async with httpx.AsyncClient(timeout=15) as cx:
        r = await cx.get(
            f"{PINTEREST_API}/user_account",
            headers={"Authorization": f"Bearer {token}"},
        )
        if r.status_code >= 400:
            return {"ok": False, "error": r.json().get("message", r.text[:200])}
        body = r.json()
    return {
        "ok": True,
        "username": body.get("username"),
        "account_type": body.get("account_type"),
    }


@router.get("/pinterest/boards")
async def list_pinterest_boards() -> dict:
    """List user's boards. Used by the UI to pick a board for posting."""
    token = await _get_pinterest_token()
    if not token:
        raise HTTPException(400, "Credenciais Pinterest não configuradas")
    boards: List[dict] = []
    async with httpx.AsyncClient(timeout=20) as cx:
        bookmark: Optional[str] = None
        for _ in range(5):  # max 5 pages
            params = {"page_size": 100}
            if bookmark:
                params["bookmark"] = bookmark
            r = await cx.get(
                f"{PINTEREST_API}/boards",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            if r.status_code >= 400:
                raise HTTPException(r.status_code, r.json().get("message", r.text[:200]))
            body = r.json()
            for b in (body.get("items") or []):
                boards.append({"id": b.get("id"), "name": b.get("name")})
            bookmark = body.get("bookmark")
            if not bookmark:
                break
    return {"items": boards}


class PinRequest(BaseModel):
    draft_id: Optional[str] = None
    board_id: Optional[str] = None
    image_url: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    link: Optional[str] = None  # optional outbound link


@router.post("/pinterest/pin")
async def create_pin(payload: PinRequest) -> dict:
    """Create a pin. Either provide draft_id (uses saved ad image + caption) or
    explicit image_url + title + description.

    The board_id falls back to the configured default_board_id when omitted.
    """
    token = await _get_pinterest_token()
    if not token:
        raise HTTPException(400, "Credenciais Pinterest não configuradas")

    creds_doc = await db.social_credentials.find_one({"provider": "pinterest"})
    board_id = payload.board_id or (creds_doc or {}).get("default_board_id")
    if not board_id:
        raise HTTPException(400, "board_id ausente — selecione um board ou configure um padrão")

    image_url = payload.image_url
    title = payload.title
    description = payload.description

    if payload.draft_id:
        draft = await db.social_ad_drafts.find_one({"id": payload.draft_id})
        if not draft:
            raise HTTPException(404, "draft não encontrado")
        image_url = image_url or draft.get("image_url")
        title = title or (draft.get("headline") or draft.get("product_name") or "")[:100]
        description = description or draft.get("caption") or ""

    if not image_url:
        raise HTTPException(400, "image_url obrigatório")
    title = (title or "")[:100]  # Pinterest title max ~100 chars
    description = (description or "")[:500]

    body = {
        "board_id": board_id,
        "title": title,
        "description": description,
        "media_source": {"source_type": "image_url", "url": image_url},
    }
    if payload.link:
        body["link"] = payload.link

    async with httpx.AsyncClient(timeout=30) as cx:
        r = await cx.post(
            f"{PINTEREST_API}/pins",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
        )
        if r.status_code >= 400:
            err_msg = r.json().get("message", r.text[:200]) if r.headers.get("content-type", "").startswith("application/json") else r.text[:200]
            await add_log("warning", f"Pinterest pin falhou: {err_msg}")
            return {"ok": False, "error": err_msg}
        result = r.json()

    await add_log("success", f"Pin Pinterest criado: {result.get('id')}")
    # Update draft if this was an ad pin
    if payload.draft_id:
        await db.social_ad_drafts.update_one(
            {"id": payload.draft_id},
            {"$set": {"pinterest_pin_id": result.get("id")}},
        )
    return {"ok": True, "pin_id": result.get("id"), "url": result.get("permalink") or f"https://pinterest.com/pin/{result.get('id')}"}
