from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.url import async_database_url


def create_runtime_engine(database_url: str | URL) -> AsyncEngine:
    url = async_database_url(database_url)
    # SQLite starts a real read transaction so a snapshot spans all its SELECTs.
    connect_args = {"autocommit": False} if url.get_backend_name() == "sqlite" else {}
    return create_async_engine(url, pool_pre_ping=True, connect_args=connect_args)


engine = create_runtime_engine(settings.database_url)
async_session_factory = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
