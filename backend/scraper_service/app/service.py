"""Who is calling, and the job runner that does the actual scraping."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, SecurityScopes
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import graph_store, repository
from app.config import RedisDep, Settings, SettingsDep
from app.logs import job_tag, tag_for
from app.models import (
    MODE_SWEEP,
    SOURCE_DEPOZITAR,
    SOURCE_OPENMONEY,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    ScrapeJob,
)
from app.sources import (
    RESOURCE_COMPANY,
    RESOURCE_COMPANY_PAGE,
    RESOURCE_DECLARATION,
    RESOURCE_FIN_DATA,
    BrowserPool,
    DepozitarSource,
    Extracted,
    OpenMoneySource,
    declaration_ids,
    idnos_in_listing,
    total_companies,
)
from app.transform import CompanyRecord, build_company

log = logging.getLogger("scraper_service.jobs")

SCOPE_READ = "scrape:read"
SCOPE_WRITE = "scrape:write"


# --- who is calling ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Caller:
    subject: str
    email: str | None
    role: str | None
    scopes: tuple[str, ...]


class Introspector:
    """Asks user_service whether a bearer token is live, and caches the answer."""

    def __init__(self, settings: Settings, redis: Redis) -> None:
        self._settings = settings
        self._redis = redis

    def _cache_key(self, token: str) -> str:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return f"{self._settings.introspection_cache_prefix}{digest}"

    async def __call__(self, token: str) -> dict:
        key = self._cache_key(token)
        ttl = self._settings.introspection_cache_ttl_seconds
        if ttl:
            cached = await self._redis.get(key)
            if cached:
                return json.loads(cached)

        url = f"{self._settings.user_service_url.rstrip('/')}/auth/introspect"
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                response = await http.post(url, json={"token": token})
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"cannot reach user_service: {exc}",
            ) from exc

        if response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="user_service rejected the introspection request",
            )
        payload = response.json()
        if ttl and payload.get("active"):
            await self._redis.set(key, json.dumps(payload), ex=ttl)
        return payload


def get_introspector(settings: SettingsDep, redis: RedisDep) -> Introspector:
    return Introspector(settings, redis)


IntrospectorDep = Annotated[Introspector, Depends(get_introspector)]

bearer_scheme = HTTPBearer(
    auto_error=False,
    description="An access_token issued by user_service",
)


def _unauthorised(detail: str, challenge: str = "Bearer") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": challenge},
    )


async def get_caller(
    security_scopes: SecurityScopes,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    introspect: IntrospectorDep,
) -> Caller:
    challenge = (
        f'Bearer scope="{" ".join(security_scopes.scopes)}"'
        if security_scopes.scopes
        else "Bearer"
    )
    if credentials is None:
        raise _unauthorised("not authenticated", challenge)

    payload = await introspect(credentials.credentials)
    if not payload.get("active"):
        raise _unauthorised("token is not active", challenge)

    scopes = tuple(payload.get("scopes") or ())
    missing = [scope for scope in security_scopes.scopes if scope not in scopes]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"missing required scope: {', '.join(missing)}",
            headers={"WWW-Authenticate": challenge},
        )
    return Caller(
        subject=str(payload.get("sub") or ""),
        email=payload.get("email"),
        role=payload.get("role"),
        scopes=scopes,
    )


ReadCaller = Annotated[Caller, Security(get_caller, scopes=[SCOPE_READ])]
WriteCaller = Annotated[Caller, Security(get_caller, scopes=[SCOPE_WRITE])]


# --- the job runner ---------------------------------------------------------


class JobCancelled(Exception):
    """The job was cancelled while it was running."""


class ScrapeRunner:
    """Runs scrape jobs in the background, and can be asked to stop one."""

    def __init__(
        self,
        settings: Settings,
        browser: BrowserPool,
        session_factory: async_sessionmaker[AsyncSession],
        driver: AsyncDriver | None = None,
    ) -> None:
        self._settings = settings
        self._browser = browser
        self._session_factory = session_factory
        # None when neo4j is not configured. The graph endpoint falls back to
        # SQL in that case, so a missing driver is a degraded mode, not a fault.
        self._driver = driver
        self.openmoney = OpenMoneySource(settings, browser)
        self.depozitar = DepozitarSource(settings, browser)
        self._tasks: dict[uuid.UUID, asyncio.Task] = {}
        self._cancelled: set[uuid.UUID] = set()

    # --- lifecycle ---

    def start(self, job_id: uuid.UUID) -> None:
        if job_id in self._tasks:
            return
        task = asyncio.create_task(self._guarded(job_id), name=f"scrape-{job_id}")
        self._tasks[job_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(job_id, None))

    def cancel(self, job_id: uuid.UUID) -> bool:
        if job_id not in self._tasks:
            return False
        self._cancelled.add(job_id)
        return True

    @property
    def running(self) -> tuple[uuid.UUID, ...]:
        return tuple(self._tasks)

    async def drain(self) -> None:
        for job_id in list(self._tasks):
            self._cancelled.add(job_id)
        tasks = list(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _check_cancelled(self, job_id: uuid.UUID) -> None:
        if job_id in self._cancelled:
            raise JobCancelled

    async def _guarded(self, job_id: uuid.UUID) -> None:
        try:
            await self.run(job_id)
        except Exception:
            log.exception("scrape job %s crashed", job_id)
        finally:
            self._cancelled.discard(job_id)

    # --- the job itself ---

    async def run(self, job_id: uuid.UUID) -> None:
        await self._browser.start()
        tag = tag_for(job_id)
        token = job_tag.set(tag)
        started = time.monotonic()
        try:
            async with self._session_factory() as session:
                job = await repository.get_job(session, job_id)
                if job is None:
                    log.warning("job %s is not in the database, nothing to run", tag)
                    return
                await repository.mark_job_running(session, job)
                await session.commit()
                log.info("job %s %s start %s", tag, job.mode, json.dumps(job.params))

                status_, error = STATUS_SUCCEEDED, None
                try:
                    if job.mode == MODE_SWEEP:
                        await self._run_sweep(session, job)
                    else:
                        await self._run_idno_list(session, job)
                except JobCancelled:
                    status_ = STATUS_CANCELLED
                except Exception as exc:
                    log.exception("scrape job %s failed", job_id)
                    status_, error = STATUS_FAILED, f"{type(exc).__name__}: {exc}"

                await repository.finish_job(session, job, status_, error)
                await session.commit()
                log.info(
                    "job %s %s companies=%d ok=%d fail=%d rows=%d in %s",
                    tag,
                    status_,
                    job.companies_done,
                    job.requests_ok,
                    job.requests_failed,
                    job.rows_loaded,
                    _elapsed(started),
                )
        finally:
            job_tag.reset(token)

    async def _run_idno_list(self, session: AsyncSession, job: ScrapeJob) -> None:
        idnos = [str(value) for value in job.params.get("idnos") or []]
        include_depozitar = self._include_depozitar(job)
        job.companies_total = len(idnos)
        done = int(job.cursor.get("done", 0))
        await session.commit()

        for position, idno in enumerate(idnos):
            if position < done:
                continue
            self._check_cancelled(job.id)
            record = await self._scrape_company(session, job, idno, include_depozitar)
            job.cursor = {"done": position + 1}
            await session.commit()
            await self._project(record)

    async def _run_sweep(self, session: AsyncSession, job: ScrapeJob) -> None:
        """Walk the register page by page, committing after every company."""
        page_size = int(job.params.get("page_size") or self._settings.sweep_page_size)
        max_pages = int(
            job.params.get("max_pages") or self._settings.sweep_default_max_pages
        )
        include_depozitar = self._include_depozitar(job)
        first_page = int(job.params.get("start_page") or 0)

        page_number = int(job.cursor.get("page", first_page))
        index_in_page = int(job.cursor.get("index", 0))
        pages_done = int(job.cursor.get("pages_done", 0))

        while pages_done < max_pages:
            self._check_cancelled(job.id)
            listing = await self.openmoney.fetch_listing(page_number, page_size)
            await self._load(session, job, listing)

            body = _body_of(listing, RESOURCE_COMPANY_PAGE)
            idnos = idnos_in_listing(body) if body else []
            if job.companies_total is None and body:
                job.companies_total = total_companies(body)
            await session.commit()
            log.info(
                "page %d listing %d companies (register total %s)",
                page_number,
                len(idnos),
                job.companies_total if job.companies_total is not None else "unknown",
            )

            if not idnos:
                log.info("page %d came back empty, stopping", page_number)
                break

            for position, idno in enumerate(idnos):
                if position < index_in_page:
                    continue
                self._check_cancelled(job.id)
                record = await self._scrape_company(
                    session, job, idno, include_depozitar
                )
                job.cursor = {
                    "page": page_number,
                    "index": position + 1,
                    "pages_done": pages_done,
                }
                await session.commit()
                await self._project(record)

            index_in_page = 0
            page_number += 1
            pages_done += 1
            job.cursor = {"page": page_number, "index": 0, "pages_done": pages_done}
            await session.commit()
            log.info("page %d done (%d/%d)", page_number - 1, pages_done, max_pages)

    def _include_depozitar(self, job: ScrapeJob) -> bool:
        return bool(
            job.params.get(
                "include_depozitar", self._settings.fetch_depozitar_by_default
            )
        )

    async def _scrape_company(
        self,
        session: AsyncSession,
        job: ScrapeJob,
        idno: str,
        include_depozitar: bool,
    ) -> CompanyRecord | None:
        log.info("company %s start", idno)
        started = time.monotonic()
        before_ok, before_failed = job.requests_ok, job.requests_failed
        before_rows = job.rows_loaded

        items = list(await self.openmoney.fetch_company(idno))

        if include_depozitar:
            index = await self.depozitar.fetch_index(idno)
            items.append(index)
            if index.ok:
                declarations = declaration_ids(index.fetched.body)
                log.info("  %s has %d declarations on file", idno, len(declarations))
                for declaration_id in declarations:
                    self._check_cancelled(job.id)
                    items.extend(
                        await self.depozitar.fetch_declaration(idno, declaration_id)
                    )
            else:
                log.warning("  %s index unavailable (%d)", idno, index.fetched.status)

        await self._load(session, job, items)
        record = await self._transform(session, idno)
        job.companies_done += 1
        log.info(
            "company %s done ok=%d fail=%d stored=%d in %s",
            idno,
            job.requests_ok - before_ok,
            job.requests_failed - before_failed,
            job.rows_loaded - before_rows,
            _elapsed(started),
        )
        return record

    async def _project(self, record: CompanyRecord | None) -> None:
        """Mirror one company into neo4j, after Postgres has committed it.

        Order matters: the projection must never describe rows that were then
        rolled back. A failure here is logged and dropped, because Postgres
        already holds the truth and `POST /scrape/reproject` can rebuild this.
        """
        if record is None or self._driver is None:
            return
        await graph_store.project_quietly(
            self._driver,
            self._settings.neo4j_database,
            idno=record.idno,
            name=record.fields.get("name"),
            people=record.people,
        )

    async def _load(
        self, session: AsyncSession, job: ScrapeJob, items: list[Extracted]
    ) -> None:
        result = await repository.store_all(session, items, job.id)
        job.rows_loaded += result.stored
        job.requests_ok += sum(1 for item in items if item.ok)
        job.requests_failed += sum(1 for item in items if not item.ok)

    async def _transform(self, session: AsyncSession, idno: str) -> CompanyRecord | None:
        record = build_company(
            idno,
            openmoney_company=await repository.latest_body(
                session,
                source=SOURCE_OPENMONEY,
                resource=RESOURCE_COMPANY,
                resource_key=idno,
            ),
            openmoney_fin_data=await repository.latest_body(
                session,
                source=SOURCE_OPENMONEY,
                resource=RESOURCE_FIN_DATA,
                resource_key=idno,
            ),
            depozitar_declarations=await repository.latest_bodies_for_idno(
                session,
                source=SOURCE_DEPOZITAR,
                resource=RESOURCE_DECLARATION,
                idno=idno,
            ),
        )
        if record.sources:
            await repository.save_company(session, record)
            log.info(
                "  transform saved %s from %s: %d people, %d statements",
                idno,
                "+".join(record.sources),
                len(record.people),
                len(record.statements),
            )
            return record
        log.warning("  transform skipped %s, no usable source body", idno)
        return None


def _elapsed(started: float) -> str:
    return f"{time.monotonic() - started:.1f}s"


def _body_of(items: list[Extracted], resource: str) -> str | None:
    for item in items:
        if item.resource == resource and item.ok:
            return item.fetched.body
    return None


# --- ownership graph --------------------------------------------------------

# A company party carries the other company's IDNO as its key, so when that
# company is already a node we point at it instead of inventing a duplicate.
COMPANY_PARTY = "COMPANY"


def build_graph(edges: list[repository.GraphEdge]) -> tuple[list[dict], list[dict]]:
    """Turns flat company-to-party rows into nodes and links, deduplicated.

    Every company becomes a node. Every distinct party key becomes one node no
    matter how many companies it sits on, which is exactly what makes the
    shared ones show up as hubs.
    """
    nodes: dict[str, dict] = {}
    degree: dict[str, int] = {}
    links: list[dict] = []

    for edge in edges:
        company_id = f"c:{edge.idno}"
        nodes.setdefault(
            company_id,
            {
                "id": company_id,
                "kind": "company",
                "label": edge.company_name or edge.idno,
                "idno": edge.idno,
            },
        )

        # A company shareholder resolves to that company's own node when we
        # hold it, so ownership between two scraped companies draws as one
        # edge rather than two unrelated blobs.
        if edge.party_type == COMPANY_PARTY and f"c:{edge.party_key}" in nodes:
            party_id = f"c:{edge.party_key}"
        else:
            party_id = f"p:{edge.party_key}"
            nodes.setdefault(
                party_id,
                {
                    "id": party_id,
                    "kind": "company_party"
                    if edge.party_type == COMPANY_PARTY
                    else "person",
                    "label": edge.party_name,
                    "idno": edge.party_key if edge.party_type == COMPANY_PARTY else None,
                    "role": edge.role,
                },
            )

        if party_id == company_id:
            continue  # a company listed as its own founder; nothing to draw

        links.append(
            {
                "source": company_id,
                "target": party_id,
                "role": edge.role,
                "share_percent": edge.share_percent,
            }
        )
        degree[company_id] = degree.get(company_id, 0) + 1
        degree[party_id] = degree.get(party_id, 0) + 1

    for node_id, node in nodes.items():
        node["degree"] = degree.get(node_id, 0)

    return list(nodes.values()), links
