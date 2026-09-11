"""The graph against a real neo4j.

Skipped unless TEST_NEO4J_URL points at one, the same bargain the rest of this
suite makes with Postgres.

WARNING: the fixture empties whatever database it is pointed at. Never aim it
at the stack's own neo4j on 7687, or the projected graph goes with it. Start a
throwaway on another port instead:

    docker run -d --name neo4j_test -p 17687:7687         -e NEO4J_AUTH=neo4j/test_password neo4j:5-community
    TEST_NEO4J_URL=bolt://localhost:17687 TEST_NEO4J_PASSWORD=test_password pytest
    docker rm -f neo4j_test
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal

import pytest
import pytest_asyncio

from app import graph_queries, graph_store
from app.models import PARTY_COMPANY, PARTY_PERSON, ROLE_ADMINISTRATOR, ROLE_FOUNDER

NEO4J_URL = os.environ.get("TEST_NEO4J_URL")

pytestmark = pytest.mark.skipif(
    not NEO4J_URL, reason="set TEST_NEO4J_URL to run the neo4j tests"
)

DATABASE = os.environ.get("TEST_NEO4J_DATABASE", "neo4j")

ALFA = "1000000000001"
BETA = "1000000000002"
GAMA = "1000000000003"
OFFSHORE = "1000000000099"


@dataclass
class Party:
    role: str
    party_type: str
    full_name: str
    source_key: str | None = None
    share_percent: Decimal | None = None


def person(name: str, key: str, role: str = ROLE_FOUNDER, share: str | None = None):
    return Party(
        role=role,
        party_type=PARTY_PERSON,
        full_name=name,
        source_key=key,
        share_percent=Decimal(share) if share else None,
    )


def holding(name: str, idno: str, share: str | None = None):
    return Party(
        role=ROLE_FOUNDER,
        party_type=PARTY_COMPANY,
        full_name=name,
        source_key=idno,
        share_percent=Decimal(share) if share else None,
    )


@pytest_asyncio.fixture(loop_scope="session")
async def driver():
    from neo4j import AsyncGraphDatabase

    client = AsyncGraphDatabase.driver(
        NEO4J_URL,
        auth=(
            os.environ.get("TEST_NEO4J_USER", "neo4j"),
            os.environ.get("TEST_NEO4J_PASSWORD", "test_password"),
        ),
    )
    async with client.session(database=DATABASE) as session:
        await session.run("match (n) detach delete n")
    await graph_store.ensure_constraints(client, DATABASE)
    try:
        yield client
    finally:
        async with client.session(database=DATABASE) as session:
            await session.run("match (n) detach delete n")
        await client.close()


@pytest_asyncio.fixture(loop_scope="session")
async def chain(driver):
    """Alice founds ALFA and BETA. BETA owns GAMA. So ALFA reaches GAMA."""
    await graph_store.project_company(
        driver, DATABASE, idno=ALFA, name="ALFA SRL", people=[person("ALICE", "alice")]
    )
    await graph_store.project_company(
        driver,
        DATABASE,
        idno=BETA,
        name="BETA SRL",
        people=[
            person("ALICE", "alice", share="60"),
            person("BOB", "bob", role=ROLE_ADMINISTRATOR),
        ],
    )
    await graph_store.project_company(
        driver,
        DATABASE,
        idno=GAMA,
        name="GAMA SRL",
        people=[holding("BETA SRL", BETA, share="100")],
    )
    return driver


async def walk(driver, idno: str, depth: int):
    nodes, links, truncated = await graph_queries.ego_graph(
        driver, DATABASE, idno=idno, depth=depth, limit=200, max_depth=4
    )
    return {node["id"] for node in nodes}, links, truncated


async def test_one_hop_shows_only_the_company_s_own_parties(chain):
    ids, links, _ = await walk(chain, ALFA, 1)
    assert ids == {f"c:{ALFA}", "p:alice"}
    assert len(links) == 1


async def test_two_hops_reach_the_company_a_shared_founder_also_sits_on(chain):
    """This is the hop the SQL query stopped at."""
    ids, _, _ = await walk(chain, ALFA, 2)
    assert ids == {f"c:{ALFA}", "p:alice", f"c:{BETA}"}


async def test_three_hops_reach_what_that_company_owns(chain):
    """And this is the hop SQL could not do without recursion."""
    ids, _, _ = await walk(chain, ALFA, 3)
    assert ids == {f"c:{ALFA}", "p:alice", f"c:{BETA}", f"c:{GAMA}", "p:bob"}


async def test_projecting_the_same_company_twice_changes_nothing(chain):
    before = await walk(chain, ALFA, 3)
    await graph_store.project_company(
        chain, DATABASE, idno=BETA, name="BETA SRL", people=[person("ALICE", "alice")]
    )
    ids, links, _ = await walk(chain, ALFA, 3)
    # Bob is gone because the re-projection dropped him, and nothing doubled.
    assert ids == before[0] - {"p:bob"}
    assert len(links) == len(before[1]) - 1


async def test_the_shortest_chain_between_two_companies_is_found(chain):
    nodes, links = await graph_queries.shortest_path(
        chain, DATABASE, idno=ALFA, other=GAMA, limit=200, max_depth=4
    )
    assert {node["id"] for node in nodes} == {
        f"c:{ALFA}",
        "p:alice",
        f"c:{BETA}",
        f"c:{GAMA}",
    }
    assert len(links) == 3


async def test_two_companies_with_nothing_between_them_return_nothing(chain):
    await graph_store.project_company(
        chain, DATABASE, idno=OFFSHORE, name="OFFSHORE LTD", people=[]
    )
    nodes, links = await graph_queries.shortest_path(
        chain, DATABASE, idno=ALFA, other=OFFSHORE, limit=200, max_depth=4
    )
    assert (nodes, links) == ([], [])


async def test_a_company_we_never_scraped_is_missing_entirely(chain):
    ids, links, _ = await walk(chain, "9999999999999", 2)
    assert (ids, links) == (set(), [])


async def test_a_holder_we_have_not_scraped_reads_as_a_party(driver):
    await graph_store.project_company(
        driver,
        DATABASE,
        idno=ALFA,
        name="ALFA SRL",
        people=[holding("OFFSHORE LTD", OFFSHORE, share="100")],
    )
    graph_nodes, _, _ = await graph_queries.ego_graph(
        driver, DATABASE, idno=ALFA, depth=1, limit=50, max_depth=4
    )
    kinds = {node["id"]: node["kind"] for node in graph_nodes}
    assert kinds[f"c:{OFFSHORE}"] == "company_party"
    assert kinds[f"c:{ALFA}"] == "company"


async def test_that_holder_becomes_a_company_once_we_scrape_it(driver):
    await graph_store.project_company(
        driver,
        DATABASE,
        idno=ALFA,
        name="ALFA SRL",
        people=[holding("OFFSHORE LTD", OFFSHORE)],
    )
    await graph_store.project_company(
        driver, DATABASE, idno=OFFSHORE, name="OFFSHORE LTD", people=[]
    )
    nodes, _, _ = await graph_queries.ego_graph(
        driver, DATABASE, idno=ALFA, depth=1, limit=50, max_depth=4
    )
    kinds = {node["id"]: node["kind"] for node in nodes}
    assert kinds[f"c:{OFFSHORE}"] == "company"


async def test_the_share_survives_the_round_trip(chain):
    _, links, _ = await walk(chain, GAMA, 1)
    assert links[0]["share_percent"] == pytest.approx(100.0)


async def test_a_limit_smaller_than_the_graph_says_so(chain):
    _, links, truncated = await graph_queries.ego_graph(
        chain, DATABASE, idno=ALFA, depth=3, limit=1, max_depth=4
    )
    assert truncated is True
    assert len(links) == 1
