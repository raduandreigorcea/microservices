"""Settings. The gateway stores nothing, so there is no database here."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Values come from the environment. docker compose reads the root .env.
    model_config = SettingsConfigDict(extra="ignore")

    service_name: str = "gateway"

    # Everything the gateway lets through ends up here.
    app_service_url: str = "http://app_service:8001"
    # Bearer tokens are checked against this service, never locally.
    user_service_url: str = "http://user_service:8002"

    request_timeout_seconds: float = Field(default=60.0, gt=0)

    # A token that user_service accepted is trusted for this long before it is
    # asked again. Zero turns the cache off.
    introspection_cache_ttl_seconds: float = Field(default=30.0, ge=0)
    introspection_cache_max_entries: int = Field(default=1024, gt=0)

    # Browsers carry the access token here instead of in a header, because an
    # httpOnly cookie is not readable by script. Set by user_service on login.
    access_cookie_name: str = "access_token"

    # Paths under these prefixes pass through without a token: they are how a
    # caller gets one in the first place, plus the gateway's own pages.
    public_prefixes: str = "/auth,/health,/docs,/redoc,/openapi.json"

    @property
    def public_prefix_list(self) -> tuple[str, ...]:
        return tuple(p.strip() for p in self.public_prefixes.split(",") if p.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()


SettingsDep = Annotated[Settings, Depends(get_settings)]
