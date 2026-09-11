"""Every read and write this service makes, against Postgres and Redis."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshToken, User


async def get_user_by_id(session: AsyncSession, user_id: str) -> User | None:
    return await session.scalar(select(User).where(User.id == user_id))


async def get_user_by_google_sub(session: AsyncSession, sub: str) -> User | None:
    return await session.scalar(select(User).where(User.google_sub == sub))


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(User.email == email.lower()))


async def add_user(session: AsyncSession, *, email: str, google_sub: str) -> User:
    user = User(email=email.lower(), google_sub=google_sub)
    session.add(user)
    return user


async def add_refresh_token(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    token_hash: str,
    ttl_seconds: int,
    user_agent: str | None,
    ip_address: str | None,
) -> RefreshToken:
    token = RefreshToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
        user_agent=user_agent,
        ip_address=ip_address,
    )
    session.add(token)
    await session.flush()
    return token


async def get_refresh_token(
    session: AsyncSession, token_hash: str, user_id: uuid.UUID | None = None
) -> RefreshToken | None:
    query = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    if user_id is not None:
        query = query.where(RefreshToken.user_id == user_id)
    return await session.scalar(query)


async def get_session_by_id(
    session: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID
) -> RefreshToken | None:
    return await session.scalar(
        select(RefreshToken).where(
            RefreshToken.id == session_id, RefreshToken.user_id == user_id
        )
    )


async def list_active_sessions(
    session: AsyncSession, user_id: uuid.UUID
) -> list[RefreshToken]:
    rows = await session.scalars(
        select(RefreshToken)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > datetime.now(UTC),
        )
        .order_by(RefreshToken.issued_at.desc())
    )
    return list(rows)


def revoke(token: RefreshToken, replaced_by: RefreshToken | None = None) -> None:
    token.revoked_at = datetime.now(UTC)
    if replaced_by is not None:
        token.replaced_by_id = replaced_by.id


async def deny_access_token(
    redis: Redis, prefix: str, token_id: str, ttl_seconds: int
) -> None:
    """Remember a revoked token only until it would have expired anyway."""
    if ttl_seconds > 0:
        await redis.set(f"{prefix}{token_id}", "revoked", ex=ttl_seconds)


async def is_access_token_denied(redis: Redis, prefix: str, token_id: str) -> bool:
    return await redis.exists(f"{prefix}{token_id}") == 1


async def remember_oauth_state(
    redis: Redis, prefix: str, state: str, verifier: str, ttl_seconds: int
) -> None:
    await redis.set(f"{prefix}{state}", verifier, ex=ttl_seconds)


async def take_oauth_state(redis: Redis, prefix: str, state: str) -> str | None:
    """Single use. Reading a state also consumes it, so a replay finds nothing."""
    return await redis.getdel(f"{prefix}{state}")
