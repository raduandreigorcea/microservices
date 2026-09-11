"""Which service owns which path. This is the whole job of app_service."""

import pytest
from app.main import route_for

USER = "user-service"
SCRAPER = "scraper-service"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/auth", USER),
        ("/auth/google/login", USER),
        ("/auth/introspect", USER),
        ("/users", USER),
        ("/users/me", USER),
        ("/users/me/sessions", USER),
        ("/scrape", SCRAPER),
        ("/scrape/companies", SCRAPER),
        ("/scrape/jobs/42", SCRAPER),
        ("/companies", SCRAPER),
        ("/companies/1003600000000", SCRAPER),
        ("/companies/1003600000000/statements/2023", SCRAPER),
    ],
)
async def test_a_path_reaches_the_service_that_owns_it(
    client, upstream, path, expected
):
    response = await client.get(path)
    assert response.status_code == 200
    assert upstream.called() == expected


@pytest.mark.parametrize(
    "path",
    ["/", "/nope", "/authorize", "/userspace", "/scrapers", "/companiesx", "/metrics"],
)
async def test_a_path_nobody_owns_is_a_404(client, upstream, path):
    response = await client.get(path)
    assert response.status_code == 404
    assert response.json()["detail"] == f"no service handles {path}"
    assert upstream.dispatched == []


def test_the_routing_table_matches_whole_segments_only():
    """A pure check on the rule, without the HTTP round trip."""
    assert route_for("/auth") == "user_service"
    assert route_for("/auth/google") == "user_service"
    assert route_for("/authorize") is None
    assert route_for("/companies") == "scraper_service"
    assert route_for("/companiesx") is None
