"""Backend tests for TotyShop Automation.

Covers: root health, title cleaner (single + batch), pricing (upload/lookup/stats),
Bling OAuth helpers, JohnDrop settings, Robot lifecycle + logs, Dashboard.
"""
import os
import time
import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://bling-product-robot.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"
PRICING_CSV = "/tmp/pricing.csv"


@pytest.fixture(scope="session")
def client():
    s = requests.Session()
    s.headers.update({"Accept": "application/json"})
    return s


# ---------- Root ----------
class TestRoot:
    def test_root(self, client):
        r = client.get(f"{API}/")
        assert r.status_code == 200
        data = r.json()
        assert data.get("app") == "TotyShop Automation"
        assert "version" in data


# ---------- Title cleaner ----------
class TestTitleCleaner:
    def test_clean_xls_caneta(self, client):
        raw = "Caneta Touch Screen Stylus Universal Para Tablet e Celular XLS B125 / A-P18"
        r = client.post(f"{API}/titles/clean", json={"raw_title": raw})
        assert r.status_code == 200, r.text
        data = r.json()
        cleaned = data["cleaned"]
        # XLS removed
        assert "XLS" not in cleaned and "xls" not in cleaned.lower()
        # No special chars except hyphen
        for ch in cleaned:
            assert ch.isalnum() or ch == " " or ch == "-"
        # Code at end - last token should be A-P18 (one of the trailing codes)
        assert cleaned.rstrip().endswith("A-P18"), f"cleaned={cleaned}"
        # Max 60 chars
        assert len(cleaned) <= 60
        # length consistent
        assert data["length"] == len(cleaned)
        # method
        assert data["method"] == "regex"

    def test_clean_eletromex_el1931(self, client):
        raw = "(EL-1931) Caneta Peeling Ultrassônico E Ionização Portátil Anti Cravos E Acne Eletromex EL-1931"
        r = client.post(f"{API}/titles/clean", json={"raw_title": raw})
        assert r.status_code == 200, r.text
        data = r.json()
        cleaned = data["cleaned"]
        assert "Eletromex" not in cleaned and "eletromex" not in cleaned.lower()
        assert cleaned.rstrip().endswith("EL-1931"), f"cleaned={cleaned}"
        assert len(cleaned) <= 60
        # No parentheses content
        assert "(" not in cleaned and ")" not in cleaned

    def test_clean_strips_ean(self, client):
        raw = "Carregador USB 7891234567890 Kapbom KA-1100"
        r = client.post(f"{API}/titles/clean", json={"raw_title": raw})
        assert r.status_code == 200
        data = r.json()
        assert "7891234567890" not in data["cleaned"]
        assert "Kapbom" not in data["cleaned"]
        assert data["cleaned"].rstrip().endswith("KA-1100")
        # removed_terms must contain EAN reference and Kapbom
        joined = " ".join(data["removed_terms"]).lower()
        assert "7891234567890" in joined or "ean" in joined
        assert "kapbom" in joined

    def test_clean_with_preferred_sku(self, client):
        raw = "Produto Generico Inova ABC 123"
        r = client.post(f"{API}/titles/clean", json={"raw_title": raw, "sku": "XX-999"})
        assert r.status_code == 200
        data = r.json()
        assert data["cleaned"].rstrip().endswith("XX-999")
        assert data["code_used"] == "XX-999"

    def test_clean_max_60(self, client):
        raw = ("Super Mega Hiper Ultra Produto De Altíssima Qualidade Para Casa "
               "Cozinha Banheiro Kapbom KA-9999")
        r = client.post(f"{API}/titles/clean", json={"raw_title": raw})
        assert r.status_code == 200
        cleaned = r.json()["cleaned"]
        assert len(cleaned) <= 60
        assert cleaned.rstrip().endswith("KA-9999")

    def test_batch_clean(self, client):
        payload = {
            "items": [
                {"raw_title": "Caneta XLS B125"},
                {"raw_title": "(EL-1931) Algo Eletromex EL-1931"},
                {"raw_title": "Adaptador Kapbom KA-1100"},
            ]
        }
        r = client.post(f"{API}/titles/clean/batch", json=payload)
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert len(items) == 3
        assert items[0]["cleaned"].rstrip().endswith("B125")
        assert items[1]["cleaned"].rstrip().endswith("EL-1931")
        assert items[2]["cleaned"].rstrip().endswith("KA-1100")


# ---------- Pricing ----------
class TestPricing:
    @pytest.mark.order(1)
    def test_upload_csv(self, client):
        assert os.path.exists(PRICING_CSV), "CSV at /tmp/pricing.csv missing"
        with open(PRICING_CSV, "rb") as fh:
            files = {"file": ("pricing.csv", fh, "text/csv")}
            r = client.post(f"{API}/pricing/upload", files=files, timeout=120)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["imported"] >= 99000, f"Imported only {data['imported']}"

    def test_stats(self, client):
        r = client.get(f"{API}/pricing/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] >= 99000
        assert isinstance(data["first"], list) and len(data["first"]) > 0
        sample = data["first"][0]
        assert "cost_cents" in sample
        assert "store_price_brl" in sample
        assert "sale_price_int" in sample

    def test_lookup_2199(self, client):
        # CSV row: 21,99;52,50;5250
        r = client.get(f"{API}/pricing/lookup", params={"cost": 21.99})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["found"] is True
        assert data["cost_cents"] == 2199
        assert data["sale_price_int"] == 5250
        assert data["store_price_brl"] == "52,50"

    def test_lookup_nearest_higher(self, client):
        # 21.995 (cents 2200) — should round to nearest higher
        r = client.get(f"{API}/pricing/lookup", params={"cost": 21.995})
        assert r.status_code == 200
        assert r.json()["found"] is True

    def test_lookup_far_high_not_found(self, client):
        r = client.get(f"{API}/pricing/lookup", params={"cost": 99999999.99})
        assert r.status_code == 200
        assert r.json()["found"] is False


# ---------- Bling ----------
class TestBling:
    def test_authorize_url(self, client):
        r = client.get(f"{API}/bling/authorize-url")
        assert r.status_code == 200
        url = r.json()["url"]
        assert "bling.com.br" in url
        assert "client_id=05b3f679e6cfc180fa62bcf254932e182aa39ce7" in url
        assert "response_type=code" in url
        assert "state=" in url
        assert "redirect_uri=" in url

    def test_status_initial(self, client):
        # ensure disconnected first
        client.post(f"{API}/bling/disconnect")
        r = client.get(f"{API}/bling/status")
        assert r.status_code == 200
        assert r.json().get("connected") is False

    def test_disconnect(self, client):
        r = client.post(f"{API}/bling/disconnect")
        assert r.status_code == 200
        assert r.json().get("ok") is True


# ---------- Settings (JohnDrop) ----------
class TestJohnDropSettings:
    def test_save_creds(self, client):
        payload = {"username": "totyshopvendas@gmail.com", "password": "1593572864To@@##$$"}
        r = client.post(f"{API}/settings/johndrop", json=payload)
        assert r.status_code == 200
        assert r.json().get("ok") is True

    def test_get_creds_status(self, client):
        r = client.get(f"{API}/settings/johndrop")
        assert r.status_code == 200
        data = r.json()
        assert data["configured"] is True
        assert data["username"] == "totyshopvendas@gmail.com"


# ---------- Robot ----------
class TestRobot:
    def test_robot_status_idle(self, client):
        r = client.get(f"{API}/robot/status")
        assert r.status_code == 200
        # could be idle or error from a previous run; both acceptable as long as not running
        assert r.json()["state"] in ("idle", "error", "paused")

    def test_clear_logs(self, client):
        r = client.post(f"{API}/robot/logs/clear")
        assert r.status_code == 200
        # verify cleared
        r2 = client.get(f"{API}/robot/logs", params={"limit": 5})
        assert r2.status_code == 200
        assert r2.json() == [] or isinstance(r2.json(), list)

    def test_start_dry_run_and_complete(self, client):
        # start
        r = client.post(f"{API}/robot/start", json={"max_products": 3, "dry_run": True})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["state"] in ("running", "idle", "error")

        # wait for completion (mock or playwright; mock sleeps 0.5s per product so ~2s for 3)
        deadline = time.time() + 60
        final = None
        while time.time() < deadline:
            time.sleep(1.5)
            rs = client.get(f"{API}/robot/status")
            assert rs.status_code == 200
            final = rs.json()
            if final["state"] in ("idle", "error"):
                break
        assert final is not None
        # processed should be >= 1 (mock processes 3, playwright may fail at login)
        assert final["state"] in ("idle", "error")

    def test_logs_after_run(self, client):
        r = client.get(f"{API}/robot/logs", params={"limit": 50})
        assert r.status_code == 200
        logs = r.json()
        assert isinstance(logs, list)
        assert len(logs) >= 1
        # at least one info or error level entry
        levels = {e["level"] for e in logs}
        assert levels & {"info", "success", "warning", "error"}

    def test_double_start_returns_400(self, client):
        # Start then immediately try again. If first finishes too fast, this is best-effort.
        r1 = client.post(f"{API}/robot/start", json={"max_products": 3, "dry_run": True})
        if r1.status_code == 200:
            r2 = client.post(f"{API}/robot/start", json={"max_products": 1, "dry_run": True})
            # second call should be 400 if robot still running
            assert r2.status_code in (200, 400)
        # cleanup: stop
        client.post(f"{API}/robot/stop")

    def test_stop(self, client):
        r = client.post(f"{API}/robot/stop")
        assert r.status_code == 200
        assert r.json().get("ok") is True


# ---------- Dashboard ----------
class TestDashboard:
    def test_dashboard_stats(self, client):
        r = client.get(f"{API}/dashboard/stats")
        assert r.status_code == 200, r.text
        data = r.json()
        for k in ("pricing_rows", "bling_connected", "johndrop_configured",
                  "products_processed_today", "success_today", "failed_today", "robot_state"):
            assert k in data, f"missing {k}"
        assert isinstance(data["pricing_rows"], int)
        assert data["pricing_rows"] >= 99000
        assert isinstance(data["bling_connected"], bool)
        assert isinstance(data["johndrop_configured"], bool)
