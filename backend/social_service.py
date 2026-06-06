"""Meta (Instagram + Facebook) credentials storage & validation.

Credentials are stored in MongoDB collection `social_credentials` as a single
document keyed by `provider="meta"`. The app secret and access token are
encrypted at rest using Fernet (key derived from MONGO_URL — already secret).
"""
import os
import base64
import hashlib
from typing import Optional

import httpx
from cryptography.fernet import Fernet
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import db
from robot_service import add_log

router = APIRouter(prefix="/social", tags=["social"])


def _fernet() -> Fernet:
    secret = os.environ.get("MONGO_URL", "fallback-key-must-be-set")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def _enc(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    return _fernet().encrypt(v.encode()).decode()


def _dec(v: Optional[str]) -> Optional[str]:
    if not v:
        return None
    try:
        return _fernet().decrypt(v.encode()).decode()
    except Exception:
        return None


class MetaCredentials(BaseModel):
    app_id: str
    app_secret: str
    page_access_token: str
    facebook_page_id: Optional[str] = None
    instagram_business_id: Optional[str] = None


@router.post("/meta/credentials")
async def save_meta_credentials(payload: MetaCredentials) -> dict:
    """Save Meta credentials (encrypted) to MongoDB."""
    _d = db
    doc = {
        "provider": "meta",
        "app_id": payload.app_id.strip(),
        "app_secret_enc": _enc(payload.app_secret.strip()),
        "page_access_token_enc": _enc(payload.page_access_token.strip()),
        "facebook_page_id": (payload.facebook_page_id or "").strip() or None,
        "instagram_business_id": (payload.instagram_business_id or "").strip() or None,
    }
    await db.social_credentials.update_one(
        {"provider": "meta"}, {"$set": doc}, upsert=True,
    )
    await add_log("info", "Credenciais Meta salvas com sucesso")
    return {"ok": True}


@router.get("/meta/credentials")
async def get_meta_credentials() -> dict:
    """Return masked Meta credentials (never returns secrets in clear)."""
    _d = db
    doc = await db.social_credentials.find_one({"provider": "meta"})
    if not doc:
        return {"configured": False}
    return {
        "configured": True,
        "app_id": doc.get("app_id"),
        "app_secret_masked": "••••••••" if doc.get("app_secret_enc") else None,
        "page_access_token_masked": "••••••••" if doc.get("page_access_token_enc") else None,
        "facebook_page_id": doc.get("facebook_page_id"),
        "instagram_business_id": doc.get("instagram_business_id"),
    }


@router.post("/meta/test")
async def test_meta_connection() -> dict:
    """Validate stored token by calling Meta /me endpoint. Also auto-detects
    facebook_page_id and instagram_business_id when possible."""
    _d = db
    doc = await db.social_credentials.find_one({"provider": "meta"})
    if not doc:
        raise HTTPException(400, "Credenciais não configuradas")
    token = _dec(doc.get("page_access_token_enc"))
    if not token:
        raise HTTPException(400, "Token inválido ou corrompido")

    async with httpx.AsyncClient(timeout=15) as cx:
        # 1. Validate token + get page info
        r = await cx.get(
            "https://graph.facebook.com/v23.0/me",
            params={"access_token": token, "fields": "id,name,category"},
        )
        if r.status_code >= 400:
            return {"ok": False, "error": r.json().get("error", {}).get("message", r.text[:200])}
        me = r.json()
        page_id = me.get("id")
        page_name = me.get("name")

        # 2. Get Instagram Business account linked to this page
        ig_id = None
        try:
            r2 = await cx.get(
                f"https://graph.facebook.com/v23.0/{page_id}",
                params={"access_token": token, "fields": "instagram_business_account"},
            )
            if r2.status_code < 400:
                ig_id = (r2.json().get("instagram_business_account") or {}).get("id")
        except Exception:
            pass

    # Persist discovered IDs
    update = {"facebook_page_id": page_id}
    if ig_id:
        update["instagram_business_id"] = ig_id
    await db.social_credentials.update_one({"provider": "meta"}, {"$set": update})

    return {
        "ok": True,
        "page_id": page_id,
        "page_name": page_name,
        "instagram_business_id": ig_id,
        "instagram_linked": bool(ig_id),
    }


async def get_meta_token_and_ids() -> Optional[dict]:
    """Internal helper for posting flows. Returns decrypted token + page/ig ids."""
    _d = db
    doc = await db.social_credentials.find_one({"provider": "meta"})
    if not doc:
        return None
    token = _dec(doc.get("page_access_token_enc"))
    if not token:
        return None
    return {
        "token": token,
        "app_id": doc.get("app_id"),
        "app_secret": _dec(doc.get("app_secret_enc")),
        "facebook_page_id": doc.get("facebook_page_id"),
        "instagram_business_id": doc.get("instagram_business_id"),
    }
