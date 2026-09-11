"""Tables owned by user_service. They live in the user_service schema."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    # Google's stable user id. The only credential we hold.
    google_sub: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    full_name: Mapped[str | None] = mapped_column(String(200), default=None)
    picture_url: Mapped[str | None] = mapped_column(String(1000), default=None)
    role: Mapped[str] = mapped_column(String(32), default="user", server_default="user")
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class RefreshToken(Base):
    """One row per login session. Rotated on refresh, revoked on logout."""

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # Set when this token is rotated out, so a whole chain can be traced.
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("refresh_tokens.id", ondelete="SET NULL"),
        default=None,
    )
    user_agent: Mapped[str | None] = mapped_column(String(400), default=None)
    ip_address: Mapped[str | None] = mapped_column(String(45), default=None)

    user: Mapped[User] = relationship(back_populates="refresh_tokens")
