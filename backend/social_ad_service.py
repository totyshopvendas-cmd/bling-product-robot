"""Social Ad creation flow.

Pipeline (single endpoint, multi-step):
  1. Fetch product from Bling (image + title + description)
  2. Generate ad image via Gemini Nano Banana, sized for Instagram (1080x1080)
  3. Generate ad copy + caption via Claude
  4. Store generated assets in MongoDB (base64); expose via public URL
  5. Publish caption+image to Instagram Business + Facebook Page via Graph API

Notes:
- Generated images live in `social_ad_assets` (Mongo) for the lifetime of the
  publishing window. Meta fetches the image via our public URL, so we keep the
  asset around for at least one publish attempt.
- We always post to BOTH Instagram and Facebook (per user's product choice).
"""
import os
import re
import uuid
import base64
import httpx
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Response, Request
from pydantic import BaseModel

from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent

import bling_service
from db import db
from robot_service import add_log
from social_service import get_meta_token_and_ids


router = APIRouter(prefix="/social", tags=["social-ads"])


EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY")
IMAGE_MODEL = "gemini-3.1-flash-image-preview"  # Nano Banana
COPY_MODEL = "claude-haiku-4-5-20251001"


# --------------------------------------------------------------- product list

@router.get("/ad/products")
async def list_ad_eligible_products(busca: str = "", pagina: int = 1, limite: int = 30) -> dict:
    """List Bling products eligible for ad creation: enriched + has image.

    Only the enriched products (marca=Generico, has descricaoCurta) are returned —
    we don't want to advertise raw JohnDrop imports."""
    params = {"pagina": pagina, "limite": min(max(limite, 1), 100)}
    if busca:
        params["pesquisa"] = busca
    r = await bling_service.bling_request("GET", "/produtos", params=params)
    if r.status_code >= 400:
        return {"items": [], "error": r.text[:200]}
    items_raw = (r.json() or {}).get("data") or []

    items: List[dict] = []
    for it in items_raw:
        pid = it.get("id")
        if not pid:
            continue
        full_r = await bling_service.bling_request("GET", f"/produtos/{pid}")
        if full_r.status_code >= 400:
            continue
        product = (full_r.json() or {}).get("data") or {}
        short = (product.get("descricaoCurta") or "").strip()
        brand = (product.get("marca") or "").strip().lower()
        if not short or brand not in ("generico", "generica"):
            continue
        # Skip variation CHILDREN — only show parent or simple products. Bling
        # names children like "Produto X Cor:Verde" and gives them tipo="P" with
        # a parent reference, so we filter by presence of "produtoPai" or the
        # ":" in the nome.
        nome_p = product.get("nome") or ""
        if (product.get("produtoPai") or {}).get("id"):
            continue
        if re.search(r"\b(Cor|Tamanho|Modelo|Voltagem):", nome_p):
            continue
        # Pick first usable image
        img_url = ""
        midia = product.get("midia") or {}
        imgs = midia.get("imagens") or {}
        # Internal hosted images
        for img in (imgs.get("internas") or []):
            link = img.get("link") or img.get("linkMiniatura") or ""
            if link:
                img_url = link
                break
        if not img_url:
            for img in (imgs.get("externas") or []):
                link = img.get("link") or ""
                if link:
                    img_url = link
                    break
        items.append({
            "id": pid,
            "codigo": product.get("codigo") or "",
            "nome": product.get("nome") or "",
            "preco": product.get("preco") or 0,
            "image_url": img_url,
        })

    return {"items": items, "pagina": pagina, "has_more": len(items_raw) >= params["limite"]}


# --------------------------------------------------------------- helpers

async def _download_image_b64(url: str) -> Optional[str]:
    """Download an image and return base64 string. Returns None on failure."""
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as cx:
            r = await cx.get(url)
            if r.status_code >= 400:
                return None
            return base64.b64encode(r.content).decode("utf-8")
    except Exception as e:
        await add_log("warning", f"Download imagem produto falhou: {e}")
        return None


def _public_asset_url(asset_id: str, request: Optional[Request] = None) -> str:
    """Build the publicly-reachable URL for a stored asset.

    Priority:
      1. PUBLIC_BACKEND_URL env (explicit override for deploys behind a CDN)
      2. The incoming Request.base_url (auto-derives external URL even when
         backend doesn't know its own hostname — works on Kubernetes ingress)
      3. REACT_APP_BACKEND_URL env (legacy fallback)
    Always returns an ABSOLUTE https URL — Meta Graph API rejects relative paths.
    """
    backend = (os.environ.get("PUBLIC_BACKEND_URL") or "").strip()
    if not backend:
        backend = os.environ.get("APP_BASE_URL", "").strip()
    if not backend and request is not None:
        # request.base_url is like "https://host/" — strip trailing slash
        backend = str(request.base_url).rstrip("/")
    if not backend:
        backend = os.environ.get("REACT_APP_BACKEND_URL", "").strip()
    backend = backend.rstrip("/")
    return f"{backend}/api/social/ad/asset/{asset_id}.png"


# --------------------------------------------------------------- generation


class GenerateAdRequest(BaseModel):
    product_id: int
    audience: str = "geral"  # user-supplied target audience hint
    extra_brief: str = ""  # optional manual brief from user


@router.post("/ad/generate")
async def generate_ad(payload: GenerateAdRequest, request: Request) -> dict:
    """Generate ad image (Nano Banana) + ad copy (Claude) for a Bling product.

    Stores the generated image in Mongo and returns its public URL plus the
    suggested caption text — UI lets the user review/edit before publishing.
    """
    if not EMERGENT_LLM_KEY:
        raise HTTPException(500, "EMERGENT_LLM_KEY não configurada")

    # 1. Fetch product
    r = await bling_service.bling_request("GET", f"/produtos/{payload.product_id}")
    if r.status_code >= 400:
        raise HTTPException(404, "Produto Bling não encontrado")
    product = (r.json() or {}).get("data") or {}
    name = (product.get("nome") or "").strip()
    short = (product.get("descricaoCurta") or "").strip()
    short_plain = re.sub(r"<[^>]+>", " ", short)
    short_plain = re.sub(r"\s+", " ", short_plain).strip()[:800]
    preco = product.get("preco") or 0

    # Pick reference image (first internal/external)
    ref_url = ""
    midia = product.get("midia") or {}
    imgs = midia.get("imagens") or {}
    for img in (imgs.get("internas") or []):
        if img.get("link"):
            ref_url = img.get("link")
            break
    if not ref_url:
        for img in (imgs.get("externas") or []):
            if img.get("link"):
                ref_url = img.get("link")
                break

    ref_b64 = await _download_image_b64(ref_url) if ref_url else None

    # 2. Generate ad copy (Claude)
    audience = payload.audience or "geral"
    brief = (payload.extra_brief or "").strip()
    copy_system = (
        "Você é um copywriter especializado em anúncios para Instagram e Facebook. "
        "Gere um anúncio curto, persuasivo e em português brasileiro. "
        "Formato OBRIGATÓRIO da resposta (JSON puro, sem markdown):\n"
        "{\n"
        '  "headline": "máximo 60 caracteres, chamativa",\n'
        '  "caption": "texto principal do post, 3-5 frases curtas, com 2-3 emojis bem posicionados, '
        "termina com call-to-action e 5 hashtags relevantes\",\n"
        '  "image_prompt": "prompt em inglês descritivo para gerar uma imagem publicitária 1:1 do produto, '
        "estilo: clean product shot com fundo gradiente moderno, iluminação suave, alta resolução, "
        "sem texto na imagem, foco no produto principal\"\n"
        "}"
    )
    copy_user = (
        f"Produto: {name}\n"
        f"Preço: R$ {preco}\n"
        f"Descrição: {short_plain}\n"
        f"Público-alvo: {audience}\n"
        + (f"Briefing adicional: {brief}\n" if brief else "")
        + "Gere o anúncio em JSON."
    )

    copy_chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"ad-copy-{uuid.uuid4()}",
        system_message=copy_system,
    ).with_model("anthropic", COPY_MODEL)
    try:
        copy_response = await copy_chat.send_message(UserMessage(text=copy_user))
    except Exception as e:
        raise HTTPException(500, f"Falha ao gerar copy: {e}")

    raw = str(copy_response).strip()
    # Extract JSON
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    json_str = m.group(0) if m else raw
    import json as _json
    try:
        parsed = _json.loads(json_str)
    except Exception:
        parsed = {"headline": name[:60], "caption": short_plain[:300], "image_prompt": f"product photo of {name}, clean studio shot"}

    headline = parsed.get("headline") or name[:60]
    caption = parsed.get("caption") or short_plain[:500]
    image_prompt = parsed.get("image_prompt") or f"product photo of {name}, clean studio shot, modern gradient background"

    # 3. Generate ad image (Nano Banana)
    img_chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"ad-img-{uuid.uuid4()}",
        system_message="You are an expert product photographer for social media ads.",
    ).with_model("gemini", IMAGE_MODEL).with_params(modalities=["image", "text"])

    final_prompt = (
        f"{image_prompt}. Square 1:1 aspect ratio for Instagram feed. "
        "Crisp clean composition, professional product photography, "
        "soft lighting, modern e-commerce aesthetic, no text overlay."
    )
    msg = UserMessage(
        text=final_prompt,
        file_contents=[ImageContent(ref_b64)] if ref_b64 else None,
    )
    try:
        _text, images_out = await img_chat.send_message_multimodal_response(msg)
    except Exception as e:
        raise HTTPException(500, f"Falha ao gerar imagem: {e}")

    if not images_out:
        raise HTTPException(500, "Nano Banana não retornou imagem")

    # 4. Store first generated image in Mongo
    asset_id = uuid.uuid4().hex[:16]
    img_data = images_out[0]
    mime = img_data.get("mime_type") or "image/png"
    b64 = img_data.get("data") or ""
    await db.social_ad_assets.insert_one({
        "id": asset_id,
        "product_id": payload.product_id,
        "mime": mime,
        "data_b64": b64,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    public_url = _public_asset_url(asset_id, request)

    # Persist ad draft so it can be re-published or audited later
    draft_id = uuid.uuid4().hex[:16]
    await db.social_ad_drafts.insert_one({
        "id": draft_id,
        "product_id": payload.product_id,
        "product_name": name,
        "asset_id": asset_id,
        "headline": headline,
        "caption": caption,
        "image_prompt": image_prompt,
        "image_url": public_url,
        "audience": audience,
        "status": "draft",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    await add_log("success", f"Anúncio gerado para produto {payload.product_id} (draft={draft_id})")

    return {
        "ok": True,
        "draft_id": draft_id,
        "asset_id": asset_id,
        "image_url": public_url,
        "headline": headline,
        "caption": caption,
        "product": {"id": payload.product_id, "nome": name, "preco": preco},
    }


# --------------------------------------------------------------- asset serving


@router.get("/ad/asset/{asset_id}.png")
async def serve_asset(asset_id: str) -> Response:
    """Serve a generated ad image. Meta fetches this URL when publishing."""
    doc = await db.social_ad_assets.find_one({"id": asset_id})
    if not doc:
        raise HTTPException(404, "asset não encontrado")
    try:
        data = base64.b64decode(doc.get("data_b64") or "")
    except Exception:
        raise HTTPException(500, "asset corrompido")
    return Response(
        content=data,
        media_type=doc.get("mime") or "image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


# --------------------------------------------------------------- publish


class PublishRequest(BaseModel):
    draft_id: str
    caption: Optional[str] = None  # user may override edited caption
    publish_instagram: bool = True
    publish_facebook: bool = True


@router.post("/ad/publish")
async def publish_ad(payload: PublishRequest, request: Request) -> dict:
    """Publish a generated ad to Instagram + Facebook via Meta Graph API."""
    draft = await db.social_ad_drafts.find_one({"id": payload.draft_id})
    if not draft:
        raise HTTPException(404, "draft não encontrado")

    creds = await get_meta_token_and_ids()
    if not creds or not creds.get("token"):
        raise HTTPException(400, "Credenciais Meta não configuradas. Conecte em Redes Sociais.")

    caption = (payload.caption or draft.get("caption") or "").strip()
    if not caption:
        raise HTTPException(400, "caption vazio")
    image_url = draft.get("image_url")
    if not image_url:
        raise HTTPException(400, "imagem ausente no draft")
    # If the stored URL is relative (legacy drafts), rebuild from current request
    if not image_url.startswith("http"):
        asset_id = draft.get("asset_id")
        if asset_id:
            image_url = _public_asset_url(asset_id, request)

    token = creds["token"]
    page_id = creds.get("facebook_page_id")
    ig_id = creds.get("instagram_business_id")

    result: dict = {"draft_id": payload.draft_id, "instagram": None, "facebook": None}

    async with httpx.AsyncClient(timeout=45) as cx:
        # Instagram: 2-step (create media → publish)
        if payload.publish_instagram:
            if not ig_id:
                result["instagram"] = {"ok": False, "error": "Instagram Business Account não configurado em Redes Sociais"}
            else:
                try:
                    r1 = await cx.post(
                        f"https://graph.facebook.com/v23.0/{ig_id}/media",
                        params={"access_token": token, "image_url": image_url, "caption": caption},
                    )
                    if r1.status_code >= 400:
                        result["instagram"] = {"ok": False, "error": r1.json().get("error", {}).get("message", r1.text[:200])}
                    else:
                        creation_id = r1.json().get("id")
                        r2 = await cx.post(
                            f"https://graph.facebook.com/v23.0/{ig_id}/media_publish",
                            params={"access_token": token, "creation_id": creation_id},
                        )
                        if r2.status_code >= 400:
                            result["instagram"] = {"ok": False, "error": r2.json().get("error", {}).get("message", r2.text[:200])}
                        else:
                            result["instagram"] = {"ok": True, "post_id": r2.json().get("id")}
                except Exception as e:
                    result["instagram"] = {"ok": False, "error": str(e)}

        # Facebook: 1-step photo post
        if payload.publish_facebook:
            if not page_id:
                result["facebook"] = {"ok": False, "error": "Facebook Page ID não configurado em Redes Sociais"}
            else:
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

    # Persist status on draft
    any_ok = (
        (result["instagram"] or {}).get("ok") or
        (result["facebook"] or {}).get("ok")
    )
    await db.social_ad_drafts.update_one(
        {"id": payload.draft_id},
        {"$set": {
            "status": "published" if any_ok else "failed",
            "publish_result": result,
            "published_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    await add_log(
        "success" if any_ok else "error",
        f"Publicação Meta draft={payload.draft_id}: "
        f"IG={(result['instagram'] or {}).get('ok')} FB={(result['facebook'] or {}).get('ok')}",
    )
    return {"ok": bool(any_ok), **result}


# --------------------------------------------------------------- drafts list


@router.get("/ad/drafts")
async def list_drafts(limit: int = 50) -> dict:
    cur = db.social_ad_drafts.find({}, {"_id": 0}).sort("created_at", -1).limit(limit)
    items = await cur.to_list(limit)
    return {"items": items}
