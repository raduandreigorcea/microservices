"""user_service: registration, OAuth2 tokens and session handling."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_engine, get_settings
from app.models import Base
from app.router import auth_router, health_router, users_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create this service's own tables on boot. It owns its schema."""
    engine = get_engine(get_settings().database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()

DESCRIPTION = """
Issues and validates the tokens the rest of the stack relies on.

Sign in with Google, then come back here:

1. open [/auth/google/login](/auth/google/login) in a browser and pick an account
2. the callback answers with JSON, copy the `access_token` out of it
3. press **Authorize** above and paste the token

`/auth/refresh` rotates the refresh token, so a stolen one is single use.
`/auth/introspect` is what the gateway calls to check a bearer token.
"""


def create_app() -> FastAPI:
    app = FastAPI(
        title="user_service",
        description=DESCRIPTION,
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        swagger_ui_parameters={"persistAuthorization": True},
    )
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(users_router)
    return app


app = create_app()
