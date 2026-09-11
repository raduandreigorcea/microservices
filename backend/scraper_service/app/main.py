"""scraper_service: company data from openmoney.md and the statistics depository."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from app import graph_store
from app.config import get_driver, get_engine, get_session_factory, get_settings
from app.logs import configure_logging
from app.models import Base
from app.router import companies_router, health_router, scrape_router
from app.service import ScrapeRunner
from app.sources import BrowserPool

log = logging.getLogger("scraper")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create this service's own tables on boot. It owns its schema."""
    settings = get_settings()
    # After uvicorn has set up its own logging, so our filters stick.
    configure_logging(settings.log_level)
    engine = get_engine(settings.database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    # The graph is a projection, so the service still runs without it: the
    # graph endpoint falls back to SQL and says which store answered.
    driver = None
    if settings.neo4j_enabled:
        driver = get_driver(
            settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password
        )
        try:
            await graph_store.ensure_constraints(driver, settings.neo4j_database)
        except (Neo4jError, ServiceUnavailable, OSError) as exc:
            # Not fatal: the constraints are created again on the next boot,
            # and `POST /scrape/reproject` creates them too.
            log.warning("neo4j did not answer on boot: %s", exc)
    app.state.neo4j = driver

    browser = BrowserPool(settings)
    app.state.browser = browser
    app.state.runner = ScrapeRunner(
        settings, browser, get_session_factory(settings.database_url), driver
    )
    try:
        yield
    finally:
        # Chromium only starts when the first job runs, so it may never exist.
        await app.state.runner.drain()
        await browser.stop()
        if driver is not None:
            await driver.close()
        await engine.dispose()


DESCRIPTION = """
Scrapes Moldovan company data and keeps it in three layers.

* **Extract** drives [openmoney.md](https://openmoney.md) and
  [depozitar.statistica.md](https://depozitar.statistica.md) in a real
  Chromium tab, by IDNO.
* **Load** writes every response into `source_data` exactly as it arrived.
* **Transform** turns those bodies into `transformed_data` and its companion
  tables: people, filed statements, and one row per statement line.
* **Project** mirrors the founder and administrator links into neo4j, which is
  what answers `GET /companies/{idno}/graph` and the shortest-path route.
  Postgres stays the source of truth; `POST /scrape/reproject` rebuilds it.

Start with `POST /scrape/companies` for a handful of IDNOs, or
`POST /scrape/sweep` to walk the register. Both return a job you can follow at
`GET /scrape/jobs/{id}`.

Every route needs a bearer token from user_service: `scrape:read` to read,
`scrape:write` to start or cancel a job.
"""


def create_app() -> FastAPI:
    app = FastAPI(
        title="scraper_service",
        description=DESCRIPTION,
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        swagger_ui_parameters={"persistAuthorization": True},
    )
    app.include_router(health_router)
    app.include_router(scrape_router)
    app.include_router(companies_router)
    return app


app = create_app()
