import hmac
import re
import secrets
import time

from fastapi import HTTPException, Request, Response, status

from app.core.config import settings

SESSION_COOKIE = "requirements_session"
SESSION_TTL = 24 * 60 * 60
SESSION_PATTERN = re.compile(r"([0-9]{1,12})\.([A-Za-z0-9_-]{22})\.([0-9a-f]{64})")


def _sign(payload: str) -> str:
    secret = settings.session_secret
    assert secret is not None
    message = f"requirements-session-v1:{payload}".encode("ascii")
    return hmac.digest(secret.get_secret_value().encode(), message, "sha256").hex()


def create_session() -> str:
    expires_at = int(time.time()) + SESSION_TTL
    payload = f"{expires_at}.{secrets.token_urlsafe(16)}"
    return f"{payload}.{_sign(payload)}"


def require_same_origin(request: Request) -> None:
    expected = str(settings.app_origin).rstrip("/") if settings.app_origin else (
        f"{request.url.scheme}://{request.url.netloc}"
    )
    if request.headers.getlist("origin") != [expected]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недопустимый источник запроса.")


def require_session(request: Request, response: Response) -> int:
    cookie = request.cookies.get(SESSION_COOKIE, "")
    match = SESSION_PATTERN.fullmatch(cookie)
    if settings.access_key and settings.session_secret and match:
        expiry, nonce, signature = match.groups()
        remaining = int(expiry) - int(time.time())
        if remaining > 0 and hmac.compare_digest(signature, _sign(f"{expiry}.{nonce}")):
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                require_same_origin(request)
            response.headers["Cache-Control"] = "no-store"
            return remaining
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется вход по ключу доступа.")
