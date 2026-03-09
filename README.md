# 🧠 LocalMind

> Your private, local AI assistant that talks like a person — not a chatbot.

Everything runs on **your machine**. No cloud, no subscriptions, no data leaving your PC. Powered by [Ollama](https://ollama.com) for local LLMs and [ChromaDB](https://www.trychroma.com) for persistent memory.

---

## ✨ Features

| Feature                  | Description                                                                   |
| ------------------------ | ----------------------------------------------------------------------------- |
| 🗣️ **Voice Output**      | LocalMind speaks to you using selectable voices (Web Speech API)              |
| 📷 **Camera/Vision**     | Capture images from your webcam and ask the AI to analyze them                |
| 🧠 **Long-term Memory**  | Remembers facts about you across conversations (ChromaDB + Ollama embeddings) |
| 🔍 **Web Search**        | Searches the web via DuckDuckGo Lite — no API key needed                      |
| 📸 **Screenshots**       | Takes screenshots of your screen and describes what it sees                   |
| 📋 **Clipboard**         | Reads your clipboard contents on command                                      |
| 💻 **Code Execution**    | Runs Python code safely with timeout and blocklist protections                |
| 📁 **File Operations**   | Reads, writes, and lists files — sandboxed to `~/LocalMind_Workspace`         |
| 🚫 **No File Deletion**  | Safety first — LocalMind can never delete your files                          |
| 🔒 **Pausable Learning** | Toggle memory on/off from the sidebar                                         |
| 📱 **PWA**               | Install on your phone's home screen for mobile access                         |
| 🌐 **Remote Access**     | Access from anywhere via Tailscale (WireGuard encryption)                     |

## 🏗️ Architecture

```
LocalMind/
├── backend/
│   ├── server.py          # FastAPI server with agent loop
│   ├── conversations.db   # SQLite for chat history
│   └── tools/             # Plugin-based tool system
│       ├── base.py        # Abstract base class for tools
│       ├── registry.py    # Auto-discovers and routes tools
│       ├── web_search.py  # DuckDuckGo Lite search
│       ├── file_tools.py  # Sandboxed read/write/list
│       ├── run_code.py    # Python execution with safety
│       ├── memory.py      # ChromaDB vector memory
│       ├── vision.py      # Image analysis via Ollama
│       ├── screenshot.py  # Screen capture
│       └── clipboard.py   # Clipboard access
├── frontend/
│   ├── index.html         # PWA shell with camera modal
│   ├── style.css          # Premium dark theme + glassmorphism
│   ├── app.js             # SSE streaming, voice, camera, tools
│   ├── manifest.json      # PWA manifest
│   └── sw.js              # Service worker for offline caching
├── install.ps1            # One-click installer (Ollama + models + venv)
├── start.ps1              # Launch script
└── requirements.txt       # Python dependencies
```

## 🚀 Quick Start

### 1. Install

```powershell
# Clone the repo
git clone https://github.com/SamDeiter/LocalMind.git
cd LocalMind

# Run the installer (downloads Ollama, models, creates venv)
.\install.ps1
```

### 2. Start

```powershell
.\start.ps1
```

Then open **http://localhost:8000** in your browser.

### 3. Mobile Access (Optional)

1. Install [Tailscale](https://tailscale.com) on your PC and phone
2. Start LocalMind on your PC
3. Open `http://<your-tailscale-ip>:8000` on your phone
4. Tap "Add to Home Screen" in your browser for the PWA experience

## 🔧 Tool Plugin System

LocalMind uses a drop-in plugin architecture. Every `.py` file in `backend/tools/` that extends `BaseTool` is automatically discovered and available to the AI.

### Creating a Custom Tool

```python
# backend/tools/my_tool.py
from backend.tools.base import BaseTool

class MyTool(BaseTool):
    name = "my_tool"
    description = "Does something cool"
    parameters = {
        "input": {"type": "string", "description": "What to process"}
    }

    def execute(self, **kwargs):
        return f"Processed: {kwargs.get('input', '')}"
```

Drop it in `backend/tools/` and restart the server. That's it.

## 🔒 Safety

- **No file deletion** — `write_file` and `list_files` only, no delete capability
- **Sandboxed workspace** — All file ops restricted to `~/LocalMind_Workspace`
- **Code execution safety** — Timeout (30s), blocked imports (`os.system`, `subprocess`, `shutil.rmtree`)
- **Pausable learning** — Toggle memory recording on/off from the UI
- **Local-first** — All data stays on your machine

## 📦 Dependencies

All open-source and free:

| Package                                          | Purpose                      |
| ------------------------------------------------ | ---------------------------- |
| [Ollama](https://ollama.com)                     | Local LLM inference          |
| [FastAPI](https://fastapi.tiangolo.com)          | Backend web framework        |
| [ChromaDB](https://www.trychroma.com)            | Vector database for memories |
| [mss](https://pypi.org/project/mss/)             | Screenshot capture           |
| [Pillow](https://pillow.readthedocs.io)          | Image processing             |
| [pyperclip](https://pypi.org/project/pyperclip/) | Clipboard access             |
| [httpx](https://www.python-httpx.org)            | Async HTTP client            |

## 📄 License

MIT — do whatever you want with it.
