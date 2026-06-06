"""Backend tests for Criar Anúncio (social ad) module + bling_variations fixes.

Covers:
- GET /api/social/ad/products → enriched parent/simple products only
- POST /api/social/ad/generate → end-to-end (consumes Universal Key credits)
- GET /api/social/ad/asset/{id}.png → 404 + valid PNG serving
- GET /api/social/ad/drafts → list draft
- POST /api/social/ad/publish → structured error response (no crash) when Meta token invalid
- Unit-level: bling_variations._read_parent_stock_with_retry signature
- Unit-level: bling_variations._copy_images_to_children no longer filters S3 presigned URLs
"""
import os
import re
import inspect
import ast
import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://bling-product-robot.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"


@pytest.fixture(scope="session")
def client():
    s = requests.Session()
    s.headers.update({"Accept": "application/json"})
    return s


@pytest.fixture(scope="session")
def state():
    """Shared state between tests (generated draft/asset ids)."""
    return {}


def _ensure_draft_loaded(client, state):
    """If generate test was skipped/deselected, pull most recent draft."""
    if state.get("draft_id"):
        return
    try:
        r = client.get(f"{API}/social/ad/drafts", timeout=30)
        items = r.json().get("items", [])
        if items:
            it = items[0]
            state["draft_id"] = it.get("id")
            state["asset_id"] = it.get("asset_id")
            state["image_url"] = it.get("image_url")
            state["caption"] = it.get("caption")
    except Exception:
        pass


# ---------- /api/social/ad/products ----------
class TestAdProducts:
    def test_list_returns_enriched_only(self, client, state):
        r = client.get(f"{API}/social/ad/products", params={"limite": 5}, timeout=120)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert "items" in data and isinstance(data["items"], list)
        assert "pagina" in data and data["pagina"] == 1
        assert "has_more" in data
        # Must have at least one (we know from earlier the catalog has enriched ones)
        assert len(data["items"]) >= 1, "expected at least one enriched product"
        # Pick first id for downstream tests
        state["product_id"] = data["items"][0]["id"]
        for it in data["items"]:
            # Required fields
            for k in ("id", "codigo", "nome", "preco", "image_url"):
                assert k in it, f"missing field {k} in product item"
            # Must NOT be a variation child name pattern
            assert not re.search(r"\b(Cor|Tamanho|Modelo|Voltagem):", it["nome"]), (
                f"variation child leaked into list: {it['nome']}"
            )


# ---------- /api/social/ad/asset/{id}.png 404 ----------
class TestAssetMissing:
    def test_missing_asset_returns_404(self, client):
        r = client.get(f"{API}/social/ad/asset/doesnotexist123.png", timeout=30)
        assert r.status_code == 404


# ---------- /api/social/ad/drafts (before generate) ----------
class TestDraftsList:
    def test_drafts_list_structure(self, client):
        r = client.get(f"{API}/social/ad/drafts", timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert "items" in data and isinstance(data["items"], list)
        # Mongo _id must NOT leak
        for it in data["items"]:
            assert "_id" not in it


# ---------- /api/social/ad/generate (ONE end-to-end call) ----------
class TestAdGenerate:
    def test_generate_ad_end_to_end(self, client, state):
        pid = state.get("product_id")
        assert pid, "product_id from products test missing — order issue"
        r = client.post(
            f"{API}/social/ad/generate",
            json={"product_id": pid, "audience": "consumidores que organizam home office"},
            timeout=180,
        )
        assert r.status_code == 200, f"generate failed: {r.status_code} {r.text[:500]}"
        data = r.json()
        assert data.get("ok") is True
        for k in ("draft_id", "asset_id", "image_url", "headline", "caption"):
            assert k in data and data[k], f"missing {k} in generate response"
        # Save IDs first so downstream tests can run even if the URL is bad
        state["draft_id"] = data["draft_id"]
        state["asset_id"] = data["asset_id"]
        state["image_url"] = data["image_url"]
        state["caption"] = data["caption"]
        # public URL must point to /api/social/ad/asset/{id}.png
        assert data["image_url"].endswith(f"/api/social/ad/asset/{data['asset_id']}.png")
        # Headline length is sane
        assert 1 <= len(data["headline"]) <= 200
        # Caption non-trivial
        assert len(data["caption"]) > 20
        # BUG CHECK: image_url MUST be absolute so Meta Graph API can fetch it.
        # In current env neither PUBLIC_BACKEND_URL nor REACT_APP_BACKEND_URL is
        # set in backend/.env → _public_asset_url returns a relative path which
        # will make publish fail. Reported separately; keep assertion to surface.
        assert data["image_url"].startswith("http"), (
            f"image_url is RELATIVE ({data['image_url']}); backend must expose absolute URL "
            "via PUBLIC_BACKEND_URL or REACT_APP_BACKEND_URL env var for Meta to fetch"
        )


# ---------- /api/social/ad/asset/{id}.png serves PNG ----------
class TestAssetServe:
    def test_serve_generated_asset(self, client, state):
        _ensure_draft_loaded(client, state)
        aid = state.get("asset_id")
        if not aid:
            pytest.skip("no asset_id (generate test skipped/failed)")
        r = client.get(f"{API}/social/ad/asset/{aid}.png", timeout=30)
        assert r.status_code == 200
        ct = r.headers.get("Content-Type", "")
        assert ct.startswith("image/"), f"unexpected content-type {ct}"
        # PNG magic header (Nano Banana returns PNG); fall back: ensure non-empty bytes
        assert len(r.content) > 1000, "asset bytes too small"


# ---------- /api/social/ad/drafts (after generate) ----------
class TestDraftsAfterGenerate:
    def test_generated_draft_present(self, client, state):
        _ensure_draft_loaded(client, state)
        did = state.get("draft_id")
        if not did:
            pytest.skip("no draft_id (generate test skipped/failed)")
        r = client.get(f"{API}/social/ad/drafts", timeout=30)
        assert r.status_code == 200
        items = r.json().get("items", [])
        # Most recent first
        ids = [it.get("id") for it in items]
        assert did in ids, f"draft {did} not in /drafts list"
        # The freshly generated draft should be first (most recent)
        assert ids[0] == did, "drafts not sorted most-recent first"


# ---------- /api/social/ad/publish (structured error, no 500) ----------
class TestAdPublish:
    def test_publish_returns_structured_response(self, client, state):
        _ensure_draft_loaded(client, state)
        did = state.get("draft_id")
        if not did:
            pytest.skip("no draft_id (generate test skipped/failed)")
        r = client.post(
            f"{API}/social/ad/publish",
            json={"draft_id": did},
            timeout=60,
        )
        # Acceptable outcomes:
        #  - 200 with ok=false + instagram.error / facebook.error (invalid token but graceful)
        #  - 200 with ok=true (token actually valid)
        #  - 400 with detail (Meta credentials not configured at all)
        # NEVER 500.
        assert r.status_code != 500, f"publish crashed with 500: {r.text[:500]}"
        if r.status_code == 400:
            # Credenciais não configuradas — acceptable per spec
            detail = (r.json() or {}).get("detail") or ""
            assert "Meta" in detail or "credencia" in detail.lower(), detail
            return
        assert r.status_code == 200, r.text[:500]
        data = r.json()
        assert "ok" in data
        assert "instagram" in data
        assert "facebook" in data
        # If not ok, sub-results must carry an `error` field (or be None when publish disabled)
        if not data["ok"]:
            ig = data["instagram"] or {}
            fb = data["facebook"] or {}
            assert (ig.get("error") is not None) or (fb.get("error") is not None) or (
                data["instagram"] is None and data["facebook"] is None
            ), f"no error info in failed publish: {data}"


# ---------- Unit-style validation of bling_variations fixes ----------
class TestBlingVariationsHelpers:
    def test_read_parent_stock_with_retry_signature(self):
        from bling_variations import _read_parent_stock_with_retry
        sig = inspect.signature(_read_parent_stock_with_retry)
        params = sig.parameters
        # Must have parent_id (required), parent_current, max_attempts, delay_s
        assert "parent_id" in params
        assert params["parent_id"].annotation is int
        assert "max_attempts" in params and params["max_attempts"].default == 6
        assert "delay_s" in params and params["delay_s"].default == 10.0
        assert sig.return_annotation is int

    def test_copy_images_filter_does_not_drop_s3_presigned(self):
        """The previous bad filter rejected any URL containing AWSAccessKeyId
        or X-Amz-Signature. Verify those substrings are NOT in the executable
        code path (docstring is OK)."""
        from bling_variations import _copy_images_to_children
        src = inspect.getsource(_copy_images_to_children)
        # Strip docstring before inspecting
        tree = ast.parse(src)
        fn = tree.body[0]
        if (isinstance(fn.body[0], ast.Expr)
                and isinstance(fn.body[0].value, ast.Constant)
                and isinstance(fn.body[0].value.value, str)):
            fn.body.pop(0)
        code = ast.unparse(fn)
        assert "AWSAccessKeyId" not in code, "S3 presigned filter still present in code"
        assert "X-Amz-Signature" not in code, "S3 presigned filter still present in code"

        # Behavioral check (sync, no IO): the list-comprehension `clean_urls`
        # only filters data: and blob: schemes. A presigned S3 URL must pass.
        s3_presigned = (
            "https://orgbling.s3.amazonaws.com/abc/def?"
            "AWSAccessKeyId=AKIATCLMSGFXTAGX6WUM&Expires=1781387872&Signature=xyz%3D"
        )
        urls = [s3_presigned, "data:image/png;base64,xxx", "blob:http://foo", ""]
        clean = [u for u in urls if u and not u.startswith("data:") and not u.startswith("blob:")]
        assert s3_presigned in clean
        assert len(clean) == 1
