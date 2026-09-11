"""Settings. app_service owns no tables, so there is no database here."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Values come from the environment. docker compose reads the root .env.
    model_config = SettingsConfigDict(extra="ignore")

    service_name: str = "app_service"

    user_service_url: str = "http://user_service:8002"
    scraper_service_url: str = "http://scraper_service:8003"

    request_timeout_seconds: float = Field(default=60.0, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()


SettingsDep = Annotated[Settings, Depends(get_settings)]
