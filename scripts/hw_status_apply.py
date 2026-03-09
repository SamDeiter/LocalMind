"""
Model Loading + System Hardware Dashboard
Adds:
1. GET /api/hardware - Ollama model status + system CPU/RAM/GPU metrics
2. Hardware status bar + system dashboard in UI
3. Polling logic for real-time updates
"""

# ──────────────────────────────────────────────────────────────────
# 1. BACKEND: Add hardware + system status endpoint
# ──────────────────────────────────────────────────────────────────

server_path = r"c:\Users\Sam Deiter\Documents\GitHub\LocalMind\backend\server.py"
with open(server_path, "r", encoding="utf-8") as f:
    server_code = f.read()

hw_endpoint = '''
@app.get("/api/hardware")
async def hardware_status():
    """Get loaded models, VRAM usage, and system metrics."""
    import httpx
    import psutil

    # System metrics
    cpu_pct = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    ram_used = round(mem.used / (1024**3), 1)
    ram_total = round(mem.total / (1024**3), 1)

    system = {
        "cpu_percent": cpu_pct,
        "ram_used_gb": ram_used,
        "ram_total_gb": ram_total,
        "ram_percent": mem.percent,
    }

    # GPU metrics via Ollama /api/ps
    models = []
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_BASE_URL}/api/ps")
            data = r.json()
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
    except Exception:
        pass

    return {
        "loaded": len(models) > 0,
        "models": models,
        "system": system,
    }

'''

marker = "# ── Document RAG"
fallback = "# ── Serve Frontend"
if "/api/hardware" not in server_code:
    target = marker if marker in server_code else fallback
    server_code = server_code.replace(target, hw_endpoint + target)
    with open(server_path, "w", encoding="utf-8") as f:
        f.write(server_code)
    print("[OK] server.py — Added /api/hardware endpoint with system metrics")
else:
    print("[SKIP] server.py — Hardware endpoint already exists")


# ──────────────────────────────────────────────────────────────────
# 2. FRONTEND: Add status bar + dashboard to HTML
# ──────────────────────────────────────────────────────────────────

html_path = r"c:\Users\Sam Deiter\Documents\GitHub\LocalMind\frontend\index.html"
with open(html_path, "r", encoding="utf-8") as f:
    html_code = f.read()

status_bar = '''        <div id="hardwareStatus" class="hardware-status">
            <div class="hw-metrics">
                <div class="hw-metric">
                    <span class="hw-metric-label">CPU</span>
                    <div class="hw-metric-bar"><div id="cpuBar" class="hw-fill"></div></div>
                    <span id="cpuVal" class="hw-metric-val">0%</span>
                </div>
                <div class="hw-metric">
                    <span class="hw-metric-label">RAM</span>
                    <div class="hw-metric-bar"><div id="ramBar" class="hw-fill"></div></div>
                    <span id="ramVal" class="hw-metric-val">0/0 GB</span>
                </div>
                <div class="hw-metric" id="modelMetric">
                    <span class="hw-metric-label" id="modelLabel">Model</span>
                    <div class="hw-metric-bar"><div id="vramBar" class="hw-fill hw-fill-purple"></div></div>
                    <span id="vramVal" class="hw-metric-val">—</span>
                </div>
            </div>
        </div>'''

if 'id="hardwareStatus"' not in html_code:
    html_code = html_code.replace(
        '<div class="input-area"',
        status_bar + '\n        <div class="input-area"'
    )
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_code)
    print("[OK] index.html — Added hardware dashboard")
else:
    print("[SKIP] index.html — Dashboard already exists")


# ──────────────────────────────────────────────────────────────────
# 3. FRONTEND: Add JS polling logic
# ──────────────────────────────────────────────────────────────────

app_path = r"c:\Users\Sam Deiter\Documents\GitHub\LocalMind\frontend\app.js"
with open(app_path, "r", encoding="utf-8") as f:
    app_code = f.read()

hw_func = '''
// ── Hardware Dashboard ──────────────────────────────────────────
let hwInterval = null;

async function pollHardware() {
  try {
    const r = await fetch(`${API}/api/hardware`);
    const d = await r.json();

    // CPU
    const cpuBar = document.getElementById("cpuBar");
    const cpuVal = document.getElementById("cpuVal");
    if (cpuBar && d.system) {
      cpuBar.style.width = `${d.system.cpu_percent}%`;
      cpuVal.textContent = `${Math.round(d.system.cpu_percent)}%`;
      cpuBar.className = `hw-fill ${d.system.cpu_percent > 80 ? "hw-fill-red" : d.system.cpu_percent > 50 ? "hw-fill-yellow" : "hw-fill-green"}`;
    }

    // RAM
    const ramBar = document.getElementById("ramBar");
    const ramVal = document.getElementById("ramVal");
    if (ramBar && d.system) {
      ramBar.style.width = `${d.system.ram_percent}%`;
      ramVal.textContent = `${d.system.ram_used_gb}/${d.system.ram_total_gb} GB`;
      ramBar.className = `hw-fill ${d.system.ram_percent > 85 ? "hw-fill-red" : d.system.ram_percent > 60 ? "hw-fill-yellow" : "hw-fill-green"}`;
    }

    // Model / VRAM
    const vramBar = document.getElementById("vramBar");
    const vramVal = document.getElementById("vramVal");
    const modelLabel = document.getElementById("modelLabel");
    if (vramBar && d.models && d.models.length > 0) {
      const m = d.models[0];
      const pct = m.size_gb > 0 ? Math.round((m.vram_gb / m.size_gb) * 100) : 0;
      vramBar.style.width = `${pct}%`;
      vramVal.textContent = `${m.vram_gb} GB VRAM`;
      modelLabel.textContent = m.name.split(":")[0];
      vramBar.className = "hw-fill hw-fill-purple";
    } else {
      vramBar.style.width = "0%";
      vramVal.textContent = "No model loaded";
      modelLabel.textContent = "Model";
      vramBar.className = "hw-fill hw-fill-dim";
    }
  } catch { /* ignore */ }
}

function startHwPolling() {
  if (hwInterval) return;
  pollHardware();
  hwInterval = setInterval(pollHardware, 3000);
}

'''

if "pollHardware" not in app_code:
    app_code = app_code.replace("// ── Boot", hw_func + "// ── Boot")
    print("[OK] app.js — Added hardware dashboard functions")
else:
    print("[SKIP] app.js — Hardware functions exist")

# Call on init
if "startHwPolling();" not in app_code:
    app_code = app_code.replace("  loadDocuments();", "  loadDocuments();\n  startHwPolling();")
    print("[OK] app.js — Start hardware polling on init")

with open(app_path, "w", encoding="utf-8") as f:
    f.write(app_code)


# ──────────────────────────────────────────────────────────────────
# 4. FRONTEND: CSS for hardware dashboard
# ──────────────────────────────────────────────────────────────────

css_path = r"c:\Users\Sam Deiter\Documents\GitHub\LocalMind\frontend\style.css"
with open(css_path, "r", encoding="utf-8") as f:
    css_code = f.read()

hw_css = """
/* Hardware Dashboard */
.hardware-status {
    display: flex;
    padding: 6px 16px;
    background: rgba(15, 15, 25, 0.5);
    border-top: 1px solid rgba(255, 255, 255, 0.04);
}
.hw-metrics {
    display: flex;
    gap: 16px;
    width: 100%;
    align-items: center;
}
.hw-metric {
    display: flex;
    align-items: center;
    gap: 6px;
    flex: 1;
}
.hw-metric-label {
    font-size: 0.65rem;
    color: rgba(255, 255, 255, 0.35);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    min-width: 32px;
}
.hw-metric-bar {
    flex: 1;
    height: 4px;
    background: rgba(255, 255, 255, 0.06);
    border-radius: 2px;
    overflow: hidden;
}
.hw-fill {
    height: 100%;
    width: 0%;
    border-radius: 2px;
    transition: width 0.5s ease, background 0.3s;
}
.hw-fill-green { background: linear-gradient(90deg, #22c55e, #4ade80); }
.hw-fill-yellow { background: linear-gradient(90deg, #eab308, #facc15); }
.hw-fill-red { background: linear-gradient(90deg, #ef4444, #f87171); }
.hw-fill-purple { background: linear-gradient(90deg, #8b5cf6, #a78bfa); }
.hw-fill-dim { background: rgba(255, 255, 255, 0.1); }
.hw-metric-val {
    font-size: 0.65rem;
    color: rgba(255, 255, 255, 0.4);
    min-width: 60px;
    text-align: right;
    white-space: nowrap;
}
"""

if ".hw-metrics" not in css_code:
    css_code += hw_css
    with open(css_path, "w", encoding="utf-8") as f:
        f.write(css_code)
    print("[OK] style.css — Added hardware dashboard styles")
else:
    print("[SKIP] style.css — Hardware styles exist")


print("\n✅ Hardware dashboard complete!")
