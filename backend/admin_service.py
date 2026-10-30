"""Recursos de "app próprio" (sem Emergent):

* Login opcional por senha  — defina APP_PASSWORD no .env para proteger o painel
  quando ele estiver num servidor/VPS. Sem APP_PASSWORD (uso no seu PC) nada muda.
* Chaves de IA pela tela    — /api/settings/ai (status, salvar, testar).
* Backup / restauração      — /api/admin/backup (baixa .json com todo o banco) e
  /api/admin/restore (envia o .json de volta). Serve para levar os dados do PC
  para um servidor, ou guardar uma cópia de segurança.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import time
from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

import ai_client
import secrets_store
from db import db

router = APIRouter(tags=["admin"])

COOKIE = "totyshop_session"
SESSION_DAYS = 30

# Caminhos que precisam ficar abertos mesmo com senha: retornos OAuth e
# arquivos que Bling / Instagram / Pinterest buscam direto no servidor.
PUBLIC_PREFIXES = (
    "/api/auth/",
    "/api/bling/callback",
    "/api/social/youtube/oauth/callback",
    "/api/social/ad/asset/",
    "/api/img/",
    "/api/system/ready",
)


# ----------------------------------------------------------------- login ---
def _password() -> str:
    return (os.environ.get("APP_PASSWORD") or "").strip()


def _secret() -> bytes:
    return (os.environ.get("APP_SECRET") or "totyshop-local").encode()


def _sign(expires: int) -> str:
    msg = f"{expires}:{hashlib.sha256(_password().encode()).hexdigest()}".encode()
    return f"{expires}.{hmac.new(_secret(), msg, hashlib.sha256).hexdigest()}"


def _valid(token: Optional[str]) -> bool:
    if not token or "." not in token:
        return False
    exp_s, _sig = token.split(".", 1)
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < time.time():
        return False
    return hmac.compare_digest(token, _sign(exp))


def auth_required() -> bool:
    return bool(_password())


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (
            auth_required()
            and path.startswith("/api/")
            and request.method != "OPTIONS"
            and not path.startswith(PUBLIC_PREFIXES)
            and not _valid(request.cookies.get(COOKIE))
        ):
            return JSONResponse({"detail": "login necessário"}, status_code=401)
        return await call_next(request)


class LoginRequest(BaseModel):
    password: str


@router.get("/auth/status")
async def auth_status(request: Request) -> dict:
    return {"required": auth_required(), "authenticated": (not auth_required()) or _valid(request.cookies.get(COOKIE))}


@router.post("/auth/login")
async def auth_login(req: LoginRequest, request: Request) -> Response:
    if not auth_required():
        return JSONResponse({"ok": True, "required": False})
    if not hmac.compare_digest(req.password.strip(), _password()):
        await asyncio.sleep(1)  # freia tentativa e erro
        raise HTTPException(401, "Senha incorreta")
    exp = int(time.time()) + SESSION_DAYS * 86400
    resp = JSONResponse({"ok": True})
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").startswith("https")
    resp.set_cookie(COOKIE, _sign(exp), max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=secure)
    return resp


@router.post("/auth/logout")
async def auth_logout() -> Response:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


# ------------------------------------------------------------------- IA ----
class AIKeysRequest(BaseModel):
    anthropic: Optional[str] = None
    openai: Optional[str] = None
    gemini: Optional[str] = None
    openrouter: Optional[str] = None


@router.get("/settings/ai")
async def ai_settings() -> dict:
    return ai_client.status()


@router.post("/settings/ai")
async def ai_settings_save(req: AIKeysRequest) -> dict:
    """Salva/atualiza chaves. Campo vazio ("") apaga; campo ausente (null) mantém."""
    current: Dict[str, str] = secrets_store.read("ai_keys") or {}
    for prov, val in req.model_dump().items():
        if val is None:
            continue
        val = val.strip()
        if val:
            current[prov] = val
        else:
            current.pop(prov, None)
    secrets_store.write("ai_keys", current)
    return ai_client.status()


class AITestRequest(BaseModel):
    provider: str


@router.post("/settings/ai/test")
async def ai_settings_test(req: AITestRequest) -> dict:
    if req.provider not in ai_client.PROVIDERS:
        raise HTTPException(400, "provedor inválido")
    return await ai_client.test_provider(req.provider)


# --------------------------------------------------------------- backup ----
# Logs e caches regeneráveis ficam de fora por padrão (deixam o arquivo enorme).
SKIP_BY_DEFAULT = {"robot_logs", "social_ad_assets"}


@router.get("/admin/backup")
async def admin_backup(full: bool = False) -> Response:
    from bson import json_util

    names = sorted(await db.list_collection_names())
    dump = {"_meta": {"app": "totyshop", "created_at": datetime.now().isoformat(), "full": full}}
    for name in names:
        if not full and name in SKIP_BY_DEFAULT:
            continue
        dump[name] = [doc async for doc in db[name].find({})]
    body = json_util.dumps(dump, ensure_ascii=False)
    fname = f"totyshop_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.post("/admin/restore")
async def admin_restore(file: UploadFile = File(...)) -> dict:
    """Restaura um backup: cada coleção presente no arquivo é SUBSTITUÍDA."""
    from bson import json_util

    raw = await file.read()
    try:
        data = json_util.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(400, "Arquivo inválido — envie o .json gerado pelo Backup")
    if not isinstance(data, dict) or (data.get("_meta") or {}).get("app") != "totyshop":
        raise HTTPException(400, "Este arquivo não é um backup do TotyShop")
    restored = {}
    for name, docs in data.items():
        if name.startswith("_") or not isinstance(docs, list):
            continue
        await db[name].delete_many({})
        if docs:
            await db[name].insert_many(docs)
        restored[name] = len(docs)
    return {"ok": True, "restored": restored}


def setup(app, api_router) -> None:
    api_router.include_router(router)
    app.add_middleware(AuthMiddleware)

