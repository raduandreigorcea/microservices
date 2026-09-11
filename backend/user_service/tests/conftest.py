import asyncio
import os
import selectors
import sys

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Base


# Only defined on Windows: pytest-asyncio rejects an implementation of this
# hook that returns nothing, so everywhere else it must stay unregistered and
# let the default loop be used.
if sys.platform == "win32":

    def _selector_loop():
        return asyncio.SelectorEventLoop(selectors.SelectSelector())

    def pytest_asyncio_loop_factories(config, item):
        """psycopg's async mode refuses the Proactor loop Windows defaults to."""
        return {"selector": _selector_loop}


# One database for everything. Tests get their own schema inside it so a test
# run never touches the tables the running service owns.
DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://postgres:dev_postgres_pw@localhost:5432/microservices"
)

TEST_SCHEMA = "user_service_test"


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


DEFAULT_TEST_REDIS_URL = "redis://:dev_redis_pw@localhost:6379/15"

TEST_JWT_SECRET = "test-jwt-secret-at-least-32-bytes-long"


@pytest.fixture(scope="session")
def redis_url() -> str:
    return os.environ.get("TEST_REDIS_URL", DEFAULT_TEST_REDIS_URL)


@pytest.fixture(scope="session")
def settings(database_url, redis_url):
    from app.config import Settings

    return Settings(
        database_url=database_url,
        redis_url=redis_url,
        jwt_secret_key=TEST_JWT_SECRET,
        access_token_ttl_seconds=900,
        google_client_id="test-client-id",
        google_client_secret="test-client-secret",
        google_redirect_uri="http://user-service/auth/google/callback",
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


class FakeGoogle:
    """Stands in for Google so the suite runs without network or credentials."""

    def __init__(self):
        from app.service import GoogleIdentity

        self.identity = GoogleIdentity(
            subject="google-sub-abc",
            email="sam@example.com",
            email_verified=True,
            full_name="Sam Example",
            picture_url="https://example.com/sam.png",
        )
        self.exchanges: list[dict] = []

    def mark_email_unverified(self):
        from dataclasses import replace

        self.identity = replace(self.identity, email_verified=False)

    def authorization_url(self, *, state: str, code_challenge: str) -> str:
        from urllib.parse import urlencode

        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {"state": state, "code_challenge": code_challenge, "response_type": "code"}
        )

    async def exchange_code(self, *, code: str, code_verifier: str):
        from app.service import GoogleAuthError

        self.exchanges.append({"code": code, "code_verifier": code_verifier})
        if code != "good-code":
            raise GoogleAuthError("invalid_grant")
        return self.identity


@pytest.fixture
def fake_google():
    return FakeGoogle()


@pytest_asyncio.fixture(loop_scope="session")
async def client(session, redis, settings, fake_google):
    from httpx import ASGITransport, AsyncClient

    from app.config import get_redis, get_session, get_settings
    from app.main import create_app
    from app.service import get_google_client

    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_google_client] = lambda: fake_google

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://user-service") as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def registered(client):
    """A user who has completed the Google flow, with their token pair."""
    from urllib.parse import parse_qs, urlparse

    started = await client.get("/auth/google/authorize")
    state = parse_qs(urlparse(started.json()["authorization_url"]).query)["state"][0]
    response = await client.get(
        "/auth/google/callback", params={"code": "good-code", "state": state}
    )
    return response.json()


@pytest.fixture
def auth_header(registered):
    return {"Authorization": f"Bearer {registered['access_token']}"}
