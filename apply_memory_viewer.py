"""
Apply memory viewer changes: API endpoints + frontend UI.
Run from project root: python apply_memory_viewer.py
"""

import re

SERVER = "backend/server.py"
HTML = "frontend/index.html"
JS = "frontend/app.js"
CSS = "frontend/style.css"

def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def write(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

# ── 1. Add /api/memories list + delete endpoints to server.py ────
print("1. Adding memory list/delete API endpoints...")
server = read(SERVER)

memory_api = '''
# ── Memory List & Delete ────────────────────────────────────────────────
@app.get("/api/memories")
async def list_memories():
    """List all stored memories with id, content, category, and timestamp."""
    try:
        from backend.tools.memory import _get_collection
        import datetime
        collection = _get_collection()
        if collection.count() == 0:
            return {"memories": [], "count": 0}
        results = collection.get(include=["documents", "metadatas"])
        memories = []
        for doc_id, doc, meta in zip(results["ids"], results["documents"], results["metadatas"]):
            ts = meta.get("created_at", "0")
            try:
                dt = datetime.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
            except (ValueError, OSError):
                dt = "unknown"
            memories.append({
                "id": doc_id,
                "content": doc,
                "category": meta.get("category", "general"),
                "created_at": dt,
            })
        memories.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return {"memories": memories, "count": len(memories)}
    except Exception as e:
        return {"memories": [], "count": 0, "error": str(e)}


@app.delete("/api/memories/{memory_id}")
async def delete_memory(memory_id: str):
    """Delete a specific memory by its ID."""
    try:
        from backend.tools.memory import _get_collection
        collection = _get_collection()
        collection.delete(ids=[memory_id])
        return {"success": True, "deleted": memory_id}
    except Exception as e:
        return {"success": False, "error": str(e)}
'''

# Insert after /api/memory/toggle block
anchor = '@app.post("/api/memory/toggle")'
idx = server.find(anchor)
if idx != -1:
    # Find end of that function (next blank line + decorator or section)
    end_marker = "# ── File Browser"
    end_idx = server.find(end_marker, idx)
    if end_idx != -1:
        server = server[:end_idx] + memory_api + "\n" + server[end_idx:]
        write(SERVER, server)
        print("   ✅ Memory API endpoints added")
    else:
        print("   ❌ Could not find insertion point")
else:
    print("   ❌ Memory toggle not found")


# ── 2. Add memory section to sidebar in index.html ──────────────
print("2. Adding memory viewer to sidebar...")
html = read(HTML)

memory_html = '''        <!-- Memory viewer -->
        <div class="sidebar-section" id="memorySection">
            <div class="sidebar-section-header" onclick="toggleMemoryList()">
                🧠 Memories <span class="badge" id="memoryCount">0</span>
            </div>
            <div class="memory-list" id="memoryList"></div>
        </div>'''

# Insert before the Documents section
doc_anchor = '📚 Documents'
doc_idx = html.find(doc_anchor)
if doc_idx != -1:
    # Go back to find the start of that sidebar-section div
    section_start = html.rfind('<div class="sidebar-section"', 0, doc_idx)
    if section_start != -1:
        html = html[:section_start] + memory_html + "\n" + html[section_start:]
        write(HTML, html)
        print("   ✅ Memory section added to sidebar")
    else:
        print("   ❌ Could not find section start")
else:
    # Just insert before closing aside
    aside_end = html.find('</aside>')
    if aside_end != -1:
        html = html[:aside_end] + memory_html + "\n" + html[aside_end:]
        write(HTML, html)
        print("   ✅ Memory section added before </aside>")
    else:
        print("   ❌ Could not find sidebar")


# ── 3. Add memory viewer JS functions to app.js ─────────────────
print("3. Adding memory viewer JS...")
js = read(JS)

memory_js = '''

// ── Memory Viewer ───────────────────────────────────────────────
async function loadMemories() {
  try {
    const res = await fetch("/api/memories");
    const data = await res.json();
    const countEl = document.getElementById("memoryCount");
    const listEl = document.getElementById("memoryList");
    if (!countEl || !listEl) return;
    countEl.textContent = data.count || 0;
    if (!data.memories || data.memories.length === 0) {
      listEl.innerHTML = '<div class="memory-empty">No memories yet. Chat naturally and I\\'ll learn!</div>';
      return;
    }
    listEl.innerHTML = data.memories.map(m => `
      <div class="memory-item">
        <span class="memory-cat memory-cat-${m.category}">${m.category}</span>
        <span class="memory-text">${escapeHtml(m.content)}</span>
        <span class="memory-time">${m.created_at}</span>
        <button class="memory-del" onclick="deleteMemory('${m.id}')" title="Delete">✕</button>
      </div>
    `).join("");
  } catch (e) {
    console.warn("Memory load failed:", e);
  }
}

async function deleteMemory(id) {
  try {
    await fetch(`/api/memories/${id}`, { method: "DELETE" });
    await loadMemories();
  } catch (e) {
    console.warn("Memory delete failed:", e);
  }
}

function toggleMemoryList() {
  const list = document.getElementById("memoryList");
  if (list) list.classList.toggle("open");
}
'''

# Insert before the "// ── Boot" section
boot_anchor = "// ── Boot"
boot_idx = js.find(boot_anchor)
if boot_idx != -1:
    js = js[:boot_idx] + memory_js + "\n" + js[boot_idx:]
    write(JS, js)
    print("   ✅ Memory JS functions added")
else:
    print("   ❌ Boot section not found")

# Also call loadMemories() in init()
js = read(JS)
init_anchor = "startHwPolling();"
init_idx = js.find(init_anchor)
if init_idx != -1:
    end_of_line = js.find("\n", init_idx)
    if end_of_line != -1:
        if "loadMemories" not in js[init_idx:init_idx+200]:
            js = js[:end_of_line+1] + "  loadMemories();\n" + js[end_of_line+1:]
            write(JS, js)
            print("   ✅ loadMemories() called in init()")
        else:
            print("   ℹ️ loadMemories already in init")
else:
    print("   ❌ Could not find init insertion point")


# ── 4. Add memory viewer CSS to style.css ────────────────────────
print("4. Adding memory viewer CSS...")
css = read(CSS)

memory_css = '''
/* Memory Viewer */
.memory-list {
  max-height: 0;
  overflow: hidden;
  transition: max-height 0.3s ease;
}
.memory-list.open {
  max-height: 400px;
  overflow-y: auto;
}
.memory-empty {
  padding: 12px 16px;
  font-size: 0.75rem;
  color: rgba(255, 255, 255, 0.35);
  font-style: italic;
}
.memory-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 16px;
  font-size: 0.73rem;
  color: rgba(255, 255, 255, 0.6);
  transition: background 0.2s;
  border-bottom: 1px solid rgba(255, 255, 255, 0.03);
}
.memory-item:hover {
  background: rgba(255, 255, 255, 0.05);
}
.memory-cat {
  font-size: 0.6rem;
  padding: 1px 5px;
  border-radius: 6px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  flex-shrink: 0;
}
.memory-cat-preference {
  background: rgba(139, 92, 246, 0.2);
  color: #a78bfa;
}
.memory-cat-fact {
  background: rgba(59, 130, 246, 0.2);
  color: #60a5fa;
}
.memory-cat-instruction {
  background: rgba(245, 158, 11, 0.2);
  color: #fbbf24;
}
.memory-cat-context {
  background: rgba(16, 185, 129, 0.2);
  color: #34d399;
}
.memory-cat-general {
  background: rgba(255, 255, 255, 0.1);
  color: rgba(255, 255, 255, 0.5);
}
.memory-text {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.memory-time {
  font-size: 0.6rem;
  color: rgba(255, 255, 255, 0.25);
  flex-shrink: 0;
  min-width: 70px;
  text-align: right;
}
.memory-del {
  background: none;
  border: none;
  color: rgba(255, 255, 255, 0.2);
  cursor: pointer;
  font-size: 0.7rem;
  padding: 2px 4px;
  border-radius: 3px;
  transition: all 0.15s;
  flex-shrink: 0;
}
.memory-del:hover {
  color: #ef4444;
  background: rgba(239, 68, 68, 0.15);
}
'''

# Append before the last block (editor panel section)
editor_anchor = "/* ── Editor Panel"
editor_idx = css.find(editor_anchor)
if editor_idx != -1:
    css = css[:editor_idx] + memory_css + "\n" + css[editor_idx:]
else:
    css += memory_css

write(CSS, css)
print("   ✅ Memory CSS added")

print("\n✅ All memory viewer changes applied!")
