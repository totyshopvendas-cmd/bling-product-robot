"""Bling API v3 OAuth 2.0 integration."""
import os
from datetime import datetime, timezone, timedelta
from typing import Tuple, Optional
import asyncio
import base64
import httpx
from fastapi import HTTPException
from itsdangerous import URLSafeSerializer, BadSignature

from db import db


# Global semaphore to serialize Bling API calls (Bling rate limit = 3 req/sec)
_bling_rate_limit = asyncio.Semaphore(1)


BLING_CLIENT_ID = os.environ.get("BLING_CLIENT_ID", "").strip()
BLING_CLIENT_SECRET = os.environ.get("BLING_CLIENT_SECRET", "").strip()
BLING_AUTHORIZE_URL = os.environ.get("BLING_AUTHORIZE_URL", "https://bling.com.br/oauth/authorize")
BLING_TOKEN_URL = os.environ.get("BLING_TOKEN_URL", "https://bling.com.br/oauth/token")
BLING_API_BASE_URL = os.environ.get("BLING_API_BASE_URL", "https://api.bling.com.br/b/api/v3")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "").strip()
APP_SECRET = os.environ.get("APP_SECRET", "").strip()

REDIRECT_URI = f"{APP_BASE_URL}/api/bling/callback"
ACCOUNT_ID = "default"  # single-account MVP


def _now():
    return datetime.now(timezone.utc)


def make_state(payload: dict) -> str:
    s = URLSafeSerializer(APP_SECRET, salt="bling-oauth-state")
    return s.dumps(payload)


def parse_state(token: str) -> dict:
    s = URLSafeSerializer(APP_SECRET, salt="bling-oauth-state")
    try:
        return s.loads(token)
    except BadSignature:
        raise HTTPException(status_code=400, detail="State inválido")


def build_authorize_url(next_path: str = "/configuracoes") -> str:
    from urllib.parse import urlencode
    state = make_state({"next": next_path, "ts": _now().isoformat()})
    params = {
        "response_type": "code",
        "client_id": BLING_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "state": state,
    }
    return f"{BLING_AUTHORIZE_URL}?{urlencode(params)}"


def _basic_auth_header() -> str:
    raw = f"{BLING_CLIENT_ID}:{BLING_CLIENT_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode()


async def exchange_code(code: str) -> dict:
    """Exchange authorization code for tokens. Bling v3 requires Basic auth header."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            BLING_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": _basic_auth_header(),
                "Accept": "application/json",
            },
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=f"Bling token exchange failed: {resp.text}")
    return resp.json()


async def refresh_tokens(refresh_token: str) -> dict:
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            BLING_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": _basic_auth_header(),
                "Accept": "application/json",
            },
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=f"Bling refresh failed: {resp.text}")
    return resp.json()


async def save_tokens(token_data: dict) -> None:
    now = _now()
    expires_in = int(token_data.get("expires_in", 21600))
    expires_at = now + timedelta(seconds=expires_in)
    doc = {
        "account_id": ACCOUNT_ID,
        "access_token": token_data["access_token"],
        "refresh_token": token_data["refresh_token"],
        "token_type": token_data.get("token_type", "Bearer"),
        "scope": token_data.get("scope"),
        "expires_at": expires_at.isoformat(),
        "updated_at": now.isoformat(),
    }
    await db.bling_tokens.update_one(
        {"account_id": ACCOUNT_ID},
        {"$set": doc, "$setOnInsert": {"created_at": now.isoformat()}},
        upsert=True,
    )


async def get_token_doc() -> Optional[dict]:
    return await db.bling_tokens.find_one({"account_id": ACCOUNT_ID}, {"_id": 0})


async def disconnect():
    await db.bling_tokens.delete_one({"account_id": ACCOUNT_ID})


async def get_valid_access_token() -> Tuple[str, str]:
    doc = await get_token_doc()
    if not doc:
        raise HTTPException(status_code=400, detail="Bling não está conectado")
    expires_at = datetime.fromisoformat(doc["expires_at"])
    if expires_at - timedelta(seconds=60) > _now():
        return doc["access_token"], doc.get("token_type", "Bearer")
    
    # Try to refresh token without deleting it on failure
    try:
        data = await refresh_tokens(doc["refresh_token"])
        await save_tokens(data)
        return data["access_token"], data.get("token_type", "Bearer")
    except HTTPException as he:
        # Token refresh failed — preserve token in DB for manual reconnection
        raise HTTPException(
            status_code=400,
            detail="Token Bling expirado e não foi possível renovar — vá em Configurações → Bling e reconecte"
        )
    except Exception as e:
        # Unexpected error — preserve token in DB for manual reconnection
        raise HTTPException(
            status_code=400,
            detail=f"Erro ao renovar token Bling: {str(e)[:100]} — reconecte em Configurações"
        )


async def bling_request(method: str, path: str, params=None, json=None) -> httpx.Response:
    """Throttled Bling request — caps at 2 requests/second to stay under Bling's 3 req/sec limit."""
    async with _bling_rate_limit:
        access_token, token_type = await get_valid_access_token()
        url = f"{BLING_API_BASE_URL}{path}"
        headers = {
            "Authorization": f"{token_type} {access_token}",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
            resp = await client.request(method, url, params=params, json=json, headers=headers)
        # Enforce ~500ms between Bling requests
        await asyncio.sleep(0.5)
    if resp.status_code == 401:
        await disconnect()
        raise HTTPException(status_code=401, detail="Bling 401 — reconecte")
    # Bling sometimes returns 429 even with our throttle — retry once after 2s
    if resp.status_code == 429:
        await asyncio.sleep(2.0)
        async with _bling_rate_limit:
            access_token, token_type = await get_valid_access_token()
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
                resp = await client.request(method, url, params=params, json=json, headers={
                    "Authorization": f"{token_type} {access_token}",
                    "Accept": "application/json",
                })
            await asyncio.sleep(0.5)
    return resp


async def status() -> dict:
    doc = await get_token_doc()
    if not doc:
        return {"connected": False}
    return {
        "connected": True,
        "expires_at": doc["expires_at"],
        "scope": doc.get("scope"),
        "updated_at": doc.get("updated_at"),
    }
