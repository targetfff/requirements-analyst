from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.routes import router
from app.core.config import settings

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Requirements analysis with Gemini",
)
app.include_router(router)
app.include_router(auth_router)
