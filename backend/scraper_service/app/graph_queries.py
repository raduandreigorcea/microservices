"""Reading the graph back out of neo4j.

The answers come out in the same shape the SQL path produces, so the frontend
renders either one without knowing which it got. What cypher buys us is the
part SQL could not do cheaply: walking out more than one hop, and finding the
chain between two companies.
"""

from __future__ import annotations

from typing import Any

from neo4j import AsyncDriver
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from app.graph_store import GraphUnavailable

# The relationship rows every query returns, so one assembler handles them all.
RETURN_EDGES = """
return
  startNode(r) as source,
  labels(startNode(r)) as source_labels,
  endNode(r) as target,
  labels(endNode(r)) as target_labels,
  r.role as role,
  r.share_percent as share_percent
"""

# Depth cannot be a parameter in cypher, so it is formatted in. `_depth` below
# is what keeps that safe: an int, range-checked, never caller text.
EGO_GRAPH = """
match path = (c:Company {{idno: $idno}})-[:HOLDS*1..{depth}]-(other)
unwind relationships(path) as r
with distinct r
{returns}
limit $limit
"""

SHORTEST_PATH = """
match (a:Company {{idno: $idno}}), (b:Company {{idno: $other}})
match path = shortestPath((a)-[:HOLDS*..{depth}]-(b))
unwind relationships(path) as r
with distinct r
{returns}
limit $limit
"""


def _depth(value: int, maximum: int) -> int:
    depth = int(value)
    if not 1 <= depth <= maximum:
        raise ValueError(f"depth must be between 1 and {maximum}")
    return depth


def _node(properties: dict, labels: list[str]) -> dict[str, Any]:
    if "Person" in labels:
        key = properties["key"]
        return {
            "id": f"p:{key}",
            "kind": "person",
            "label": properties.get("name") or key,
            "idno": None,
            "role": None,
        }
    idno = properties["idno"]
    return {
        # A company we have scraped is a company; one we only know about
        # because somebody holds shares in it is drawn as a party.
        "id": f"c:{idno}",
        "kind": "company" if properties.get("scraped") else "company_party",
        "label": properties.get("name") or idno,
        "idno": idno,
        "role": None,
    }


def assemble(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Relationship rows in, deduplicated nodes and links out."""
    nodes: dict[str, dict] = {}
    degree: dict[str, int] = {}
    links: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for row in rows:
        source = _node(dict(row["source"]), list(row["source_labels"]))
        target = _node(dict(row["target"]), list(row["target_labels"]))
        role = row["role"] or ""

        edge = (source["id"], target["id"], role)
        if edge in seen or source["id"] == target["id"]:
            continue
        seen.add(edge)

        for node in (source, target):
            nodes.setdefault(node["id"], node)
            degree[node["id"]] = degree.get(node["id"], 0) + 1
        # The role belongs to the party, and reads better on its node.
        if nodes[source["id"]]["role"] is None:
            nodes[source["id"]]["role"] = role or None

        links.append(
            {
                "source": source["id"],
                "target": target["id"],
                "role": role,
                "share_percent": row["share_percent"],
            }
        )

    for node_id, node in nodes.items():
        node["degree"] = degree.get(node_id, 0)
    return list(nodes.values()), links


async def _run(driver: AsyncDriver, database: str, query: str, **params) -> list[dict]:
    try:
        async with driver.session(database=database) as session:
            result = await session.run(query, **params)
            return [record.data() for record in await result.fetch(params["limit"])]
    except (Neo4jError, ServiceUnavailable, OSError) as exc:
        raise GraphUnavailable(str(exc)) from exc


async def ego_graph(
    driver: AsyncDriver,
    database: str,
    *,
    idno: str,
    depth: int,
    limit: int,
    max_depth: int,
) -> tuple[list[dict], list[dict], bool]:
    """This company and everything within `depth` hops of it."""
    query = EGO_GRAPH.format(depth=_depth(depth, max_depth), returns=RETURN_EDGES)
    rows = await _run(driver, database, query, idno=idno, limit=limit + 1)
    truncated = len(rows) > limit
    nodes, links = assemble(rows[:limit])
    return nodes, links, truncated


async def shortest_path(
    driver: AsyncDriver,
    database: str,
    *,
    idno: str,
    other: str,
    limit: int,
    max_depth: int,
) -> tuple[list[dict], list[dict]]:
    """The shortest chain of holdings between two companies, if there is one."""
    query = SHORTEST_PATH.format(
        depth=_depth(max_depth, max_depth), returns=RETURN_EDGES
    )
    rows = await _run(driver, database, query, idno=idno, other=other, limit=limit)
    return assemble(rows)
