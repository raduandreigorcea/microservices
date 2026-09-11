/** A force-directed network. Used whole on its own page, and per company. */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationNodeDatum,
} from "d3-force";

import { count } from "../lib/format";
import type { GraphLink, GraphNode, NodeKind } from "../lib/types";

/** How many ticks to run before drawing. Enough for the layout to settle. */
const SETTLE_TICKS = 320;

/** Past this many nodes, labelling every one turns the picture into soup. */
const LABEL_EVERYTHING_UNDER = 40;

const KIND_LABEL: Record<NodeKind, string> = {
  company: "companie",
  person: "persoană",
  company_party: "firmă asociată",
};

interface Placed extends SimulationNodeDatum, GraphNode {
  x: number;
  y: number;
  r: number;
}

interface Drawn {
  source: Placed;
  target: Placed;
  role: string;
  share: string | null;
}

/** Degree decides the radius, flattened so one hub cannot dwarf the rest. */
const radiusFor = (node: GraphNode, focused: boolean) =>
  (focused ? 9 : node.kind === "person" ? 3.5 : 5) + Math.sqrt(node.degree) * 2.4;

export function Network({
  nodes: input,
  links: rawLinks,
  focus,
  width = 1200,
  height = 780,
}: {
  nodes: GraphNode[];
  links: GraphLink[];
  /** Node id to treat as the centre, if this is one company's neighbourhood. */
  focus?: string;
  width?: number;
  height?: number;
}) {
  const [selected, setSelected] = useState<Placed | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const svgRef = useRef<SVGSVGElement>(null);

  /* The simulation runs to a standstill before anything is drawn, so the page
     paints one settled layout instead of animating a cloud into place. */
  const layout = useMemo(() => {
    const nodes: Placed[] = input.map((node) => ({
      ...node,
      x: 0,
      y: 0,
      r: radiusFor(node, node.id === focus),
    }));
    const byId = new Map(nodes.map((node) => [node.id, node]));

    const links = rawLinks.flatMap((link) => {
      const source = byId.get(link.source);
      const target = byId.get(link.target);
      return source && target
        ? [{ source, target, role: link.role, share: link.share_percent }]
        : [];
    });

    const simulation = forceSimulation(nodes)
      .force(
        "link",
        forceLink<Placed, Drawn>(links)
          .id((node) => node.id)
          .distance(nodes.length < LABEL_EVERYTHING_UNDER ? 110 : 58)
          .strength(0.6),
      )
      .force("charge", forceManyBody().strength(nodes.length < 40 ? -600 : -190))
      .force("collide", forceCollide<Placed>().radius((node) => node.r + 8))
      .force("center", forceCenter(width / 2, height / 2))
      // Gentle pull inward, so detached stars do not drift off the canvas.
      .force("x", forceX(width / 2).strength(0.035))
      .force("y", forceY(height / 2).strength(0.035))
      .stop();

    // The focused company is pinned dead centre; everything arranges round it.
    const centre = focus ? byId.get(focus) : undefined;
    if (centre) {
      centre.fx = width / 2;
      centre.fy = height / 2;
    }

    simulation.tick(SETTLE_TICKS);
    return { nodes, links, simulation };
  }, [input, rawLinks, focus, width, height]);

  const labelAll = layout.nodes.length < LABEL_EVERYTHING_UNDER;

  /* Which ids stay lit. Null means everything. */
  const lit = useMemo(() => {
    const target = hovered ?? selected?.id ?? null;
    if (!target) return null;
    const keep = new Set([target]);
    for (const link of layout.links) {
      if (link.source.id === target) keep.add(link.target.id);
      if (link.target.id === target) keep.add(link.source.id);
    }
    return keep;
  }, [hovered, selected, layout]);

  // --- pan, zoom, drag ------------------------------------------------------

  const dragging = useRef<{ node: Placed; id: number } | null>(null);
  const panning = useRef<{ x: number; y: number; id: number } | null>(null);
  const frame = useRef(0);

  /** Screen point to graph point, undoing the current view transform. */
  const toGraph = useCallback(
    (event: React.PointerEvent) => {
      const box = svgRef.current?.getBoundingClientRect();
      if (!box) return { x: 0, y: 0 };
      const scale = width / box.width;
      return {
        x: ((event.clientX - box.left) * scale - view.x) / view.k,
        y: ((event.clientY - box.top) * scale - view.y) / view.k,
      };
    },
    [view, width],
  );

  /* Dragging re-heats the simulation and writes straight to the DOM. Going
     through React state at sixty frames a second would drop half of them. */
  const paint = useCallback(() => {
    const svg = svgRef.current;
    if (!svg) return;
    for (const node of layout.nodes) {
      svg
        .querySelector(`[data-node="${CSS.escape(node.id)}"]`)
        ?.setAttribute("transform", `translate(${node.x} ${node.y})`);
    }
    for (const [index, link] of layout.links.entries()) {
      const line = svg.querySelector(`[data-link="${index}"]`);
      if (!line) continue;
      line.setAttribute("x1", String(link.source.x));
      line.setAttribute("y1", String(link.source.y));
      line.setAttribute("x2", String(link.target.x));
      line.setAttribute("y2", String(link.target.y));
    }
  }, [layout]);

  const step = useCallback(() => {
    if (!dragging.current) return;
    layout.simulation.tick(1);
    paint();
    frame.current = requestAnimationFrame(step);
  }, [layout, paint]);

  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  const onNodeDown = (event: React.PointerEvent, node: Placed) => {
    event.stopPropagation();
    (event.target as Element).setPointerCapture(event.pointerId);
    dragging.current = { node, id: event.pointerId };
    node.fx = node.x;
    node.fy = node.y;
    layout.simulation.alphaTarget(0.25).restart();
    frame.current = requestAnimationFrame(step);
  };

  const onPointerMove = (event: React.PointerEvent) => {
    if (dragging.current?.id === event.pointerId) {
      const point = toGraph(event);
      dragging.current.node.fx = point.x;
      dragging.current.node.fy = point.y;
      return;
    }
    if (panning.current?.id === event.pointerId) {
      const box = svgRef.current?.getBoundingClientRect();
      if (!box) return;
      const scale = width / box.width;
      setView((current) => ({
        ...current,
        x: current.x + (event.clientX - panning.current!.x) * scale,
        y: current.y + (event.clientY - panning.current!.y) * scale,
      }));
      panning.current = { ...panning.current, x: event.clientX, y: event.clientY };
    }
  };

  const endGesture = (event: React.PointerEvent) => {
    if (dragging.current?.id === event.pointerId) {
      const { node } = dragging.current;
      // The pinned centre stays pinned; everything else floats free again.
      if (node.id !== focus) {
        node.fx = null;
        node.fy = null;
      }
      dragging.current = null;
      cancelAnimationFrame(frame.current);
      layout.simulation.alphaTarget(0);
    }
    if (panning.current?.id === event.pointerId) panning.current = null;
  };

  /** Scales around a point, keeping whatever sits under it pinned in place. */
  const zoomAt = useCallback(
    (px: number, py: number, factor: number) => {
      setView((current) => {
        const k = Math.min(4, Math.max(0.3, current.k * factor));
        return {
          k,
          x: px - ((px - current.x) / current.k) * k,
          y: py - ((py - current.y) / current.k) * k,
        };
      });
    },
    [],
  );

  const zoomCentre = (factor: number) => zoomAt(width / 2, height / 2, factor);

  /* The wheel belongs to the page. This graph is embedded in a long screen,
     and a widget that eats the scroll traps the reader inside it. Zooming
     takes a deliberate ctrl (or cmd) plus wheel, which is also what maps and
     canvases do, so the gesture is already familiar.

     It has to be a native listener: React attaches wheel passively at the
     root, where preventDefault does nothing, and without it ctrl+wheel would
     zoom the whole browser instead. */
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;

    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      const box = svg.getBoundingClientRect();
      const scale = width / box.width;
      zoomAt(
        (event.clientX - box.left) * scale,
        (event.clientY - box.top) * scale,
        event.deltaY < 0 ? 1.12 : 0.89,
      );
    };

    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, [width, zoomAt]);

  return (
    <div className="graph">
      <svg
        ref={svgRef}
        className="graph__canvas"
        viewBox={`0 0 ${width} ${height}`}
        onPointerDown={(event) => {
          panning.current = {
            x: event.clientX,
            y: event.clientY,
            id: event.pointerId,
          };
          setSelected(null);
        }}
        onPointerMove={onPointerMove}
        onPointerUp={endGesture}
        onPointerCancel={endGesture}
      >
        <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
          <g>
            {layout.links.map((link, index) => (
              <line
                key={index}
                data-link={index}
                x1={link.source.x}
                y1={link.source.y}
                x2={link.target.x}
                y2={link.target.y}
                className="graph__link"
                data-role={link.role}
                data-dim={lit && !(lit.has(link.source.id) && lit.has(link.target.id))}
              />
            ))}
          </g>

          {layout.nodes.map((node) => (
            <g
              key={node.id}
              data-node={node.id}
              className="graph__node"
              data-kind={node.kind}
              data-dim={lit ? !lit.has(node.id) : undefined}
              data-picked={selected?.id === node.id}
              data-focus={node.id === focus}
              transform={`translate(${node.x} ${node.y})`}
              onPointerDown={(event) => onNodeDown(event, node)}
              onPointerEnter={() => setHovered(node.id)}
              onPointerLeave={() => setHovered(null)}
              onClick={(event) => {
                event.stopPropagation();
                setSelected(node);
              }}
            >
              <circle r={node.r} />
              {(labelAll || node.degree > 2 || selected?.id === node.id) && (
                <text y={node.r + 12}>{node.label.slice(0, 30)}</text>
              )}
            </g>
          ))}
        </g>
      </svg>

      {selected && (
        <aside className="graph__card">
          <span className="eyebrow">{KIND_LABEL[selected.kind]}</span>
          <p className="graph__card-name">{selected.label}</p>
          <p className="graph__card-meta">
            {count(selected.degree)} legături
            {selected.role ? ` · ${selected.role.toLowerCase()}` : ""}
          </p>
          {/* Only a company we actually hold has a page. A shareholder the
              source could not give an IDNO for carries a hashed key instead,
              and linking to it lands on a 404. */}
          {selected.kind === "company" && selected.id !== focus && (
            <Link className="btn" to={`/companies/${selected.idno}`} viewTransition>
              deschide firma
            </Link>
          )}
        </aside>
      )}

      <div className="graph__zoom">
        <button type="button" className="btn" onClick={() => zoomCentre(1.25)}>
          +
        </button>
        <button type="button" className="btn" onClick={() => zoomCentre(0.8)}>
          −
        </button>
        <button
          type="button"
          className="btn"
          onClick={() => setView({ x: 0, y: 0, k: 1 })}
        >
          reset
        </button>
      </div>

      <span className="graph__hint">
        trage nodurile · ctrl + rotiță pentru zoom
      </span>
    </div>
  );
}
