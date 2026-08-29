"""Unit tests for Bling OAuth v3 (URLs, JWT headers, state, public origin)."""
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "bling_robot_test")
os.environ.setdefault("APP_SECRET", "unit-test-secret")
os.environ.setdefault("BLING_CLIENT_ID", "test-client-id")
os.environ.setdefault("BLING_CLIENT_SECRET", "test-client-secret")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bling_service  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def test_authorize_url_uses_official_v3_path():
    async def _go():
        with patch.object(bling_service, "_load_stored_creds", AsyncMock(return_value=("", ""))):
            url = await bling_service.build_authorize_url(
                next_path="/configuracoes",
                app_base="https://app.totyshop.example",
            )
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        assert parsed.netloc in ("www.bling.com.br", "bling.com.br")
        assert parsed.path == "/Api/v3/oauth/authorize"
        assert qs["response_type"] == ["code"]
        assert qs["client_id"] == ["test-client-id"]
        assert qs["redirect_uri"] == ["https://app.totyshop.example/api/bling/callback"]
        assert "state" in qs
        return qs["state"][0]

    state = _run(_go())
    payload = bling_service.parse_state(state)
    assert payload["next"] == "/configuracoes"
    assert payload["redirect_uri"] == "https://app.totyshop.example/api/bling/callback"


def test_friendly_oauth_maps_invalid_client():
    msg = bling_service.friendly_oauth_error(
        '{"error":{"type":"invalid_client","description":"The client credentials are invalid"}}'
    )
    assert "Client ID" in msg
    assert "Client Secret" in msg
    assert "invalid_client" not in msg.lower()


def test_legacy_oauth_path_is_not_used():
    assert bling_service.DEFAULT_AUTHORIZE_URL.endswith("/Api/v3/oauth/authorize")
    assert "/oauth/authorize" != urlparse(bling_service.DEFAULT_AUTHORIZE_URL).path
    assert bling_service.DEFAULT_TOKEN_URL.endswith("/Api/v3/oauth/token")


def test_token_headers_enable_jwt_and_accept_1():
    headers = bling_service._token_headers("id", "secret")
    assert headers["enable-jwt"] == "1"
    assert headers["Accept"] == "1.0"
    assert headers["Authorization"].startswith("Basic ")
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"


def test_api_headers_enable_jwt():
    headers = bling_service._api_headers("tok", "Bearer")
    assert headers["Authorization"] == "Bearer tok"
    assert headers["enable-jwt"] == "1"


def test_public_base_url_prefers_origin():
    assert (
        bling_service.public_base_url(origin="https://preview.example/")
        == "https://preview.example"
    )


def test_redirect_uri_strips_slash():
    assert (
        bling_service.redirect_uri("https://shop.example/")
        == "https://shop.example/api/bling/callback"
    )


def test_state_rejects_tampering():
    token = bling_service.make_state({"next": "/x", "redirect_uri": "https://a/api/bling/callback"})
    try:
        bling_service.parse_state(token + "nope")
        assert False, "should have raised"
    except Exception as exc:
        assert "State inválido" in str(exc)


def test_exchange_code_posts_jwt_header():
    captured = {}

    class FakeResp:
        status_code = 200
        text = '{"access_token":"A","refresh_token":"R","expires_in":21600}'

        def json(self):
            return {"access_token": "A", "refresh_token": "R", "expires_in": 21600}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, headers=None):
            captured["url"] = url
            captured["data"] = data
            captured["headers"] = headers
            return FakeResp()

    async def _go():
        with patch.object(bling_service, "_load_stored_creds", AsyncMock(return_value=("", ""))), \
             patch("bling_service.httpx.AsyncClient", FakeClient):
            return await bling_service.exchange_code(
                "auth-code",
                callback_uri="https://app.example/api/bling/callback",
            )

    result = _run(_go())
    assert result["access_token"] == "A"
    assert captured["headers"]["enable-jwt"] == "1"
    assert captured["headers"]["Accept"] == "1.0"
    assert captured["data"]["grant_type"] == "authorization_code"
    assert captured["data"]["code"] == "auth-code"
    assert captured["data"]["redirect_uri"] == "https://app.example/api/bling/callback"
    assert "/Api/v3/oauth/token" in captured["url"] or captured["url"].endswith("/oauth/token")
