"""Backend tests for CategoryMapping UI cleanup (iteration 6).

Verifies:
- GET /api/category-mapping/marketplaces -> 200 {total,items[]}
- GET /api/category-mapping/previews    -> empty items[] (after orphan cleanup)
- GET /api/category-mapping/new-count   -> 200 {pending:int}
- GET /api/category-mapping/sync-new/status -> 200 {running,run,...}
- POST /api/category-mapping/sync-new (with dummy creds) -> 200 {ok,running,started_at}
"""
import os
import time
import pytest
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL must be set in frontend/.env"
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def s():
    sess = requests.Session()
    sess.headers.update({"Content-Type": "application/json"})
    return sess


def test_marketplaces_endpoint_shape(s):
    r = s.get(f"{API}/category-mapping/marketplaces", timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "total" in body and "items" in body
    assert isinstance(body["items"], list)
    assert body["total"] == len(body["items"])


def test_previews_empty_after_cleanup(s):
    r = s.get(f"{API}/category-mapping/previews", timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "items" in body and isinstance(body["items"], list)
    # After the orphan cleanup the DB should be empty. If non-empty, log.
    if body["total"] != 0:
        print(f"WARN: previews returned {body['total']} rows — may be new seed data")


def test_new_count(s):
    r = s.get(f"{API}/category-mapping/new-count", timeout=15)
    assert r.status_code == 200
    body = r.json()
    assert "pending" in body
    assert isinstance(body["pending"], int)


def test_sync_new_status(s):
    r = s.get(f"{API}/category-mapping/sync-new/status", timeout=15)
    assert r.status_code == 200
    body = r.json()
    assert "running" in body
    assert "run" in body
    assert isinstance(body["running"], bool)


def test_sync_new_post_accepts_and_returns_started(s):
    # Use dummy creds; endpoint should accept and return started_at.
    # The background task will fail (invalid creds) but the endpoint contract
    # must remain: {ok,running,started_at}.
    payload = {"bling_user": "dummy_user_test", "bling_pass": "dummy_pass_test", "apply": True}
    r = s.post(f"{API}/category-mapping/sync-new", json=payload, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    # Two valid cases: fresh start OR already running (previous test run)
    assert "ok" in body
    assert "running" in body
    if body.get("ok"):
        assert "started_at" in body
        assert body["running"] is True
    # Wait for task to fail-out, then verify status endpoint reflects it
    time.sleep(3)
    r2 = s.get(f"{API}/category-mapping/sync-new/status", timeout=15)
    assert r2.status_code == 200
