import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_production_rejects_insecure_configuration() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production", cookie_secure=False)
    with pytest.raises(ValidationError):
        Settings(environment="production", cookie_secure=True, allowed_origins=["http://localhost"])
    with pytest.raises(ValidationError):
        Settings(environment="development", allowed_origins=["*"])
    settings = Settings(
        environment="production",
        cookie_secure=True,
        allowed_origins=["https://omnimind.example"],
        database_url="postgresql+psycopg://app:unique@db/omnimind",
    )
    assert settings.cookie_name == "__Host-omnimind_session"
