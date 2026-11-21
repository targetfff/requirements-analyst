from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import settings
from app.db.base import Base
from app.db.dependencies import get_db
from app.db.session import create_runtime_engine
from app.main import app
from app.models.conversation import Conversation
from app.repositories.conversation import CONVERSATION_ID

TEST_ORIGIN = "http://testserver"
TEST_ACCESS_KEY = "test-only-access-key"
TEST_SESSION_SECRET = "test-only-signing-secret-32-bytes-minimum"


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture()
def auth_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "access_key", SecretStr(TEST_ACCESS_KEY))
    monkeypatch.setattr(settings, "session_secret", SecretStr(TEST_SESSION_SECRET))
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "app_origin", None)
    monkeypatch.setattr(settings, "session_cookie_secure", False)

@pytest.fixture()
async def db_engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    engine = create_runtime_engine(f"sqlite+aiosqlite:///{(tmp_path / 'chat.sqlite3').as_posix()}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture()
async def db_sessions(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    factory = async_sessionmaker(db_engine, autoflush=False, expire_on_commit=False)
    async with factory.begin() as db:
        db.add(Conversation(id=CONVERSATION_ID, revision=0))
    return factory


@pytest.fixture()
async def anonymous_client(
    db_sessions: async_sessionmaker[AsyncSession], auth_settings: None,
) -> AsyncIterator[AsyncClient]:
    async def override_db() -> AsyncIterator[AsyncSession]:
        async with db_sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=TEST_ORIGIN, headers={"Origin": TEST_ORIGIN},
        ) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
async def client(anonymous_client: AsyncClient) -> AsyncClient:
    response = await anonymous_client.post("/api/auth/login", json={"key": TEST_ACCESS_KEY})
    assert response.status_code == 200
    return anonymous_client
