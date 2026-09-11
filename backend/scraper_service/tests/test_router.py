"""The HTTP surface, including the scopes it insists on."""

import pytest

from app import repository
from app.models import MODE_IDNO_LIST
from tests.conftest import SAMPLE_IDNO


async def test_health_needs_no_token(client):
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_checks_both_stores(client):
    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["database"] == "ok"


@pytest.mark.parametrize(
    "path",
    ["/scrape/jobs", "/companies", f"/companies/{SAMPLE_IDNO}"],
)
async def test_reading_without_a_token_is_refused(client, path):
    response = await client.get(path)

    assert response.status_code == 401


async def test_an_inactive_token_is_refused(client, auth_header, introspector):
    introspector.active = False

    response = await client.get("/scrape/jobs", headers=auth_header)

    assert response.status_code == 401


async def test_starting_a_scrape_needs_the_write_scope(
    client, auth_header, introspector
):
    introspector.scopes = ["scrape:read"]

    response = await client.post(
        "/scrape/companies", json={"idnos": [SAMPLE_IDNO]}, headers=auth_header
    )

    assert response.status_code == 403
    assert "scrape:write" in response.json()["detail"]


async def test_a_bad_idno_is_rejected_before_any_work(client, auth_header, browser):
    response = await client.post(
        "/scrape/companies", json={"idnos": ["not-an-idno"]}, headers=auth_header
    )

    assert response.status_code == 422
    assert browser.requested == []


async def test_starting_a_scrape_returns_a_job(client, auth_header, session):
    response = await client.post(
        "/scrape/companies",
        json={"idnos": [SAMPLE_IDNO], "include_depozitar": False},
        headers=auth_header,
    )

    assert response.status_code == 202
    body = response.json()
    assert body["mode"] == MODE_IDNO_LIST
    assert body["params"]["idnos"] == [SAMPLE_IDNO]
    assert body["params"]["include_depozitar"] is False


async def test_a_sweep_carries_its_paging_settings(client, auth_header):
    response = await client.post(
        "/scrape/sweep",
        json={"start_page": 2, "page_size": 10, "max_pages": 3},
        headers=auth_header,
    )

    assert response.status_code == 202
    assert response.json()["params"]["start_page"] == 2
    assert response.json()["params"]["max_pages"] == 3


async def test_an_unknown_job_is_a_404(client, auth_header):
    response = await client.get(
        "/scrape/jobs/11111111-2222-3333-4444-555555555555", headers=auth_header
    )

    assert response.status_code == 404


async def test_cancelling_a_finished_job_conflicts(client, auth_header, session):
    job = await repository.add_job(
        session, mode=MODE_IDNO_LIST, params={"idnos": []}, created_by=None
    )
    await repository.finish_job(session, job, "succeeded")
    await session.commit()

    response = await client.post(f"/scrape/jobs/{job.id}/cancel", headers=auth_header)

    assert response.status_code == 409


async def test_a_company_is_readable_once_it_has_been_scraped(
    client, auth_header, session, runner
):
    job = await repository.add_job(
        session,
        mode=MODE_IDNO_LIST,
        params={"idnos": [SAMPLE_IDNO], "include_depozitar": True},
        created_by=None,
    )
    await session.commit()
    await runner.run(job.id)

    response = await client.get(f"/companies/{SAMPLE_IDNO}", headers=auth_header)

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "LENSES GRUP"
    assert body["employees"] == 5
    assert len(body["people"]) == 3
    assert body["statements"]

    listing = await client.get(
        "/companies", params={"search": "LENSES"}, headers=auth_header
    )
    assert listing.json()["total"] == 1

    statement = await client.get(
        f"/companies/{SAMPLE_IDNO}/statements/2024", headers=auth_header
    )
    assert statement.status_code == 200
    assert statement.json()["source"] == "depozitar"
    assert statement.json()["line_items"]

    raw = await client.get(f"/companies/{SAMPLE_IDNO}/source-data", headers=auth_header)
    assert raw.status_code == 200
    assert {row["source"] for row in raw.json()} == {"openmoney", "depozitar"}
    # The raw listing never carries the bodies.
    assert "body" not in raw.json()[0]


async def test_an_unscraped_company_is_a_404(client, auth_header):
    response = await client.get("/companies/9999999999999", headers=auth_header)

    assert response.status_code == 404
