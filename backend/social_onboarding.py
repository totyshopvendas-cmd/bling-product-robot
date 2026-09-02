"""Onboarding status — single endpoint that aggregates the state of every
social integration so the UI can render a guided checklist.

Each step returns:
  - status: "ok" | "warning" | "error" | "pending"
  - label: short description of the step
  - detail: what's wrong / what to do next
  - action: optional CTA the UI can render (link, button id, etc.)
"""
import os

from fastapi import APIRouter
import httpx

from db import db
from social_service import _dec


router = APIRouter(prefix="/social", tags=["social-onboarding"])


async def _check_meta() -> list:
    """Return the 3 Meta-related steps: token valid, page selected, IG linked."""
    doc = await db.social_credentials.find_one({"provider": "meta"})
    if not doc:
        return [
            {"id": "meta-creds", "status": "pending", "label": "Salvar credenciais Meta", "detail": "Cole App ID, App Secret e Token em Redes Sociais.", "action_route": "/redes-sociais"},
            {"id": "meta-token", "status": "pending", "label": "Token vitalício", "detail": "Após salvar credenciais, clique em 'Tornar Token Vitalício'.", "action_route": "/redes-sociais"},
            {"id": "meta-page", "status": "pending", "label": "Página selecionada", "detail": "Clique em 'Escolher Página' e selecione TotyShop.com.", "action_route": "/redes-sociais"},
            {"id": "meta-ig", "status": "pending", "label": "Instagram vinculado", "detail": "Vincule sua conta Instagram Business à página em business.facebook.com.", "action_route": "/redes-sociais"},
        ]

    token = _dec(doc.get("page_access_token_enc"))
    page_id = doc.get("facebook_page_id")
    ig_id = doc.get("instagram_business_id")

    # Probe the token: /me to confirm it works + extract type/expiry
    token_status = "error"
    token_detail = "Token não pôde ser validado"
    page_name = None
    if token:
        try:
            async with httpx.AsyncClient(timeout=10) as cx:
                r = await cx.get(
                    "https://graph.facebook.com/v23.0/me",
                    params={"access_token": token, "fields": "id,name"},
                )
                if r.status_code < 400:
                    me = r.json()
                    page_name = me.get("name")
                    token_status = "ok"
                    token_detail = f"Token válido — autenticado como {page_name}"
                else:
                    err = (r.json() or {}).get("error", {}).get("message", "")
                    if "expired" in err.lower():
                        token_status = "error"
                        token_detail = "Token EXPIROU. Renove no Graph API Explorer e clique em 'Tornar Token Vitalício'."
                    else:
                        token_status = "error"
                        token_detail = err[:200]
        except Exception as e:
            token_detail = f"Erro de rede: {e}"
    else:
        token_status = "pending"
        token_detail = "Cole o Token de Acesso em Redes Sociais."

    steps = [
        {
            "id": "meta-creds",
            "status": "ok" if doc.get("app_id") and token else "pending",
            "label": "Credenciais Meta salvas",
            "detail": "App ID + App Secret + Token armazenados criptografados." if doc.get("app_id") else "Faltam credenciais.",
            "action_route": "/redes-sociais",
        },
        {
            "id": "meta-token",
            "status": token_status,
            "label": "Token Meta válido (vitalício)",
            "detail": token_detail,
            "action_route": "/redes-sociais",
            "action_label": "Renovar / Tornar Vitalício" if token_status != "ok" else None,
        },
        {
            "id": "meta-page",
            "status": "ok" if page_id and token_status == "ok" else ("warning" if page_id else "pending"),
            "label": "Página Facebook selecionada",
            "detail": (
                f"Página atual: {page_name or page_id}" if page_id and token_status == "ok"
                else f"ID salvo: {page_id} — valide o token primeiro" if page_id
                else "Clique em 'Escolher Página'."
            ),
            "action_route": "/redes-sociais",
        },
        {
            "id": "meta-ig",
            "status": "ok" if ig_id else "warning",
            "label": "Instagram Business vinculado",
            "detail": (
                f"IG Business ID: {ig_id}" if ig_id
                else "Sua conta Instagram Business não está vinculada à página Facebook. Vincule em business.facebook.com → Configurações → Contas do Instagram. Depois clique 'Testar conexão' no TotyShop."
            ),
            "action_route": "/redes-sociais",
            "action_external_url": "https://business.facebook.com/settings/instagram-accounts" if not ig_id else None,
        },
    ]
    return steps


async def _check_pinterest() -> list:
    doc = await db.social_credentials.find_one({"provider": "pinterest"})
    if not doc:
        return [{
            "id": "pinterest-creds",
            "status": "pending",
            "label": "Pinterest conectado",
            "detail": "Opcional: gere um Access Token em developers.pinterest.com e cole em Redes Sociais.",
            "action_route": "/redes-sociais",
        }]
    token = _dec(doc.get("access_token_enc"))
    board_id = doc.get("default_board_id")

    # Probe
    status = "error"
    detail = "Token não validado"
    username = None
    if token:
        try:
            async with httpx.AsyncClient(timeout=10) as cx:
                r = await cx.get(
                    "https://api.pinterest.com/v5/user_account",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if r.status_code < 400:
                    username = (r.json() or {}).get("username")
                    status = "ok"
                    detail = f"Conectado como @{username}"
                else:
                    msg = (r.json() or {}).get("message", r.text[:200])
                    if "consumer type" in msg.lower():
                        status = "warning"
                        detail = "App em modo Sandbox. Aplique para Produção em developers.pinterest.com/apps → seu app → Apply for Production."
                    else:
                        status = "error"
                        detail = msg
        except Exception as e:
            detail = str(e)

    steps = [
        {
            "id": "pinterest-creds",
            "status": status,
            "label": "Pinterest conectado",
            "detail": detail,
            "action_route": "/redes-sociais",
            "action_external_url": "https://developers.pinterest.com/apps/" if status != "ok" else None,
        },
        {
            "id": "pinterest-board",
            "status": "ok" if board_id else "warning",
            "label": "Board padrão Pinterest",
            "detail": f"Board ID: {board_id}" if board_id else "Defina um board padrão para os pins automáticos.",
            "action_route": "/redes-sociais",
        },
    ]
    return steps


async def _check_youtube() -> list:
    """YouTube status: credentials + OAuth refresh token + channel."""
    doc = await db.social_credentials.find_one({"provider": "google_youtube"})
    if not doc:
        return [{
            "id": "yt-creds",
            "status": "pending",
            "label": "Google OAuth (YouTube) — credenciais",
            "detail": "Crie um projeto no Google Cloud Console, habilite YouTube Data API v3, e cole Client ID + Secret em Redes Sociais.",
            "action_external_url": "https://console.cloud.google.com/apis/credentials",
        }]
    has_refresh = bool(doc.get("refresh_token_enc"))
    base = (os.environ.get("APP_BASE_URL") or "").rstrip("/")
    return [
        {
            "id": "yt-creds",
            "status": "ok",
            "label": "Google OAuth (YouTube) — credenciais",
            "detail": f"Client ID configurado. Redirect URI: {base}/api/social/youtube/oauth/callback",
            "action_route": "/redes-sociais",
        },
        {
            "id": "yt-oauth",
            "status": "ok" if has_refresh else "pending",
            "label": "Autorização YouTube concedida",
            "detail": (
                f"Conectado ao canal: {doc.get('channel_title') or doc.get('channel_id')}"
                if has_refresh else
                "Clique em 'Conectar YouTube' em Redes Sociais para autorizar acesso."
            ),
            "action_route": "/redes-sociais",
        },
    ]


@router.get("/onboarding/status")
async def onboarding_status() -> dict:
    """Aggregate status of every integration step, ready for a checklist UI."""
    meta = await _check_meta()
    pinterest = await _check_pinterest()
    youtube = await _check_youtube()
    all_steps = meta + pinterest + youtube

    # Decide which "next action" the user should focus on
    next_step = None
    for s in all_steps:
        if s["status"] in ("error", "pending"):
            next_step = s
            break

    summary = {
        "total": len(all_steps),
        "ok": sum(1 for s in all_steps if s["status"] == "ok"),
        "warnings": sum(1 for s in all_steps if s["status"] == "warning"),
        "errors": sum(1 for s in all_steps if s["status"] == "error"),
        "pending": sum(1 for s in all_steps if s["status"] == "pending"),
    }
    summary["ready_to_post"] = (
        any(s["id"] == "meta-token" and s["status"] == "ok" for s in all_steps)
        and any(s["id"] == "meta-page" and s["status"] == "ok" for s in all_steps)
    )

    return {
        "summary": summary,
        "next_step": next_step,
        "groups": {
            "meta": meta,
            "pinterest": pinterest,
            "youtube": youtube,
        },
    }
