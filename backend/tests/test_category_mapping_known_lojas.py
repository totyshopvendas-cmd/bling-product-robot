"""Iteration 8: tests for the new known-lojas / sync-api / rename-alias flows.

Covers:
- GET /api/category-mapping/lojas — merges Bling API results with manual lojas
  (Amazon 205274346 and Google Shopping 205744700 must be present + manual=true)
- POST /api/category-mapping/lojas/known — add + verify + cleanup
- PUT /api/category-mapping/lojas/alias — update + verify + cleanup
- POST /api/category-mapping/sync-api — dry_run=True must NOT create real bindings
- GET  /api/category-mapping/sync-api/status — reports phase/state
"""
import os
import time

import pytest
import requests

def _resolve_base_url() -> str:
    url = os.environ.get("REACT_APP_BACKEND_URL")
    if not url:
        try:
            with open("/app/frontend/.env") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("REACT_APP_BACKEND_URL="):
                        url = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        except FileNotFoundError:
            pass
    if not url:
        raise RuntimeError("REACT_APP_BACKEND_URL not configured")
    return url.rstrip("/")


BASE_URL = _resolve_base_url()


@pytest.fixture(scope="module")
def s():
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    return session


@pytest.fixture(scope="module")
def mongo():
    """Direct MongoDB access for verifying + cleaning up test seeds."""
    from pymongo import MongoClient
    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        # Read from /app/backend/.env manually
        try:
            with open("/app/backend/.env") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("MONGO_URL="):
                        mongo_url = line.split("=", 1)[1].strip('"').strip("'")
                    elif line.startswith("DB_NAME="):
                        db_name = line.split("=", 1)[1].strip('"').strip("'")
        except FileNotFoundError:
            pass
    if not mongo_url or not db_name:
        pytest.skip("MONGO_URL/DB_NAME not available")
    client = MongoClient(mongo_url)
    yield client[db_name]
    client.close()


# ---------------------------------------------------------------- lojas list
class TestLojasMerge:
    def test_lojas_returns_seeded_manual_lojas(self, s):
        r = s.get(f"{BASE_URL}/api/category-mapping/lojas", timeout=60)
        assert r.status_code == 200, r.text
        payload = r.json()
        # Backend now returns {items: [...], total: N}
        assert isinstance(payload, dict)
        assert "items" in payload
        data = payload["items"]
        assert isinstance(data, list)
        # Required fields
        for item in data:
            for k in ("loja_id", "name", "default_name", "custom_alias",
                      "manual", "sample_code", "mapping_count", "linked_count"):
                assert k in item, f"missing field {k} in {item}"

        by_id = {i["loja_id"]: i for i in data}
        # Two manual lojas already seeded per review_request
        assert 205274346 in by_id, "Amazon manual loja missing"
        assert 205744700 in by_id, "Google Shopping manual loja missing"

        amz = by_id[205274346]
        gsp = by_id[205744700]
        assert amz["manual"] is True
        assert gsp["manual"] is True
        assert "Amazon" in amz["name"], f"expected Amazon in name, got {amz['name']}"
        assert "Google Shopping" in gsp["name"], f"expected Google Shopping, got {gsp['name']}"
        # Google Shopping has zero mappings yet
        assert gsp["linked_count"] == 0

    def test_lojas_min_count(self, s):
        r = s.get(f"{BASE_URL}/api/category-mapping/lojas", timeout=60)
        assert r.status_code == 200
        payload = r.json()
        data = payload.get("items", [])
        # Expect at least 7 (6 API-detected + 2 manual, 1 possibly overlaps)
        assert len(data) >= 7, f"expected >=7 lojas, got {len(data)}"


# --------------------------------------------------------- add known loja
class TestKnownLojaCRUD:
    TEST_LOJA_ID = 999999

    def test_add_known_and_verify(self, s, mongo):
        # Ensure clean state
        mongo.category_mapping_known_lojas.delete_one({"loja_id": self.TEST_LOJA_ID})
        mongo.category_mapping_loja_aliases.delete_one({"loja_id": self.TEST_LOJA_ID})

        r = s.post(
            f"{BASE_URL}/api/category-mapping/lojas/known",
            json={"loja_id": self.TEST_LOJA_ID, "name": "TEST_Loja"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("ok") is True
        assert body.get("loja_id") == self.TEST_LOJA_ID

        # Verify it appears in /lojas with manual=true and custom_alias=true
        list_r = s.get(f"{BASE_URL}/api/category-mapping/lojas", timeout=60)
        assert list_r.status_code == 200
        by_id = {i["loja_id"]: i for i in (list_r.json().get("items") or [])}
        assert self.TEST_LOJA_ID in by_id, "new loja not merged into list"
        entry = by_id[self.TEST_LOJA_ID]
        assert entry["manual"] is True
        assert entry["custom_alias"] is True
        assert entry["name"] == "TEST_Loja"

        # Cleanup
        mongo.category_mapping_known_lojas.delete_one({"loja_id": self.TEST_LOJA_ID})
        mongo.category_mapping_loja_aliases.delete_one({"loja_id": self.TEST_LOJA_ID})

        # Verify removed
        list_r2 = s.get(f"{BASE_URL}/api/category-mapping/lojas", timeout=60)
        by_id2 = {i["loja_id"]: i for i in (list_r2.json().get("items") or [])}
        assert self.TEST_LOJA_ID not in by_id2, "test loja not cleaned up"


# --------------------------------------------------------- alias update
class TestLojaAlias:
    ALIAS_LOJA_ID = 205283359

    def test_set_and_reset_alias(self, s, mongo):
        # Snapshot original alias if any
        original = mongo.category_mapping_loja_aliases.find_one(
            {"loja_id": self.ALIAS_LOJA_ID}
        )
        try:
            r = s.put(
                f"{BASE_URL}/api/category-mapping/lojas/alias",
                json={"loja_id": self.ALIAS_LOJA_ID, "alias": "TEST_Mercado Livre BR"},
                timeout=30,
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body.get("ok") is True

            list_r = s.get(f"{BASE_URL}/api/category-mapping/lojas", timeout=60)
            by_id = {i["loja_id"]: i for i in (list_r.json().get("items") or [])}
            if self.ALIAS_LOJA_ID in by_id:
                entry = by_id[self.ALIAS_LOJA_ID]
                assert entry["name"] == "TEST_Mercado Livre BR"
                assert entry["custom_alias"] is True
        finally:
            # Cleanup — delete alias row entirely (not just reset)
            mongo.category_mapping_loja_aliases.delete_one({"loja_id": self.ALIAS_LOJA_ID})
            # If original existed, restore it
            if original:
                original.pop("_id", None)
                mongo.category_mapping_loja_aliases.insert_one(original)


# --------------------------------------------------------- sync-api (dry_run)
class TestSyncApi:
    def test_sync_api_dry_run_and_status(self, s, mongo):
        # Wait for any prior sync to finish (max 3 min)
        deadline_wait = time.time() + 180
        while time.time() < deadline_wait:
            st = s.get(
                f"{BASE_URL}/api/category-mapping/sync-api/status", timeout=30,
            ).json()
            if not st.get("running"):
                break
            time.sleep(5)

        r = s.post(
            f"{BASE_URL}/api/category-mapping/sync-api",
            json={"include_subcategorias": False, "dry_run": True},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "ok" in body or "running" in body
        if body.get("ok"):
            assert body.get("running") is True
            assert body.get("started_at")
        my_started = body.get("started_at")

        # Poll status up to ~4 min
        deadline = time.time() + 240
        last = None
        while time.time() < deadline:
            st = s.get(
                f"{BASE_URL}/api/category-mapping/sync-api/status", timeout=30,
            )
            assert st.status_code == 200
            last = st.json()
            run = last.get("run") or {}
            # ensure looking at the run WE started (or a later one)
            if run.get("status") == "done" and (
                not my_started or run.get("started_at", "") >= my_started
            ):
                break
            if run.get("status") == "error":
                pytest.fail(f"sync-api errored: {run.get('error')}")
            time.sleep(5)
        assert last is not None
        run = last.get("run") or {}
        assert run.get("status") == "done", f"never reached done: {run}"
        assert run.get("phase") == "done"
        assert run.get("total_pairs", 0) >= 0
        # dry_run — no real creations
        assert run.get("created", 0) == 0

    def test_sync_api_status_endpoint(self, s):
        r = s.get(f"{BASE_URL}/api/category-mapping/sync-api/status", timeout=30)
        assert r.status_code == 200
        body = r.json()
        assert "running" in body
        assert "run" in body
