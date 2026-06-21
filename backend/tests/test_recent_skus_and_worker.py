"""Iteration 3 — Recent-SKUs tab + enrich-worker + raw_description persistence.

Covers:
  - GET /api/bling/recent-skus (default + custom limit + Bling hydration)
  - GET /api/enrich/queue (worker.running flag + summary)
  - POST /api/enrich/queue/tick-now (manual tick trigger)
  - POST /api/bling/raw-description (validation + persistence)
  - GET /api/bling/raw-description/{sku}
  - GET /api/bling/products-with-status?filtro=not_enriched
  - GET /api/dashboard/stats
  - bling_variations._variation_sigla() mapping for Azul/Verde/Vermelho
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://bling-product-robot.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"
TEST_SKU = "TEST_RAWDESC_PYTEST_001"


@pytest.fixture(scope="module")
def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ---------- Bling connection prereq ----------
class TestBlingPrereq:
    def test_bling_connected(self, session):
        r = session.get(f"{API}/bling/status", timeout=20)
        assert r.status_code == 200
        data = r.json()
        assert data.get("connected") is True, f"Bling not connected: {data}"


# ---------- Enrich Worker lifecycle ----------
class TestEnrichWorker:
    def test_queue_endpoint_returns_worker_running(self, session):
        r = session.get(f"{API}/enrich/queue?limit=5", timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert "items" in data
        assert "summary" in data
        assert "worker" in data
        worker = data["worker"]
        assert worker.get("running") is True, "enrich_worker is not running"
        assert worker.get("poll_interval_s") == 90
        # last_tick should be ISO string (worker has ticked at least once on startup)
        assert worker.get("last_tick") is not None

    def test_queue_summary_has_all_status_buckets(self, session):
        r = session.get(f"{API}/enrich/queue", timeout=15)
        data = r.json()
        summary = data["summary"]
        for k in ("pending", "processing", "done", "giveup"):
            assert k in summary
            assert isinstance(summary[k], int)

    def test_tick_now_triggers_manual_run(self, session):
        r = session.post(f"{API}/enrich/queue/tick-now", timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True
        assert data.get("triggered") is True


# ---------- Recent SKUs ----------
class TestRecentSkus:
    def test_default_limit_returns_items(self, session):
        r = session.get(f"{API}/bling/recent-skus?limit=50", timeout=60)
        assert r.status_code == 200
        data = r.json()
        assert "items" in data
        assert "total" in data
        assert data.get("limit") == 50
        # Should have at least a few items in this seeded env
        assert len(data["items"]) > 0, "No recent SKUs found — expected seeded products"

    def test_each_item_has_required_fields(self, session):
        r = session.get(f"{API}/bling/recent-skus?limit=10", timeout=60)
        data = r.json()
        items = data["items"]
        assert len(items) > 0
        for it in items:
            for k in ("sku", "nome", "source", "registered_at", "bling_found", "enriched"):
                assert k in it, f"Field {k} missing in item {it}"
            assert isinstance(it["sku"], str) and len(it["sku"]) > 0
            assert isinstance(it["bling_found"], bool)
            assert isinstance(it["enriched"], bool)

    def test_items_sorted_desc_by_registered_at(self, session):
        r = session.get(f"{API}/bling/recent-skus?limit=10", timeout=60)
        items = r.json()["items"]
        ts = [(it.get("registered_at") or "") for it in items]
        # Sorted descending (ISO strings sort lexicographically)
        assert ts == sorted(ts, reverse=True), "Items not sorted desc by registered_at"

    def test_custom_limit_clamped(self, session):
        r = session.get(f"{API}/bling/recent-skus?limit=5", timeout=60)
        data = r.json()
        assert data["limit"] == 5
        assert len(data["items"]) <= 5

    def test_recent_skus_hydrates_bling_state(self, session):
        """At least one item should have bling_found=true with marca/preco fields."""
        r = session.get(f"{API}/bling/recent-skus?limit=20", timeout=120)
        items = r.json()["items"]
        hydrated = [it for it in items if it.get("bling_found")]
        assert len(hydrated) > 0, "No items hydrated from Bling — recent-skus join broken"
        # Hydrated items should have preco field (numeric)
        sample = hydrated[0]
        assert "preco" in sample
        assert "marca" in sample


# ---------- Raw Description persistence ----------
class TestRawDescription:
    def test_post_raw_description_validation_short(self, session):
        r = session.post(
            f"{API}/bling/raw-description",
            json={"sku": "X", "raw_description": "short"},
            timeout=10,
        )
        assert r.status_code == 400

    def test_post_raw_description_validation_missing_sku(self, session):
        r = session.post(
            f"{API}/bling/raw-description",
            json={"sku": "", "raw_description": "a" * 50},
            timeout=10,
        )
        assert r.status_code == 400

    def test_post_then_get_persistence(self, session):
        long_desc = (
            "Produto de teste para persistência via pytest. "
            "Disponível nas cores Azul, Verde, Vermelho. "
            "Material durável e resistente para uso diário."
        )
        # POST
        r = session.post(
            f"{API}/bling/raw-description",
            json={"sku": TEST_SKU, "raw_description": long_desc, "raw_title": "TEST Pytest Product"},
            timeout=15,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["sku"] == TEST_SKU
        assert data["length"] == len(long_desc)

        # GET — verify persistence
        r = session.get(f"{API}/bling/raw-description/{TEST_SKU}", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["sku"] == TEST_SKU
        assert data["exists"] is True
        assert data["raw_description"] == long_desc
        assert data["source"] == "manual"

    def test_get_unknown_sku_returns_exists_false(self, session):
        r = session.get(f"{API}/bling/raw-description/TEST_DOES_NOT_EXIST_PYTEST", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["exists"] is False


# ---------- Existing filter (regression) ----------
class TestProductsWithStatus:
    def test_not_enriched_filter(self, session):
        r = session.get(
            f"{API}/bling/products-with-status?pagina=1&limite=5&filtro=not_enriched",
            timeout=60,
        )
        assert r.status_code == 200
        data = r.json()
        assert "items" in data
        assert data["filtro"] == "not_enriched"
        # All items must be flagged enriched=False
        for it in data["items"]:
            assert it["enriched"] is False, f"not_enriched filter leaked enriched item: {it.get('codigo')}"

    def test_enriched_filter(self, session):
        r = session.get(
            f"{API}/bling/products-with-status?pagina=1&limite=5&filtro=enriched",
            timeout=60,
        )
        assert r.status_code == 200
        data = r.json()
        for it in data["items"]:
            assert it["enriched"] is True


# ---------- Dashboard ----------
class TestDashboard:
    def test_stats_no_errors(self, session):
        r = session.get(f"{API}/dashboard/stats", timeout=10)
        assert r.status_code == 200
        data = r.json()
        for k in ("pricing_rows", "bling_connected", "johndrop_configured",
                  "products_processed_today", "success_today", "failed_today", "robot_state"):
            assert k in data
        assert isinstance(data["pricing_rows"], int)
        assert isinstance(data["bling_connected"], bool)


# ---------- bling_variations sigla mapping (in-process unit) ----------
class TestVariationSigla:
    def test_azul_verde_vermelho(self):
        import sys
        sys.path.insert(0, "/app/backend")
        from bling_variations import _variation_sigla
        assert _variation_sigla("Azul") == "AZ"
        assert _variation_sigla("Verde") == "VD"
        assert _variation_sigla("Vermelho") == "VM"
        assert _variation_sigla("Vermelha") == "VM"

    def test_size_mapping(self):
        import sys
        sys.path.insert(0, "/app/backend")
        from bling_variations import _variation_sigla
        assert _variation_sigla("P") == "P"
        assert _variation_sigla("M") == "M"
        assert _variation_sigla("G") == "G"

    def test_unknown_falls_back_to_first_two(self):
        import sys
        sys.path.insert(0, "/app/backend")
        from bling_variations import _variation_sigla
        # Unknown returns first 2 alnum uppercase
        result = _variation_sigla("Furtacor")
        assert len(result) == 2
        assert result.isupper()


# ---------- Teardown cleanup ----------
@pytest.fixture(scope="module", autouse=True)
def cleanup_test_data(session):
    yield
    # Best-effort cleanup of our seeded TEST raw_description doc
    try:
        # The collection is product_raw; no direct DELETE endpoint exists, leave for next run
        pass
    except Exception:
        pass
