from pathlib import Path
from re import compile
from typing import Literal, Self

from pydantic import AnyHttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ACCESS_KEY_PATTERN = compile(r"[A-Za-z0-9_-]{8,255}")


class Settings(BaseSettings):
    app_name: str = "Requirements Analyst"
    database_url: str = "sqlite:///./requirements.db"
    google_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    access_key: SecretStr | None = None
    session_secret: SecretStr | None = None
    app_env: Literal["development", "production"] = "development"
    session_cookie_secure: bool = False
    app_origin: AnyHttpUrl | None = None

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    @field_validator("app_origin")
    @classmethod
    def validate_origin(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if value and (
            value.path not in (None, "/") or value.username is not None
            or value.password is not None or value.query is not None or value.fragment is not None
        ):
            raise ValueError("APP_ORIGIN must contain only a scheme, host and optional port")
        return value

    @model_validator(mode="after")
    def validate_auth_settings(self) -> Self:
        key = self.access_key.get_secret_value() if self.access_key else ""
        secret = self.session_secret.get_secret_value() if self.session_secret else ""
        if self.app_env == "production" and (not key or not secret):
            raise ValueError("ACCESS_KEY and SESSION_SECRET are required in production")
        if key and not ACCESS_KEY_PATTERN.fullmatch(key):
            raise ValueError("ACCESS_KEY must contain 8-255 Latin letters, digits, hyphens or underscores")
        if secret and len(secret.encode()) < 32:
            raise ValueError("SESSION_SECRET must contain at least 32 bytes")
        if key and key == secret:
            raise ValueError("SESSION_SECRET must differ from ACCESS_KEY")
        return self

    @property
    def secure_session_cookie(self) -> bool:
        return self.app_env == "production" or self.session_cookie_secure


settings = Settings()
