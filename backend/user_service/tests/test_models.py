from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import RefreshToken, User


async def make_user(session, email="a@example.com", google_sub=None):
    user = User(email=email, google_sub=google_sub or f"google-{email}")
    session.add(user)
    await session.flush()
    return user


async def test_a_new_user_is_an_active_plain_user(session):
    user = await make_user(session)
    assert user.role == "user"
    assert user.is_active is True
    assert user.created_at is not None


async def test_a_user_has_no_password_column_at_all(session):
    assert "hashed_password" not in User.__table__.columns


async def test_two_users_cannot_share_a_google_account(session):
    await make_user(session, "one@example.com", google_sub="same-google-sub")
    session.add(User(email="two@example.com", google_sub="same-google-sub"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_two_users_cannot_share_an_email(session):
    await make_user(session, "taken@example.com")
    session.add(User(email="taken@example.com", google_sub="google-other"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_a_refresh_token_belongs_to_its_user(session):
    user = await make_user(session, "owner@example.com")
    token = RefreshToken(
        user_id=user.id,
        token_hash="hash-1",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add(token)
    await session.flush()
    assert token.revoked_at is None
    assert token.user_id == user.id


async def test_the_same_token_hash_cannot_be_stored_twice(session):
    user = await make_user(session, "dup@example.com")
    expires = datetime.now(UTC) + timedelta(days=1)
    session.add(RefreshToken(user_id=user.id, token_hash="same", expires_at=expires))
    await session.flush()
    session.add(RefreshToken(user_id=user.id, token_hash="same", expires_at=expires))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_deleting_a_user_takes_their_sessions_with_them(session):
    user = await make_user(session, "gone@example.com")
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash="hash-2",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    await session.flush()
    await session.delete(user)
    await session.flush()
    remaining = await session.scalars(
        select(RefreshToken).where(RefreshToken.user_id == user.id)
    )
    assert remaining.all() == []
