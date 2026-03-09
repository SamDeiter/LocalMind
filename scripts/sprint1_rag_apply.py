"""
Sprint 1 — Document RAG: Server Endpoints + Frontend UI
Adds:
1. POST /api/documents/upload — file upload + indexing
2. GET /api/documents — list indexed documents
3. DELETE /api/documents/{filename} — remove from index
4. RAG context injection into chat
5. Frontend upload button and document list
"""

# ──────────────────────────────────────────────────────────────────
# 1. BACKEND: Add RAG endpoints to server.py
# ──────────────────────────────────────────────────────────────────

server_path = r"c:\Users\Sam Deiter\Documents\GitHub\LocalMind\backend\server.py"
with open(server_path, "r", encoding="utf-8") as f:
    server_code = f.read()

# Add UploadFile import
if "from fastapi import FastAPI, Request" in server_code and "UploadFile" not in server_code:
    server_code = server_code.replace(
        "from fastapi import FastAPI, Request",
        "from fastapi import FastAPI, Request, UploadFile, File"
    )
    print("[OK] server.py — Added UploadFile import")

# Add RAG import at the top (after registry import)
rag_import = "from backend.tools.rag import index_document, query_documents, list_indexed_documents, delete_document"
if "from backend.tools.rag" not in server_code:
    # Insert after the registry import
    server_code = server_code.replace(
        "registry = ToolRegistry()",
        f"registry = ToolRegistry()\n\n# RAG imports\ntry:\n    {rag_import}\n    RAG_AVAILABLE = True\nexcept ImportError:\n    RAG_AVAILABLE = False"
    )
    print("[OK] server.py — Added RAG imports")

# Add RAG endpoints before "Serve Frontend"
rag_endpoints = '''
# ── Document RAG ────────────────────────────────────────────────────────

@app.post("/api/documents/upload")
async def upload_document(file: UploadFile = File(...)):
    """Upload and index a document for RAG queries."""
    if not RAG_AVAILABLE:
        return {"error": "RAG not available — install chromadb"}

    content = await file.read()
    text = content.decode("utf-8", errors="replace")

    if not text.strip():
        return {"error": "Empty file"}

    result = index_document(file.filename, text)
    return result


@app.get("/api/documents")
async def list_documents():
    """List all indexed documents."""
    if not RAG_AVAILABLE:
        return {"documents": []}
    return list_indexed_documents()


@app.delete("/api/documents/{filename:path}")
async def remove_document(filename: str):
    """Remove a document from the RAG index."""
    if not RAG_AVAILABLE:
        return {"error": "RAG not available"}
    return delete_document(filename)

'''

marker = "# ── Serve Frontend"
if "/api/documents/upload" not in server_code:
    server_code = server_code.replace(marker, rag_endpoints + marker)
    with open(server_path, "w", encoding="utf-8") as f:
        f.write(server_code)
    print("[OK] server.py — Added RAG endpoints")
else:
    print("[SKIP] server.py — RAG endpoints already exist")

# Also inject RAG context into chat — add document context alongside memory
with open(server_path, "r", encoding="utf-8") as f:
    server_code = f.read()

# Find where system_prompt is used in chat and add RAG context injection
rag_injection = '''
        # Inject RAG context if documents are indexed
        if RAG_AVAILABLE:
            try:
                from backend.tools.rag import query_documents as _rag_query
                rag_results = _rag_query(user_message, n_results=3)
                if rag_results.get("results"):
                    rag_context = "\\n\\nRelevant document context:\\n"
                    for r in rag_results["results"]:
                        rag_context += f"[From {r['source']}]: {r['content'][:500]}\\n"
                    system_prompt += rag_context
            except Exception:
                pass  # RAG query failed, continue without context
'''

# Insert RAG injection after the system prompt loading logic
old_default_check = '    if not system_prompt:\n        system_prompt = DEFAULT_SYSTEM_PROMPT'
if 'Relevant document context' not in server_code and old_default_check in server_code:
    server_code = server_code.replace(
        old_default_check,
        old_default_check + rag_injection
    )
    with open(server_path, "w", encoding="utf-8") as f:
        f.write(server_code)
    print("[OK] server.py — Added RAG context injection into chat")
else:
    if 'Relevant document context' in server_code:
        print("[SKIP] server.py — RAG injection already exists")
    else:
        print("[WARN] server.py — Could not find insertion point for RAG injection")


# ──────────────────────────────────────────────────────────────────
# 2. FRONTEND: Add upload button + document panel
# ──────────────────────────────────────────────────────────────────

html_path = r"c:\Users\Sam Deiter\Documents\GitHub\LocalMind\frontend\index.html"
with open(html_path, "r", encoding="utf-8") as f:
    html_code = f.read()

# Add upload button next to camera button in the input area
if 'id="uploadBtn"' not in html_code:
    html_code = html_code.replace(
        '<button id="cameraBtn"',
        '<button id="uploadBtn" class="icon-btn" title="Upload document for RAG">📎</button>\n                <input type="file" id="fileInput" accept=".txt,.md,.py,.js,.ts,.json,.csv,.html,.css,.xml,.yaml,.yml,.toml,.cfg,.ini,.log,.sql,.sh,.bat,.ps1,.java,.cpp,.c,.h,.rb,.go,.rs,.php" style="display:none" multiple />\n                <button id="cameraBtn"'
    )
    print("[OK] index.html — Added upload button")
else:
    print("[SKIP] index.html — Upload button already exists")

# Add document list panel in sidebar
if 'id="documentList"' not in html_code:
    html_code = html_code.replace(
        '<div id="versionBadge"',
        '''<div class="sidebar-section">
            <div class="sidebar-section-header" id="docsToggle">📚 Documents <span id="docCount" class="badge">0</span></div>
            <div id="documentList" class="document-list"></div>
        </div>
        <div id="versionBadge"'''
    )
    print("[OK] index.html — Added document list panel")
else:
    print("[SKIP] index.html — Document list already exists")

with open(html_path, "w", encoding="utf-8") as f:
    f.write(html_code)


# ──────────────────────────────────────────────────────────────────
# 3. FRONTEND: Add upload logic to app.js
# ──────────────────────────────────────────────────────────────────

app_path = r"c:\Users\Sam Deiter\Documents\GitHub\LocalMind\frontend\app.js"
with open(app_path, "r", encoding="utf-8") as f:
    app_code = f.read()

# Add document management functions before the Boot section
doc_functions = '''
// ── Document RAG ────────────────────────────────────────────────
async function uploadDocuments(files) {
  for (const file of files) {
    const formData = new FormData();
    formData.append("file", file);
    try {
      const r = await fetch(`${API}/api/documents/upload`, {
        method: "POST",
        body: formData,
      });
      const d = await r.json();
      if (d.success) {
        console.log(`Indexed ${file.name}: ${d.chunks} chunks`);
      } else {
        console.error(`Upload failed: ${d.error}`);
      }
    } catch (e) {
      console.error("Upload error:", e);
    }
  }
  await loadDocuments();
}

async function loadDocuments() {
  try {
    const r = await fetch(`${API}/api/documents`);
    const d = await r.json();
    const list = document.getElementById("documentList");
    const count = document.getElementById("docCount");
    if (!list) return;

    const docs = d.documents || [];
    count.textContent = docs.length;
    list.innerHTML = "";

    docs.forEach((doc) => {
      const div = document.createElement("div");
      div.className = "document-item";
      div.innerHTML = `
        <span class="doc-icon">📄</span>
        <span class="doc-name" title="${escapeHtml(doc.filename)}">${escapeHtml(doc.filename)}</span>
        <span class="doc-chunks">${doc.chunks} chunks</span>
        <button class="delete-btn" title="Remove">✕</button>
      `;
      div.querySelector(".delete-btn").addEventListener("click", async () => {
        await fetch(`${API}/api/documents/${encodeURIComponent(doc.filename)}`, {
          method: "DELETE",
        });
        await loadDocuments();
      });
      list.appendChild(div);
    });
  } catch { /* ignore */ }
}

'''

if "uploadDocuments" not in app_code:
    app_code = app_code.replace("// ── Boot", doc_functions + "// ── Boot")
    print("[OK] app.js — Added document upload/list functions")
else:
    print("[SKIP] app.js — Document functions already exist")

# Add event bindings for upload button
upload_bindings = '''
  // Document upload
  const uploadBtn = document.getElementById("uploadBtn");
  const fileInput = document.getElementById("fileInput");
  if (uploadBtn && fileInput) {
    uploadBtn.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", (e) => {
      if (e.target.files.length > 0) {
        uploadDocuments(e.target.files);
        fileInput.value = "";
      }
    });
  }

  // Document list toggle
  const docsToggle = document.getElementById("docsToggle");
  const docList = document.getElementById("documentList");
  if (docsToggle && docList) {
    docsToggle.addEventListener("click", () => {
      docList.classList.toggle("open");
    });
  }

'''

if '"uploadBtn"' not in app_code:
    # Insert before the closing of bindEvents
    app_code = app_code.replace(
        "  // Keyboard shortcuts",
        upload_bindings + "  // Keyboard shortcuts"
    )
    print("[OK] app.js — Added upload event bindings")
else:
    print("[SKIP] app.js — Upload bindings already exist")

# Call loadDocuments in init
if "loadDocuments()" not in app_code:
    app_code = app_code.replace("  loadVersion();", "  loadVersion();\n  loadDocuments();")
    print("[OK] app.js — Added loadDocuments to init")
else:
    print("[SKIP] app.js — loadDocuments already in init")

with open(app_path, "w", encoding="utf-8") as f:
    f.write(app_code)


# ──────────────────────────────────────────────────────────────────
# 4. FRONTEND: Add CSS for document list and upload
# ──────────────────────────────────────────────────────────────────

css_path = r"c:\Users\Sam Deiter\Documents\GitHub\LocalMind\frontend\style.css"
with open(css_path, "r", encoding="utf-8") as f:
    css_code = f.read()

doc_css = """
/* Document RAG sidebar */
.sidebar-section {
    border-top: 1px solid rgba(255, 255, 255, 0.06);
}
.sidebar-section-header {
    padding: 10px 16px;
    font-size: 0.8rem;
    color: rgba(255, 255, 255, 0.5);
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 6px;
    transition: color 0.2s;
}
.sidebar-section-header:hover {
    color: rgba(255, 255, 255, 0.8);
}
.badge {
    background: rgba(139, 92, 246, 0.3);
    color: #a78bfa;
    border-radius: 10px;
    padding: 1px 7px;
    font-size: 0.7rem;
    margin-left: auto;
}
.document-list {
    max-height: 0;
    overflow: hidden;
    transition: max-height 0.3s ease;
}
.document-list.open {
    max-height: 300px;
    overflow-y: auto;
}
.document-item {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 16px;
    font-size: 0.75rem;
    color: rgba(255, 255, 255, 0.6);
    transition: background 0.2s;
}
.document-item:hover {
    background: rgba(255, 255, 255, 0.05);
}
.doc-icon { font-size: 0.8rem; }
.doc-name {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.doc-chunks {
    font-size: 0.65rem;
    color: rgba(255, 255, 255, 0.3);
}
"""

if ".document-list" not in css_code:
    css_code += doc_css
    with open(css_path, "w", encoding="utf-8") as f:
        f.write(css_code)
    print("[OK] style.css — Added document RAG styles")
else:
    print("[SKIP] style.css — Document styles already exist")


print("\n✅ Document RAG feature complete!")
