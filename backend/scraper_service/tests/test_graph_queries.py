"""Turning neo4j relationship rows back into the shape the frontend draws."""

import pytest

from app.graph_queries import EGO_GRAPH, RETURN_EDGES, _depth, assemble


def company(idno: str, *, name: str | None = None, scraped: bool = True) -> dict:
    return {"idno": idno, "name": name or f"COMPANY {idno}", "scraped": scraped}


def person(key: str, *, name: str | None = None) -> dict:
    return {"key": key, "name": name or f"PARTY {key}"}


def row(source: dict, target: dict, *, role: str = "FOUNDER", share=None) -> dict:
    """One `(party)-[:HOLDS]->(company)` row, as `record.data()` hands it over."""
    return {
        "source": source,
        "source_labels": ["Person" if "key" in source else "Company"],
        "target": target,
        "target_labels": ["Company"],
        "role": role,
        "share_percent": share,
    }


def by_id(nodes: list[dict]) -> dict[str, dict]:
    return {node["id"]: node for node in nodes}


def test_both_ends_of_a_relationship_become_nodes() -> None:
    nodes, links = assemble([row(person("alice"), company("1"))])

    assert by_id(nodes).keys() == {"p:alice", "c:1"}
    assert links == [
        {
            "source": "p:alice",
            "target": "c:1",
            "role": "FOUNDER",
            "share_percent": None,
        }
    ]


def test_one_party_on_two_companies_is_a_single_node() -> None:
    nodes, _ = assemble(
        [row(person("alice"), company("1")), row(person("alice"), company("2"))]
    )

    assert by_id(nodes).keys() == {"p:alice", "c:1", "c:2"}
    assert by_id(nodes)["p:alice"]["degree"] == 2


def test_a_scraped_company_and_one_we_only_heard_of_read_differently() -> None:
    nodes, _ = assemble([row(company("2", scraped=False), company("1"))])

    assert by_id(nodes)["c:1"]["kind"] == "company"
    assert by_id(nodes)["c:2"]["kind"] == "company_party"


def test_a_company_to_company_holding_is_one_edge() -> None:
    """This is the case the SQL query could only draw when both were scraped."""
    nodes, links = assemble([row(company("2"), company("1"))])

    assert by_id(nodes).keys() == {"c:1", "c:2"}
    assert links[0] == {
        "source": "c:2",
        "target": "c:1",
        "role": "FOUNDER",
        "share_percent": None,
    }


def test_the_same_edge_arriving_twice_is_drawn_once() -> None:
    """A walk of depth two reaches the same relationship down two paths."""
    edge = row(person("alice"), company("1"))
    nodes, links = assemble([edge, dict(edge)])

    assert len(links) == 1
    assert by_id(nodes)["p:alice"]["degree"] == 1


def test_two_roles_between_the_same_pair_stay_two_edges() -> None:
    nodes, links = assemble(
        [
            row(person("alice"), company("1"), role="FOUNDER"),
            row(person("alice"), company("1"), role="ADMINISTRATOR"),
        ]
    )

    assert len(links) == 2
    assert by_id(nodes)["p:alice"]["degree"] == 2


def test_a_self_loop_is_dropped() -> None:
    assert assemble([row(company("1"), company("1"))]) == ([], [])


def test_degree_counts_both_ends_of_every_link() -> None:
    nodes, _ = assemble(
        [row(person("alice"), company("1")), row(person("bob"), company("1"))]
    )
    counted = by_id(nodes)

    assert counted["c:1"]["degree"] == 2
    assert counted["p:alice"]["degree"] == 1


def test_the_share_survives_onto_the_link() -> None:
    _, links = assemble([row(person("alice"), company("1"), share=51.0)])
    assert links[0]["share_percent"] == pytest.approx(51.0)


def test_the_role_lands_on_the_party_node_too() -> None:
    nodes, _ = assemble([row(person("alice"), company("1"), role="ADMINISTRATOR")])
    assert by_id(nodes)["p:alice"]["role"] == "ADMINISTRATOR"


def test_a_node_falls_back_to_its_key_when_the_name_is_missing() -> None:
    nodes, _ = assemble([row({"key": "alice", "name": None}, company("1"))])
    assert by_id(nodes)["p:alice"]["label"] == "alice"


def test_nothing_in_gives_nothing_out() -> None:
    assert assemble([]) == ([], [])


# --- depth ------------------------------------------------------------------


@pytest.mark.parametrize("depth", [1, 2, 4])
def test_a_depth_inside_the_ceiling_is_accepted(depth: int) -> None:
    assert _depth(depth, 4) == depth


@pytest.mark.parametrize("depth", [0, -1, 5, 99])
def test_a_depth_outside_the_ceiling_is_refused(depth: int) -> None:
    with pytest.raises(ValueError, match="depth must be between"):
        _depth(depth, 4)


def test_depth_is_never_caller_text() -> None:
    """It is formatted into the query, so it has to be an int or nothing."""
    with pytest.raises((ValueError, TypeError)):
        _depth("2 or 1=1", 4)


def test_the_depth_reaches_the_query_it_is_formatted_into() -> None:
    query = EGO_GRAPH.format(depth=_depth(3, 4), returns=RETURN_EDGES)
    assert "[:HOLDS*1..3]" in query
    assert "$idno" in query
