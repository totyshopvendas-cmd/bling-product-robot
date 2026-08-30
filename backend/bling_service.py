"""Bling API v3 OAuth 2.0 integration.

Official endpoints (https://developer.bling.com.br/aplicativos):

    GET  https://www.bling.com.br/Api/v3/oauth/authorize
    POST https://www.bling.com.br/Api/v3/oauth/token   (also accepted on api.bling.com.br)
    API  https://api.bling.com.br/Api/v3

JWT is required going forward — every token and resource call sends ``enable-jwt: 1``.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request
from itsdangerous import BadSignature, URLSafeSerializer

from db import db
import secrets_store

logger = logging.getLogger(__name__)

# Serialize Bling API calls. Documented limit is 3 req/sec; we stay well under.
_bling_rate_limit = asyncio.Semaphore(1)

DEFAULT_AUTHORIZE_URL = "https://www.bling.com.br/Api/v3/oauth/authorize"
DEFAULT_TOKEN_URL = "https://www.bling.com.br/Api/v3/oauth/token"
DEFAULT_API_BASE_URL = "https://api.bling.com.br/Api/v3"
# Fallbacks if the primary token host rejects the request (Bling docs list both).
TOKEN_URL_FALLBACKS = (
    "https://www.bling.com.br/Api/v3/oauth/token",
    "https://api.bling.com.br/Api/v3/oauth/token",
    "https://api.bling.com.br/oauth/token",
)

ACCOUNT_ID = "default"
BLING_APP_SETTINGS_KEY = "bling_app"

# Kept as aliases so older imports / tests still resolve. Values are read live
# from the environment so a restart is not required after editing .env.
def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def BLING_CLIENT_ID() -> str:  # noqa: N802 — public alias used by tests
    return _env("BLING_CLIENT_ID")


def BLING_CLIENT_SECRET() -> str:  # noqa: N802
    return _env("BLING_CLIENT_SECRET")


BLING_AUTHORIZE_URL = DEFAULT_AUTHORIZE_URL
BLING_TOKEN_URL = DEFAULT_TOKEN_URL
BLING_API_BASE_URL = DEFAULT_API_BASE_URL
APP_BASE_URL = ""
APP_SECRET = ""
REDIRECT_URI = "/api/bling/callback"


def authorize_url() -> str:
    return _env("BLING_AUTHORIZE_URL", DEFAULT_AUTHORIZE_URL)


def token_url() -> str:
    return _env("BLING_TOKEN_URL", DEFAULT_TOKEN_URL)


def api_base_url() -> str:
    return _env("BLING_API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/")


def _app_secret() -> str:
    secret = _env("APP_SECRET")
    if secret:
        return secret
    cid, csec = BLING_CLIENT_ID(), BLING_CLIENT_SECRET()
    if cid or csec:
        return f"totyshop-bling-{cid}-{csec}"
    return "totyshop-bling-dev-secret"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def public_base_url(request: Optional[Request] = None, origin: Optional[str] = None) -> str:
    """Public origin Bling will redirect to after the user authorizes.

    Priority:
      1. Explicit ``origin`` (frontend sends window.location.origin)
      2. APP_BASE_URL env
      3. Forwarded host on the incoming request
    """
    if origin:
        value = origin.strip().rstrip("/")
        if value:
            return value
    env = _env("APP_BASE_URL").rstrip("/")
    if env:
        return env
    if request is not None:
        proto = (
            request.headers.get("x-forwarded-proto")
            or (request.url.scheme if request.url else None)
            or "https"
        )
        host = (
            request.headers.get("x-forwarded-host")
            or request.headers.get("host")
            or ""
        )
        host = host.split(",")[0].strip()
        if host:
            return f"{proto}://{host}".rstrip("/")
    return ""


def redirect_uri(app_base: str = "") -> str:
    base = (app_base or _env("APP_BASE_URL")).strip().rstrip("/")
    return f"{base}/api/bling/callback"


def make_state(payload: dict) -> str:
    s = URLSafeSerializer(_app_secret(), salt="bling-oauth-state")
    return s.dumps(payload)


def parse_state(token: str) -> dict:
    s = URLSafeSerializer(_app_secret(), salt="bling-oauth-state")
    try:
        return s.loads(token)
    except BadSignature:
        raise HTTPException(status_code=400, detail="State inválido — tente conectar de novo")


async def _load_stored_creds() -> Tuple[str, str]:
    try:
        doc = await db.settings.find_one({"key": BLING_APP_SETTINGS_KEY}, {"_id": 0})
    except Exception as exc:
        logger.warning("não foi possível ler credenciais Bling no Mongo: %s", exc)
        doc = None
    value = (doc or {}).get("value") or {}
    cid = (value.get("client_id") or "").strip()
    secret = (value.get("client_secret") or "").strip()
    if cid and secret:
        return cid, secret
    stored = secrets_store.read("bling_app") or {}
    return (stored.get("client_id") or cid).strip(), (stored.get("client_secret") or secret).strip()


async def get_bling_app_creds() -> Tuple[str, str]:
    stored_id, stored_secret = await _load_stored_creds()
    cid = stored_id or BLING_CLIENT_ID()
    secret = stored_secret or BLING_CLIENT_SECRET()
    return cid, secret


async def save_bling_app_creds(client_id: str, client_secret: str) -> dict:
    client_id = (client_id or "").strip()
    client_secret = (client_secret or "").strip()
    if not client_id:
        raise HTTPException(status_code=400, detail="Client ID é obrigatório")
    existing_id, existing_secret = await _load_stored_creds()
    if not client_secret:
        client_secret = existing_secret or BLING_CLIENT_SECRET()
    if not client_secret:
        raise HTTPException(status_code=400, detail="Client Secret é obrigatório")
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "updated_at": _now().isoformat(),
    }
    await db.settings.update_one(
        {"key": BLING_APP_SETTINGS_KEY},
        {"$set": {"key": BLING_APP_SETTINGS_KEY, "value": payload}},
        upsert=True,
    )
    secrets_store.write("bling_app", payload)
    return {"ok": True, "client_id": client_id}
def _basic_auth_header(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _token_headers(client_id: str, client_secret: str) -> dict:
    return {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": _basic_auth_header(client_id, client_secret),
        "Accept": "1.0",
        "enable-jwt": "1",
    }


def _api_headers(access_token: str, token_type: str = "Bearer") -> dict:
    kind = token_type or "Bearer"
    return {
        "Authorization": f"{kind} {access_token}",
        "Accept": "application/json",
        "enable-jwt": "1",
    }


def _bling_error_text(resp: httpx.Response) -> str:
    text = (resp.text or "").strip()
    try:
        data = resp.json()
    except Exception:
        return text[:400] or f"HTTP {resp.status_code}"
    if isinstance(data, dict):
        err = data.get("error_description") or data.get("message") or data.get("error")
        if isinstance(err, dict):
            err = err.get("description") or err.get("message") or err.get("type") or json.dumps(err, ensure_ascii=False)
        if err:
            return str(err)[:400]
        return json.dumps(data, ensure_ascii=False)[:400]
    return str(data)[:400]


def friendly_oauth_error(raw: str) -> str:
    """Turn Bling/OAuth JSON into a short Portuguese action for the user."""
    text = (raw or "").strip()
    low = text.lower()
    if "invalid_client" in low or "client credentials are invalid" in low:
        return (
            "Client ID ou Client Secret inválidos. No Bling: Central de Extensões → "
            "Área do Integrador → Informações do app. Copie as duas chaves, cole em "
            "Configurações, salve e clique Conectar de novo."
        )
    if "invalid_grant" in low or "authorization code" in low:
        return (
            "Código expirado ou o Link de redirecionamento no Bling é diferente deste painel. "
            "Copie o link desta tela, cole no aplicativo Bling, salve e conecte de novo."
        )
    if "missing_code" in low:
        return (
            "O Bling não devolveu o código. Confira se o Link de redirecionamento "
            "cadastrado no aplicativo é exatamente o desta tela."
        )
    if "state inválido" in low:
        return "Sessão OAuth inválida — clique em Conectar Bling outra vez."
    return text[:240]


async def build_authorize_url(next_path: str = "/configuracoes", app_base: str = "") -> str:
    client_id, _secret = await get_bling_app_creds()
    if not client_id:
        raise HTTPException(
            status_code=400,
            detail="Client ID do Bling não configurado. Salve as credenciais do aplicativo em Configurações.",
        )
    base = (app_base or _env("APP_BASE_URL")).strip().rstrip("/")
    if not base:
        raise HTTPException(
            status_code=400,
            detail="Não foi possível detectar o endereço público da aplicação. Defina APP_BASE_URL.",
        )
    callback = redirect_uri(base)
    state = make_state({
        "next": next_path or "/configuracoes",
        "ts": _now().isoformat(),
        "redirect_uri": callback,
    })
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": callback,
        "state": state,
    }
    return f"{authorize_url()}?{urlencode(params)}"


async def _post_token(data: dict, client_id: str, client_secret: str) -> dict:
    headers = _token_headers(client_id, client_secret)
    primary = token_url()
    urls = [primary] + [u for u in TOKEN_URL_FALLBACKS if u != primary]
    last_error = "token exchange failed"
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        for url in urls:
            try:
                resp = await client.post(url, data=data, headers=headers)
            except httpx.HTTPError as exc:
                last_error = f"{url}: {exc}"
                logger.warning("Bling token POST falhou em %s: %s", url, exc)
                continue
            if resp.status_code == 200:
                payload = resp.json()
                if payload.get("access_token"):
                    return payload
                last_error = _bling_error_text(resp)
                continue
            last_error = _bling_error_text(resp)
            # 404/405 → try next host. Other 4xx are credential/code errors.
            if resp.status_code in (404, 405, 301, 302, 308):
                logger.warning("Bling token URL %s → %s, tentando fallback", url, resp.status_code)
                continue
            raise HTTPException(
                status_code=400,
                detail=friendly_oauth_error(last_error),
            )
    raise HTTPException(status_code=400, detail=friendly_oauth_error(last_error))


async def exchange_code(code: str, callback_uri: str = "") -> dict:
    """Exchange authorization code for tokens. Code expires in 60 seconds."""
    client_id, client_secret = await get_bling_app_creds()
    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Credenciais do aplicativo Bling ausentes")
    payload = {
        "grant_type": "authorization_code",
        "code": code,
    }
    # RFC 6749: redirect_uri is required on the token request if it was sent
    # on authorize. Bling also accepts omitting it (uses the registered URI).
    if callback_uri:
        payload["redirect_uri"] = callback_uri
    return await _post_token(payload, client_id, client_secret)


async def refresh_tokens(refresh_token: str) -> dict:
    client_id, client_secret = await get_bling_app_creds()
    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Credenciais do aplicativo Bling ausentes")
    return await _post_token(
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
        client_id,
        client_secret,
    )


async def save_tokens(token_data: dict) -> None:
    now = _now()
    expires_in = int(token_data.get("expires_in") or 21600)
    expires_at = now + timedelta(seconds=expires_in)
    doc = {
        "account_id": ACCOUNT_ID,
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token") or "",
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
    secrets_store.write("bling_tokens", doc)


async def get_token_doc() -> Optional[dict]:
    doc = await db.bling_tokens.find_one({"account_id": ACCOUNT_ID}, {"_id": 0})
    if doc and doc.get("access_token"):
        return doc
    stored = secrets_store.read("bling_tokens")
    if stored and stored.get("access_token"):
        try:
            await db.bling_tokens.update_one(
                {"account_id": ACCOUNT_ID},
                {"$set": stored},
                upsert=True,
            )
        except Exception as exc:
            logger.warning("não restaurou token Bling no Mongo: %s", exc)
        return stored
    return None


async def disconnect() -> None:
    await db.bling_tokens.delete_one({"account_id": ACCOUNT_ID})
    secrets_store.delete("bling_tokens")


async def get_valid_access_token() -> Tuple[str, str]:
    doc = await get_token_doc()
    if not doc:
        raise HTTPException(status_code=400, detail="Bling não está conectado")
    expires_at = datetime.fromisoformat(doc["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at - timedelta(seconds=60) > _now() and doc.get("access_token"):
        return doc["access_token"], doc.get("token_type", "Bearer")

    refresh = doc.get("refresh_token")
    if not refresh:
        raise HTTPException(
            status_code=400,
            detail="Token Bling expirado sem refresh_token — reconecte em Configurações",
        )
    try:
        data = await refresh_tokens(refresh)
        if data.get("refresh_token") is None:
            data["refresh_token"] = refresh
        await save_tokens(data)
        return data["access_token"], data.get("token_type", "Bearer")
    except HTTPException:
        raise HTTPException(
            status_code=400,
            detail="Token Bling expirado e não foi possível renovar — vá em Configurações → Bling e reconecte",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Erro ao renovar token Bling: {str(exc)[:100]} — reconecte em Configurações",
        )


async def _raw_request(method: str, path: str, params=None, json_body=None) -> httpx.Response:
    access_token, token_type = await get_valid_access_token()
    url = f"{api_base_url()}{path}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
        return await client.request(
            method,
            url,
            params=params,
            json=json_body,
            headers=_api_headers(access_token, token_type),
        )


async def bling_request(method: str, path: str, params=None, json=None) -> httpx.Response:
    """Throttled Bling request — caps at ~2 requests/second."""
    async with _bling_rate_limit:
        resp = await _raw_request(method, path, params=params, json_body=json)
        if resp.status_code == 401:
            # Force a refresh and retry once. Do NOT wipe tokens on a transient 401 —
            # that was deleting a valid connection whenever JWT headers were missing.
            doc = await get_token_doc()
            if doc and doc.get("refresh_token"):
                try:
                    data = await refresh_tokens(doc["refresh_token"])
                    if data.get("refresh_token") is None:
                        data["refresh_token"] = doc["refresh_token"]
                    await save_tokens(data)
                    resp = await _raw_request(method, path, params=params, json_body=json)
                except Exception as exc:
                    logger.warning("refresh após 401 falhou: %s", exc)
        await asyncio.sleep(0.5)

    if resp.status_code == 401:
        raise HTTPException(
            status_code=401,
            detail="Bling 401 — o token foi recusado. Reconecte em Configurações.",
        )
    if resp.status_code == 429:
        await asyncio.sleep(2.0)
        async with _bling_rate_limit:
            resp = await _raw_request(method, path, params=params, json_body=json)
            await asyncio.sleep(0.5)
    return resp


async def status() -> dict:
    doc = await get_token_doc()
    if not doc:
        return {"connected": False}
    return {
        "connected": True,
        "expires_at": doc.get("expires_at"),
        "scope": doc.get("scope"),
        "updated_at": doc.get("updated_at"),
    }


async def ping() -> dict:
    """Cheap authenticated call used by the Settings 'Testar conexão' button."""
    resp = await bling_request("GET", "/produtos", params={"pagina": 1, "limite": 1})
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, _bling_error_text(resp))
    body = resp.json() if resp.content else {}
    data = body.get("data") if isinstance(body, dict) else None
    return {"ok": True, "status_code": resp.status_code, "items": len(data or [])}


async def oauth_config(app_base: str = "") -> dict:
    client_id, client_secret = await get_bling_app_creds()
    stored_id, stored_secret = await _load_stored_creds()
    source = "database" if stored_id or stored_secret else ("env" if client_id else "none")
    issues = []
    if not client_id:
        issues.append("Client ID não configurado")
    if not client_secret:
        issues.append("Client Secret não configurado")
    if not app_base:
        issues.append("Endereço público da aplicação não detectado (APP_BASE_URL)")
    st = await status()
    callback = redirect_uri(app_base) if app_base else ""
    return {
        "connected": st.get("connected", False),
        "expires_at": st.get("expires_at"),
        "scope": st.get("scope"),
        "updated_at": st.get("updated_at"),
        "configured": bool(client_id and client_secret),
        "client_id": client_id,
        "has_secret": bool(client_secret),
        "source": source,
        "redirect_uri": callback,
        "authorize_url": authorize_url(),
        "token_url": token_url(),
        "api_base_url": api_base_url(),
        "issues": issues,
    }
