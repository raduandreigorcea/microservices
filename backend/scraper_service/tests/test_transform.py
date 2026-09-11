"""The Transform step, against bodies captured from the live sources."""

import json
from decimal import Decimal

import pytest

from app.models import PARTY_PERSON, ROLE_ADMINISTRATOR, ROLE_FOUNDER
from app.transform import (
    build_company,
    company_from_depozitar,
    company_from_openmoney,
    loads,
    parse_number,
    people_from_openmoney,
    statement_from_depozitar,
    statements_from_openmoney,
)
from tests.conftest import SAMPLE_IDNO, fixture_text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("505 397", Decimal(505397)),
        # The depositary separates thousands with a non-breaking space.
        ("505 397", Decimal(505397)),
        ("1 234,56", Decimal("1234.56")),
        ("(87)", Decimal(-87)),
        (15923, Decimal(15923)),
        ("", None),
        ("   ", None),
        (None, None),
        ("not a number", None),
        (True, None),
    ],
)
def test_parse_number_handles_what_the_sources_send(raw, expected):
    assert parse_number(raw) == expected


def test_company_from_openmoney_reads_the_profile():
    fields = company_from_openmoney(loads(fixture_text("openmoney_company.json")))

    assert fields["name"] == "LENSES GRUP"
    assert fields["legal_form"] == "SRL"
    assert fields["is_active"] is True
    assert str(fields["registered_at"]) == "2003-07-09"
    assert fields["city"] == "Chișinău"
    assert fields["street"] == "Fierarilor 2"
    assert fields["cuatm_name"] == "SEC.CENTRU"
    assert len(fields["licensed_activities"]) >= 1


def test_people_carry_their_role_and_share():
    people = people_from_openmoney(loads(fixture_text("openmoney_company.json")))

    founders = [person for person in people if person.role == ROLE_FOUNDER]
    admins = [person for person in people if person.role == ROLE_ADMINISTRATOR]

    assert {person.full_name for person in founders} == {
        "PERETEATCO ALEXANDR",
        "PERETEATCO ALINA",
    }
    assert all(person.share_percent == Decimal(50) for person in founders)
    assert [person.full_name for person in admins] == ["PERETEATCO ALINA"]
    assert all(person.party_type == PARTY_PERSON for person in people)


def test_openmoney_financials_become_one_statement_per_year():
    statements = statements_from_openmoney(
        loads(fixture_text("openmoney_fin_data.json"))
    )

    assert [statement.year for statement in statements] == [2020, 2021, 2022]
    assert all(statement.source == "openmoney" for statement in statements)

    latest = statements[-1]
    assert latest.line_items, "the flat CODE keys should produce line items"
    assert all(item.group_code in {"5", "6", "7", "8"} for item in latest.line_items)

    by_code = {(item.group_code, item.field_code): item for item in latest.line_items}
    fixed_assets = by_code[("5", "130")]
    assert fixed_assets.value_current == Decimal(509710)
    assert "Total imobilizari corporale" in fixed_assets.label
    # The registry only ever gives one column.
    assert fixed_assets.value_previous is None


def test_depozitar_declaration_becomes_a_statement_with_both_columns():
    statement = statement_from_depozitar(
        loads(fixture_text("depozitar_declaration.json"))
    )

    assert statement.year == 2024
    assert statement.source == "depozitar"
    assert statement.source_ref == "81167164-d369-4d45-9edd-8e70c4105a11"
    assert statement.is_audited is True
    assert statement.is_signed is False
    assert str(statement.period_from) == "2024-01-01"
    assert str(statement.period_to) == "2024-12-31"

    by_code = {
        (item.group_code, item.field_code): item for item in statement.line_items
    }
    revenue = by_code[("6", "010")]
    assert revenue.value_current == Decimal(22914391)
    assert revenue.value_previous == Decimal(21070712)
    assert "Venituri din vînzări" in revenue.label


def test_empty_declaration_rows_are_dropped():
    statement = statement_from_depozitar(
        loads(fixture_text("depozitar_declaration.json"))
    )
    assert all(
        any(
            value is not None
            for value in (
                item.value_current,
                item.value_previous,
                item.value_extra_1,
                item.value_extra_2,
            )
        )
        for item in statement.line_items
    )


def test_company_from_depozitar_reads_the_legal_entity():
    fields = company_from_depozitar(loads(fixture_text("depozitar_declaration.json")))

    assert fields["caem_code"] == "G4646"
    assert fields["cuiio"] == "40067824"
    assert fields["employees"] == 5
    assert fields["in_liquidation"] is False
    assert fields["ownership_name"] == "Proprietate privată"


def test_build_company_merges_both_sources():
    record = build_company(
        SAMPLE_IDNO,
        openmoney_company=fixture_text("openmoney_company.json"),
        openmoney_fin_data=fixture_text("openmoney_fin_data.json"),
        depozitar_declarations=[fixture_text("depozitar_declaration.json")],
    )

    assert record.sources == ["openmoney", "depozitar"]
    # The registry wins the fields both sources carry.
    assert record.fields["name"] == "LENSES GRUP"
    assert record.fields["legal_form"] == "SRL"
    # The depositary contributes the rest.
    assert record.fields["caem_code"] == "G4646"
    assert record.fields["email"] == "jeneamor@mail.ru"

    years = {(statement.year, statement.source) for statement in record.statements}
    assert (2024, "depozitar") in years
    assert (2022, "openmoney") in years


def test_a_second_filing_for_a_year_replaces_the_first():
    original = fixture_text("depozitar_declaration.json")
    corrected = json.dumps(
        {**json.loads(original), "id": "second-filing", "status": "corrected"}
    )

    record = build_company(SAMPLE_IDNO, depozitar_declarations=[original, corrected])

    assert len(record.statements) == 1
    assert record.statements[0].status == "corrected"
    assert record.statements[0].source_ref == "second-filing"


def test_build_company_survives_missing_and_broken_bodies():
    record = build_company(
        SAMPLE_IDNO,
        openmoney_company="not json at all",
        openmoney_fin_data=None,
        depozitar_declarations=["{}", ""],
    )

    assert record.sources == []
    assert record.fields == {}
    assert record.statements == []
