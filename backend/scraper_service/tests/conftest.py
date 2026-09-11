import asyncio
import json
import os
import pathlib
import selectors
import sys
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Base
from app.sources import Fetched

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SAMPLE_IDNO = "1003600069773"


# Only defined on Windows: pytest-asyncio rejects an implementation of this
# hook that returns nothing, so everywhere else it must stay unregistered and
# let the default loop be used.
if sys.platform == "win32":

    def _selector_loop():
        return asyncio.SelectorEventLoop(selectors.SelectSelector())

    def pytest_asyncio_loop_factories(config, item):
        """psycopg's async mode refuses the Proactor loop Windows defaults to."""
        return {"selector": _selector_loop}


def fixture_text(name: str) -> str:
    return FIXTURES.joinpath(name).read_text(encoding="utf-8")


# One database for everything. Tests get their own schema inside it so a test
# run never touches the tables the running service owns.
DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://postgres:dev_postgres_pw@localhost:5432/microservices"
)

TEST_SCHEMA = "scraper_service_test"


async def _run_as_admin(url: str, statement: str) -> None:
    import psycopg
    from sqlalchemy.engine import make_url

    target = make_url(url)
    dsn = (
        f"postgresql://{target.username}:{target.password}"
        f"@{target.host}:{target.port}/{target.database}"
    )
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await conn.execute(statement)


@pytest.fixture(scope="session")
def database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine(database_url):
    await _run_as_admin(database_url, f'create schema if not exists "{TEST_SCHEMA}"')
    engine = create_async_engine(
        database_url,
        poolclass=NullPool,
        connect_args={"options": f"-csearch_path={TEST_SCHEMA}"},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()
    # Leave the database exactly as we found it.
    await _run_as_admin(database_url, f'drop schema if exists "{TEST_SCHEMA}" cascade')


@pytest_asyncio.fixture(loop_scope="session")
async def session(engine):
    """Each test runs inside a transaction that is rolled back afterwards."""
    connection = await engine.connect()
    transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()


DEFAULT_TEST_REDIS_URL = "redis://:dev_redis_pw@localhost:6379/14"


@pytest.fixture(scope="session")
def redis_url() -> str:
    return os.environ.get("TEST_REDIS_URL", DEFAULT_TEST_REDIS_URL)


@pytest.fixture(scope="session")
def settings(database_url, redis_url):
    from app.config import Settings

    return Settings(
        database_url=database_url,
        redis_url=redis_url,
        user_service_url="http://user-service:8002",
        # Nothing here should sleep or open a browser.
        request_min_interval_seconds=0,
        request_backoff_seconds=0,
        capture_rendered_html=True,
        introspection_cache_ttl_seconds=0,
    )


@pytest_asyncio.fixture(loop_scope="session")
async def redis(redis_url):
    from redis.asyncio import Redis

    client = Redis.from_url(redis_url, decode_responses=True)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


class FakeBrowser:
    """Answers from the saved fixtures instead of opening Chromium."""

    def __init__(self) -> None:
        self.requested: list[str] = []
        self.rendered: list[str] = []
        self.status_overrides: dict[str, int] = {}
        self.declaration = json.loads(fixture_text("depozitar_declaration.json"))
        # The depositary holds one declaration per year, each under its own id.
        self.years_by_declaration = {
            row["id"]: row["year"]
            for row in json.loads(fixture_text("depozitar_index.json"))
        }

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    @asynccontextmanager
    async def page(self):
        yield object()

    async def render(self, tab, url: str) -> Fetched:
        self.rendered.append(url)
        return Fetched(
            url=url,
            status=200,
            content_type="text/html",
            body=f"<html><body>{url}</body></html>",
        )

    async def get_with_retry(self, url: str, *, referer: str | None = None) -> Fetched:
        self.requested.append(url)
        for marker, status in self.status_overrides.items():
            if marker in url:
                return Fetched(
                    url=url, status=status, content_type=None, body='{"error":true}'
                )
        return Fetched(
            url=url,
            status=200,
            content_type="application/json",
            body=self._body_for(url),
        )

    def _body_for(self, url: str) -> str:
        if "/api/fin-data-v1/" in url:
            return fixture_text("openmoney_fin_data.json")
        if "/api/companies?" in url:
            return fixture_text("openmoney_companies_page.json")
        if "/api/companies/" in url:
            return fixture_text("openmoney_company.json")
        if "/fs/economic-agent" in url:
            return fixture_text("depozitar_index.json")
        if "/api/public/v1/fs/" in url:
            declaration_id = url.rsplit("/", 1)[-1]
            year = self.years_by_declaration.get(declaration_id, 2024)
            payload = dict(
                self.declaration,
                id=declaration_id,
                year=year,
                periodFrom=f"{year}-01-01",
                periodTo=f"{year}-12-31",
            )
            return json.dumps(payload, ensure_ascii=False)
        return "{}"


@pytest.fixture
def browser() -> FakeBrowser:
    return FakeBrowser()


@pytest.fixture
def runner(settings, browser, session):
    """A runner whose jobs all share the test's single rolled-back session."""
    from app.service import ScrapeRunner

    @asynccontextmanager
    async def one_session():
        yield session

    def session_factory():
        return one_session()

    return ScrapeRunner(settings, browser, session_factory)


class FakeIntrospector:
    """Stands in for user_service so the suite runs without it."""

    def __init__(self) -> None:
        self.active = True
        self.scopes = ["scrape:read", "scrape:write"]
        self.calls: list[str] = []

    async def __call__(self, token: str) -> dict:
        self.calls.append(token)
        if not self.active:
            return {"active": False}
        return {
            "active": True,
            "sub": "11111111-1111-1111-1111-111111111111",
            "email": "sam@example.com",
            "role": "user",
            "scopes": list(self.scopes),
        }


@pytest.fixture
def introspector() -> FakeIntrospector:
    return FakeIntrospector()


@pytest_asyncio.fixture(loop_scope="session")
async def client(session, redis, settings, introspector, browser, runner):
    from httpx import ASGITransport, AsyncClient

    from app.config import get_redis, get_session, get_settings
    from app.main import create_app
    from app.service import get_introspector

    app = create_app()
    # ASGITransport never runs the lifespan, so no tables or Chromium here.
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_introspector] = lambda: introspector
    app.state.browser = browser
    app.state.runner = runner

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://scraper-service") as c:
        yield c


@pytest.fixture
def auth_header():
    return {"Authorization": "Bearer test-token"}
