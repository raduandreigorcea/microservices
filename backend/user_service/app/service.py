"""Tokens, PKCE, Google, and the auth use cases the router exposes."""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Protocol
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, Response, Security, status
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
    SecurityScopes,
)
from jwt import PyJWKClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app import repository
from app.config import RedisDep, SessionDep, Settings, SettingsDep
from app.models import User
from app.schemas import IntrospectResponse, TokenPair

# --- scopes ----------------------------------------------------------------

SCOPE_DESCRIPTIONS = {
    "users:read": "Read your own profile and sessions",
    "scrape:read": "Read scraped company data",
    "scrape:write": "Trigger new scrapes",
}

DEFAULT_ROLE = "user"

# One role, and it holds every scope there is. The mapping stays because the
# scopes are what the services actually enforce; a second role would be added
# here and nowhere else.
ROLE_SCOPES: dict[str, tuple[str, ...]] = {
    DEFAULT_ROLE: tuple(SCOPE_DESCRIPTIONS),
}


def scopes_for_role(role: str) -> tuple[str, ...]:
    """Unknown roles, including any left over in the database, get the default."""
    return ROLE_SCOPES.get(role, ROLE_SCOPES[DEFAULT_ROLE])


# --- tokens and PKCE -------------------------------------------------------

DEFAULT_ALGORITHM = "HS256"
DEFAULT_ACCESS_TOKEN_TTL_SECONDS = 900


class InvalidToken(Exception):
    """Token is malformed, expired, or signed with the wrong key."""


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    subject: str
    scopes: tuple[str, ...]
    token_id: str
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PkcePair:
    verifier: str
    challenge: str


def create_access_token(
    subject: str,
    scopes: Iterable[str],
    *,
    secret: str,
    algorithm: str = DEFAULT_ALGORITHM,
    ttl_seconds: int = DEFAULT_ACCESS_TOKEN_TTL_SECONDS,
) -> tuple[str, AccessTokenClaims]:
    issued_at = datetime.now(UTC)
    claims = AccessTokenClaims(
        subject=subject,
        scopes=tuple(scopes),
        token_id=str(uuid.uuid4()),
        issued_at=issued_at,
        expires_at=issued_at + timedelta(seconds=ttl_seconds),
    )
    payload = {
        "sub": claims.subject,
        "scope": " ".join(claims.scopes),
        "jti": claims.token_id,
        "iat": int(claims.issued_at.timestamp()),
        "exp": int(claims.expires_at.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=algorithm), claims


def decode_access_token(
    token: str, *, secret: str, algorithm: str = DEFAULT_ALGORITHM
) -> AccessTokenClaims:
    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm])
    except jwt.PyJWTError as exc:
        raise InvalidToken(str(exc)) from exc

    try:
        return AccessTokenClaims(
            subject=payload["sub"],
            scopes=tuple(payload.get("scope", "").split()),
            token_id=payload["jti"],
            issued_at=datetime.fromtimestamp(payload["iat"], UTC),
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidToken("token is missing required claims") from exc


def new_refresh_token() -> str:
    """Opaque, not a JWT. Only its hash is ever stored."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_pkce_pair() -> PkcePair:
    """RFC 7636 S256. The verifier never leaves us, only the challenge does."""
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return PkcePair(verifier=verifier, challenge=challenge)


def new_oauth_state() -> str:
    """Opaque value tying an authorization redirect to its callback."""
    return secrets.token_urlsafe(32)


# --- cookies ---------------------------------------------------------------


def set_auth_cookies(response: Response, pair: TokenPair, settings: Settings) -> None:
    """Hands the pair to a browser in a form script cannot read."""
    common = {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "path": settings.cookie_path,
        "domain": settings.cookie_domain or None,
    }
    response.set_cookie(
        settings.access_cookie_name,
        pair.access_token,
        max_age=settings.access_token_ttl_seconds,
        **common,
    )
    response.set_cookie(
        settings.refresh_cookie_name,
        pair.refresh_token,
        max_age=settings.refresh_token_ttl_seconds,
        **common,
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    for name in (settings.access_cookie_name, settings.refresh_cookie_name):
        response.delete_cookie(
            name,
            path=settings.cookie_path,
            domain=settings.cookie_domain or None,
        )


def cookie_value(request: Request, name: str) -> str | None:
    value = request.cookies.get(name)
    return value.strip() if value and value.strip() else None


# --- Google ----------------------------------------------------------------

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")

# Google stamps `iat` from its own clock. Ours is never exactly the same, and
# a container's drifts further every time the host sleeps. Without any grace a
# clock a single second behind Google's rejects a perfectly good token as "not
# yet valid". Google's own library allows ten seconds; thirty costs nothing
# against a token that lives an hour.
CLOCK_SKEW_SECONDS = 30

# Identity only. We never ask for access to the user's Google data.
GOOGLE_SCOPES = "openid email profile"


class GoogleAuthError(Exception):
    """Google refused the exchange, or returned something we cannot trust."""


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    subject: str
    email: str
    email_verified: bool
    full_name: str | None = None
    picture_url: str | None = None


class SupportsGoogleOAuth(Protocol):
    def authorization_url(self, *, state: str, code_challenge: str) -> str: ...

    async def exchange_code(
        self, *, code: str, code_verifier: str
    ) -> GoogleIdentity: ...


class GoogleOAuthClient:
    def __init__(
        self, *, client_id: str, client_secret: str, redirect_uri: str
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._jwks: PyJWKClient | None = None

    def authorization_url(self, *, state: str, code_challenge: str) -> str:
        parameters = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": GOOGLE_SCOPES,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "access_type": "online",
            "prompt": "select_account",
        }
        return f"{AUTHORIZATION_ENDPOINT}?{urlencode(parameters)}"

    async def exchange_code(self, *, code: str, code_verifier: str) -> GoogleIdentity:
        async with httpx.AsyncClient(timeout=10) as http:
            response = await http.post(
                TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "redirect_uri": self._redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": code_verifier,
                },
            )
        if response.status_code != 200:
            raise GoogleAuthError(f"google rejected the code: {response.text}")

        id_token = response.json().get("id_token")
        if not id_token:
            raise GoogleAuthError("google returned no id_token")
        return self._identity_from_id_token(id_token)

    def _identity_from_id_token(self, id_token: str) -> GoogleIdentity:
        if self._jwks is None:
            self._jwks = PyJWKClient(JWKS_URI)
        try:
            key = self._jwks.get_signing_key_from_jwt(id_token).key
            claims = jwt.decode(
                id_token,
                key,
                algorithms=["RS256"],
                audience=self._client_id,
                leeway=CLOCK_SKEW_SECONDS,
            )
        except Exception as exc:
            raise GoogleAuthError(f"id_token failed verification: {exc}") from exc

        if claims.get("iss") not in ISSUERS:
            raise GoogleAuthError("id_token has an unexpected issuer")

        return GoogleIdentity(
            subject=claims["sub"],
            email=claims["email"],
            email_verified=bool(claims.get("email_verified", False)),
            full_name=claims.get("name"),
            picture_url=claims.get("picture"),
        )


def get_google_client(settings: SettingsDep) -> SupportsGoogleOAuth:
    return GoogleOAuthClient(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        redirect_uri=settings.google_redirect_uri,
    )


GoogleDep = Annotated[SupportsGoogleOAuth, Depends(get_google_client)]


# --- who is calling --------------------------------------------------------


def _unauthorised(detail: str, challenge: str = "Bearer") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": challenge},
    )


# Swagger cannot finish the Google round trip, so it just takes the
# access_token from /auth/google/callback pasted in by hand.
bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Paste the access_token returned by /auth/google/callback",
)


async def get_bearer_token(
    request: Request,
    settings: SettingsDep,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
) -> str:
    if credentials is not None:
        return credentials.credentials
    from_cookie = cookie_value(request, settings.access_cookie_name)
    if from_cookie is None:
        raise _unauthorised("not authenticated")
    return from_cookie


@dataclass(frozen=True)
class Identity:
    user: User
    claims: AccessTokenClaims


async def get_current_identity(
    security_scopes: SecurityScopes,
    token: Annotated[str, Depends(get_bearer_token)],
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
) -> Identity:
    challenge = (
        f'Bearer scope="{" ".join(security_scopes.scopes)}"'
        if security_scopes.scopes
        else "Bearer"
    )
    try:
        claims = decode_access_token(
            token, secret=settings.jwt_secret_key, algorithm=settings.jwt_algorithm
        )
    except InvalidToken as exc:
        raise _unauthorised(str(exc), challenge) from exc

    if await repository.is_access_token_denied(
        redis, settings.token_denylist_prefix, claims.token_id
    ):
        raise _unauthorised("token has been revoked", challenge)

    try:
        user_id = uuid.UUID(claims.subject)
    except ValueError as exc:
        raise _unauthorised("token subject is not a user id", challenge) from exc

    user = await repository.get_user_by_id(session, str(user_id))
    if user is None or not user.is_active:
        raise _unauthorised("user is inactive or gone", challenge)

    missing = [s for s in security_scopes.scopes if s not in claims.scopes]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"missing required scope: {', '.join(missing)}",
            headers={"WWW-Authenticate": challenge},
        )
    return Identity(user=user, claims=claims)


CurrentIdentity = Annotated[Identity, Security(get_current_identity)]
ReadIdentity = Annotated[
    Identity, Security(get_current_identity, scopes=["users:read"])
]


# --- use cases -------------------------------------------------------------


async def issue_token_pair(
    session: AsyncSession,
    user: User,
    settings: Settings,
    request: Request,
    replaces=None,
) -> TokenPair:
    scopes = scopes_for_role(user.role)
    access_token, _ = create_access_token(
        str(user.id),
        scopes,
        secret=settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
        ttl_seconds=settings.access_token_ttl_seconds,
    )
    raw_refresh = new_refresh_token()
    stored = await repository.add_refresh_token(
        session,
        user_id=user.id,
        token_hash=hash_refresh_token(raw_refresh),
        ttl_seconds=settings.refresh_token_ttl_seconds,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    if replaces is not None:
        repository.revoke(replaces, replaced_by=stored)

    await session.commit()
    return TokenPair(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=settings.access_token_ttl_seconds,
        scope=" ".join(scopes),
    )


async def begin_authorization(
    redis: Redis, settings: Settings, google: SupportsGoogleOAuth
) -> tuple[str, str]:
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "google sign-in is not configured: set GOOGLE_CLIENT_ID and "
                "GOOGLE_CLIENT_SECRET in .env, then restart the service"
            ),
        )
    state = new_oauth_state()
    pkce = new_pkce_pair()
    await repository.remember_oauth_state(
        redis,
        settings.oauth_state_prefix,
        state,
        pkce.verifier,
        settings.oauth_state_ttl_seconds,
    )
    return google.authorization_url(state=state, code_challenge=pkce.challenge), state


async def complete_authorization(
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
    google: SupportsGoogleOAuth,
    request: Request,
    *,
    state: str,
    code: str | None,
    error: str | None,
) -> TokenPair:
    if error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"google returned an error: {error}",
        )
    if code is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing authorization code",
        )

    verifier = await repository.take_oauth_state(
        redis, settings.oauth_state_prefix, state
    )
    if verifier is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="unknown or already used state",
        )

    try:
        who = await google.exchange_code(code=code, code_verifier=verifier)
    except GoogleAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    if not who.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="this google account has no verified email address",
        )

    user = await _upsert_google_user(session, who)
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="account is disabled"
        )
    return await issue_token_pair(session, user, settings, request)


async def _upsert_google_user(session: AsyncSession, who: GoogleIdentity) -> User:
    """First login creates the account. Later ones just refresh the profile."""
    user = await repository.get_user_by_google_sub(session, who.subject)
    if user is None:
        user = await repository.get_user_by_email(session, who.email)
        if user is not None:
            # Same address, new Google account. Take it over rather than duplicate.
            user.google_sub = who.subject
        else:
            user = await repository.add_user(
                session, email=who.email, google_sub=who.subject
            )

    user.email_verified = who.email_verified
    user.full_name = who.full_name
    user.picture_url = who.picture_url
    await session.flush()
    return user


async def rotate_refresh_token(
    session: AsyncSession, settings: Settings, request: Request, raw_token: str
) -> TokenPair:
    stored = await repository.get_refresh_token(session, hash_refresh_token(raw_token))
    now = datetime.now(UTC)
    if stored is None or stored.revoked_at is not None or stored.expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="refresh token is not valid",
        )
    user = await repository.get_user_by_id(session, str(stored.user_id))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="user is inactive or gone"
        )
    return await issue_token_pair(session, user, settings, request, replaces=stored)


async def log_out(
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
    identity: Identity,
    raw_token: str,
) -> None:
    stored = await repository.get_refresh_token(
        session, hash_refresh_token(raw_token), user_id=identity.user.id
    )
    if stored is not None and stored.revoked_at is None:
        repository.revoke(stored)
        await session.commit()

    remaining = int((identity.claims.expires_at - datetime.now(UTC)).total_seconds())
    await repository.deny_access_token(
        redis, settings.token_denylist_prefix, identity.claims.token_id, remaining
    )


async def introspect_token(
    session: AsyncSession, redis: Redis, settings: Settings, token: str
) -> IntrospectResponse:
    """Used by the gateway. Always 200, with active telling the real story."""
    try:
        claims = decode_access_token(
            token, secret=settings.jwt_secret_key, algorithm=settings.jwt_algorithm
        )
    except InvalidToken:
        return IntrospectResponse(active=False)

    if await repository.is_access_token_denied(
        redis, settings.token_denylist_prefix, claims.token_id
    ):
        return IntrospectResponse(active=False)

    user = await repository.get_user_by_id(session, claims.subject)
    if user is None or not user.is_active:
        return IntrospectResponse(active=False)

    return IntrospectResponse(
        active=True,
        sub=str(user.id),
        email=user.email,
        role=user.role,
        scopes=list(claims.scopes),
        expires_at=claims.expires_at,
    )
