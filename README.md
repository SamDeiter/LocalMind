# 🧠 LocalMind

> Your private, local AI assistant that thinks, codes, and improves itself — all on your machine.

Everything runs **locally**. No cloud, no subscriptions, no data leaving your PC. Powered by [Ollama](https://ollama.com) for local LLMs, [ChromaDB](https://www.trychroma.com) for persistent memory, and an **Autonomy Engine** that reflects, researches, proposes, and executes code improvements without human intervention.

**Current Version:** v0.6.0 · Phase 7 — Autonomy

---

## ✨ Features

| Feature                        | Description                                                                   |
| ------------------------------ | ----------------------------------------------------------------------------- |
| 🤖 **Autonomy Engine**         | Self-improving AI — reflects on codebase, proposes fixes, executes edits      |
| 🧠 **Metacognition**           | Uncertainty gating, intent parsing, self-checking, and calibration            |
| 📊 **Action Stream**           | Live pipeline of proposed/approved/completed/failed tasks with full reasoning |
| ⚡ **Multi-Model Router**       | Routes tasks to fast 7B, deep 14B, or cloud Gemini based on complexity        |
| 🔬 **Research Engine**         | Background web + academic research fed into reflection prompts                |
| 🗣️ **Voice Output**            | Selectable voices via Web Speech API                                          |
| 📷 **Camera/Vision**           | Capture images from webcam and ask the AI to analyze them                     |
| 🧠 **Long-term Memory**        | Remembers facts across conversations (ChromaDB + embeddings)                  |
| 🔍 **Web Search**              | Multi-provider: DuckDuckGo → Google → Brave (auto-fallback)                  |
| 📸 **Screenshots**             | Takes screenshots and describes what it sees                                  |
| 📋 **Clipboard**               | Reads clipboard contents on command                                           |
| 💻 **Code Execution**          | Runs Python safely with timeout and blocklist protections                     |
| 📁 **File Operations**         | Reads, writes, and lists files — sandboxed to `~/LocalMind_Workspace`         |
| 🖥️ **Integrated Code Editor**  | Monaco Editor (VS Code engine) with file tree, Ctrl+S, Send to AI            |
| 📊 **Hardware Dashboard**      | Live CPU, RAM, VRAM monitoring in status bar                                  |
| 📄 **Document RAG**            | Index files and ask questions about your documents                            |
| 📱 **PWA**                     | Install on your phone's home screen for mobile access                         |
| 🌐 **Remote Access**           | Access from anywhere via Tailscale (WireGuard encryption)                     |
| 🔒 **Infrastructure Guard**    | Critical files protected from self-edit; confidence gating on proposals       |
| 📈 **Success Tracking**        | Engine tracks edit success/failure rates to improve over time                 |

## 🏗️ Architecture

```
LocalMind/
├── backend/
│   ├── server.py              # FastAPI app, route mounting, startup
│   ├── agent.py               # Core AI agent with tool-calling
│   ├── config.py              # Centralized configuration
│   ├── model_router.py        # Routes to 7B/14B/Gemini by task complexity
│   ├── proposals.py           # Proposal lifecycle (create, dedup, approve, execute)
│   ├── code_editor.py         # AI-driven code editing (search/replace diffs)
│   ├── git_ops.py             # Git operations (commit, branch, revert)
│   ├── gemini_client.py       # Optional Gemini cloud fallback (PII-scrubbed)
│   ├── self_improver.py       # Self-improvement logic
│   ├── meta_critic.py         # Meta-analysis of engine decisions
│   ├── priority_queue.py      # User priority directives
│   ├── autonomy/
│   │   ├── engine.py          # Main autonomy engine (reflection, research, execution)
│   │   ├── execution.py       # Proposal execution pipeline
│   │   ├── reflection.py      # Codebase self-reflection
│   │   ├── task_executor.py   # General task executor (research, docs)
│   │   ├── config.py          # Engine constants
│   │   └── loops/
│   │       ├── health.py      # Self-healing monitor (auto-restart, pre-warm)
│   │       ├── reflection.py  # Periodic reflection scheduler
│   │       ├── research.py    # Background research scheduler
│   │       ├── execution.py   # Execution loop scheduler
│   │       └── digest.py      # Daily summary generation
│   ├── logic/
│   │   ├── llm_client.py      # Ollama streaming client with retry
│   │   ├── chat_service.py    # Chat orchestration and tool dispatch
│   │   ├── prompt_factory.py  # System prompt construction
│   │   ├── token_manager.py   # Context window management
│   │   └── summarizer.py      # Conversation summarization
│   ├── metacognition/
│   │   ├── controller.py      # Metacognition orchestrator
│   │   ├── intent_parser.py   # User intent classification
│   │   ├── uncertainty_gate.py # Confidence-based routing
│   │   ├── self_checker.py    # Output quality validation
│   │   ├── calibration.py     # Model calibration tracking
│   │   ├── tool_router.py     # Smart tool selection
│   │   ├── memory_manager.py  # Memory relevance scoring
│   │   ├── ui_generator.py    # Dynamic UI generation
│   │   └── revision_controller.py # Edit revision logic
│   ├── research/
│   │   ├── web.py             # Web research (academic + general)
│   │   ├── scanner.py         # Codebase complexity/smell scanner
│   │   └── analyzer.py        # Failure analysis + success tracking
│   ├── tools/                 # 15+ plugin-based AI tools
│   │   ├── base.py            # Abstract tool base class
│   │   ├── registry.py        # Auto-discovers and routes tools
│   │   ├── web_search.py      # Multi-provider search
│   │   ├── file_tools.py      # Sandboxed read/write/list
│   │   ├── run_code.py        # Python execution with safety
│   │   ├── memory.py          # ChromaDB vector memory
│   │   ├── rag.py             # Document RAG (index + query)
│   │   ├── vision.py          # Image analysis via Ollama
│   │   ├── screenshot.py      # Screen capture
│   │   ├── clipboard.py       # Clipboard access
│   │   ├── git_tools.py       # Git status, diff, commit, log
│   │   ├── self_edit.py       # AI self-editing capability
│   │   ├── self_reflect.py    # Self-reflection tool
│   │   ├── self_test.py       # Self-testing tool
│   │   ├── propose_action.py  # Proposal creation tool
│   │   ├── generate_ui.py     # Dynamic UI generation
│   │   ├── mcp_browser.py     # MCP browser integration
│   │   ├── manage_model.py    # Ollama model management
│   │   ├── dependency_manager.py # Package dependency tool
│   │   └── project_context.py # Project structure context
│   └── routes/                # API endpoints
│       ├── chat.py            # /api/chat SSE streaming
│       ├── autonomy_routes.py # /api/autonomy/* engine control
│       ├── research_routes.py # /api/research/* endpoints
│       ├── conversations.py   # /api/conversations CRUD
│       ├── documents.py       # /api/documents RAG endpoints
│       ├── files.py           # /api/files sandboxed ops
│       ├── memory.py          # /api/memory CRUD
│       ├── settings.py        # /api/settings
│       ├── system.py          # /api/system health + metrics
│       └── tools.py           # /api/tools execution
├── frontend/
│   ├── index.html             # SPA shell + all UI panels
│   ├── app.js                 # Entry point, module init
│   ├── style.css              # Premium dark theme
│   ├── manifest.json          # PWA manifest
│   ├── sw.js                  # Service worker for offline
│   └── modules/
│       ├── autonomy_ui.js     # Brain dashboard + action stream
│       ├── chat.js            # Chat interface + SSE streaming
│       ├── editor.js          # Monaco code editor
│       ├── events.js          # DOM event binding
│       ├── state.js           # Shared state + DOM refs
│       ├── settings_ui.js     # Settings modal
│       ├── sidebar.js         # Sidebar navigation
│       ├── dashboard.js       # Hardware dashboard
│       ├── conversations.js   # Conversation management
│       ├── proposals_ui.js    # Proposal review UI
│       ├── research_ui.js     # Research results UI
│       ├── media.js           # Camera/voice/media
│       ├── utils.js           # Shared utilities
│       └── live_reload.js     # Dev hot-reload
├── docs/
│   ├── PROGRESS.md            # Development progress & roadmap
│   ├── FEATURES.md            # Feature request backlog
│   ├── BUGS.md                # Bug tracker
│   ├── TASK.md                # Phase completion tracking
│   ├── PLAN.md                # Architecture planning
│   ├── RESEARCH.md            # Research notes
│   ├── MARKET_RESEARCH.md     # Competitive analysis
│   ├── BUSINESS_STRATEGY.md   # Business plan
│   └── audits/                # Code audit reports
├── scripts/
│   ├── bump_build.py          # Auto-increment build number
│   └── generate_docs.py       # API doc generator
├── tests/                     # Jest + pytest test suites
├── install.ps1                # One-click installer (Ollama + models + venv)
├── uninstall.ps1              # Clean uninstaller
├── LocalMind.bat              # Launch script
├── run.py                     # Python entry point
├── pyproject.toml             # Python project config
├── requirements.txt           # Production dependencies
└── requirements-dev.txt       # Dev dependencies
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

```cmd
.\LocalMind.bat
```

The launcher opens **http://localhost:8001** automatically.

### 3. Mobile Access (Optional)

1. Install [Tailscale](https://tailscale.com) on your PC and phone
2. Start LocalMind on your PC
3. Open `http://<your-tailscale-ip>:8001` on your phone
4. Tap "Add to Home Screen" for the PWA experience

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

    async def execute(self, **kwargs):
        return {"success": True, "result": f"Processed: {kwargs.get('input', '')}"}
```

Drop it in `backend/tools/` and restart. That's it.

## 🤖 Autonomy Engine

The engine runs continuous background loops:

| Loop | Interval | Purpose |
|------|----------|---------|
| **Reflection** | Every 15 min | Scans codebase for issues, proposes improvements |
| **Research** | Every 30 min | Web + academic research to inform proposals |
| **Execution** | Continuous | Executes approved proposals (branch → edit → test → merge) |
| **Health** | Every 5 min | Self-healing: auto-restart, model pre-warming |
| **Digest** | Daily | Generates daily summary of engine activity |

### Safety Features
- **Confidence gating** — Proposals below 30% confidence are auto-denied
- **Infrastructure protection** — Critical files (`llm_client.py`, `code_editor.py`, etc.) blocked from self-edit
- **Git branching** — All edits happen on feature branches, only merged after tests pass
- **Max retries** — Failed proposals capped at 5 retries before permanent archival
- **Deduplication** — Synonym-aware title matching prevents duplicate proposals

## 🔒 Safety

- **No file deletion** — Write and list only, no delete capability
- **Sandboxed workspace** — All file ops restricted to `~/LocalMind_Workspace`
- **Code execution safety** — Timeout (30s), blocked imports (`os.system`, `subprocess`, `shutil.rmtree`)
- **Pausable learning** — Toggle memory recording on/off from the UI
- **Local-first** — All data stays on your machine
- **Port guard** — Server auto-kills duplicate processes on startup
- **Infrastructure guard** — Engine cannot modify its own core runtime files

## 📦 Dependencies

All open-source and free:

| Package                                                     | Purpose                          |
| ----------------------------------------------------------- | -------------------------------- |
| [Ollama](https://ollama.com)                                | Local LLM inference              |
| [FastAPI](https://fastapi.tiangolo.com)                     | Backend web framework            |
| [ChromaDB](https://www.trychroma.com)                       | Vector database for memories     |
| [Monaco Editor](https://microsoft.github.io/monaco-editor/) | VS Code editor engine (CDN)      |
| [mss](https://pypi.org/project/mss/)                        | Screenshot capture               |
| [Pillow](https://pillow.readthedocs.io)                     | Image processing                 |
| [pyperclip](https://pypi.org/project/pyperclip/)            | Clipboard access                 |
| [httpx](https://www.python-httpx.org)                       | Async HTTP client                |

## 📄 License

MIT — do whatever you want with it.
