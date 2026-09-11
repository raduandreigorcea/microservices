/** Reading a company's neighbourhood out of its graph. */

import type { Graph, GraphNode } from "./types";

export interface Touch {
  company: GraphNode;
  /** The parties both companies share. Empty when the link is direct. */
  via: GraphNode[];
}

/**
 * Which other companies this one touches, and through whom.
 *
 * Two companies touch when the same party sits on both, which is the whole
 * reason the graph is worth drawing. A party that is itself a company shows up
 * as a touch too, since owning a company is a relationship of its own.
 */
export function touchedCompanies(graph: Graph, idno: string): Touch[] {
  const focus = `c:${idno}`;
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));

  const neighbours = (id: string): string[] =>
    graph.links.flatMap((link) =>
      link.source === id ? [link.target] : link.target === id ? [link.source] : [],
    );

  const ownParties = new Set(neighbours(focus));

  return graph.nodes
    .filter((node) => node.idno && node.id !== focus)
    .map((company) => ({
      company,
      via: ownParties.has(company.id)
        ? []
        : neighbours(company.id)
            .filter((id) => ownParties.has(id))
            .map((id) => byId.get(id))
            .filter((node): node is GraphNode => node !== undefined),
    }))
    .sort((a, b) => b.company.degree - a.company.degree);
}

/** Romanian plurals for the two things a company's graph holds. */
export function describeGraph(graph: Graph, idno: string): string {
  let people = 0;
  let companies = 0;
  for (const node of graph.nodes) {
    if (node.id === `c:${idno}`) continue;
    if (node.kind === "person") people += 1;
    else companies += 1;
  }

  const parts: string[] = [];
  if (people) parts.push(`${people} ${people === 1 ? "persoană" : "persoane"}`);
  if (companies) parts.push(`${companies} ${companies === 1 ? "firmă" : "firme"}`);
  return parts.join(" · ");
}
