"""
Model Loading Indicator — shows hardware status when AI is loading into VRAM
Adds:
1. GET /api/hardware - backend endpoint proxying Ollama /api/ps
2. Hardware status bar in UI header
3. Polling logic to show loading state
"""

# ──────────────────────────────────────────────────────────────────
# 1. BACKEND: Add hardware status endpoint
# ──────────────────────────────────────────────────────────────────

server_path = r"c:\Users\Sam Deiter\Documents\GitHub\LocalMind\backend\server.py"
with open(server_path, "r", encoding="utf-8") as f:
    server_code = f.read()

hw_endpoint = '''
@app.get("/api/hardware")
async def hardware_status():
    """Get loaded models and VRAM usage from Ollama."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_BASE_URL}/api/ps")
            data = r.json()
            models = []
            for m in data.get("models", []):
                size_gb = m.get("size", 0) / (1024**3)
                vram_gb = m.get("size_vram", 0) / (1024**3)
                models.append({
                    "name": m.get("name", "unknown"),
                    "size_gb": round(size_gb, 1),
                    "vram_gb": round(vram_gb, 1),
                    "processor": m.get("details", {}).get("quantization_level", ""),
                    "expires": m.get("expires_at", ""),
                })
            return {"loaded": len(models) > 0, "models": models}
    except Exception:
        return {"loaded": False, "models": []}

'''

marker = "# ── Document RAG"
if "/api/hardware" not in server_code:
    if marker in server_code:
        server_code = server_code.replace(marker, hw_endpoint + marker)
    else:
        # Fallback: insert before Serve Frontend
        server_code = server_code.replace("# ── Serve Frontend", hw_endpoint + "# ── Serve Frontend")
    with open(server_path, "w", encoding="utf-8") as f:
        f.write(server_code)
    print("[OK] server.py — Added /api/hardware endpoint")
else:
    print("[SKIP] server.py — Hardware endpoint exists")


# ──────────────────────────────────────────────────────────────────
# 2. FRONTEND: Add hardware status bar to HTML
# ──────────────────────────────────────────────────────────────────

html_path = r"c:\Users\Sam Deiter\Documents\GitHub\LocalMind\frontend\index.html"
with open(html_path, "r", encoding="utf-8") as f:
    html_code = f.read()

# Add status bar above chat input
status_bar = '''        <div id="hardwareStatus" class="hardware-status hidden">
            <div class="hw-indicator">
                <div class="hw-spinner"></div>
                <span id="hwText">Loading model...</span>
            </div>
            <div class="hw-bar-container">
                <div id="hwBar" class="hw-bar"></div>
            </div>
            <span id="hwVram" class="hw-vram"></span>
        </div>'''

# Insert above the input area
if 'id="hardwareStatus"' not in html_code:
    html_code = html_code.replace(
        '<div class="input-area"',
        status_bar + '\n        <div class="input-area"'
    )
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_code)
    print("[OK] index.html — Added hardware status bar")
else:
    print("[SKIP] index.html — Status bar exists")


# ──────────────────────────────────────────────────────────────────
# 3. FRONTEND: Add JS polling logic
# ──────────────────────────────────────────────────────────────────

app_path = r"c:\Users\Sam Deiter\Documents\GitHub\LocalMind\frontend\app.js"
with open(app_path, "r", encoding="utf-8") as f:
    app_code = f.read()

hw_func = '''
// ── Hardware Status (Model Loading Indicator) ───────────────────
let hwPolling = null;
let lastModelLoaded = null;

async function checkHardware() {
  const bar = document.getElementById("hardwareStatus");
  if (!bar) return;

  try {
    const r = await fetch(`${API}/api/hardware`);
    const d = await r.json();

    if (d.models && d.models.length > 0) {
      const m = d.models[0];
      const vramPct = m.size_gb > 0 ? Math.round((m.vram_gb / m.size_gb) * 100) : 100;
      document.getElementById("hwText").textContent = `${m.name} loaded`;
      document.getElementById("hwBar").style.width = `${vramPct}%`;
      document.getElementById("hwVram").textContent = `${m.vram_gb}GB VRAM`;
      bar.classList.remove("hidden");
      bar.classList.add("loaded");
      lastModelLoaded = m.name;

      // Auto-hide after 5 seconds when fully loaded
      setTimeout(() => {
        bar.classList.add("hidden");
        bar.classList.remove("loaded");
      }, 5000);
    } else {
      bar.classList.add("hidden");
      lastModelLoaded = null;
    }
  } catch {
    bar.classList.add("hidden");
  }
}

function startHwPolling() {
  const bar = document.getElementById("hardwareStatus");
  if (bar) {
    bar.classList.remove("hidden");
    bar.classList.remove("loaded");
    document.getElementById("hwText").textContent = "Loading model into GPU...";
    document.getElementById("hwBar").style.width = "0%";
    document.getElementById("hwVram").textContent = "";
  }

  // Poll every 2s while loading
  if (hwPolling) clearInterval(hwPolling);
  hwPolling = setInterval(async () => {
    await checkHardware();
    const bar = document.getElementById("hardwareStatus");
    if (bar && bar.classList.contains("loaded")) {
      clearInterval(hwPolling);
      hwPolling = null;
    }
  }, 2000);
}

'''

if "checkHardware" not in app_code:
    app_code = app_code.replace("// ── Boot", hw_func + "// ── Boot")
    print("[OK] app.js — Added hardware status functions")
else:
    print("[SKIP] app.js — Hardware functions exist")

# Trigger hw poll when sending a message (model might need to load)
if "startHwPolling" not in app_code:
    # Patch is not applicable since function already inserted
    pass
else:
    print("[SKIP] app.js — startHwPolling already referenced")

# Call checkHardware on init
if "checkHardware();" not in app_code:
    app_code = app_code.replace("  loadDocuments();", "  loadDocuments();\n  checkHardware();")

# Trigger loading indicator when user sends message
old_send_start = "  sendBtn.disabled = true;"
new_send_start = "  sendBtn.disabled = true;\n    startHwPolling();"  
if "startHwPolling();" not in app_code and old_send_start in app_code:
    app_code = app_code.replace(old_send_start, new_send_start, 1)  # Only first occurrence
    print("[OK] app.js — Trigger loading indicator on send")

with open(app_path, "w", encoding="utf-8") as f:
    f.write(app_code)


# ──────────────────────────────────────────────────────────────────
# 4. FRONTEND: Add CSS for hardware status bar
# ──────────────────────────────────────────────────────────────────

css_path = r"c:\Users\Sam Deiter\Documents\GitHub\LocalMind\frontend\style.css"
with open(css_path, "r", encoding="utf-8") as f:
    css_code = f.read()

hw_css = """
/* Hardware / Model Loading Status */
.hardware-status {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 16px;
    background: rgba(139, 92, 246, 0.08);
    border-top: 1px solid rgba(139, 92, 246, 0.15);
    font-size: 0.75rem;
    color: rgba(255, 255, 255, 0.6);
    transition: all 0.3s ease;
}
.hardware-status.hidden {
    display: none;
}
.hardware-status.loaded {
    background: rgba(34, 197, 94, 0.08);
    border-top-color: rgba(34, 197, 94, 0.15);
}
.hw-indicator {
    display: flex;
    align-items: center;
    gap: 6px;
    white-space: nowrap;
}
.hw-spinner {
    width: 12px;
    height: 12px;
    border: 2px solid rgba(139, 92, 246, 0.3);
    border-top-color: #8b5cf6;
    border-radius: 50%;
    animation: hw-spin 1s linear infinite;
}
.loaded .hw-spinner {
    border-color: rgba(34, 197, 94, 0.5);
    border-top-color: #22c55e;
    animation: none;
}
@keyframes hw-spin {
    to { transform: rotate(360deg); }
}
.hw-bar-container {
    flex: 1;
    height: 4px;
    background: rgba(255, 255, 255, 0.06);
    border-radius: 2px;
    overflow: hidden;
}
.hw-bar {
    height: 100%;
    width: 0%;
    background: linear-gradient(90deg, #8b5cf6, #a78bfa);
    border-radius: 2px;
    transition: width 0.5s ease;
}
.loaded .hw-bar {
    background: linear-gradient(90deg, #22c55e, #4ade80);
}
.hw-vram {
    font-size: 0.7rem;
    color: rgba(255, 255, 255, 0.4);
    white-space: nowrap;
}
"""

if ".hardware-status" not in css_code:
    css_code += hw_css
    with open(css_path, "w", encoding="utf-8") as f:
        f.write(css_code)
    print("[OK] style.css — Added hardware status styles")
else:
    print("[SKIP] style.css — Hardware styles exist")


print("\n✅ Model loading indicator complete!")
