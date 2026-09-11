"""Tables owned by scraper_service. They live in the scraper_service schema."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

SOURCE_OPENMONEY = "openmoney"
SOURCE_DEPOZITAR = "depozitar"

MODE_IDNO_LIST = "idno_list"
MODE_SWEEP = "sweep"

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
FINAL_STATUSES = (STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED)

ROLE_FOUNDER = "FOUNDER"
ROLE_ADMINISTRATOR = "ADMINISTRATOR"

PARTY_PERSON = "PERSON"
PARTY_COMPANY = "COMPANY"


class Base(DeclarativeBase):
    pass


class ScrapeJob(Base):
    """One run of the scraper. Holds its own resume point."""

    __tablename__ = "scrape_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(
        String(16), default=STATUS_PENDING, server_default=STATUS_PENDING, index=True
    )
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Where to pick up after a restart: next page, and how far into it we got.
    cursor: Mapped[dict] = mapped_column(JSONB, default=dict)

    companies_done: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    companies_total: Mapped[int | None] = mapped_column(Integer, default=None)
    requests_ok: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    requests_failed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rows_loaded: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, default=None)

    created_by: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )


class SourceData(Base):
    """Extract and Load. Bodies land here exactly as the source returned them."""

    __tablename__ = "source_data"
    __table_args__ = (
        UniqueConstraint(
            "source", "resource", "resource_key", "body_sha256", name="uq_source_body"
        ),
        Index("ix_source_data_lookup", "idno", "source", "resource"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scrape_jobs.id", ondelete="SET NULL"),
        default=None,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(32))
    resource: Mapped[str] = mapped_column(String(48))
    # Whatever identifies this response inside its resource: an IDNO, a page
    # number, a declaration id.
    resource_key: Mapped[str] = mapped_column(String(128))
    idno: Mapped[str | None] = mapped_column(String(20), default=None, index=True)

    request_url: Mapped[str] = mapped_column(String(1000))
    http_status: Mapped[int] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(128), default=None)
    body: Mapped[str] = mapped_column(Text)
    body_sha256: Mapped[str] = mapped_column(String(64))

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Company(Base):
    """Transform. One cleaned row per company, merged from both sources."""

    __tablename__ = "transformed_data"

    idno: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(500), default=None, index=True)
    legal_form: Mapped[str | None] = mapped_column(String(64), default=None)
    is_active: Mapped[bool | None] = mapped_column(Boolean, default=None)
    registered_at: Mapped[date | None] = mapped_column(Date, default=None)
    revision_date: Mapped[date | None] = mapped_column(Date, default=None)

    address: Mapped[str | None] = mapped_column(String(500), default=None)
    city: Mapped[str | None] = mapped_column(String(200), default=None)
    street: Mapped[str | None] = mapped_column(String(300), default=None)
    postal_code: Mapped[str | None] = mapped_column(String(16), default=None)
    cuatm_code: Mapped[str | None] = mapped_column(String(16), default=None)
    cuatm_name: Mapped[str | None] = mapped_column(String(200), default=None)

    caem_code: Mapped[str | None] = mapped_column(String(16), default=None)
    caem_name: Mapped[str | None] = mapped_column(String(300), default=None)
    ownership_name: Mapped[str | None] = mapped_column(String(200), default=None)
    cuiio: Mapped[str | None] = mapped_column(String(32), default=None)

    email: Mapped[str | None] = mapped_column(String(320), default=None)
    phone: Mapped[str | None] = mapped_column(String(64), default=None)
    website: Mapped[str | None] = mapped_column(String(500), default=None)
    employees: Mapped[int | None] = mapped_column(Integer, default=None)
    in_liquidation: Mapped[bool | None] = mapped_column(Boolean, default=None)

    licensed_activities: Mapped[list | None] = mapped_column(JSONB, default=None)
    unlicensed_activities: Mapped[list | None] = mapped_column(JSONB, default=None)
    # Which sources contributed to this row.
    sources: Mapped[list | None] = mapped_column(JSONB, default=None)

    first_scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    transformed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    people: Mapped[list[CompanyPerson]] = relationship(
        back_populates="company", cascade="all, delete-orphan", passive_deletes=True
    )
    statements: Mapped[list[FinancialStatement]] = relationship(
        back_populates="company", cascade="all, delete-orphan", passive_deletes=True
    )


class CompanyPerson(Base):
    """A founder or an administrator, person or company."""

    __tablename__ = "company_people"
    __table_args__ = (
        UniqueConstraint(
            "idno", "role", "party_type", "full_name", name="uq_company_person"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    idno: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("transformed_data.idno", ondelete="CASCADE"),
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20))
    party_type: Mapped[str] = mapped_column(String(10))
    full_name: Mapped[str] = mapped_column(String(300))
    # The source's own identifier: a hashed person id, or another company's IDNO.
    source_key: Mapped[str | None] = mapped_column(String(128), default=None)
    share_percent: Mapped[Decimal | None] = mapped_column(Numeric(9, 4), default=None)

    company: Mapped[Company] = relationship(back_populates="people")


class FinancialStatement(Base):
    """One filed year, from one source."""

    __tablename__ = "financial_statements"
    __table_args__ = (
        UniqueConstraint("idno", "year", "source", name="uq_statement_year_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    idno: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("transformed_data.idno", ondelete="CASCADE"),
        index=True,
    )
    year: Mapped[int] = mapped_column(Integer, index=True)
    source: Mapped[str] = mapped_column(String(32))
    # The declaration id at the depositary, when that is where this came from.
    source_ref: Mapped[str | None] = mapped_column(String(64), default=None)

    doctype: Mapped[str | None] = mapped_column(String(32), default=None)
    status: Mapped[str | None] = mapped_column(String(32), default=None)
    origin: Mapped[str | None] = mapped_column(String(32), default=None)
    entity_name: Mapped[str | None] = mapped_column(String(500), default=None)
    period_from: Mapped[date | None] = mapped_column(Date, default=None)
    period_to: Mapped[date | None] = mapped_column(Date, default=None)
    declaration_date: Mapped[date | None] = mapped_column(Date, default=None)
    is_audited: Mapped[bool | None] = mapped_column(Boolean, default=None)
    is_signed: Mapped[bool | None] = mapped_column(Boolean, default=None)

    company: Mapped[Company] = relationship(back_populates="statements")
    line_items: Mapped[list[FinancialLineItem]] = relationship(
        back_populates="statement", cascade="all, delete-orphan", passive_deletes=True
    )


class FinancialLineItem(Base):
    """One row of one statement form, with its current and prior value."""

    __tablename__ = "financial_line_items"
    __table_args__ = (
        UniqueConstraint(
            "statement_id", "group_code", "field_code", name="uq_line_item"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    statement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("financial_statements.id", ondelete="CASCADE"),
        index=True,
    )
    # Both sources number the forms the same way: 5 balance sheet, 6 profit and
    # loss, 7 equity, 8 cash flow.
    group_code: Mapped[str] = mapped_column(String(16))
    group_name: Mapped[str | None] = mapped_column(String(200), default=None)
    field_code: Mapped[str] = mapped_column(String(16))
    label: Mapped[str | None] = mapped_column(String(500), default=None)
    value_current: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), default=None)
    value_previous: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), default=None)
    # The equity form carries four columns instead of two.
    value_extra_1: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), default=None)
    value_extra_2: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), default=None)

    statement: Mapped[FinancialStatement] = relationship(back_populates="line_items")
