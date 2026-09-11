"""Turning flat company-to-party rows into a graph."""

from decimal import Decimal

from app.models import PARTY_COMPANY, PARTY_PERSON, ROLE_ADMINISTRATOR, ROLE_FOUNDER
from app.repository import GraphEdge
from app.service import build_graph


def edge(
    idno: str,
    party_key: str,
    *,
    company_name: str | None = None,
    party_name: str | None = None,
    party_type: str = PARTY_PERSON,
    role: str = ROLE_FOUNDER,
    share: str | None = None,
) -> GraphEdge:
    return GraphEdge(
        idno=idno,
        company_name=company_name or f"COMPANY {idno}",
        party_key=party_key,
        party_name=party_name or f"PARTY {party_key}",
        party_type=party_type,
        role=role,
        share_percent=Decimal(share) if share is not None else None,
    )


def by_id(nodes: list[dict]) -> dict[str, dict]:
    return {node["id"]: node for node in nodes}


def test_each_company_becomes_one_node() -> None:
    nodes, links = build_graph([edge("1", "alice"), edge("1", "bob")])

    assert by_id(nodes).keys() == {"c:1", "p:alice", "p:bob"}
    assert len(links) == 2


def test_one_party_on_two_companies_is_a_single_node() -> None:
    """The dedup that makes the picture a network instead of separate stars."""
    nodes, links = build_graph([edge("1", "alice"), edge("2", "alice")])

    assert len([node for node in nodes if node["kind"] == "person"]) == 1
    assert {link["source"] for link in links} == {"c:1", "c:2"}
    assert {link["target"] for link in links} == {"p:alice"}


def test_the_same_name_under_different_keys_stays_two_parties() -> None:
    """Two different people the source happens to have named alike."""
    nodes, _ = build_graph(
        [
            edge("1", "key-a", party_name="ION POPESCU"),
            edge("2", "key-b", party_name="ION POPESCU"),
        ]
    )

    assert len([node for node in nodes if node["kind"] == "person"]) == 2


def test_a_company_party_we_already_hold_resolves_to_its_own_node() -> None:
    """Ownership between two scraped companies draws as one edge, not two blobs."""
    nodes, links = build_graph(
        [
            edge("1", "alice"),
            edge("2", "1", party_type=PARTY_COMPANY, party_name="COMPANY 1"),
        ]
    )

    assert by_id(nodes).keys() == {"c:1", "p:alice", "c:2"}
    assert {"source": "c:2", "target": "c:1"} in [
        {"source": link["source"], "target": link["target"]} for link in links
    ]


def test_a_company_party_we_do_not_hold_becomes_its_own_kind() -> None:
    nodes, _ = build_graph(
        [edge("1", "999", party_type=PARTY_COMPANY, party_name="CONSILIUL")]
    )

    node = by_id(nodes)["p:999"]
    assert node["kind"] == "company_party"
    assert node["idno"] == "999"


def test_a_company_listed_as_its_own_founder_draws_no_loop() -> None:
    nodes, links = build_graph(
        [
            edge("1", "alice"),
            edge("1", "1", party_type=PARTY_COMPANY, party_name="COMPANY 1"),
        ]
    )

    assert links == [
        {
            "source": "c:1",
            "target": "p:alice",
            "role": ROLE_FOUNDER,
            "share_percent": None,
        }
    ]
    assert by_id(nodes)["c:1"]["degree"] == 1


def test_degree_counts_both_ends_of_every_link() -> None:
    nodes, _ = build_graph(
        [
            edge("1", "alice"),
            edge("1", "bob", role=ROLE_ADMINISTRATOR),
            edge("2", "alice"),
        ]
    )

    degrees = {node["id"]: node["degree"] for node in nodes}
    assert degrees == {"c:1": 2, "c:2": 1, "p:alice": 2, "p:bob": 1}


def test_the_role_and_share_survive_onto_the_link() -> None:
    _, links = build_graph(
        [edge("1", "alice", role=ROLE_ADMINISTRATOR, share="51.5")]
    )

    assert links[0]["role"] == ROLE_ADMINISTRATOR
    assert links[0]["share_percent"] == Decimal("51.5")


def test_nothing_in_gives_nothing_out() -> None:
    assert build_graph([]) == ([], [])
