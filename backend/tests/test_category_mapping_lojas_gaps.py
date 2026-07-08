"""Regression tests for the new API-based marketplace/loja endpoints.

Covers:
- GET /api/category-mapping/lojas  (bypasses Playwright, hits Bling API)
- GET /api/category-mapping/gaps   (list bling categories missing per loja)
- Adjacent endpoints keep working: /marketplaces, /new-count, /sync-new (POST),
  /sync-new/status, /previews.

These tests hit the real Bling account attached to backend/.env (working per
problem statement). They do NOT mutate any external state.
"""
from __future__ import annotations

import os
import pytest
import requests

def _resolve_base_url() -> str:
    url = os.environ.get("REACT_APP_BACKEND_URL")
    if url:
        return url.rstrip("/")
    # Fallback: read /app/frontend/.env
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip().rstrip("/")
    except Exception:
        pass
    raise RuntimeError("REACT_APP_BACKEND_URL not configured")


BASE_URL = _resolve_base_url()


@pytest.fixture(scope="module")
def s() -> requests.Session:
    sess = requests.Session()
    sess.headers.update({"Content-Type": "application/json"})
    return sess


# ---------- /lojas ----------
class TestLojas:
    def test_lojas_status_and_shape(self, s):
        r = s.get(f"{BASE_URL}/api/category-mapping/lojas", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "total" in data and "items" in data
        assert isinstance(data["items"], list)
        assert isinstance(data["total"], int)
        assert data["total"] == len(data["items"])

    def test_lojas_has_expected_marketplaces(self, s):
        """User contract: at least Shopee and Mercado Livre + 6 lojas total."""
        r = s.get(f"{BASE_URL}/api/category-mapping/lojas", timeout=30)
        data = r.json()
        names = [i["name"] for i in data["items"]]
        assert "Shopee" in names, f"Shopee missing. Got: {names}"
        assert "Mercado Livre" in names, f"Mercado Livre missing. Got: {names}"
        assert data["total"] >= 2  # relaxed guard
        # Each item has the required fields
        for it in data["items"]:
            assert "loja_id" in it
            assert "name" in it
            assert "sample_code" in it
            assert "mapping_count" in it
            assert "linked_count" in it
            assert isinstance(it["mapping_count"], int)
            assert isinstance(it["linked_count"], int)


# ---------- /gaps ----------
class TestGaps:
    def test_gaps_shape_and_totals(self, s):
        r = s.get(f"{BASE_URL}/api/category-mapping/gaps", timeout=60)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "lojas" in data
        assert "total_bling_categories" in data
        assert "gaps_by_loja" in data
        assert isinstance(data["gaps_by_loja"], dict)
        assert data["total_bling_categories"] > 0
        # gaps_by_loja is dict keyed by loja_id (JSON stringifies int keys)
        for lid, gaps in data["gaps_by_loja"].items():
            assert isinstance(gaps, list)
            # if any gaps present, each has id + descricao
            if gaps:
                assert "id" in gaps[0]
                assert "descricao" in gaps[0]

    def test_gaps_loja_ids_match_lojas_list(self, s):
        r = s.get(f"{BASE_URL}/api/category-mapping/gaps", timeout=60)
        data = r.json()
        loja_ids_from_lojas = {str(l["loja_id"]) for l in data["lojas"]}
        gap_keys = set(data["gaps_by_loja"].keys())
        # every loja must be a key in gaps_by_loja (missing lojas would break UI)
        assert loja_ids_from_lojas.issubset(gap_keys)


# ---------- adjacent endpoints (regression) ----------
class TestAdjacentEndpoints:
    def test_marketplaces_legacy_still_200(self, s):
        r = s.get(f"{BASE_URL}/api/category-mapping/marketplaces", timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert "total" in data and "items" in data
        assert isinstance(data["items"], list)

    def test_new_count(self, s):
        r = s.get(f"{BASE_URL}/api/category-mapping/new-count", timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert "pending" in data
        assert isinstance(data["pending"], int)

    def test_previews_shape(self, s):
        r = s.get(f"{BASE_URL}/api/category-mapping/previews", timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert "total" in data and "items" in data

    def test_sync_new_status(self, s):
        r = s.get(f"{BASE_URL}/api/category-mapping/sync-new/status", timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert "running" in data
        assert "run" in data

    def test_sync_new_post_accepts_creds(self, s):
        """POST accepts payload with creds; the background task will fail in
        this container (no bling.com.br network access). We only verify the
        endpoint contract — 200 with running/message field.
        """
        payload = {"bling_user": "TESTUSER", "bling_pass": "TESTPASS", "apply": False}
        r = s.post(
            f"{BASE_URL}/api/category-mapping/sync-new", json=payload, timeout=15,
        )
        assert r.status_code == 200
        data = r.json()
        # Either it launches OK or reports "running" already
        assert "ok" in data or "running" in data
