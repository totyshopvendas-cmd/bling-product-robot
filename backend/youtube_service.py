"""YouTube Shorts integration: Google OAuth + TTS + ffmpeg video generation + resumable upload.

Flow (per ad):
  1. Get ad draft (already has image + caption from Nano Banana + Claude)
  2. Generate vertical image 1080x1920 by re-running Nano Banana with 9:16 prompt
     (or scale the existing 1:1 image with ffmpeg pad+blur background)
  3. Generate audio MP3 via OpenAI TTS (voice="nova") reading headline+benefit+CTA
  4. ffmpeg combines image + audio into 9:16 MP4 (matches audio duration, max 60s)
  5. Resumable upload to YouTube Data API v3 with #shorts hashtag

The user authenticates ONCE via OAuth consent. We store refresh_token encrypted
in Mongo. Subsequent uploads use refresh_token → access_token (1h life).
"""
import os
import re
import asyncio
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from emergentintegrations.llm.openai.text_to_speech import OpenAITextToSpeech

from db import db
from robot_service import add_log
from social_service import _enc, _dec


router = APIRouter(prefix="/social", tags=["youtube-shorts"])

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY")
APP_BASE_URL = (os.environ.get("APP_BASE_URL") or "").rstrip("/")

YT_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
YT_TOKEN_URL = "https://oauth2.googleapis.com/token"
YT_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
YT_API_URL = "https://www.googleapis.com/youtube/v3"
YT_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


# ---------- OAuth ----------

class GoogleCredsRequest(BaseModel):
    client_id: str
    client_secret: str


@router.post("/youtube/credentials")
async def save_yt_creds(payload: GoogleCredsRequest) -> dict:
    """Save Google OAuth credentials (client_id + client_secret).

    User obtains these from Google Cloud Console → APIs & Services → Credentials
    → Create OAuth Client ID (Web application). The redirect URI must be
    `{APP_BASE_URL}/api/social/youtube/oauth/callback`.
    """
    await db.social_credentials.update_one(
        {"provider": "google_youtube"},
        {"$set": {
            "provider": "google_youtube",
            "client_id": payload.client_id.strip(),
            "client_secret_enc": _enc(payload.client_secret.strip()),
        }},
        upsert=True,
    )
    return {"ok": True}


@router.get("/youtube/credentials")
async def get_yt_creds() -> dict:
    doc = await db.social_credentials.find_one({"provider": "google_youtube"})
    if not doc:
        return {"configured": False}
    return {
        "configured": True,
        "client_id": doc.get("client_id"),
        "has_refresh_token": bool(doc.get("refresh_token_enc")),
        "redirect_uri": f"{APP_BASE_URL}/api/social/youtube/oauth/callback",
        "channel_id": doc.get("channel_id"),
        "channel_title": doc.get("channel_title"),
    }


@router.get("/youtube/oauth/start")
async def yt_oauth_start():
    """Redirects the user to Google's consent screen."""
    doc = await db.social_credentials.find_one({"provider": "google_youtube"})
    if not doc:
        raise HTTPException(400, "Credenciais Google não configuradas")
    redirect_uri = f"{APP_BASE_URL}/api/social/youtube/oauth/callback"
    params = {
        "client_id": doc["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(YT_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    import urllib.parse
    url = f"{YT_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return RedirectResponse(url)


@router.get("/youtube/oauth/callback")
async def yt_oauth_callback(request: Request):
    """Exchanges authorization code for refresh_token + access_token."""
    code = request.query_params.get("code")
    err = request.query_params.get("error")
    if err:
        return RedirectResponse(f"{APP_BASE_URL}/redes-sociais?yt_error={err}")
    if not code:
        raise HTTPException(400, "code ausente")
    doc = await db.social_credentials.find_one({"provider": "google_youtube"})
    if not doc:
        raise HTTPException(400, "credenciais ausentes")
    redirect_uri = f"{APP_BASE_URL}/api/social/youtube/oauth/callback"
    async with httpx.AsyncClient(timeout=20) as cx:
        r = await cx.post(YT_TOKEN_URL, data={
            "code": code,
            "client_id": doc["client_id"],
            "client_secret": _dec(doc["client_secret_enc"]),
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })
    if r.status_code >= 400:
        raise HTTPException(400, f"token exchange falhou: {r.text[:200]}")
    body = r.json()
    refresh = body.get("refresh_token")
    access = body.get("access_token")
    if not refresh:
        raise HTTPException(400, "Google não retornou refresh_token. Tente novamente com prompt=consent.")

    # Fetch channel info
    channel_id = None
    channel_title = None
    async with httpx.AsyncClient(timeout=15) as cx:
        rc = await cx.get(f"{YT_API_URL}/channels",
                          params={"part": "snippet", "mine": "true"},
                          headers={"Authorization": f"Bearer {access}"})
        if rc.status_code < 400:
            items = (rc.json() or {}).get("items") or []
            if items:
                channel_id = items[0].get("id")
                channel_title = (items[0].get("snippet") or {}).get("title")

    await db.social_credentials.update_one(
        {"provider": "google_youtube"},
        {"$set": {
            "refresh_token_enc": _enc(refresh),
            "channel_id": channel_id,
            "channel_title": channel_title,
            "connected_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    await add_log("success", f"YouTube conectado: {channel_title or channel_id}")
    return RedirectResponse(f"{APP_BASE_URL}/redes-sociais?yt_connected=1")


async def _get_yt_access_token() -> Optional[str]:
    doc = await db.social_credentials.find_one({"provider": "google_youtube"})
    if not doc or not doc.get("refresh_token_enc"):
        return None
    refresh = _dec(doc["refresh_token_enc"])
    async with httpx.AsyncClient(timeout=15) as cx:
        r = await cx.post(YT_TOKEN_URL, data={
            "client_id": doc["client_id"],
            "client_secret": _dec(doc["client_secret_enc"]),
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        })
    if r.status_code >= 400:
        await add_log("error", f"YouTube refresh falhou: {r.text[:200]}")
        return None
    return r.json().get("access_token")


# ---------- TTS + ffmpeg helpers ----------

async def _generate_tts(text: str) -> bytes:
    """Generate MP3 bytes using OpenAI TTS via Emergent Universal Key."""
    if not EMERGENT_LLM_KEY:
        raise RuntimeError("EMERGENT_LLM_KEY ausente")
    tts = OpenAITextToSpeech(api_key=EMERGENT_LLM_KEY)
    return await tts.generate_speech(
        text=text[:4000],  # OpenAI limit
        model="tts-1",
        voice="nova",
        response_format="mp3",
    )


async def _generate_vertical_image_from_existing(image_url: str) -> bytes:
    """Download the 1:1 ad image and ffmpeg-pad it to 1080x1920 with blurred background."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as cx:
        r = await cx.get(image_url)
        if r.status_code >= 400:
            raise RuntimeError("falha ao baixar imagem do anúncio")
        img_bytes = r.content
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(img_bytes)
        src = f.name
    dst = src + "-9x16.jpg"
    # ffmpeg: scale → blurred background full-screen, then overlay original centered
    cmd = [
        "ffmpeg", "-y", "-i", src,
        "-filter_complex",
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=40:5[bg];"
        "[0:v]scale=1080:-1[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2",
        "-frames:v", "1", dst,
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg pad falhou: {err.decode()[:200]}")
    with open(dst, "rb") as f:
        out = f.read()
    os.unlink(src)
    os.unlink(dst)
    return out


async def _build_short_video(image_9x16: bytes, audio_mp3: bytes) -> bytes:
    """Combine vertical image + audio into MP4 (image as static, length = audio length)."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(image_9x16)
        img_path = f.name
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio_mp3)
        aud_path = f.name
    vid_path = tempfile.mktemp(suffix=".mp4")
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", img_path, "-i", aud_path,
        "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-shortest", "-r", "30",
        "-vf", "scale=1080:1920",
        vid_path,
    ]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg merge falhou: {err.decode()[:200]}")
    with open(vid_path, "rb") as f:
        out = f.read()
    os.unlink(img_path)
    os.unlink(aud_path)
    os.unlink(vid_path)
    return out


# ---------- Publish to YouTube ----------

class PublishYouTubeShortRequest(BaseModel):
    draft_id: str
    privacy_status: str = "public"  # public | unlisted | private
    category_id: str = "22"  # People & Blogs


@router.post("/youtube/publish")
async def publish_youtube_short(payload: PublishYouTubeShortRequest) -> dict:
    """Generate vertical video for an ad draft and upload as YouTube Short."""
    draft = await db.social_ad_drafts.find_one({"id": payload.draft_id})
    if not draft:
        raise HTTPException(404, "draft não encontrado")
    access_token = await _get_yt_access_token()
    if not access_token:
        raise HTTPException(400, "YouTube não conectado. Configure OAuth em Redes Sociais.")

    headline = (draft.get("headline") or "").strip()
    caption = (draft.get("caption") or "").strip()
    image_url = draft.get("image_url") or ""
    if not (headline and caption and image_url):
        raise HTTPException(400, "draft incompleto")

    # 1. Generate audio (TTS reads headline + first sentence of caption + CTA)
    first_sentence = re.split(r"[.!?]\s+", caption)[0][:200]
    audio_text = f"{headline}. {first_sentence}. Aproveite agora!"
    try:
        audio = await _generate_tts(audio_text)
    except Exception as e:
        raise HTTPException(500, f"TTS falhou: {e}")

    # 2. Build vertical image
    try:
        img_v = await _generate_vertical_image_from_existing(image_url)
    except Exception as e:
        raise HTTPException(500, f"Geração imagem 9:16 falhou: {e}")

    # 3. Combine into MP4
    try:
        video = await _build_short_video(img_v, audio)
    except Exception as e:
        raise HTTPException(500, f"Merge ffmpeg falhou: {e}")

    # 4. Upload to YouTube (resumable single-PUT for small files)
    title_text = f"{headline} #shorts"[:100]
    description = f"{caption}\n\n#shorts"
    metadata = {
        "snippet": {
            "title": title_text,
            "description": description,
            "categoryId": payload.category_id,
            "tags": ["shorts", "totyshop"],
        },
        "status": {"privacyStatus": payload.privacy_status},
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Length": str(len(video)),
        "X-Upload-Content-Type": "video/mp4",
    }
    async with httpx.AsyncClient(timeout=60) as cx:
        init_r = await cx.post(
            f"{YT_UPLOAD_URL}?uploadType=resumable&part=snippet,status",
            headers=headers, json=metadata,
        )
    if init_r.status_code not in (200, 201):
        raise HTTPException(init_r.status_code, f"YouTube init falhou: {init_r.text[:200]}")
    upload_url = init_r.headers.get("Location")
    if not upload_url:
        raise HTTPException(500, "YouTube não retornou upload URL")

    async with httpx.AsyncClient(timeout=300) as cx:
        up_r = await cx.put(upload_url, headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Length": str(len(video)),
            "Content-Type": "video/mp4",
        }, content=video)
    if up_r.status_code not in (200, 201):
        raise HTTPException(up_r.status_code, f"YouTube upload falhou: {up_r.text[:300]}")
    result = up_r.json()
    video_id = result.get("id")
    yt_url = f"https://youtube.com/shorts/{video_id}" if video_id else None

    await db.social_ad_drafts.update_one(
        {"id": payload.draft_id},
        {"$set": {"youtube_video_id": video_id, "youtube_url": yt_url}},
    )
    await add_log("success", f"YouTube Short publicado: {yt_url}")
    return {"ok": True, "video_id": video_id, "url": yt_url}
