"""Regression checks for the managed-host Docker build profile."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_docker_defers_large_browser_download_until_runtime():
    """Railway must not combine pip + Chromium download in one fragile build step."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT=120000" in dockerfile
    assert "RUN pip install --no-cache-dir -r /app/backend/requirements.txt" in dockerfile
    assert "RUN python -m playwright install-deps chromium" in dockerfile
    assert "python -m playwright install --with-deps chromium" not in dockerfile


def test_railway_healthcheck_remains_public_app_probe():
    config = (ROOT / "railway.toml").read_text(encoding="utf-8")
    assert 'healthcheckPath = "/healthz"' in config
    assert "numReplicas = 1" in config
