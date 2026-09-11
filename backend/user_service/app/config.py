"""Settings and the connections built from them."""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class Settings(BaseSettings):
    # Values come from the environment. docker compose reads the root .env.
    model_config = SettingsConfigDict(extra="ignore")

    service_name: str = "user_service"
    database_url: str
    redis_url: str

    # RFC 7518 puts the floor for HS256 at 32 bytes.
    jwt_secret_key: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "user_service"

    access_token_ttl_seconds: int = Field(default=900, gt=0)
    refresh_token_ttl_seconds: int = Field(default=14 * 24 * 3600, gt=0)

    # Revoked access tokens live in Redis until they would have expired anyway.
    token_denylist_prefix: str = "user_service:denylist:"

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8002/auth/google/callback"
    # Where to send the browser once login succeeds. Empty means return JSON.
    login_success_redirect: str = ""

    # A pending authorization holds its PKCE verifier here until the callback.
    oauth_state_prefix: str = "user_service:oauth_state:"
    oauth_state_ttl_seconds: int = 600


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True, future=True)


@lru_cache
def get_session_factory(database_url: str) -> async_sessionmaker:
    return async_sessionmaker(
        bind=get_engine(database_url), expire_on_commit=False, autoflush=False
    )


@lru_cache
def get_redis_client(redis_url: str) -> Redis:
    return Redis.from_url(redis_url, decode_responses=True)


SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_session(settings: SettingsDep) -> AsyncIterator[AsyncSession]:
    factory = get_session_factory(settings.database_url)
    async with factory() as session:
        yield session


async def get_redis(settings: SettingsDep) -> Redis:
    return get_redis_client(settings.redis_url)


SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
