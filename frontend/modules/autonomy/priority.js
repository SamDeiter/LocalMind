import { API, priorityContainer, priorityInput } from "../state.js";
import { escapeHtml, showToast } from "../utils.js";

export async function loadPriorities() {
  if (!priorityContainer) return;
  try {
    const r = await fetch(`${API}/api/autonomy/priorities`);
    const d = await r.json();
    renderPriorities(d.priorities || []);
  } catch (err) {
    console.error("Failed to load priorities:", err);
  }
}

export async function addPriority() {
  if (!priorityInput) return;
  const val = priorityInput.value.trim();
  if (!val) return;

  try {
    const r = await fetch(`${API}/api/autonomy/priorities`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ priority: val })
    });
    const d = await r.json();
    if (d.ok) {
      priorityInput.value = "";
      renderPriorities(d.priorities);
      showToast("🎯 Target Locked", "info");
    }
  } catch {
    showToast("❌ Failed to add priority", "error");
  }
}

export async function removePriority(priority) {
  try {
    const r = await fetch(`${API}/api/autonomy/priorities?priority=${encodeURIComponent(priority)}`, {
      method: "DELETE"
    });
    const d = await r.json();
    if (d.ok) {
      renderPriorities(d.priorities);
    }
  } catch {
    showToast("❌ Failed to remove priority", "error");
  }
}

function renderPriorities(priorities) {
  if (!priorityContainer) return;
  priorityContainer.innerHTML = "";
  priorities.forEach(p => {
    // Handle both string and object formats
    const label = typeof p === "string" ? p : (p.description || p.id || String(p));
    const id = typeof p === "string" ? p : (p.id || p.description || String(p));
    const tag = document.createElement("div");
    tag.className = "priority-tag";
    tag.innerHTML = `
      <span>${escapeHtml(label)}</span>
      <button class="remove-priority" data-val="${escapeHtml(id)}">&times;</button>
    `;
    priorityContainer.appendChild(tag);
  });

  priorityContainer.querySelectorAll(".remove-priority").forEach(btn => {
    btn.onclick = () => removePriority(btn.dataset.val);
  });
}
