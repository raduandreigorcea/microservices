"""app_service owns no data and calls nothing but two HTTP services, so the
suite runs in process: a mock transport stands in for user_service and
scraper_service, and the app is driven through ASGI."""

from __future__ import annotations

from contextlib import AsyncExitStack

import httpx
import pytest
import pytest_asyncio

USER_SERVICE_URL = "http://user-service"
SCRAPER_SERVICE_URL = "http://scraper-service"

# What the gateway stamps on a request once it knows who is calling.
IDENTITY = {
    "x-user-id": "7",
    "x-user-email": "sam@example.com",
    "x-user-role": "member",
    "x-user-scopes": "users:read scrape:write",
}


def echo(request: httpx.Request) -> httpx.Response:
    """The default upstream: hands back what it was given, so a test can assert
    on exactly what crossed the wire."""
    return httpx.Response(
        200,
        json={
            "service": request.url.host,
            "method": request.method,
            "path": request.url.path,
            "query": request.url.query.decode(),
            "headers": dict(request.headers),
            "body": request.content.decode(),
        },
    )


class Upstream:
    """Both services in front of app_service, and the knobs to break them."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.handler = echo
        self.health_status = {"user-service": 200, "scraper-service": 200}
        self.unreachable: set[str] = set()

    @property
    def dispatched(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path != "/health"]

    def called(self) -> str:
        """The host of the single dispatched request."""
        assert len(self.dispatched) == 1, f"expected one call, got {self.dispatched}"
        return self.dispatched[0].url.host

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host not in ("user-service", "scraper-service"):
            raise AssertionError(f"app_service called an unexpected host: {host}")
        if host in self.unreachable:
            raise httpx.ConnectError(f"{host} is down", request=request)
        if request.url.path == "/health":
            return httpx.Response(self.health_status[host])
        return self.handler(request)


@pytest.fixture
def upstream() -> Upstream:
    return Upstream()


@pytest_asyncio.fixture(loop_scope="session")
async def client(upstream):
    from app.config import Settings, get_settings
    from app.main import create_app

    settings = Settings(
        user_service_url=USER_SERVICE_URL, scraper_service_url=SCRAPER_SERVICE_URL
    )
    orchestrator = create_app()
    orchestrator.dependency_overrides[get_settings] = lambda: settings

    async with AsyncExitStack() as stack:
        # Lifespan does not run under ASGITransport, so the outbound client is
        # planted here instead.
        orchestrator.state.client = await stack.enter_async_context(
            httpx.AsyncClient(transport=httpx.MockTransport(upstream.handle))
        )
        yield await stack.enter_async_context(
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=orchestrator),
                base_url="http://app-service",
            )
        )
