import pytest
from httpx import ASGITransport, AsyncClient

from app.core import session
from app.main import app
from tests.conftest import TEST_ACCESS_KEY, TEST_ORIGIN, TEST_SESSION_SECRET

pytestmark = pytest.mark.anyio

async def test_login_and_reload(anonymous_client: AsyncClient) -> None:
    response = await anonymous_client.post("/api/auth/login", json={"key": TEST_ACCESS_KEY})
    assert response.status_code == 200
    assert response.json() == {"authenticated": True, "expires_in": session.SESSION_TTL}
    cookie = response.headers["set-cookie"].lower()
    assert all(flag in cookie for flag in ("httponly", "samesite=lax", "max-age=86400", "path=/api"))
    assert "secure" not in cookie
    assert response.headers["cache-control"] == "no-store"

    async with AsyncClient(transport=ASGITransport(app=app), base_url=TEST_ORIGIN) as reloaded:
        reloaded.cookies.update(anonymous_client.cookies)
        check = await reloaded.get("/api/auth/me")
    assert check.status_code == 200
    assert check.json()["authenticated"] is True
    assert 0 < check.json()["expires_in"] <= session.SESSION_TTL
    assert check.headers["cache-control"] == "no-store"


async def test_wrong_key(anonymous_client: AsyncClient) -> None:
    response = await anonymous_client.post("/api/auth/login", json={"key": "different-valid-key"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Неверный ключ доступа."
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("key", ["short-7", "a" * 256, "bad key_123"])
async def test_invalid_key_format(anonymous_client: AsyncClient, key: str) -> None:
    response = await anonymous_client.post("/api/auth/login", json={"key": key})
    assert response.status_code == 422
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("key", ["a_12345-", "A" * 255])
async def test_valid_key_length_boundaries(
    anonymous_client: AsyncClient,
    key: str | int | None,
) -> None:
    assert (await anonymous_client.post("/api/auth/login", json={"key": key})).status_code == 401


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/chat"),
    ("POST", "/api/messages"),
])
async def test_protected_routes_without_session(
    anonymous_client: AsyncClient,
    method: str,
    path: str,
) -> None:
    args = {"json": {"text": "Idea"}} if method == "POST" else {}
    assert (await anonymous_client.request(method, path, **args)).status_code == 401


async def test_authorized_reads_and_logout(client: AsyncClient) -> None:
    assert (await client.get("/api/chat")).json() == {"messages": [], "requirements": None}
    response = await client.get("/api/messages")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    response = await client.post("/api/auth/logout")
    assert response.status_code == 204
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await client.get("/api/chat")).status_code == 401


async def test_modified_session_cookie(client: AsyncClient) -> None:
    token = client.cookies.get(session.SESSION_COOKIE)
    replacement = "0" if token[-1] != "0" else "1"
    forged = token[:-1] + replacement
    client.cookies.clear()
    client.cookies.set(session.SESSION_COOKIE, forged, path="/api")
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await client.get("/api/messages")).status_code == 401


async def test_csrf_blocks_mutations(client: AsyncClient) -> None:
    client.headers["Origin"] = "https://elsewhere.test"
    assert (await client.post("/api/auth/login", json={"key": TEST_ACCESS_KEY})).status_code == 403
    assert (await client.post("/api/auth/logout")).status_code == 403
    assert (await client.post("/api/messages", json={"text": "Idea"})).status_code == 403
    assert (await client.delete("/api/messages")).status_code == 403
    assert (await client.get("/api/auth/me")).status_code == 200


async def test_secrets_are_not_returned_or_logged(
    anonymous_client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    responses = [
        await anonymous_client.post(
            "/api/auth/login", json={"key": [TEST_ACCESS_KEY, TEST_SESSION_SECRET]},
        ),
        await anonymous_client.post("/api/auth/login", json={"key": TEST_ACCESS_KEY}),
        await anonymous_client.get("/api/auth/me"),
    ]
    assert [response.status_code for response in responses] == [422, 200, 200]
    for secret in (TEST_ACCESS_KEY, TEST_SESSION_SECRET):
        assert secret not in caplog.text
        for response in responses:
            assert secret not in response.text
            assert secret not in str(response.headers)
