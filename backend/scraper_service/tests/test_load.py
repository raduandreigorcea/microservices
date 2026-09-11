"""The Load step. Raw in, history of real changes out."""

from sqlalchemy import func, select

from app.models import SourceData
from app.repository import store, store_all
from app.sources import RESOURCE_COMPANY, Extracted, Fetched
from tests.conftest import SAMPLE_IDNO, fixture_text


def _item(body: str, key: str = SAMPLE_IDNO) -> Extracted:
    return Extracted(
        source="openmoney",
        resource=RESOURCE_COMPANY,
        resource_key=key,
        idno=key,
        fetched=Fetched(
            url=f"https://api.openmoney.md/api/companies/{key}",
            status=200,
            content_type="application/json",
            body=body,
        ),
    )


async def _count(session) -> int:
    return await session.scalar(select(func.count()).select_from(SourceData))


async def test_a_body_is_stored_untouched(session):
    body = fixture_text("openmoney_company.json")

    assert await store(session, _item(body)) is True

    stored = await session.scalar(select(SourceData))
    assert stored.body == body
    assert stored.http_status == 200
    assert stored.idno == SAMPLE_IDNO


async def test_an_unchanged_body_is_not_stored_twice(session):
    body = fixture_text("openmoney_company.json")

    assert await store(session, _item(body)) is True
    assert await store(session, _item(body)) is False
    assert await _count(session) == 1

    row = await session.scalar(select(SourceData))
    assert row.last_seen_at >= row.first_seen_at


async def test_a_changed_body_is_kept_alongside_the_old_one(session):
    assert await store(session, _item('{"a": 1}')) is True
    assert await store(session, _item('{"a": 2}')) is True

    assert await _count(session) == 2


async def test_failures_are_recorded_rather_than_dropped(session):
    missing = Extracted(
        source="openmoney",
        resource=RESOURCE_COMPANY,
        resource_key="9999999999999",
        idno="9999999999999",
        fetched=Fetched(
            url="https://api.openmoney.md/api/companies/9999999999999",
            status=404,
            content_type="application/json",
            body='{"statusCode":404,"message":"Not Found"}',
        ),
    )

    result = await store_all(session, [missing])

    assert result.stored == 1
    row = await session.scalar(select(SourceData))
    assert row.http_status == 404
