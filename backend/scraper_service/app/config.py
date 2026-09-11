"""Settings and the connections built from them."""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from neo4j import AsyncDriver, AsyncGraphDatabase
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

    service_name: str = "scraper_service"
    database_url: str
    redis_url: str

    # INFO narrates every request and every page the browser opens. WARNING
    # leaves only the failures.
    log_level: str = "INFO"

    # Where to check the bearer tokens this service is handed.
    user_service_url: str = "http://user_service:8002"
    introspection_cache_prefix: str = "scraper_service:introspect:"
    introspection_cache_ttl_seconds: int = Field(default=30, ge=0)

    # --- sources ---
    openmoney_site_url: str = "https://openmoney.md"
    openmoney_api_url: str = "https://api.openmoney.md"
    depozitar_site_url: str = "https://depozitar.statistica.md"
    depozitar_api_url: str = "https://depozitar-cabinet.statistica.md"

    # --- browser ---
    browser_headless: bool = True
    browser_locale: str = "ro-RO"
    # Identifies us to the sites we read. Leave the default in place.
    browser_user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/141.0.0.0 Safari/537.36 scraper_service/0.1"
    )
    browser_navigation_timeout_ms: int = Field(default=45_000, gt=0)
    browser_request_timeout_ms: int = Field(default=30_000, gt=0)
    # Pages rendered at once. Each one is a real tab, so keep this small.
    browser_max_pages: int = Field(default=2, ge=1, le=16)
    # Store the rendered DOM next to the JSON the page loaded.
    capture_rendered_html: bool = True

    # --- politeness ---
    # Minimum gap between two requests to the same host.
    request_min_interval_seconds: float = Field(default=1.0, ge=0)
    request_max_attempts: int = Field(default=3, ge=1)
    request_backoff_seconds: float = Field(default=2.0, ge=0)

    # --- neo4j ---
    # The relationship graph is projected here after every transform. Postgres
    # stays the source of truth; this is a read model that can be rebuilt.
    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"
    # How deep a caller may walk out from one company.
    graph_max_depth: int = Field(default=4, ge=1, le=8)

    @property
    def neo4j_enabled(self) -> bool:
        """No password, no graph. The SQL fallback serves the graph instead."""
        return bool(self.neo4j_password)

    # --- jobs ---
    sweep_page_size: int = Field(default=50, ge=1, le=200)
    # A sweep asks for this many pages unless the caller says otherwise.
    sweep_default_max_pages: int = Field(default=10, ge=1)
    fetch_depozitar_by_default: bool = True


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


@lru_cache
def get_driver(uri: str, user: str, password: str) -> AsyncDriver:
    return AsyncGraphDatabase.driver(uri, auth=(user, password))


SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_session(settings: SettingsDep) -> AsyncIterator[AsyncSession]:
    factory = get_session_factory(settings.database_url)
    async with factory() as session:
        yield session


async def get_redis(settings: SettingsDep) -> Redis:
    return get_redis_client(settings.redis_url)


async def get_neo4j(settings: SettingsDep) -> AsyncDriver | None:
    """None when neo4j is not configured, which callers treat as "fall back"."""
    if not settings.neo4j_enabled:
        return None
    return get_driver(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)


SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
DriverDep = Annotated["AsyncDriver | None", Depends(get_neo4j)]
