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
        # 1. Validate token + get page/user info. Use minimal fields to support both
        # User Tokens and Page Tokens (the `category` field doesn't exist on user tokens).
        r = await cx.get(
            "https://graph.facebook.com/v23.0/me",
            params={"access_token": token, "fields": "id,name"},
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


@router.post("/meta/exchange-token")
async def exchange_to_long_lived_token() -> dict:
    """Convert a short-lived User Token into a NEVER-EXPIRING Page Access Token.

    Three steps (per Meta docs):
      1. Exchange short-lived USER token for long-lived USER token (60-day)
      2. List the user's pages — get the matching Page's access_token (never expires
         once derived from a long-lived user token)
      3. Save the Page token into the credentials doc + re-fetch instagram_business_id

    The user pastes the short-lived token in the same field as before (page_access_token).
    On first save it works for ~1 hour, but if they click "Tornar Vitalício" it gets
    upgraded permanently.
    """
    doc = await db.social_credentials.find_one({"provider": "meta"})
    if not doc:
        raise HTTPException(400, "Credenciais não configuradas — salve App ID + Secret + Token primeiro")
    app_id = doc.get("app_id")
    app_secret = _dec(doc.get("app_secret_enc"))
    current_token = _dec(doc.get("page_access_token_enc"))
    if not (app_id and app_secret and current_token):
        raise HTTPException(400, "App ID, App Secret ou Token ausente/corrompido")

    async with httpx.AsyncClient(timeout=20) as cx:
        # Step 1: exchange short-lived → long-lived (60 days). Works for USER tokens.
        # If the user already pasted a Page Token, this returns the same token (idempotent).
        r1 = await cx.get(
            "https://graph.facebook.com/v23.0/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": current_token,
            },
        )
        if r1.status_code >= 400:
            err = (r1.json().get("error") or {}).get("message", r1.text[:200])
            raise HTTPException(400, f"Falha ao trocar token: {err}")
        long_user_token = r1.json().get("access_token")
        if not long_user_token:
            raise HTTPException(400, "Resposta do Meta sem access_token")

        # Step 2: List user's pages and pick the one matching our stored facebook_page_id
        # (or the first one if not set).
        r2 = await cx.get(
            "https://graph.facebook.com/v23.0/me/accounts",
            params={"access_token": long_user_token},
        )
        if r2.status_code >= 400:
            err = (r2.json().get("error") or {}).get("message", r2.text[:200])
            raise HTTPException(400, f"Falha ao listar páginas: {err}")
        pages = (r2.json() or {}).get("data") or []
        if not pages:
            raise HTTPException(400, "Nenhuma página encontrada — verifique permissões pages_show_list, pages_manage_posts, pages_read_engagement")

        target_page_id = doc.get("facebook_page_id")
        match = None
        for p in pages:
            if str(p.get("id")) == str(target_page_id):
                match = p
                break
        if not match:
            match = pages[0]  # fallback to first page

        page_token = match.get("access_token")
        page_id = match.get("id")
        page_name = match.get("name")
        if not page_token:
            raise HTTPException(400, "Página não retornou access_token (faltam permissões?)")

        # Step 3: try to discover the IG Business id linked to this page
        ig_id = None
        try:
            r3 = await cx.get(
                f"https://graph.facebook.com/v23.0/{page_id}",
                params={"access_token": page_token, "fields": "instagram_business_account"},
            )
            if r3.status_code < 400:
                ig_id = (r3.json().get("instagram_business_account") or {}).get("id")
        except Exception:
            pass

    # Persist
    update = {
        "page_access_token_enc": _enc(page_token),
        "facebook_page_id": page_id,
    }
    if ig_id:
        update["instagram_business_id"] = ig_id
    await db.social_credentials.update_one({"provider": "meta"}, {"$set": update})

    await add_log("success", f"Token Meta convertido para vitalício (página {page_name})")
    return {
        "ok": True,
        "page_id": page_id,
        "page_name": page_name,
        "instagram_business_id": ig_id,
        "instagram_linked": bool(ig_id),
        "note": "Token vitalício salvo. Não expira mais — desde que você não revogue manualmente em Meta Business Suite.",
    }
