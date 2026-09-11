"""The gateway talks to nothing but two HTTP services, so the whole suite runs
in process: a mock transport stands in for app_service and user_service, and
the app is driven through ASGI. No network, no database, no containers."""

from __future__ import annotations

from contextlib import AsyncExitStack

import httpx
import pytest
import pytest_asyncio

APP_SERVICE_URL = "http://app-service"
USER_SERVICE_URL = "http://user-service"

# What user_service says about a token everybody is happy with.
VALID_CLAIMS = {
    "active": True,
    "sub": "7",
    "email": "sam@example.com",
    "role": "member",
    "scopes": ["users:read", "scrape:write"],
}


def echo(request: httpx.Request) -> httpx.Response:
    """The default app_service: hands back what it was given, so a test can
    assert on exactly what crossed the wire."""
    return httpx.Response(
        200,
        json={
            "method": request.method,
            "path": request.url.path,
            "query": request.url.query.decode(),
            "headers": dict(request.headers),
            "body": request.content.decode(),
        },
    )


class Upstream:
    """Both services behind the gateway, and the knobs to make them misbehave."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.claims: dict = dict(VALID_CLAIMS)
        self.app_handler = echo
        self.app_health_status = 200
        self.user_health_status = 200
        self.app_unreachable = False
        self.user_unreachable = False

    @property
    def introspections(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == "/auth/introspect"]

    @property
    def forwarded(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == "app-service"]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        path = request.url.path

        if host == "user-service":
            if self.user_unreachable:
                raise httpx.ConnectError("user_service is down", request=request)
            if path == "/health":
                return httpx.Response(self.user_health_status)
            if path == "/auth/introspect":
                return httpx.Response(200, json=self.claims)
            return httpx.Response(404)

        if host == "app-service":
            if self.app_unreachable:
                raise httpx.ConnectError("app_service is down", request=request)
            if path == "/health":
                return httpx.Response(self.app_health_status)
            return self.app_handler(request)

        raise AssertionError(f"the gateway called an unexpected host: {host}")


@pytest.fixture
def upstream() -> Upstream:
    return Upstream()


@pytest_asyncio.fixture(loop_scope="session")
async def make_client(upstream):
    """Builds a gateway wired to the mock upstream. Settings can be overridden
    per test, which is how the introspection cache gets exercised both ways."""
    from app import auth
    from app.config import Settings, get_settings
    from app.main import create_app

    async with AsyncExitStack() as stack:

        async def _make(**overrides) -> httpx.AsyncClient:
            # The token cache lives on the module, not on the app.
            auth._cache.clear()
            settings = Settings(
                **{
                    "app_service_url": APP_SERVICE_URL,
                    "user_service_url": USER_SERVICE_URL,
                    # Off by default, so a test that does not care about the
                    # cache cannot be influenced by it.
                    "introspection_cache_ttl_seconds": 0,
                    **overrides,
                }
            )
            gateway = create_app()
            gateway.dependency_overrides[get_settings] = lambda: settings
            # Lifespan does not run under ASGITransport, so the outbound client
            # is planted here instead.
            gateway.state.client = await stack.enter_async_context(
                httpx.AsyncClient(transport=httpx.MockTransport(upstream.handle))
            )
            return await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=gateway),
                    base_url="http://gateway",
                )
            )

        yield _make


@pytest_asyncio.fixture(loop_scope="session")
async def client(make_client):
    return await make_client()


@pytest.fixture
def auth_header() -> dict[str, str]:
    return {"Authorization": "Bearer good-token"}
