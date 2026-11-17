from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../../.env", extra="ignore")
    environment: Literal["development", "production", "test"] = "production"
    database_url: str = "postgresql+psycopg://omnimind:change-me@localhost:5432/omnimind"
    allowed_origins: list[str] = ["https://localhost"]
    allowed_hosts: list[str] = ["localhost", "127.0.0.1"]
    cookie_secure: bool = True
    session_hours: int = 8
    login_limit: int = 10
    login_window_seconds: int = 900

    @property
    def cookie_name(self) -> str:
        return "__Host-omnimind_session" if self.cookie_secure else "omnimind_session"

    @model_validator(mode="after")
    def secure_production(self) -> "Settings":
        if self.session_hours < 1 or self.login_limit < 1 or self.login_window_seconds < 1:
            raise ValueError("Security limits must be positive")
        if not self.allowed_origins or any("*" in origin for origin in self.allowed_origins):
            raise ValueError("Explicit origins are required")
        if self.environment == "production":
            if not self.cookie_secure or any(
                not origin.startswith("https://") for origin in self.allowed_origins
            ):
                raise ValueError("Production requires HTTPS origins and secure cookies")
            if "change-me" in self.database_url:
                raise ValueError("Configure production database credentials")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
