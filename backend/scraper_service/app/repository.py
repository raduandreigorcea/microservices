"""Every read and write this service makes against Postgres."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, func, literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    STATUS_RUNNING,
    Company,
    CompanyPerson,
    FinancialLineItem,
    FinancialStatement,
    ScrapeJob,
    SourceData,
)
from app.sources import Extracted
from app.transform import CompanyRecord

# --- jobs -------------------------------------------------------------------


async def add_job(
    session: AsyncSession, *, mode: str, params: dict, created_by: str | None
) -> ScrapeJob:
    job = ScrapeJob(mode=mode, params=params, cursor={}, created_by=created_by)
    session.add(job)
    await session.flush()
    return job


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> ScrapeJob | None:
    return await session.get(ScrapeJob, job_id)


async def list_jobs(
    session: AsyncSession, *, limit: int, offset: int, status: str | None = None
) -> list[ScrapeJob]:
    query = select(ScrapeJob).order_by(ScrapeJob.created_at.desc())
    if status:
        query = query.where(ScrapeJob.status == status)
    rows = await session.scalars(query.limit(limit).offset(offset))
    return list(rows)


async def mark_job_running(session: AsyncSession, job: ScrapeJob) -> None:
    job.status = STATUS_RUNNING
    job.started_at = datetime.now(UTC)
    job.last_error = None


async def finish_job(
    session: AsyncSession, job: ScrapeJob, status: str, error: str | None = None
) -> None:
    job.status = status
    job.finished_at = datetime.now(UTC)
    if error:
        job.last_error = error[:4000]


# --- raw: the Load step -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class LoadResult:
    stored: int
    changed: int


def body_digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


async def store(
    session: AsyncSession, item: Extracted, job_id: uuid.UUID | None = None
) -> bool:
    """Keep one response. Returns True when the body differs from last time.

    An unchanged body only moves last_seen_at, so the table records the times
    the data actually changed rather than the times we asked for it.
    """
    statement = (
        insert(SourceData)
        .values(
            id=uuid.uuid4(),
            job_id=job_id,
            source=item.source,
            resource=item.resource,
            resource_key=item.resource_key,
            idno=item.idno,
            request_url=item.fetched.url[:1000],
            http_status=item.fetched.status,
            content_type=(item.fetched.content_type or None),
            body=item.fetched.body,
            body_sha256=body_digest(item.fetched.body),
        )
        .on_conflict_do_update(
            constraint="uq_source_body", set_={"last_seen_at": func.now()}
        )
        .returning(literal_column("xmax = 0").label("inserted"))
    )
    return bool(await session.scalar(statement))


async def store_all(
    session: AsyncSession, items: list[Extracted], job_id: uuid.UUID | None = None
) -> LoadResult:
    changed = 0
    for item in items:
        if await store(session, item, job_id):
            changed += 1
    return LoadResult(stored=len(items), changed=changed)


async def latest_body(
    session: AsyncSession, *, source: str, resource: str, resource_key: str
) -> str | None:
    return await session.scalar(
        select(SourceData.body)
        .where(
            SourceData.source == source,
            SourceData.resource == resource,
            SourceData.resource_key == resource_key,
        )
        .order_by(SourceData.last_seen_at.desc())
        .limit(1)
    )


async def latest_bodies_for_idno(
    session: AsyncSession, *, source: str, resource: str, idno: str
) -> list[str]:
    """The newest body per resource_key, for resources that repeat per company."""
    newest = (
        select(
            SourceData.resource_key,
            func.max(SourceData.last_seen_at).label("seen"),
        )
        .where(
            SourceData.source == source,
            SourceData.resource == resource,
            SourceData.idno == idno,
        )
        .group_by(SourceData.resource_key)
        .subquery()
    )
    rows = await session.scalars(
        select(SourceData.body)
        .join(
            newest,
            (SourceData.resource_key == newest.c.resource_key)
            & (SourceData.last_seen_at == newest.c.seen),
        )
        .where(
            SourceData.source == source,
            SourceData.resource == resource,
            SourceData.idno == idno,
        )
        .order_by(SourceData.resource_key)
    )
    return list(rows)


async def list_source_data(
    session: AsyncSession, *, idno: str, limit: int, offset: int
) -> list[SourceData]:
    rows = await session.scalars(
        select(SourceData)
        .where(SourceData.idno == idno)
        .order_by(SourceData.last_seen_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(rows)


# --- transformed ------------------------------------------------------------


async def save_company(session: AsyncSession, record: CompanyRecord) -> Company:
    """Replace this company's transformed rows with what the record says."""
    company = await session.get(Company, record.idno)
    if company is None:
        company = Company(idno=record.idno)
        session.add(company)
    for key, value in record.fields.items():
        setattr(company, key, value)
    company.sources = record.sources or None
    company.transformed_at = datetime.now(UTC)
    await session.flush()

    await session.execute(
        delete(CompanyPerson).where(CompanyPerson.idno == record.idno)
    )
    for person in record.people:
        session.add(
            CompanyPerson(
                idno=record.idno,
                role=person.role,
                party_type=person.party_type,
                full_name=person.full_name,
                source_key=person.source_key,
                share_percent=person.share_percent,
            )
        )

    await session.execute(
        delete(FinancialStatement).where(FinancialStatement.idno == record.idno)
    )
    await session.flush()
    for record_statement in record.statements:
        statement = FinancialStatement(
            idno=record.idno,
            year=record_statement.year,
            source=record_statement.source,
            source_ref=record_statement.source_ref,
            doctype=record_statement.doctype,
            status=record_statement.status,
            origin=record_statement.origin,
            entity_name=record_statement.entity_name,
            period_from=record_statement.period_from,
            period_to=record_statement.period_to,
            declaration_date=record_statement.declaration_date,
            is_audited=record_statement.is_audited,
            is_signed=record_statement.is_signed,
        )
        session.add(statement)
        await session.flush()
        for item in record_statement.line_items:
            session.add(
                FinancialLineItem(
                    statement_id=statement.id,
                    group_code=item.group_code,
                    group_name=item.group_name,
                    field_code=item.field_code,
                    label=item.label,
                    value_current=item.value_current,
                    value_previous=item.value_previous,
                    value_extra_1=item.value_extra_1,
                    value_extra_2=item.value_extra_2,
                )
            )
    await session.flush()
    return company


async def get_company(session: AsyncSession, idno: str) -> Company | None:
    return await session.scalar(
        select(Company)
        .where(Company.idno == idno)
        .options(selectinload(Company.people), selectinload(Company.statements))
    )


async def list_companies(
    session: AsyncSession, *, limit: int, offset: int, search: str | None = None
) -> tuple[list[Company], int]:
    query = select(Company)
    counter = select(func.count()).select_from(Company)
    if search:
        pattern = f"%{search.strip()}%"
        condition = Company.name.ilike(pattern) | Company.idno.ilike(pattern)
        query = query.where(condition)
        counter = counter.where(condition)
    rows = await session.scalars(
        query.order_by(Company.name.asc()).limit(limit).offset(offset)
    )
    total = await session.scalar(counter) or 0
    return list(rows), total


async def get_statement(
    session: AsyncSession, *, idno: str, year: int, source: str | None = None
) -> FinancialStatement | None:
    query = (
        select(FinancialStatement)
        .where(FinancialStatement.idno == idno, FinancialStatement.year == year)
        .options(selectinload(FinancialStatement.line_items))
    )
    if source:
        query = query.where(FinancialStatement.source == source)
    # Prefer the filed declaration over the registry's flattened copy.
    query = query.order_by(FinancialStatement.source.asc())
    return await session.scalar(query.limit(1))


async def companies_with_people(session: AsyncSession) -> list[Company]:
    """Every transformed company and its filed parties.

    This is what a rebuild of the neo4j projection reads, which is the whole
    reason the projection is allowed to be thrown away.
    """
    result = await session.scalars(
        select(Company).options(selectinload(Company.people)).order_by(Company.idno)
    )
    return list(result)


# --- ownership graph --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """One company-to-party link, as the source filed it."""

    idno: str
    company_name: str | None
    party_key: str
    party_name: str
    party_type: str
    role: str
    share_percent: Decimal | None


async def graph_edges(
    session: AsyncSession, *, idno: str, limit: int
) -> list[GraphEdge]:
    """One company's neighbourhood, as flat rows.

    Its own parties, plus every other company those same parties sit on. That
    second hop is the whole point: it is what shows a company touching another
    through a shared founder.
    """
    # A party is the same party across companies when the source gives it the
    # same key. Names alone would merge two different people who happen to
    # share one, and split one person the source spelled twice.
    party_key = func.coalesce(CompanyPerson.source_key, CompanyPerson.full_name)
    of_this_company = select(party_key).where(CompanyPerson.idno == idno)

    rows = await session.execute(
        select(
            CompanyPerson.idno,
            Company.name,
            party_key.label("party_key"),
            CompanyPerson.full_name,
            CompanyPerson.party_type,
            CompanyPerson.role,
            CompanyPerson.share_percent,
        )
        .join(Company, Company.idno == CompanyPerson.idno)
        .where(party_key.in_(of_this_company))
        .order_by(CompanyPerson.idno)
        .limit(limit)
    )
    return [
        GraphEdge(
            idno=row[0],
            company_name=row[1],
            party_key=row[2],
            party_name=row[3],
            party_type=row[4],
            role=row[5],
            share_percent=row[6],
        )
        for row in rows
    ]
