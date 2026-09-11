"""The relationship graph, projected into neo4j.

Postgres stays the source of truth: `transformed_data` and `company_people`
are written first, and this runs afterwards. Everything here is idempotent and
can be rebuilt from Postgres alone, which is what `reproject` does.

The model is two labels and one relationship:

    (:Person {key, name})   -[:HOLDS {role, share_percent}]-> (:Company)
    (:Company {idno, name}) -[:HOLDS {role, share_percent}]-> (:Company)

A shareholder that is itself a company becomes a `:Company` node keyed by its
IDNO whether or not we have scraped it yet, so ownership between two companies
is one edge rather than two unconnected blobs. `scraped` says which is which.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from neo4j import AsyncDriver
from neo4j.exceptions import Neo4jError, ServiceUnavailable

log = logging.getLogger("scraper")

COMPANY_PARTY = "COMPANY"

# Run on boot. Both are also the index the lookups rely on.
CONSTRAINTS = (
    "create constraint company_idno if not exists for (c:Company) require c.idno is unique",
    "create constraint person_key if not exists for (p:Person) require p.key is unique",
)

# Upsert the company and drop the relationships it previously filed, so a
# re-scrape replaces rather than accumulates.
UPSERT_COMPANY = """
merge (c:Company {idno: $idno})
set c.name = coalesce($name, c.name), c.scraped = true
with c
optional match (c)<-[held:HOLDS]-()
delete held
"""

# Two passes, because the label a party gets depends on what it is and cypher
# cannot pick a label at runtime without apoc.
ATTACH_COMPANY_PARTIES = """
match (c:Company {idno: $idno})
unwind $parties as party
merge (p:Company {idno: party.key})
on create set p.name = party.name, p.scraped = false
merge (p)-[r:HOLDS {role: party.role}]->(c)
set r.share_percent = party.share_percent
"""

ATTACH_PERSON_PARTIES = """
match (c:Company {idno: $idno})
unwind $parties as party
merge (p:Person {key: party.key})
set p.name = party.name
merge (p)-[r:HOLDS {role: party.role}]->(c)
set r.share_percent = party.share_percent
"""


class GraphUnavailable(Exception):
    """Neo4j did not answer. Callers fall back to Postgres."""


def _share(value: Decimal | float | None) -> float | None:
    """Neo4j has no decimal type, and a percentage does not need one."""
    return None if value is None else float(value)


def parties_of(people: Iterable[Any], idno: str) -> tuple[list[dict], list[dict]]:
    """Splits a company's filed parties into company ones and person ones.

    The key is the source's own identifier where there is one. Names alone
    would merge two different people who happen to share one, and split one
    person the source spelled twice.
    """
    companies: list[dict] = []
    persons: list[dict] = []
    for person in people:
        key = person.source_key or person.full_name
        if not key:
            continue
        party = {
            "key": key,
            "name": person.full_name,
            "role": person.role,
            "share_percent": _share(person.share_percent),
        }
        if person.party_type == COMPANY_PARTY:
            # A company listed as its own founder draws nothing.
            if key == idno:
                continue
            companies.append(party)
        else:
            persons.append(party)
    return companies, persons


async def ensure_constraints(driver: AsyncDriver, database: str) -> None:
    async with driver.session(database=database) as session:
        for statement in CONSTRAINTS:
            await session.run(statement)


async def project_company(
    driver: AsyncDriver, database: str, *, idno: str, name: str | None, people
) -> None:
    """Writes one company's neighbourhood. Raises GraphUnavailable, never worse."""
    companies, persons = parties_of(people, idno)
    try:
        async with (
            driver.session(database=database) as session,
            await session.begin_transaction() as tx,
        ):
            await tx.run(UPSERT_COMPANY, idno=idno, name=name)
            if companies:
                await tx.run(ATTACH_COMPANY_PARTIES, idno=idno, parties=companies)
            if persons:
                await tx.run(ATTACH_PERSON_PARTIES, idno=idno, parties=persons)
            await tx.commit()
    except (Neo4jError, ServiceUnavailable, OSError) as exc:
        raise GraphUnavailable(str(exc)) from exc


async def project_quietly(driver: AsyncDriver | None, database: str, **kwargs) -> bool:
    """Projection must never fail a scrape job. Postgres already has the row."""
    if driver is None:
        return False
    try:
        await project_company(driver, database, **kwargs)
        return True
    except GraphUnavailable as exc:
        log.warning("  graph projection skipped for %s: %s", kwargs.get("idno"), exc)
        return False
