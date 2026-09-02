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

# Serializes token refreshes — Bling refresh tokens são de uso único; dois
# refreshes concorrentes invalidam o token e derrubam a conexão inteira.
_refresh_lock = asyncio.Lock()


BLING_CLIENT_ID = os.environ["BLING_CLIENT_ID"]
BLING_CLIENT_SECRET = os.environ["BLING_CLIENT_SECRET"]
BLING_AUTHORIZE_URL = os.environ["BLING_AUTHORIZE_URL"]
BLING_TOKEN_URL = os.environ["BLING_TOKEN_URL"]
BLING_API_BASE_URL = os.environ["BLING_API_BASE_URL"]
APP_BASE_URL = os.environ["APP_BASE_URL"]
APP_SECRET = os.environ["APP_SECRET"]

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
        "needs_reconnect": False,
        "last_refresh_error": None,
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


async def _mark_needs_reconnect(reason: str) -> None:
    """NUNCA apaga o token — apenas marca que o usuário precisa reconectar."""
    await db.bling_tokens.update_one(
        {"account_id": ACCOUNT_ID},
        {"$set": {
            "needs_reconnect": True,
            "last_refresh_error": (reason or "")[:300],
            "updated_at": _now().isoformat(),
        }},
    )


async def get_valid_access_token() -> Tuple[str, str]:
    doc = await get_token_doc()
    if not doc:
        raise HTTPException(status_code=400, detail="Bling não está conectado")
    if doc.get("needs_reconnect"):
        raise HTTPException(status_code=400, detail="Token Bling expirou — reconecte em Configurações")
    expires_at = datetime.fromisoformat(doc["expires_at"])
    if expires_at - timedelta(seconds=60) > _now():
        return doc["access_token"], doc.get("token_type", "Bearer")
    async with _refresh_lock:
        # Re-lê: outra coroutine pode ter renovado enquanto esperávamos o lock
        doc = await get_token_doc()
        if not doc:
            raise HTTPException(status_code=400, detail="Bling não está conectado")
        expires_at = datetime.fromisoformat(doc["expires_at"])
        if expires_at - timedelta(seconds=60) > _now():
            return doc["access_token"], doc.get("token_type", "Bearer")
        last_err = ""
        for attempt in range(3):
            try:
                data = await refresh_tokens(doc["refresh_token"])
                await save_tokens(data)
                return data["access_token"], data.get("token_type", "Bearer")
            except HTTPException as he:
                last_err = str(he.detail)
                if "invalid_grant" in last_err.lower():
                    # Refresh token definitivamente inválido — marca, não apaga
                    await _mark_needs_reconnect(last_err)
                    raise HTTPException(status_code=400, detail="Refresh token Bling expirou — reconecte em Configurações")
            except Exception as e:
                last_err = str(e)
            await asyncio.sleep(2.0 * (attempt + 1))
        # Falha transitória (rede/5xx): mantém o token salvo e tenta na próxima chamada
        raise HTTPException(status_code=502, detail=f"Bling refresh temporariamente indisponível: {last_err[:200]}")


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
        # Access token revogado — força UM refresh e repete a chamada.
        # Jamais apaga o token aqui: 401 transitório não é desconexão.
        async with _refresh_lock:
            doc = await get_token_doc()
            if not doc or doc.get("needs_reconnect"):
                raise HTTPException(status_code=401, detail="Bling 401 — reconecte em Configurações")
            try:
                data = await refresh_tokens(doc["refresh_token"])
                await save_tokens(data)
            except HTTPException as he:
                if "invalid_grant" in str(he.detail).lower():
                    await _mark_needs_reconnect(str(he.detail))
                raise HTTPException(status_code=401, detail="Bling 401 — reconecte em Configurações")
            except Exception:
                raise HTTPException(status_code=401, detail="Bling 401 — falha ao renovar token")
        async with _bling_rate_limit:
            access_token, token_type = await get_valid_access_token()
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
                resp = await client.request(method, url, params=params, json=json, headers={
                    "Authorization": f"{token_type} {access_token}",
                    "Accept": "application/json",
                })
            await asyncio.sleep(0.5)
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
    if doc.get("needs_reconnect"):
        return {
            "connected": False,
            "needs_reconnect": True,
            "last_refresh_error": doc.get("last_refresh_error"),
            "updated_at": doc.get("updated_at"),
        }
    return {
        "connected": True,
        "expires_at": doc["expires_at"],
        "scope": doc.get("scope"),
        "updated_at": doc.get("updated_at"),
    }
