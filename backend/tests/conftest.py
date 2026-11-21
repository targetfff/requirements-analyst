import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.conversation import Conversation


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
def client(db_engine):
    def override_db():
        with Session(db_engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
