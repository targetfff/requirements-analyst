import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.conversation import Conversation

TEST_ORIGIN = "http://testserver"
TEST_ACCESS_KEY = "test-only-access-key"
TEST_SESSION_SECRET = "test-only-signing-secret-32-bytes-minimum"


@pytest.fixture()
def auth_settings(monkeypatch):
    monkeypatch.setattr(settings, "access_key", SecretStr(TEST_ACCESS_KEY))
    monkeypatch.setattr(settings, "session_secret", SecretStr(TEST_SESSION_SECRET))
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "app_origin", None)
    monkeypatch.setattr(settings, "session_cookie_secure", False)

@pytest.fixture()
def db_engine(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'chat.sqlite3').as_posix()}")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db, db.begin():
        db.add(Conversation(id=1, revision=0))
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def anonymous_client(db_engine, auth_settings):
    def override_db():
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    with TestClient(app, headers={"Origin": TEST_ORIGIN}) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def client(anonymous_client):
    assert anonymous_client.post("/api/auth/login", json={"key": TEST_ACCESS_KEY}).status_code == 200
    return anonymous_client
