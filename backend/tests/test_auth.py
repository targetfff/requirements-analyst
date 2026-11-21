from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import AnyHttpUrl, ValidationError

from app.core import session
from app.core.config import Settings, settings
from app.main import app
from tests.conftest import TEST_ACCESS_KEY, TEST_ORIGIN, TEST_SESSION_SECRET


def test_login_and_reload(anonymous_client):
    response = anonymous_client.post("/api/auth/login", json={"key": TEST_ACCESS_KEY})
    assert response.status_code == 200
    assert response.json() == {"authenticated": True, "expires_in": session.SESSION_TTL}
    cookie = response.headers["set-cookie"].lower()
    assert all(flag in cookie for flag in ("httponly", "samesite=lax", "max-age=86400", "path=/api"))
    assert "secure" not in cookie
    assert response.headers["cache-control"] == "no-store"

    with TestClient(app) as reloaded:
        reloaded.cookies.update(anonymous_client.cookies)
        check = reloaded.get("/api/auth/me")
    assert check.status_code == 200
    assert check.json()["authenticated"] is True
    assert 0 < check.json()["expires_in"] <= session.SESSION_TTL
    assert check.headers["cache-control"] == "no-store"


def test_wrong_key(anonymous_client):
    response = anonymous_client.post("/api/auth/login", json={"key": "different-valid-key"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Неверный ключ доступа."
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("key", ["short-7", "a" * 256, "bad key_123", "ключ-доступа", "bad+key_123", 12345678, None])
def test_invalid_key_format(anonymous_client, key):
    response = anonymous_client.post("/api/auth/login", json={"key": key})
    assert response.status_code == 422
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("key", ["a_12345-", "A" * 255])
def test_valid_key_length_boundaries(anonymous_client, key):
    assert anonymous_client.post("/api/auth/login", json={"key": key}).status_code == 401


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/auth/me"), ("GET", "/api/chat"), ("GET", "/api/messages"),
    ("POST", "/api/messages"), ("DELETE", "/api/messages"),
])
def test_protected_routes_without_session(anonymous_client, method, path):
    args = {"json": {"text": "Idea"}} if method == "POST" else {}
    assert anonymous_client.request(method, path, **args).status_code == 401


def test_authorized_reads_and_logout(client):
    assert client.get("/api/chat").json() == {"messages": [], "requirements": None}
    response = client.get("/api/messages")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    response = client.post("/api/auth/logout")
    assert response.status_code == 204
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/chat").status_code == 401


def test_expired_session(anonymous_client, monkeypatch):
    clock = SimpleNamespace(time=lambda: 1_800_000_000)
    monkeypatch.setattr(session, "time", clock)
    assert anonymous_client.post("/api/auth/login", json={"key": TEST_ACCESS_KEY}).status_code == 200
    clock.time = lambda: 1_800_000_000 + session.SESSION_TTL
    assert anonymous_client.get("/api/auth/me").status_code == 401


def test_modified_session_cookie(client):
    token = client.cookies.get(session.SESSION_COOKIE)
    replacement = "0" if token[-1] != "0" else "1"
    forged = token[:-1] + replacement
    client.cookies.clear()
    client.cookies.set(session.SESSION_COOKIE, forged, path="/api")
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/messages").status_code == 401


@pytest.mark.parametrize("origin", [None, "null", "https://elsewhere.test", "http://testserver:8000"])
def test_csrf_blocks_mutations(client, origin):
    client.headers.pop("origin", None)
    if origin is not None:
        client.headers["Origin"] = origin
    assert client.post("/api/auth/login", json={"key": TEST_ACCESS_KEY}).status_code == 403
    assert client.post("/api/auth/logout").status_code == 403
    assert client.post("/api/messages", json={"text": "Idea"}).status_code == 403
    assert client.delete("/api/messages").status_code == 403
    assert client.get("/api/auth/me").status_code == 200


def test_production_can_use_public_https_origin(auth_settings, monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "app_origin", AnyHttpUrl("https://requirements.test"))
    with TestClient(app, base_url="https://requirements.test", headers={"Origin": "https://requirements.test"}) as client:
        response = client.post("/api/auth/login", json={"key": TEST_ACCESS_KEY})
        assert response.status_code == 200
        assert "Secure" in response.headers["set-cookie"]
        assert client.get("/api/auth/me").status_code == 200
        assert client.post("/api/auth/logout", headers={"Origin": "http://requirements.test"}).status_code == 403


def test_secrets_are_not_returned_or_logged(anonymous_client, caplog):
    responses = [
        anonymous_client.post("/api/auth/login", json={"key": [TEST_ACCESS_KEY, TEST_SESSION_SECRET]}),
        anonymous_client.post("/api/auth/login", json={"key": TEST_ACCESS_KEY}),
        anonymous_client.get("/api/auth/me"),
    ]
    assert [response.status_code for response in responses] == [422, 200, 200]
    for secret in (TEST_ACCESS_KEY, TEST_SESSION_SECRET):
        assert secret not in caplog.text
        for response in responses:
            assert secret not in response.text
            assert secret not in str(response.headers)


def test_production_requires_secrets(monkeypatch):
    for variable in ("ACCESS_KEY", "SESSION_SECRET"):
        monkeypatch.delenv(variable, raising=False)
    with pytest.raises(ValidationError, match="ACCESS_KEY and SESSION_SECRET are required in production"):
        Settings(_env_file=None, app_env="production")


def test_secure_cookie_override_for_development(auth_settings, monkeypatch):
    monkeypatch.setattr(settings, "session_cookie_secure", True)
    with TestClient(app, headers={"Origin": TEST_ORIGIN}) as client:
        response = client.post("/api/auth/login", json={"key": TEST_ACCESS_KEY})
    assert "Secure" in response.headers["set-cookie"]
