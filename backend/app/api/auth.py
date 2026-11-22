import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, SecretStr, field_validator

from app.core.config import ACCESS_KEY_PATTERN, settings
from app.core.session import (
    SESSION_COOKIE,
    SESSION_TTL,
    create_session,
    require_same_origin,
    require_session,
)


class AuthRoute(APIRoute):
    def get_route_handler(self):
        handle = super().get_route_handler()

        async def handle_auth(request: Request) -> Response:
            try:
                return await handle(request)
            except RequestValidationError:
                # FastAPI's default 422 response includes submitted input.
                return JSONResponse(
                    status_code=422,
                    content={"detail": "Ключ должен содержать от 8 до 255 латинских букв, цифр, дефисов или подчёркиваний."},
                    headers={"Cache-Control": "no-store"},
                )

        return handle_auth


router = APIRouter(prefix="/api/auth", tags=["auth"], route_class=AuthRoute)


class LoginRequest(BaseModel):
    key: SecretStr

    @field_validator("key")
    @classmethod
    def check_format(cls, value: SecretStr) -> SecretStr:
        if not ACCESS_KEY_PATTERN.fullmatch(value.get_secret_value()):
            raise ValueError("Invalid key format")
        return value


class SessionRead(BaseModel):
    authenticated: bool = True
    expires_in: int


@router.post("/login", dependencies=[Depends(require_same_origin)])
def login(payload: LoginRequest, response: Response) -> SessionRead:
    if not settings.access_key or not settings.session_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Вход пока не настроен на сервере.")
    if not hmac.compare_digest(payload.key.get_secret_value(), settings.access_key.get_secret_value()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный ключ доступа.")
    response.set_cookie(
        SESSION_COOKIE, create_session(), max_age=SESSION_TTL, path="/api",
        httponly=True, samesite="lax", secure=settings.secure_session_cookie,
    )
    response.headers["Cache-Control"] = "no-store"
    return SessionRead(expires_in=SESSION_TTL)


@router.get("/me")
def me(remaining: int = Depends(require_session)) -> SessionRead:
    return SessionRead(expires_in=remaining)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_session)])
def logout(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE, path="/api", httponly=True, samesite="lax", secure=settings.secure_session_cookie,
    )
