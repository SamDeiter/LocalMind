
/**
 * Intelligence Map UI — Unified cognitive visualization for LocalMind v1.0.0.
 *
 * Fetches /api/intelligence/map and renders nodes (memories, skills, gaps)
 * using D3.js force-directed layout with cryptographic verification status.
 */

import { API } from "./state.js";

const TYPE_COLORS = {
  memory: "#818cf8", // indigo
  skill: "#10b981",  // emerald (verified)
  skill_unverified: "#ef4444", // red
  gap: "#f59e0b",    // amber
  unknown: "#64748b", // slate
};

let _simulation = null;

/**
 * Initialize the Intelligence Map components.
 */
export function initIntelligenceMap() {
  const refreshBtn = document.getElementById("refreshBrainBtn");
  const triggerBtn = document.getElementById("triggerLearningBtn");

  if (refreshBtn) {
    refreshBtn.onclick = () => {
        console.log("Refreshing Intelligence Map...");
        loadIntelligenceMap();
    };
  }
  
  if (triggerBtn) {
    triggerBtn.onclick = triggerLearning;
  }

  // Initial load when tab becomes active (handled by nav_rail usually, 
  // but we can trigger it here if the section is visible)
  if (!document.getElementById("mainBrain").classList.contains("hidden")) {
      loadIntelligenceMap();
  }
}

/**
 * Trigger a real-time learning cycle to identify and fill gaps.
 */
async function triggerLearning() {
  const btn = document.getElementById("triggerLearningBtn");
  if (!btn) return;

  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="material-symbols-outlined animate-spin text-sm">cycle</span> learning...`;

  try {
    const r = await fetch(`${API}/api/learning/learn`, { method: "POST" });
    const data = await r.json();
    
    if (data.ok) {
        // Show success notification/toast (assuming one exists or alert)
        const summary = data.summary || "Gap identified and researched.";
        alert(`Learning Cycle Complete!\n\nTopic: ${data.topic}\n\n${summary}`);
        loadIntelligenceMap(); // Refresh map to show new skill/memory
    } else {
        alert("Learning Failed: " + (data.error || "Unknown error"));
    }
  } catch (err) {
    console.error("Learning cycle error:", err);
    alert("Error triggering learning: " + err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalHtml;
  }
}

/**
 * Fetch and render the unified intelligence data.
 */
export async function loadIntelligenceMap() {
  const svg = document.getElementById("brainSvg");
  if (!svg) return;

  try {
    const r = await fetch(`${API}/api/intelligence/map`);
    if (!r.ok) throw new Error(`HTTP error! status: ${r.status}`);
    const data = await r.json();
    
    if (!data.nodes || data.nodes.length === 0) {
        console.warn("No intelligence data received.");
        _renderEmptyState(svg);
        return;
    }

    _renderGraph(svg, data);
  } catch (err) {
    console.error("Failed to load intelligence map:", err);
  }
}

/**
 * Render the graph using D3.js.
 */
function _renderGraph(svgEl, data) {
  // Clear previous simulation
  if (_simulation) _simulation.stop();

  const d3svg = d3.select(svgEl);
  d3svg.selectAll("*").remove();

  const container = document.getElementById("brainContainer");
  const width = container.clientWidth || 800;
  const height = container.clientHeight || 600;

  // Deep copy for D3
  const nodes = data.nodes.map(n => ({ ...n }));
  const links = data.links.map(l => ({ ...l }));

  const g = d3svg.append("g");

  // Zoom behavior
  const zoom = d3.zoom()
    .scaleExtent([0.1, 10])
    .on("zoom", (event) => g.attr("transform", event.transform));
  
  d3svg.call(zoom);

  // Simulation setup
  _simulation = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(links).id(d => d.id).distance(120).strength(0.8))
    .force("charge", d3.forceManyBody().strength(-400))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collision", d3.forceCollide().radius(40));

  // Render Links
  const link = g.append("g")
    .attr("stroke", "#1e293b")
    .attr("stroke-opacity", 0.4)
    .selectAll("line")
    .data(links)
    .join("line")
    .attr("stroke-width", 1.5)
    .attr("stroke-dasharray", d => d.type === 'gap' ? "4 4" : "none");

  // Render Node Groups
  const node = g.append("g")
    .selectAll("g")
    .data(nodes)
    .join("g")
    .attr("class", "node-group")
    .style("cursor", "pointer")
    .call(d3.drag()
        .on("start", (event, d) => {
            if (!event.active) _simulation.alphaTarget(0.3).restart();
            d.fx = d.x; d.fy = d.y;
        })
        .on("drag", (event, d) => {
            d.fx = event.x; d.fy = event.y;
        })
        .on("end", (event, d) => {
            if (!event.active) _simulation.alphaTarget(0);
            d.fx = null; d.fy = null;
        })
    )
    .on("click", (event, d) => {
        event.stopPropagation();
        _showDetails(d);
    });

  // Background glow for specific nodes
  node.filter(d => d.type === 'gap')
    .append("circle")
    .attr("r", 15)
    .attr("fill", "rgba(245, 158, 11, 0.2)")
    .attr("class", "animate-pulse");

  // Node Shapes
  node.append("circle")
    .attr("r", d => d.type === 'skill' ? 14 : 10)
    .attr("fill", d => {
        if (d.type === 'skill') {
            return d.verified ? TYPE_COLORS.skill : TYPE_COLORS.skill_unverified;
        }
        return TYPE_COLORS[d.type] || TYPE_COLORS.unknown;
    })
    .attr("stroke", "#0f172a")
    .attr("stroke-width", 2)
    .style("filter", d => d.verified === false ? "drop-shadow(0 0 8px rgba(239, 68, 68, 0.8))" : "none");

  // Symbols inside nodes
  node.append("text")
    .attr("class", "material-symbols-outlined")
    .attr("text-anchor", "middle")
    .attr("dominant-baseline", "central")
    .attr("fill", "white")
    .attr("font-size", d => d.type === 'skill' ? "14px" : "10px")
    .text(d => {
        if (d.type === 'skill') return "extension";
        if (d.type === 'gap') return "question_mark";
        if (d.type === 'memory') return "database";
        return "circle";
    });

  // Labels
  node.append("text")
    .text(d => d.label)
    .attr("x", 18)
    .attr("y", 4)
    .attr("fill", "#e2e8f0")
    .attr("font-size", "11px")
    .attr("font-weight", 600)
    .style("pointer-events", "none")
    .style("paint-order", "stroke")
    .style("stroke", "#0d1117")
    .style("stroke-width", "3px")
    .style("stroke-linecap", "round")
    .style("stroke-linejoin", "round");

  // Verification badges for skills
  node.filter(d => d.type === 'skill')
    .append("text")
    .attr("class", "material-symbols-outlined")
    .attr("x", 10)
    .attr("y", -10)
    .attr("font-size", "10px")
    .attr("fill", d => d.verified ? "#10b981" : "#ef4444")
    .text(d => d.verified ? "verified" : "warning");

  // Tick updates
  _simulation.on("tick", () => {
    link
      .attr("x1", d => d.source.x)
      .attr("y1", d => d.source.y)
      .attr("x2", d => d.target.x)
      .attr("y2", d => d.target.y);

    node.attr("transform", d => `translate(${d.x},${d.y})`);
  });

  // Re-center on double click
  d3svg.on("dblclick.zoom", null);
  d3svg.on("click", () => {
      document.getElementById("nodeDetailPanel").classList.add("hidden");
  });
}

/**
 * Handle detail panel display.
 */
function _showDetails(node) {
    const panel = document.getElementById("nodeDetailPanel");
    const title = document.getElementById("nodeTitle");
    const type = document.getElementById("nodeType");
    const desc = document.getElementById("nodeDesc");
    const actions = document.getElementById("nodeActions");

    if (!panel) return;

    panel.classList.remove("hidden");
    title.innerText = node.label;
    
    let typeText = node.type.toUpperCase();
    if (node.type === 'skill') {
        typeText += node.verified ? " (MINTED & AUTHENTIC)" : " (TAMPERED / UNVERIFIED)";
    }
    type.innerText = typeText;
    type.style.color = (node.type === 'skill' && !node.verified) ? "#ef4444" : "#818cf8";

    let description = node.description || "Aggregated cognitive node.";
    if (node.type === 'skill' && !node.verified) {
        description = "⚠️ WARNING: This skill's cryptographic signature does not match the content. " +
                      "The tool source code may have been modified outside of secure channels.\n\n" + description;
    }
    desc.innerText = description;

    actions.innerHTML = "";
    
    // Action: Research Gap
    if (node.type === 'gap') {
        const btn = document.createElement("button");
        btn.className = "w-full py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded text-[10px] font-bold uppercase transition-colors shadow-md";
        btn.innerText = "Research this Gap Now";
        btn.onclick = () => {
            _triggerSpecificResearch(node.label);
        };
        actions.appendChild(btn);
    }

    // Action: View Code (for skills)
    if (node.type === 'skill') {
        const btn = document.createElement("button");
        btn.className = "w-full py-2 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded text-[10px] font-bold uppercase transition-colors";
        btn.innerText = "Inspect Source Code";
        btn.onclick = () => {
             alert(`Viewing source code for ${node.id} is coming in Phase H.`);
        };
        actions.appendChild(btn);
    }
}

async function _triggerSpecificResearch(topic) {
    const triggerBtn = document.getElementById("triggerLearningBtn");
    const originalText = triggerBtn.innerText;
    triggerBtn.disabled = true;
    
    try {
        const r = await fetch(`${API}/api/learning/learn?topic=${encodeURIComponent(topic)}`, { method: "POST" });
        const data = await r.json();
        if (data.ok) {
            alert(`Research successful: ${data.topic}\n\n${data.summary}`);
            loadIntelligenceMap();
        } else {
            alert("Research failed: " + data.error);
        }
    } catch (err) {
        alert("Error: " + err.message);
    } finally {
        triggerBtn.disabled = false;
        triggerBtn.innerText = originalText;
    }
}

function _renderEmptyState(svgEl) {
    const d3svg = d3.select(svgEl);
    d3svg.selectAll("*").remove();
    
    const container = document.getElementById("brainContainer");
    const width = container.clientWidth || 800;
    const height = container.clientHeight || 600;
    
    d3svg.append("text")
        .attr("x", width / 2)
        .attr("y", height / 2)
        .attr("text-anchor", "middle")
        .attr("fill", "#475569")
        .attr("font-size", "14px")
        .attr("font-family", "Space Grotesk")
        .attr("font-weight", 600)
        .text("Awaiting Cognitive Synchronization...");

    d3svg.append("text")
        .attr("x", width / 2)
        .attr("y", height / 2 + 25)
        .attr("text-anchor", "middle")
        .attr("fill", "#334155")
        .attr("font-size", "10px")
        .attr("font-family", "Inter")
        .text("Establish a mission or teach a skill to populate the map.");
}
