import asyncio
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db.session import create_runtime_engine
from app.db.url import async_database_url


@pytest.mark.anyio
async def test_migrate_existing_sqlite_history(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'migration.sqlite3').as_posix()}"
    backend = Path(__file__).resolve().parents[1]

    async def upgrade(revision: str) -> None:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-B", "-m", "alembic", "upgrade", revision,
            cwd=backend,
            env={**os.environ, "DATABASE_URL": url},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, errors = await process.communicate()
        assert process.returncode == 0, errors.decode(errors="replace")

    await upgrade("0001")
    engine = create_runtime_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("""
                INSERT INTO messages (role, mode, content, created_at)
                VALUES ('user', 'analyst', 'Old idea', CURRENT_TIMESTAMP)
            """))

        await upgrade("head")
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT content, requirements_version FROM messages")
            )
            assert result.one() == ("Old idea", None)
            assert await connection.scalar(text("SELECT revision FROM conversation")) == 0
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002"
    finally:
        await engine.dispose()


def test_legacy_postgresql_url_uses_asyncpg() -> None:
    url = async_database_url("postgresql+psycopg://app:local%40test@db:5432/chat?sslmode=require")
    assert (url.drivername, url.host, url.database) == ("postgresql+asyncpg", "db", "chat")
    assert url.password == "local@test"
    assert url.query == {"ssl": "require"}
