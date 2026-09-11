"""Who is calling, and the job runner that does the actual scraping."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, SecurityScopes
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import repository
from app.config import RedisDep, Settings, SettingsDep
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
from app.transform import build_company

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
    ) -> None:
        self._settings = settings
        self._browser = browser
        self._session_factory = session_factory
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
        async with self._session_factory() as session:
            job = await repository.get_job(session, job_id)
            if job is None:
                return
            await repository.mark_job_running(session, job)
            await session.commit()

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
            await self._scrape_company(session, job, idno, include_depozitar)
            job.cursor = {"done": position + 1}
            await session.commit()

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

            if not idnos:
                break

            for position, idno in enumerate(idnos):
                if position < index_in_page:
                    continue
                self._check_cancelled(job.id)
                await self._scrape_company(session, job, idno, include_depozitar)
                job.cursor = {
                    "page": page_number,
                    "index": position + 1,
                    "pages_done": pages_done,
                }
                await session.commit()

            index_in_page = 0
            page_number += 1
            pages_done += 1
            job.cursor = {"page": page_number, "index": 0, "pages_done": pages_done}
            await session.commit()

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
    ) -> None:
        items = list(await self.openmoney.fetch_company(idno))

        if include_depozitar:
            index = await self.depozitar.fetch_index(idno)
            items.append(index)
            if index.ok:
                for declaration_id in declaration_ids(index.fetched.body):
                    self._check_cancelled(job.id)
                    items.extend(
                        await self.depozitar.fetch_declaration(idno, declaration_id)
                    )

        await self._load(session, job, items)
        await self._transform(session, idno)
        job.companies_done += 1

    async def _load(
        self, session: AsyncSession, job: ScrapeJob, items: list[Extracted]
    ) -> None:
        result = await repository.store_all(session, items, job.id)
        job.rows_loaded += result.stored
        job.requests_ok += sum(1 for item in items if item.ok)
        job.requests_failed += sum(1 for item in items if not item.ok)

    async def _transform(self, session: AsyncSession, idno: str) -> None:
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


def _body_of(items: list[Extracted], resource: str) -> str | None:
    for item in items:
        if item.resource == resource and item.ok:
            return item.fetched.body
    return None
