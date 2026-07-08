"""API integration tests for the new /category-mapping/marketplaces endpoint
and the surrounding endpoints (new-count, sync-new, sync-new/status).

Uses the public REACT_APP_BACKEND_URL for realistic ingress testing.
Seeds and cleans up documents in the `category_mapping_trees` collection.
"""
import os
import sys
import asyncio

import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient

# Load backend .env for MONGO_URL/DB_NAME
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# BASE_URL from frontend env (public ingress)
def _load_frontend_backend_url() -> str:
    p = "/app/frontend/.env"
    with open(p, "r") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                return line.split("=", 1)[1].strip().strip('"').rstrip("/")
    raise RuntimeError("REACT_APP_BACKEND_URL not found")


BASE_URL = _load_frontend_backend_url()
MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")

SEED_MARKETPLACES = ["Test_Amazon", "Test_Shopee", "Test_ML"]


@pytest.fixture(scope="module")
def api_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def mongo_seed(event_loop):
    """Seed 3 marketplaces in category_mapping_trees, cleanup after."""
    async def _seed():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        # Cleanup any prior test docs first
        await db.category_mapping_trees.delete_many(
            {"marketplace": {"$in": SEED_MARKETPLACES}}
        )
        for name in SEED_MARKETPLACES:
            await db.category_mapping_trees.insert_one({
                "marketplace": name,
                "categories": [],
            })
        client.close()

    async def _cleanup():
        client = AsyncIOMotorClient(MONGO_URL)
        db = client[DB_NAME]
        await db.category_mapping_trees.delete_many(
            {"marketplace": {"$in": SEED_MARKETPLACES}}
        )
        client.close()

    event_loop.run_until_complete(_seed())
    yield
    event_loop.run_until_complete(_cleanup())


# -------- 1) New endpoint: shape and empty behaviour ------------------------

class TestMarketplacesEndpoint:
    """/api/category-mapping/marketplaces contract."""

    def test_returns_200_and_correct_shape(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/category-mapping/marketplaces")
        assert r.status_code == 200, r.text
        data = r.json()
        assert "total" in data
        assert "items" in data
        assert isinstance(data["items"], list)
        assert isinstance(data["total"], int)
        assert data["total"] == len(data["items"])
        # All items must be strings
        assert all(isinstance(x, str) for x in data["items"])

    def test_returns_seeded_marketplaces_sorted(self, api_client, mongo_seed):
        r = api_client.get(f"{BASE_URL}/api/category-mapping/marketplaces")
        assert r.status_code == 200, r.text
        data = r.json()
        items = data["items"]
        # Every seeded marketplace must be present
        for name in SEED_MARKETPLACES:
            assert name in items, f"{name} missing from {items}"
        # The subset consisting of seeded names should appear sorted (list is globally sorted)
        seeded_only = [x for x in items if x in SEED_MARKETPLACES]
        assert seeded_only == sorted(SEED_MARKETPLACES), (
            f"expected sorted {sorted(SEED_MARKETPLACES)}, got {seeded_only}"
        )
        # And the whole list itself must be sorted
        assert items == sorted(items)


# -------- 2) Existing endpoints still work ----------------------------------

class TestExistingEndpointsStillWork:
    def test_new_count_returns_pending_int(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/category-mapping/new-count")
        assert r.status_code == 200, r.text
        data = r.json()
        assert "pending" in data
        assert isinstance(data["pending"], int)

    def test_sync_new_status_shape(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/category-mapping/sync-new/status")
        assert r.status_code == 200, r.text
        data = r.json()
        # keys required
        for key in ("running", "started_at", "last_summary", "run"):
            assert key in data, f"missing key {key} in {data}"
        assert isinstance(data["running"], bool)

    def test_sync_new_post_accepts_empty_credentials(self, api_client):
        """Contract test: POST accepts empty credentials and returns ok/running.
        Playwright login will fail asynchronously; that's fine."""
        payload = {"bling_user": "", "bling_pass": "", "apply": False}
        r = api_client.post(
            f"{BASE_URL}/api/category-mapping/sync-new", json=payload
        )
        assert r.status_code == 200, r.text
        data = r.json()
        # Either it accepted the run (ok=True, running=True), or another
        # run was already in progress (ok=False, running=True).
        assert data.get("running") is True
        assert "ok" in data
