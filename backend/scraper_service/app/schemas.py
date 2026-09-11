"""Request and response bodies. Nothing here touches the database."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

IDNO_PATTERN = re.compile(r"^\d{13}$")


def _clean_idno(value: str) -> str:
    idno = "".join(value.split())
    if not IDNO_PATTERN.match(idno):
        raise ValueError("an IDNO is 13 digits")
    return idno


class ScrapeByIdnoRequest(BaseModel):
    idnos: list[str] = Field(min_length=1, max_length=500)
    include_depozitar: bool | None = None

    @field_validator("idnos")
    @classmethod
    def check_idnos(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for raw in value:
            idno = _clean_idno(raw)
            if idno not in seen:
                seen.append(idno)
        return seen


class SweepRequest(BaseModel):
    """Walks the register from start_page, at most max_pages of it."""

    start_page: int = Field(default=0, ge=0)
    page_size: int = Field(default=50, ge=1, le=200)
    max_pages: int = Field(default=10, ge=1, le=100_000)
    include_depozitar: bool | None = None


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mode: str
    status: str
    params: dict
    cursor: dict
    companies_done: int
    companies_total: int | None
    requests_ok: int
    requests_failed: int
    rows_loaded: int
    last_error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class PersonRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: str
    party_type: str
    full_name: str
    share_percent: Decimal | None


class StatementSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    year: int
    source: str
    status: str | None
    origin: str | None
    period_from: date | None
    period_to: date | None
    is_audited: bool | None


class LineItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    group_code: str
    group_name: str | None
    field_code: str
    label: str | None
    value_current: Decimal | None
    value_previous: Decimal | None
    value_extra_1: Decimal | None
    value_extra_2: Decimal | None


class StatementRead(StatementSummary):
    source_ref: str | None
    declaration_date: date | None
    entity_name: str | None
    line_items: list[LineItemRead]


class CompanySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    idno: str
    name: str | None
    legal_form: str | None
    is_active: bool | None
    registered_at: date | None
    city: str | None
    caem_code: str | None
    sources: list | None
    transformed_at: datetime


class CompanyRead(CompanySummary):
    revision_date: date | None
    address: str | None
    street: str | None
    postal_code: str | None
    cuatm_code: str | None
    cuatm_name: str | None
    caem_name: str | None
    ownership_name: str | None
    cuiio: str | None
    email: str | None
    phone: str | None
    website: str | None
    employees: int | None
    in_liquidation: bool | None
    licensed_activities: list | None
    unlicensed_activities: list | None
    people: list[PersonRead]
    statements: list[StatementSummary]


class CompanyPage(BaseModel):
    items: list[CompanySummary]
    total: int
    limit: int
    offset: int


class SourceDataRead(BaseModel):
    """The raw layer, without the body. Ask for one row to see that."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: str
    resource: str
    resource_key: str
    request_url: str
    http_status: int
    content_type: str | None
    body_sha256: str
    first_seen_at: datetime
    last_seen_at: datetime


# --- ownership graph --------------------------------------------------------


class GraphNode(BaseModel):
    """A company or a party. `id` is what the edges point at."""

    id: str
    kind: str
    label: str
    idno: str | None = None
    role: str | None = None
    degree: int = 0


class GraphLink(BaseModel):
    source: str
    target: str
    role: str
    share_percent: Decimal | None = None


class GraphRead(BaseModel):
    nodes: list[GraphNode]
    links: list[GraphLink]
    truncated: bool
