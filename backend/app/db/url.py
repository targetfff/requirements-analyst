from sqlalchemy.engine import URL, make_url


def async_database_url(value: str | URL) -> URL:
    url = make_url(value)
    if url.get_backend_name() == "sqlite":
        return url.set(drivername="sqlite+aiosqlite")
    if url.get_backend_name() in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+asyncpg")
        sslmode = url.query.get("sslmode")
        if sslmode is not None:
            if "ssl" not in url.query:
                url = url.update_query_dict({"ssl": sslmode})
            url = url.difference_update_query(["sslmode"])
    return url
