"""Who gets in, and what the gateway tells app_service about them."""

import pytest


async def test_a_protected_route_without_a_token_is_refused(client):
    response = await client.get("/companies")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "header",
    ["", "Bearer", "Bearer    ", "Basic abc", "good-token"],
    ids=["empty", "no-token", "blank-token", "wrong-scheme", "no-scheme"],
)
async def test_an_authorization_header_that_is_not_a_bearer_is_refused(client, header):
    response = await client.get("/companies", headers={"Authorization": header})
    assert response.status_code == 401


async def test_a_token_user_service_rejects_is_refused(client, upstream, auth_header):
    upstream.claims = {"active": False}
    response = await client.get("/companies", headers=auth_header)
    assert response.status_code == 401
    assert upstream.forwarded == []


async def test_a_refused_token_never_reaches_app_service(client, upstream):
    await client.get("/companies")
    assert upstream.forwarded == []


async def test_a_valid_token_gets_the_request_forwarded(client, upstream, auth_header):
    response = await client.get("/companies", headers=auth_header)
    assert response.status_code == 200
    assert upstream.forwarded[0].url.path == "/companies"


async def test_the_caller_is_stapled_to_the_forwarded_request(
    client, upstream, auth_header
):
    await client.get("/companies", headers=auth_header)
    headers = upstream.forwarded[0].headers
    assert headers["x-user-id"] == "7"
    assert headers["x-user-email"] == "sam@example.com"
    assert headers["x-user-role"] == "member"
    assert headers["x-user-scopes"] == "users:read scrape:write"


async def test_a_caller_cannot_promote_themselves_with_a_header(
    client, upstream, auth_header
):
    await client.get(
        "/companies",
        headers={
            **auth_header,
            "x-user-id": "1",
            "x-user-role": "admin",
            "x-user-scopes": "everything",
        },
    )
    headers = upstream.forwarded[0].headers
    assert headers["x-user-id"] == "7"
    assert headers["x-user-role"] == "member"
    assert headers["x-user-scopes"] == "users:read scrape:write"


async def test_claims_the_token_does_not_carry_arrive_empty(
    client, upstream, auth_header
):
    upstream.claims = {"active": True, "sub": "7"}
    await client.get("/companies", headers=auth_header)
    headers = upstream.forwarded[0].headers
    assert headers["x-user-email"] == ""
    assert headers["x-user-scopes"] == ""


# --- what passes without a token --------------------------------------------


async def test_the_auth_routes_are_open_because_that_is_where_tokens_come_from(
    client, upstream
):
    response = await client.get("/auth/google/login")
    assert response.status_code == 200
    assert upstream.introspections == []
    assert upstream.forwarded[0].url.path == "/auth/google/login"


async def test_an_open_route_carries_no_identity_at_all(client, upstream):
    await client.get(
        "/auth/google/login", headers={"x-user-id": "1", "x-user-role": "admin"}
    )
    headers = upstream.forwarded[0].headers
    assert "x-user-id" not in headers
    assert "x-user-role" not in headers


async def test_a_prefix_is_a_path_segment_not_a_string_prefix(client):
    """/auth is open. /authorize is somebody else's route and is not."""
    assert (await client.get("/authorize")).status_code == 401
    assert (await client.get("/healthz")).status_code == 401


# --- introspection cache ----------------------------------------------------


async def test_the_same_token_is_checked_once_while_the_cache_is_warm(
    make_client, upstream, auth_header
):
    client = await make_client(introspection_cache_ttl_seconds=30)
    await client.get("/companies", headers=auth_header)
    await client.get("/companies", headers=auth_header)
    assert len(upstream.introspections) == 1


async def test_two_tokens_are_checked_separately(make_client, upstream):
    client = await make_client(introspection_cache_ttl_seconds=30)
    await client.get("/companies", headers={"Authorization": "Bearer one"})
    await client.get("/companies", headers={"Authorization": "Bearer two"})
    assert len(upstream.introspections) == 2


async def test_turning_the_cache_off_checks_every_request(
    make_client, upstream, auth_header
):
    client = await make_client(introspection_cache_ttl_seconds=0)
    await client.get("/companies", headers=auth_header)
    await client.get("/companies", headers=auth_header)
    assert len(upstream.introspections) == 2


async def test_a_rejected_token_is_rejected_again_from_the_cache(
    make_client, upstream, auth_header
):
    client = await make_client(introspection_cache_ttl_seconds=30)
    upstream.claims = {"active": False}
    assert (await client.get("/companies", headers=auth_header)).status_code == 401
    assert (await client.get("/companies", headers=auth_header)).status_code == 401
    assert upstream.forwarded == []


async def test_a_silent_user_service_is_a_bad_gateway_not_a_refusal(
    client, upstream, auth_header
):
    """502, because we do not know whether the token is good."""
    upstream.user_unreachable = True
    response = await client.get("/companies", headers=auth_header)
    assert response.status_code == 502
    assert upstream.forwarded == []
