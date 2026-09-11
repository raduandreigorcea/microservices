"""What the gateway does to a request on the way through, and to the answer on
the way back. The short version: as little as possible."""

import gzip

import httpx
import pytest


async def test_the_method_and_body_survive_the_hop(client, upstream, auth_header):
    response = await client.post(
        "/scrape/companies", headers=auth_header, json={"idnos": ["1003600000000"]}
    )
    assert response.status_code == 200
    echoed = response.json()
    assert echoed["method"] == "POST"
    assert echoed["body"] == '{"idnos":["1003600000000"]}'


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
async def test_every_method_the_api_uses_is_forwarded(
    client, upstream, auth_header, method
):
    response = await client.request(method, "/companies/1003", headers=auth_header)
    assert response.status_code == 200
    assert upstream.forwarded[0].method == method


async def test_the_query_string_survives_the_hop(client, auth_header):
    response = await client.get(
        "/companies", headers=auth_header, params={"q": "acme srl", "page": 2}
    )
    assert response.json()["query"] == "q=acme+srl&page=2"


async def test_the_path_survives_the_hop(client, auth_header):
    response = await client.get("/companies/1003600000000", headers=auth_header)
    assert response.json()["path"] == "/companies/1003600000000"


async def test_the_upstream_status_is_handed_back_untouched(
    client, upstream, auth_header
):
    upstream.app_handler = lambda request: httpx.Response(
        409, json={"detail": "that job is already running"}
    )
    response = await client.post("/scrape/companies", headers=auth_header)
    assert response.status_code == 409
    assert response.json() == {"detail": "that job is already running"}


async def test_a_custom_upstream_header_is_handed_back(client, upstream, auth_header):
    upstream.app_handler = lambda request: httpx.Response(
        201, json={}, headers={"location": "/scrape/jobs/42"}
    )
    response = await client.post("/scrape/companies", headers=auth_header)
    assert response.headers["location"] == "/scrape/jobs/42"


async def test_hop_by_hop_headers_are_not_carried_to_the_next_hop(
    client, upstream, auth_header
):
    await client.get(
        "/companies",
        headers={
            **auth_header,
            "connection": "close",
            "te": "trailers",
            "upgrade": "websocket",
        },
    )
    forwarded = upstream.forwarded[0].headers
    assert "te" not in forwarded
    assert "upgrade" not in forwarded
    # The outgoing client sets its own connection header; ours did not survive.
    assert forwarded["connection"] != "close"
    # The host belongs to the hop, so it names app_service, not the gateway.
    assert forwarded["host"] == "app-service"


async def test_the_authorization_header_reaches_app_service(
    client, upstream, auth_header
):
    """app_service forwards it on to user_service, which still needs it."""
    await client.get("/users/me", headers=auth_header)
    assert upstream.forwarded[0].headers["authorization"] == "Bearer good-token"


async def test_a_compressed_answer_is_decoded_and_its_encoding_dropped(
    client, upstream, auth_header
):
    """httpx hands us a decoded body, so keeping content-encoding would lie."""
    upstream.app_handler = lambda request: httpx.Response(
        200,
        content=gzip.compress(b'{"status":"ok"}'),
        headers={"content-encoding": "gzip", "content-type": "application/json"},
    )
    response = await client.get("/companies", headers=auth_header)
    assert response.content == b'{"status":"ok"}'
    assert "content-encoding" not in response.headers


async def test_a_silent_app_service_is_a_bad_gateway(client, upstream, auth_header):
    upstream.app_unreachable = True
    response = await client.get("/companies", headers=auth_header)
    assert response.status_code == 502
    assert response.json()["detail"] == "app_service could not be reached"


async def test_a_redirect_is_handed_back_rather_than_followed(
    client, upstream, auth_header
):
    """The browser has to see the redirect: that is how the Google flow works."""
    upstream.app_handler = lambda request: httpx.Response(
        307, headers={"location": "https://accounts.google.com/o/oauth2/v2/auth"}
    )
    response = await client.get(
        "/auth/google/login", headers=auth_header, follow_redirects=False
    )
    assert response.status_code == 307
    assert response.headers["location"].startswith("https://accounts.google.com/")
