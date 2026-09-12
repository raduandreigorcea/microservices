"""Who is calling. The answer always comes from user_service.

The gateway never opens a token itself: it hands the bearer to
``user_service`` and believes the answer. That keeps the signing key in one
place, and means a revoked session stops working here too.
"""

from __future__ import annotations

import time

import httpx
from fastapi import HTTPException, Request, status

from app.config import Settings

# token -> (moment the entry goes stale, the introspection body)
_cache: dict[str, tuple[float, dict]] = {}

UNAUTHENTICATED = {"WWW-Authenticate": "Bearer"}


def _bearer(request: Request, settings: Settings) -> str:
    """The header first, then the cookie a browser was given at login."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()

    from_cookie = request.cookies.get(settings.access_cookie_name, "").strip()
    if from_cookie:
        return from_cookie

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="this route needs a bearer token",
        headers=UNAUTHENTICATED,
    )


def _remember(token: str, payload: dict, settings: Settings, now: float) -> None:
    if not settings.introspection_cache_ttl_seconds:
        return
    for key, (stale_at, _) in list(_cache.items()):
        if stale_at <= now:
            del _cache[key]
    if len(_cache) >= settings.introspection_cache_max_entries:
        _cache.clear()
    _cache[token] = (now + settings.introspection_cache_ttl_seconds, payload)


async def introspect(
    client: httpx.AsyncClient, settings: Settings, token: str
) -> dict:
    now = time.monotonic()
    cached = _cache.get(token)
    if cached is not None and cached[0] > now:
        return cached[1]

    url = f"{settings.user_service_url.rstrip('/')}/auth/introspect"
    try:
        response = await client.post(url, json={"token": token})
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="user_service could not be reached to check the token",
        ) from exc

    payload = response.json()
    _remember(token, payload, settings, now)
    return payload


async def authenticate(
    request: Request, client: httpx.AsyncClient, settings: Settings
) -> tuple[str, dict]:
    """Returns the token and the introspection body, or raises 401.

    The token comes back so the proxy can put it in an Authorization header on
    the way inward: a cookie is a browser detail, and no service behind the
    gateway should have to know about it.
    """
    token = _bearer(request, settings)
    payload = await introspect(client, settings, token)
    if not payload.get("active"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="the token is expired, revoked or unknown",
            headers=UNAUTHENTICATED,
        )
    return token, payload
