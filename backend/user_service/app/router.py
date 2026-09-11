"""Every HTTP route this service serves."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from app import repository, service
from app.config import RedisDep, SessionDep, SettingsDep
from app.schemas import (
    AuthorizationUrl,
    IntrospectRequest,
    IntrospectResponse,
    LogoutRequest,
    RefreshRequest,
    SessionRead,
    TokenPair,
    UserRead,
)

health_router = APIRouter(tags=["health"])
auth_router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])


@health_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@health_router.get("/health/ready")
async def ready(session: SessionDep, redis: RedisDep) -> dict[str, str]:
    await session.execute(text("select 1"))
    await redis.ping()
    return {"status": "ready", "database": "ok", "redis": "ok"}


@auth_router.get("/google/authorize", response_model=AuthorizationUrl)
async def google_authorize(
    redis: RedisDep, settings: SettingsDep, google: service.GoogleDep
) -> AuthorizationUrl:
    """Start a login. The caller sends the browser to the returned URL."""
    url, state = await service.begin_authorization(redis, settings, google)
    return AuthorizationUrl(authorization_url=url, state=state)


@auth_router.get("/google/login", include_in_schema=False)
async def google_login(
    redis: RedisDep, settings: SettingsDep, google: service.GoogleDep
) -> RedirectResponse:
    """Same thing, for a browser hitting the API directly."""
    url, _ = await service.begin_authorization(redis, settings, google)
    return RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@auth_router.get("/google/callback", response_model=TokenPair)
async def google_callback(
    request: Request,
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
    google: service.GoogleDep,
    state: str,
    code: str | None = None,
    error: str | None = None,
):
    pair = await service.complete_authorization(
        session, redis, settings, google, request, state=state, code=code, error=error
    )
    if settings.login_success_redirect:
        return RedirectResponse(
            f"{settings.login_success_redirect}"
            f"#access_token={pair.access_token}&refresh_token={pair.refresh_token}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return pair


@auth_router.post("/refresh", response_model=TokenPair)
async def refresh(
    request: Request,
    payload: RefreshRequest,
    session: SessionDep,
    settings: SettingsDep,
) -> TokenPair:
    return await service.rotate_refresh_token(
        session, settings, request, payload.refresh_token
    )


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: LogoutRequest,
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
    identity: service.CurrentIdentity,
) -> None:
    await service.log_out(session, redis, settings, identity, payload.refresh_token)


@auth_router.post("/introspect", response_model=IntrospectResponse)
async def introspect(
    payload: IntrospectRequest,
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
) -> IntrospectResponse:
    return await service.introspect_token(session, redis, settings, payload.token)


@users_router.get("/me", response_model=UserRead)
async def read_me(identity: service.ReadIdentity):
    return identity.user


@users_router.get("/me/sessions", response_model=list[SessionRead])
async def list_my_sessions(identity: service.ReadIdentity, session: SessionDep):
    return await repository.list_active_sessions(session, identity.user.id)


@users_router.delete(
    "/me/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_my_session(
    session_id: uuid.UUID, identity: service.ReadIdentity, session: SessionDep
) -> None:
    stored = await repository.get_session_by_id(session, session_id, identity.user.id)
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no such session"
        )
    if stored.revoked_at is None:
        repository.revoke(stored)
        await session.commit()
