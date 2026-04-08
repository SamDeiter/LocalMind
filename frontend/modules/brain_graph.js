/**
 * Brain Knowledge Graph — D3.js force-directed visualization of memories.
 *
 * Fetches /api/memories/graph and renders nodes (memories) grouped by
 * category with force-directed layout. Nodes are color-coded by subcategory
 * and sized by access count.
 */

import { API } from "./state.js";

const CATEGORY_COLORS = {
  preference: "#818cf8", // indigo
  fact: "#34d399",       // emerald
  instruction: "#f59e0b", // amber
  context: "#a78bfa",    // violet
  semantic: "#6366f1",   // primary
  episodic: "#06b6d4",   // cyan
  procedural: "#f472b6", // pink
  unknown: "#64748b",    // slate
};

let _simulation = null;

export function initBrainGraph() {
  loadBrainGraph();
  // Refresh every 60 seconds
  setInterval(loadBrainGraph, 60000);
}

export async function loadBrainGraph() {
  const svg = document.getElementById("brainGraphSvg");
  const empty = document.getElementById("brainGraphEmpty");
  const statsEl = document.getElementById("brainGraphStats");
  if (!svg) return;

  try {
    const r = await fetch(`${API}/api/memories/graph`);
    const data = await r.json();

    if (!data.nodes || data.nodes.length === 0) {
      if (empty) empty.style.display = "";
      return;
    }
    if (empty) empty.style.display = "none";

    _renderGraph(svg, data);

    // Stats
    if (statsEl && data.stats) {
      const parts = Object.entries(data.stats.by_category || {})
        .map(([cat, count]) => {
          const color = CATEGORY_COLORS[cat] || CATEGORY_COLORS.unknown;
          return `<span style="color:${color}">${cat}: ${count}</span>`;
        });
      statsEl.innerHTML = `<span>${data.stats.total} memories</span> | ${parts.join(" | ")}`;
    }
  } catch {
    // Non-critical — silently ignore
  }
}

function _renderGraph(svgEl, data) {
  // Clear previous content
  if (_simulation) _simulation.stop();

  const d3svg = d3.select(svgEl);
  d3svg.selectAll("*").remove();

  const rect = svgEl.getBoundingClientRect();
  const width = rect.width || 280;
  const height = rect.height || 240;

  // Deep copy to avoid D3 mutating original data
  const nodes = data.nodes.map((n) => ({ ...n }));
  const edges = data.edges.map((e) => ({ ...e }));

  // Force simulation
  _simulation = d3
    .forceSimulation(nodes)
    .force("link", d3.forceLink(edges).id((d) => d.id).distance(30).strength(0.4))
    .force("charge", d3.forceManyBody().strength(-40))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collision", d3.forceCollide().radius(8));

  const g = d3svg.append("g");

  // Zoom
  d3svg.call(
    d3.zoom().scaleExtent([0.3, 4]).on("zoom", (event) => {
      g.attr("transform", event.transform);
    })
  );

  // Edges
  const link = g
    .append("g")
    .selectAll("line")
    .data(edges)
    .join("line")
    .attr("stroke", "#334155")
    .attr("stroke-opacity", 0.4)
    .attr("stroke-width", 1);

  // Nodes
  const node = g
    .append("g")
    .selectAll("circle")
    .data(nodes)
    .join("circle")
    .attr("r", (d) => Math.min(3 + (d.access_count || 0) * 0.5, 10))
    .attr("fill", (d) => CATEGORY_COLORS[d.subcategory] || CATEGORY_COLORS[d.category] || CATEGORY_COLORS.unknown)
    .attr("stroke", "#0f172a")
    .attr("stroke-width", 1)
    .style("cursor", "pointer")
    .call(_drag(_simulation));

  // Tooltip on hover
  node.append("title").text((d) => `[${d.subcategory}] ${d.label}`);

  // Tick
  _simulation.on("tick", () => {
    link
      .attr("x1", (d) => d.source.x)
      .attr("y1", (d) => d.source.y)
      .attr("x2", (d) => d.target.x)
      .attr("y2", (d) => d.target.y);
    node.attr("cx", (d) => d.x).attr("cy", (d) => d.y);
  });
}

function _drag(simulation) {
  return d3
    .drag()
    .on("start", (event, d) => {
      if (!event.active) simulation.alphaTarget(0.3).restart();
      d.fx = d.x;
      d.fy = d.y;
    })
    .on("drag", (event, d) => {
      d.fx = event.x;
      d.fy = event.y;
    })
    .on("end", (event, d) => {
      if (!event.active) simulation.alphaTarget(0);
      d.fx = null;
      d.fy = null;
    });
}
