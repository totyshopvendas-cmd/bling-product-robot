"""Image proxy: download images from JohnDrop (or any external source) and
serve them via our own /api/img/{id} endpoint with a stable, parameterless URL.

Why: Bling API rejects URLs with query-string signatures (S3 presigned with
AWSAccessKeyId/X-Amz-Signature) → HTTP 500. By re-hosting we get clean URLs
Bling accepts as `midia.imagens.imagensURL`.
"""
import os
import hashlib
import base64
import httpx
from fastapi import APIRouter, HTTPException, Response

from db import db


router = APIRouter(prefix="/img", tags=["image-proxy"])

APP_BASE_URL = (os.environ.get("APP_BASE_URL") or "").rstrip("/")


async def cache_external_image(url: str) -> str:
    """Download external image (e.g., JohnDrop S3 presigned) and store in Mongo
    under a stable hash-based ID. Returns the public URL we can hand to Bling.

    Cache hit: existing record returned immediately, no re-download.
    """
    if not url or not url.startswith("http"):
        return url
    image_id = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    existing = await db.image_cache.find_one({"id": image_id}, {"id": 1, "_id": 0})
    if existing:
        return _public_image_url(image_id)
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as cx:
            r = await cx.get(url)
            if r.status_code >= 400:
                return url  # give up and return original (Bling will likely reject)
            content_type = r.headers.get("content-type", "image/jpeg")
            data = r.content
            if len(data) > 8 * 1024 * 1024:  # 8MB cap
                return url
            await db.image_cache.insert_one({
                "id": image_id,
                "url_original": url,
                "data_b64": base64.b64encode(data).decode("ascii"),
                "content_type": content_type,
                "bytes": len(data),
            })
            return _public_image_url(image_id)
    except Exception:
        return url


def _public_image_url(image_id: str) -> str:
    base = APP_BASE_URL or ""
    return f"{base}/api/img/{image_id}.jpg"


@router.get("/{image_id}.jpg")
async def serve_cached_image(image_id: str) -> Response:
    image_id = image_id.split(".")[0]  # strip extension just in case
    doc = await db.image_cache.find_one({"id": image_id})
    if not doc:
        raise HTTPException(404, "imagem não encontrada")
    return Response(
        content=base64.b64decode(doc["data_b64"]),
        media_type=doc.get("content_type", "image/jpeg"),
        headers={"Cache-Control": "public, max-age=86400"},
    )
