"""What app_service does to a request on the way through, and to the answer on
the way back. The short version: as little as possible."""

import gzip

import httpx
import pytest

from tests.conftest import IDENTITY


async def test_the_method_and_body_survive_the_hop(client):
    response = await client.post(
        "/scrape/companies", json={"idnos": ["1003600000000"]}
    )
    echoed = response.json()
    assert echoed["method"] == "POST"
    assert echoed["body"] == '{"idnos":["1003600000000"]}'


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
async def test_every_method_the_api_uses_is_dispatched(client, upstream, method):
    response = await client.request(method, "/companies/1003")
    assert response.status_code == 200
    assert upstream.dispatched[0].method == method


async def test_the_path_and_query_survive_the_hop(client):
    response = await client.get("/companies", params={"q": "acme", "page": 2})
    echoed = response.json()
    assert echoed["path"] == "/companies"
    assert echoed["query"] == "q=acme&page=2"


async def test_the_caller_the_gateway_identified_is_passed_along(client, upstream):
    """app_service does not decide who is calling, but it must not lose it."""
    await client.get("/companies", headers=IDENTITY)
    forwarded = upstream.dispatched[0].headers
    for name, value in IDENTITY.items():
        assert forwarded[name] == value


async def test_the_bearer_token_is_passed_along(client, upstream):
    """user_service still checks it for the routes that take one."""
    await client.get("/users/me", headers={"Authorization": "Bearer good-token"})
    assert upstream.dispatched[0].headers["authorization"] == "Bearer good-token"


async def test_hop_by_hop_headers_are_not_carried_to_the_next_hop(client, upstream):
    await client.get(
        "/companies",
        headers={"connection": "close", "te": "trailers", "upgrade": "websocket"},
    )
    forwarded = upstream.dispatched[0].headers
    assert "te" not in forwarded
    assert "upgrade" not in forwarded
    # The outgoing client sets its own connection header; ours did not survive.
    assert forwarded["connection"] != "close"
    # The host belongs to the hop, so it names the service being called.
    assert forwarded["host"] == "scraper-service"


async def test_the_upstream_status_is_handed_back_untouched(client, upstream):
    upstream.handler = lambda request: httpx.Response(
        409, json={"detail": "that job is already running"}
    )
    response = await client.post("/scrape/companies")
    assert response.status_code == 409
    assert response.json() == {"detail": "that job is already running"}


async def test_a_custom_upstream_header_is_handed_back(client, upstream):
    upstream.handler = lambda request: httpx.Response(
        202, json={}, headers={"location": "/scrape/jobs/42"}
    )
    response = await client.post("/scrape/companies")
    assert response.status_code == 202
    assert response.headers["location"] == "/scrape/jobs/42"


async def test_a_compressed_answer_is_decoded_and_its_encoding_dropped(
    client, upstream
):
    """httpx hands us a decoded body, so keeping content-encoding would lie."""
    upstream.handler = lambda request: httpx.Response(
        200,
        content=gzip.compress(b'{"status":"ok"}'),
        headers={"content-encoding": "gzip", "content-type": "application/json"},
    )
    response = await client.get("/companies")
    assert response.content == b'{"status":"ok"}'
    assert "content-encoding" not in response.headers


async def test_a_redirect_is_handed_back_rather_than_followed(client, upstream):
    """The browser has to see it: that is how the Google flow starts."""
    upstream.handler = lambda request: httpx.Response(
        307, headers={"location": "https://accounts.google.com/o/oauth2/v2/auth"}
    )
    response = await client.get("/auth/google/login", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].startswith("https://accounts.google.com/")


@pytest.mark.parametrize(
    ("path", "service"),
    [("/users/me", "user_service"), ("/companies", "scraper_service")],
)
async def test_a_silent_service_is_a_bad_gateway_that_names_it(
    client, upstream, path, service
):
    upstream.unreachable = {"user-service", "scraper-service"}
    response = await client.get(path)
    assert response.status_code == 502
    assert response.json()["detail"] == f"{service} could not be reached"
