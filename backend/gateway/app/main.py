"""gateway: the one door into the backend.

A request arrives here, gets its bearer token checked against user_service,
and is handed to app_service with the caller stapled to it. Nothing else in
the stack is meant to be reachable from outside.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, status

from app.auth import authenticate
from app.config import SettingsDep, get_settings

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
# httpx hands us a decoded body, so the encoding it arrived in is a lie by the
# time we write it out.
RESPONSE_DROP = HOP_BY_HOP | {"content-encoding"}

# Who the caller is, as decided here. Stripped off the incoming request first,
# so nobody can walk in claiming to be an admin.
IDENTITY_HEADERS = ("x-user-id", "x-user-email", "x-user-role", "x-user-scopes")

PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


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
The entry point for every client request.

Anything under `/auth` passes straight through, because that is where a token
comes from. Everything else needs `Authorization: Bearer <token>`; the token is
checked with user_service and the request is then forwarded to app_service.

Sign in at [/auth/google/login](/auth/google/login), copy the `access_token`
out of the answer, and send it back as a bearer.
"""


def create_app() -> FastAPI:
    app = FastAPI(
        title="gateway",
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
        """Green only when both services behind the door answer."""
        client: httpx.AsyncClient = request.app.state.client
        report: dict[str, str] = {"status": "ready"}
        for name, base in (
            ("app_service", settings.app_service_url),
            ("user_service", settings.user_service_url),
        ):
            try:
                response = await client.get(f"{base.rstrip('/')}/health", timeout=5.0)
                report[name] = "ok" if response.status_code == 200 else "unhealthy"
            except httpx.HTTPError:
                report[name] = "unreachable"
        if any(value != "ok" for key, value in report.items() if key != "status"):
            report["status"] = "degraded"
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=report
            )
        return report

    @app.api_route("/{path:path}", methods=PROXY_METHODS, include_in_schema=False)
    async def proxy(path: str, request: Request, settings: SettingsDep) -> Response:
        client: httpx.AsyncClient = request.app.state.client
        target_path = f"/{path}"

        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in HOP_BY_HOP and key.lower() not in IDENTITY_HEADERS
        }

        if not any(
            target_path == prefix or target_path.startswith(f"{prefix}/")
            for prefix in settings.public_prefix_list
        ):
            claims = await authenticate(request, client, settings)
            headers["x-user-id"] = str(claims.get("sub") or "")
            headers["x-user-email"] = str(claims.get("email") or "")
            headers["x-user-role"] = str(claims.get("role") or "")
            headers["x-user-scopes"] = " ".join(claims.get("scopes") or ())

        url = httpx.URL(
            f"{settings.app_service_url.rstrip('/')}{target_path}",
            query=request.url.query.encode(),
        )
        try:
            upstream = await client.request(
                request.method, url, headers=headers, content=await request.body()
            )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="app_service could not be reached",
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
