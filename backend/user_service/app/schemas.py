"""Request and response bodies. Nothing here touches the database."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    picture_url: str | None
    role: str
    is_active: bool
    email_verified: bool
    created_at: datetime


class AuthorizationUrl(BaseModel):
    authorization_url: str
    state: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    scope: str


class RefreshRequest(BaseModel):
    """A browser sends nothing: the token rides in an httpOnly cookie."""

    refresh_token: str | None = None


class LogoutRequest(BaseModel):
    """A browser sends nothing: the token rides in an httpOnly cookie."""

    refresh_token: str | None = None


class IntrospectRequest(BaseModel):
    token: str


class IntrospectResponse(BaseModel):
    """Shaped after RFC 7662, with the extras the gateway needs."""

    active: bool
    sub: str | None = None
    email: str | None = None
    role: str | None = None
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    user_agent: str | None
    ip_address: str | None
