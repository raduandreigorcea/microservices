"""app_service: the orchestrator behind the gateway.

Everything the gateway lets through arrives here and is handed to the service
that owns the path. This service stores nothing of its own.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, status

from app.config import SettingsDep, get_settings

USER = "user_service"
SCRAPER = "scraper_service"

# First prefix that matches wins, so order the specific ones first.
ROUTES: tuple[tuple[str, str], ...] = (
    ("/auth", USER),
    ("/users", USER),
    ("/scrape", SCRAPER),
    ("/companies", SCRAPER),
)

# Headers that describe one hop and must not be copied to the next one.
HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)
# httpx decodes the body for us, so the encoding it arrived in no longer holds.
RESPONSE_DROP = HOP_BY_HOP | {"content-encoding"}

PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


def base_url(service: str, settings: SettingsDep) -> str:
    urls = {
        USER: settings.user_service_url,
        SCRAPER: settings.scraper_service_url,
    }
    return urls[service].rstrip("/")


def route_for(path: str) -> str | None:
    for prefix, service in ROUTES:
        if path == prefix or path.startswith(f"{prefix}/"):
            return service
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.client = httpx.AsyncClient(
        timeout=settings.request_timeout_seconds, follow_redirects=False
    )
    try:
        yield
    finally:
        await app.state.client.aclose()


DESCRIPTION = """
The orchestrator. Paths go to the service that owns them:

* `/auth` and `/users` to **user_service**
* `/scrape` and `/companies` to **scraper_service**

Reach this through the gateway on port 8000, which is what checks the token.
"""


def create_app() -> FastAPI:
    app = FastAPI(
        title="app_service",
        description=DESCRIPTION,
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    async def ready(request: Request, settings: SettingsDep) -> dict[str, str]:
        """Green only when both services this one orchestrates answer."""
        client: httpx.AsyncClient = request.app.state.client
        report: dict[str, str] = {"status": "ready"}
        for service in (USER, SCRAPER):
            try:
                response = await client.get(
                    f"{base_url(service, settings)}/health", timeout=5.0
                )
                report[service] = "ok" if response.status_code == 200 else "unhealthy"
            except httpx.HTTPError:
                report[service] = "unreachable"
        if any(value != "ok" for key, value in report.items() if key != "status"):
            report["status"] = "degraded"
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=report
            )
        return report

    @app.api_route("/{path:path}", methods=PROXY_METHODS, include_in_schema=False)
    async def dispatch(path: str, request: Request, settings: SettingsDep) -> Response:
        """Hand the request on untouched and give the answer back untouched."""
        service = route_for(f"/{path}")
        if service is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"no service handles /{path}",
            )

        url = httpx.URL(
            f"{base_url(service, settings)}{request.url.path}",
            query=request.url.query.encode(),
        )
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in HOP_BY_HOP
        }
        try:
            upstream = await request.app.state.client.request(
                request.method, url, headers=headers, content=await request.body()
            )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"{service} could not be reached",
            ) from exc

        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers={
                key: value
                for key, value in upstream.headers.items()
                if key.lower() not in RESPONSE_DROP
            },
        )

    return app


app = create_app()
