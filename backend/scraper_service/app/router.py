"""Every HTTP route this service serves."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import text

from app import graph_queries, graph_store, repository, service
from app.config import DriverDep, RedisDep, SessionDep, SettingsDep
from app.graph_store import GraphUnavailable
from app.models import FINAL_STATUSES, MODE_IDNO_LIST, MODE_SWEEP
from app.schemas import (
    CompanyPage,
    CompanyRead,
    GraphRead,
    JobRead,
    PathRead,
    ReprojectRead,
    ScrapeByIdnoRequest,
    SourceDataRead,
    StatementRead,
    SweepRequest,
)
from app.service import ReadCaller, ScrapeRunner, WriteCaller

log = logging.getLogger("scraper")

health_router = APIRouter(tags=["health"])
scrape_router = APIRouter(prefix="/scrape", tags=["scrape"])
companies_router = APIRouter(prefix="/companies", tags=["companies"])


def get_runner(request: Request) -> ScrapeRunner:
    runner = getattr(request.app.state, "runner", None)
    if runner is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="the scrape runner is not available",
        )
    return runner


@health_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@health_router.get("/health/ready")
async def ready(session: SessionDep, redis: RedisDep) -> dict[str, str]:
    await session.execute(text("select 1"))
    await redis.ping()
    return {"status": "ready", "database": "ok", "redis": "ok"}


# --- jobs -------------------------------------------------------------------


@scrape_router.post(
    "/companies", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED
)
async def scrape_companies(
    payload: ScrapeByIdnoRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    caller: WriteCaller,
) -> JobRead:
    """Scrape the companies behind these IDNOs, both sources."""
    include = (
        payload.include_depozitar
        if payload.include_depozitar is not None
        else settings.fetch_depozitar_by_default
    )
    job = await repository.add_job(
        session,
        mode=MODE_IDNO_LIST,
        params={"idnos": payload.idnos, "include_depozitar": include},
        created_by=caller.subject or None,
    )
    await session.commit()
    get_runner(request).start(job.id)
    return JobRead.model_validate(job)


@scrape_router.post(
    "/sweep", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED
)
async def scrape_sweep(
    payload: SweepRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    caller: WriteCaller,
) -> JobRead:
    """Walk the register page by page. Resumes from its own cursor."""
    include = (
        payload.include_depozitar
        if payload.include_depozitar is not None
        else settings.fetch_depozitar_by_default
    )
    job = await repository.add_job(
        session,
        mode=MODE_SWEEP,
        params={
            "start_page": payload.start_page,
            "page_size": payload.page_size,
            "max_pages": payload.max_pages,
            "include_depozitar": include,
        },
        created_by=caller.subject or None,
    )
    await session.commit()
    get_runner(request).start(job.id)
    return JobRead.model_validate(job)


@scrape_router.get("/jobs", response_model=list[JobRead])
async def list_jobs(
    session: SessionDep,
    caller: ReadCaller,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    job_status: str | None = Query(default=None, alias="status"),
):
    return await repository.list_jobs(
        session, limit=limit, offset=offset, status=job_status
    )


@scrape_router.get("/jobs/{job_id}", response_model=JobRead)
async def read_job(job_id: uuid.UUID, session: SessionDep, caller: ReadCaller):
    job = await repository.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such job")
    return job


@scrape_router.post("/jobs/{job_id}/cancel", response_model=JobRead)
async def cancel_job(
    job_id: uuid.UUID, request: Request, session: SessionDep, caller: WriteCaller
):
    """Asks the job to stop. It finishes the company it is on, then gives up."""
    job = await repository.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such job")
    if job.status in FINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"job already {job.status}",
        )
    get_runner(request).cancel(job_id)
    return job


@scrape_router.post("/reproject", response_model=ReprojectRead)
async def reproject_graph(
    session: SessionDep,
    driver: DriverDep,
    settings: SettingsDep,
    caller: WriteCaller,
) -> ReprojectRead:
    """Rebuilds the whole neo4j graph from `transformed_data`.

    The projection is disposable by design, so this is how it comes back after
    an empty neo4j, a lost volume, or a change to the graph model.
    """
    if driver is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="the graph store is not configured",
        )
    companies = await repository.companies_with_people(session)
    try:
        await graph_store.ensure_constraints(driver, settings.neo4j_database)
        for company in companies:
            await graph_store.project_company(
                driver,
                settings.neo4j_database,
                idno=company.idno,
                name=company.name,
                people=company.people,
            )
    except GraphUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="the graph store could not be reached",
        ) from exc
    return ReprojectRead(
        companies=len(companies),
        message=f"projected {len(companies)} companies into neo4j",
    )


# --- transformed data -------------------------------------------------------


@companies_router.get("", response_model=CompanyPage)
async def list_companies(
    session: SessionDep,
    caller: ReadCaller,
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> CompanyPage:
    items, total = await repository.list_companies(
        session, limit=limit, offset=offset, search=search
    )
    return CompanyPage(items=items, total=total, limit=limit, offset=offset)


@companies_router.get("/{idno}", response_model=CompanyRead)
async def read_company(idno: str, session: SessionDep, caller: ReadCaller):
    company = await repository.get_company(session, idno)
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="this company has not been scraped yet",
        )
    return company


@companies_router.get("/{idno}/graph", response_model=GraphRead)
async def company_ego_graph(
    idno: str,
    session: SessionDep,
    driver: DriverDep,
    settings: SettingsDep,
    caller: ReadCaller,
    depth: int = Query(default=2, ge=1, le=4),
    limit: int = Query(default=200, ge=1, le=1000),
) -> GraphRead:
    """This company, the parties behind it, and where else those parties sit.

    Answered from neo4j, which is what makes `depth` more than one possible.
    If neo4j is not there, the one-hop SQL query stands in and says so.
    """
    if driver is not None:
        try:
            nodes, links, truncated = await graph_queries.ego_graph(
                driver,
                settings.neo4j_database,
                idno=idno,
                depth=depth,
                limit=limit,
                max_depth=settings.graph_max_depth,
            )
            return GraphRead(
                nodes=nodes,
                links=links,
                truncated=truncated,
                depth=depth,
                source="neo4j",
            )
        except GraphUnavailable as exc:
            log.warning("graph for %s fell back to postgres: %s", idno, exc)

    edges = await repository.graph_edges(session, idno=idno, limit=limit + 1)
    truncated = len(edges) > limit
    nodes, links = service.build_graph(edges[:limit])
    return GraphRead(
        nodes=nodes, links=links, truncated=truncated, depth=1, source="postgres"
    )


@companies_router.get("/{idno}/path/{other_idno}", response_model=PathRead)
async def company_path(
    idno: str,
    other_idno: str,
    driver: DriverDep,
    settings: SettingsDep,
    caller: ReadCaller,
    limit: int = Query(default=200, ge=1, le=1000),
) -> PathRead:
    """The shortest chain of holdings joining two companies.

    There is no SQL fallback for this one: it is the query the relational
    shape could not answer without recursion, which is why neo4j is here.
    """
    if driver is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="the graph store is not configured",
        )
    try:
        nodes, links = await graph_queries.shortest_path(
            driver,
            settings.neo4j_database,
            idno=idno,
            other=other_idno,
            limit=limit,
            max_depth=settings.graph_max_depth,
        )
    except GraphUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="the graph store could not be reached",
        ) from exc
    return PathRead(nodes=nodes, links=links, found=bool(links))


@companies_router.get("/{idno}/statements/{year}", response_model=StatementRead)
async def read_statement(
    idno: str,
    year: int,
    session: SessionDep,
    caller: ReadCaller,
    source: str | None = Query(default=None),
):
    """One year in full, line by line. Defaults to the filed declaration."""
    statement = await repository.get_statement(
        session, idno=idno, year=year, source=source
    )
    if statement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no statement for that year"
        )
    return statement


@companies_router.get("/{idno}/source-data", response_model=list[SourceDataRead])
async def list_source_data(
    idno: str,
    session: SessionDep,
    caller: ReadCaller,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """What the raw layer holds for this company, bodies left out."""
    return await repository.list_source_data(
        session, idno=idno, limit=limit, offset=offset
    )
