"""The iframe/CORS middleware must not use BaseHTTPMiddleware.call_next."""
import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server  # noqa: E402


def test_asgi_header_middleware_keeps_robot_status_response():
    client = TestClient(server.app)
    response = client.get("/api/robot/status")

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == "frame-ancestors *"
    assert response.headers["access-control-allow-origin"] == "*"
    assert "x-frame-options" not in response.headers
