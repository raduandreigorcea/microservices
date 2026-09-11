import pytest
from pydantic import ValidationError

from app.config import Settings

VALID = {
    "database_url": "postgresql+psycopg://u:p@localhost/db",
    "redis_url": "redis://localhost:6379/0",
    "jwt_secret_key": "x" * 32,
}


def test_a_jwt_secret_shorter_than_32_bytes_is_rejected():
    with pytest.raises(ValidationError):
        Settings(**{**VALID, "jwt_secret_key": "x" * 31})


def test_access_tokens_are_short_lived_by_default():
    assert Settings(**VALID).access_token_ttl_seconds <= 900


def test_refresh_tokens_outlive_access_tokens():
    settings = Settings(**VALID)
    assert settings.refresh_token_ttl_seconds > settings.access_token_ttl_seconds


def test_settings_are_read_from_the_environment(monkeypatch):
    for key, value in VALID.items():
        monkeypatch.setenv(key.upper(), value)
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "60")
    assert Settings().access_token_ttl_seconds == 60


def test_a_missing_database_url_is_a_startup_error(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(
            redis_url=VALID["redis_url"],
            jwt_secret_key=VALID["jwt_secret_key"],
        )
