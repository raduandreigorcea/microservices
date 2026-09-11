"""The job runner, driven by a browser that answers from fixtures."""

from sqlalchemy import func, select

from app import repository
from app.models import (
    MODE_IDNO_LIST,
    MODE_SWEEP,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    Company,
    CompanyPerson,
    FinancialLineItem,
    FinancialStatement,
    SourceData,
)
from app.service import JobCancelled
from tests.conftest import SAMPLE_IDNO


async def _job(session, mode, params):
    job = await repository.add_job(
        session, mode=mode, params=params, created_by="tester"
    )
    await session.commit()
    return job


async def test_an_idno_job_walks_the_whole_pipeline(session, runner, browser):
    job = await _job(
        session,
        MODE_IDNO_LIST,
        {"idnos": [SAMPLE_IDNO], "include_depozitar": True},
    )

    await runner.run(job.id)
    await session.refresh(job)

    assert job.status == STATUS_SUCCEEDED
    assert job.companies_done == 1
    assert job.requests_failed == 0
    assert job.cursor == {"done": 1}

    # Extract went to both sources, through rendered pages and their APIs.
    assert any("openmoney.md/companies/" in url for url in browser.rendered)
    assert any("fin-data-v1" in url for url in browser.requested)
    assert any("fs/economic-agent" in url for url in browser.requested)

    # Load kept every response.
    assert await session.scalar(select(func.count()).select_from(SourceData)) > 0

    # Transform produced the company and everything hanging off it.
    company = await repository.get_company(session, SAMPLE_IDNO)
    assert company is not None
    assert company.name == "LENSES GRUP"
    assert company.caem_code == "G4646"
    assert sorted(company.sources) == ["depozitar", "openmoney"]
    assert len(company.people) == 3
    assert {statement.source for statement in company.statements} == {
        "openmoney",
        "depozitar",
    }
    assert await session.scalar(select(func.count()).select_from(FinancialLineItem)) > 0


async def test_skipping_the_depositary_leaves_only_registry_data(
    session, runner, browser
):
    job = await _job(
        session,
        MODE_IDNO_LIST,
        {"idnos": [SAMPLE_IDNO], "include_depozitar": False},
    )

    await runner.run(job.id)

    assert not any("statistica.md" in url for url in browser.requested)
    company = await repository.get_company(session, SAMPLE_IDNO)
    assert company.sources == ["openmoney"]
    assert {statement.source for statement in company.statements} == {"openmoney"}


async def test_running_the_same_idno_twice_does_not_duplicate_rows(session, runner):
    for _ in range(2):
        job = await _job(
            session,
            MODE_IDNO_LIST,
            {"idnos": [SAMPLE_IDNO], "include_depozitar": True},
        )
        await runner.run(job.id)

    assert await session.scalar(select(func.count()).select_from(Company)) == 1
    people = await session.scalar(select(func.count()).select_from(CompanyPerson))
    assert people == 3
    statements = await session.scalar(
        select(func.count()).select_from(FinancialStatement)
    )
    # Three years from the registry, six filed at the depositary.
    assert statements == 9


async def test_a_sweep_follows_the_listing_and_records_its_place(
    session, runner, browser
):
    job = await _job(
        session,
        MODE_SWEEP,
        {
            "start_page": 0,
            "page_size": 5,
            "max_pages": 1,
            "include_depozitar": False,
        },
    )

    await runner.run(job.id)
    await session.refresh(job)

    assert job.status == STATUS_SUCCEEDED
    # The fixture page holds five companies.
    assert job.companies_done == 5
    assert job.companies_total == 575518
    assert job.cursor == {"page": 1, "index": 0, "pages_done": 1}
    assert any("page=0" in url for url in browser.requested)
    assert await session.scalar(select(func.count()).select_from(Company)) == 5


async def test_a_sweep_resumes_from_its_cursor(session, runner):
    job = await _job(
        session,
        MODE_SWEEP,
        {"page_size": 5, "max_pages": 1, "include_depozitar": False},
    )
    job.cursor = {"page": 0, "index": 3, "pages_done": 0}
    await session.commit()

    await runner.run(job.id)
    await session.refresh(job)

    # Three companies were already done, so only two are left on that page.
    assert job.companies_done == 2


async def test_cancelling_stops_the_run(session, runner):
    job = await _job(
        session,
        MODE_IDNO_LIST,
        {"idnos": [SAMPLE_IDNO], "include_depozitar": False},
    )
    runner._cancelled.add(job.id)

    await runner.run(job.id)
    await session.refresh(job)

    assert job.status == "cancelled"
    assert job.companies_done == 0


async def test_a_source_that_errors_is_counted_not_fatal(session, runner, browser):
    browser.status_overrides = {"fs/economic-agent": 403}
    job = await _job(
        session,
        MODE_IDNO_LIST,
        {"idnos": [SAMPLE_IDNO], "include_depozitar": True},
    )

    await runner.run(job.id)
    await session.refresh(job)

    assert job.status == STATUS_SUCCEEDED
    assert job.requests_failed == 1
    # The registry half still landed.
    company = await repository.get_company(session, SAMPLE_IDNO)
    assert company.sources == ["openmoney"]


async def test_a_crash_marks_the_job_failed(session, runner, browser, monkeypatch):
    async def explode(*args, **kwargs):
        raise RuntimeError("chromium fell over")

    monkeypatch.setattr(runner.openmoney, "fetch_company", explode)
    job = await _job(
        session, MODE_IDNO_LIST, {"idnos": [SAMPLE_IDNO], "include_depozitar": False}
    )

    await runner.run(job.id)
    await session.refresh(job)

    assert job.status == STATUS_FAILED
    assert "chromium fell over" in job.last_error


def test_job_cancelled_is_its_own_error():
    assert issubclass(JobCancelled, Exception)
