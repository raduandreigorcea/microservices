"""Splitting a company's filed parties into what neo4j gets written."""

from decimal import Decimal

import pytest

from app.graph_store import parties_of
from app.models import PARTY_COMPANY, PARTY_PERSON, ROLE_ADMINISTRATOR, ROLE_FOUNDER
from app.transform import PersonRecord


def person(
    name: str,
    *,
    key: str | None = None,
    party_type: str = PARTY_PERSON,
    role: str = ROLE_FOUNDER,
    share: str | None = None,
) -> PersonRecord:
    return PersonRecord(
        role=role,
        party_type=party_type,
        full_name=name,
        source_key=key,
        share_percent=Decimal(share) if share is not None else None,
    )


def test_people_and_companies_are_written_by_different_passes() -> None:
    companies, persons = parties_of(
        [
            person("ALICE"),
            person("HOLDING SRL", key="1003600000001", party_type=PARTY_COMPANY),
        ],
        "1003600000000",
    )

    assert [p["key"] for p in companies] == ["1003600000001"]
    assert [p["key"] for p in persons] == ["ALICE"]


def test_the_source_key_is_preferred_over_the_name() -> None:
    companies, persons = parties_of([person("ALICE", key="hash-abc")], "1")
    assert persons[0]["key"] == "hash-abc"
    assert persons[0]["name"] == "ALICE"
    assert companies == []


def test_a_party_with_neither_key_nor_name_is_dropped() -> None:
    companies, persons = parties_of([person("")], "1")
    assert (companies, persons) == ([], [])


def test_a_company_listed_as_its_own_founder_is_dropped() -> None:
    """It would draw a self-loop, which says nothing."""
    companies, persons = parties_of(
        [person("SELF SRL", key="1003", party_type=PARTY_COMPANY)], "1003"
    )
    assert (companies, persons) == ([], [])


def test_a_person_sharing_the_company_idno_is_kept() -> None:
    """The self-loop rule is about companies, not a coincidence of keys."""
    _, persons = parties_of([person("ODD", key="1003")], "1003")
    assert [p["key"] for p in persons] == ["1003"]


def test_the_share_becomes_a_float_because_neo4j_has_no_decimal() -> None:
    _, persons = parties_of([person("ALICE", share="33.3300")], "1")
    assert persons[0]["share_percent"] == pytest.approx(33.33)
    assert isinstance(persons[0]["share_percent"], float)


def test_a_missing_share_stays_missing() -> None:
    _, persons = parties_of([person("ALICE")], "1")
    assert persons[0]["share_percent"] is None


def test_the_role_rides_along_because_it_keys_the_relationship() -> None:
    """One person can be both founder and administrator of one company."""
    _, persons = parties_of(
        [
            person("ALICE", key="a", role=ROLE_FOUNDER),
            person("ALICE", key="a", role=ROLE_ADMINISTRATOR),
        ],
        "1",
    )
    assert [p["role"] for p in persons] == [ROLE_FOUNDER, ROLE_ADMINISTRATOR]


def test_nothing_in_gives_nothing_out() -> None:
    assert parties_of([], "1") == ([], [])
