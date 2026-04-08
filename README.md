# 🧠 LocalMind

> An autonomous task worker that thinks, executes, and delivers — powered by local AI.

LocalMind is an **enterprise-ready autonomous task worker** (not a chat assistant). It accepts jobs via Slack or the web UI, plans multi-step execution, runs tools, reviews its own output with 3-tier QA, and delivers artifacts back — all on your infrastructure. Powered by [Ollama](https://ollama.com) for local LLMs, [ChromaDB](https://www.trychroma.com) for persistent memory, and a durable **Job Pipeline** with RBAC, policy enforcement, and human approval workflows.

**Current Version:** v0.7.0 · Phase 8 — Enterprise (Complete)

---

## ✨ Features

### Core Intelligence

| Feature                        | Description                                                                   |
| ------------------------------ | ----------------------------------------------------------------------------- |
| 🤖 **Autonomy Engine**         | Self-improving AI — reflects on codebase, proposes fixes, executes edits      |
| 🧠 **Metacognition**           | Uncertainty gating, intent parsing, self-checking, and calibration            |
| 📊 **Action Stream**           | Live pipeline of proposed/approved/completed/failed tasks with full reasoning |
| ⚡ **Multi-Model Router**       | Routes tasks to fast 7B, deep 14B, or cloud Gemini based on complexity        |
| 🔬 **Research Engine**         | Background web + academic research fed into reflection prompts                |
| 🧠 **Long-term Memory**        | Remembers facts across conversations (ChromaDB + embeddings)                  |
| 📈 **Success Tracking**        | Engine tracks edit success/failure rates to improve over time                 |

### Enterprise Job Pipeline

| Feature                        | Description                                                                   |
| ------------------------------ | ----------------------------------------------------------------------------- |
| 📋 **Job Queue**               | SQLite-backed durable job queue with planning, execution, and review stages   |
| 🚂 **Train Model Planner**     | Breaks jobs into node graphs — plans before executing                         |
| 🔍 **3-Tier QA Reviewer**      | Format checks → LLM self-critique → human approval before delivery            |
| 💬 **Slack Bot Integration**    | Submit jobs, get status updates, and receive artifacts directly in Slack       |
| ✅ **Approval Workflows**       | Human-in-the-loop approval gates for sensitive operations                     |
| 📄 **Job Templates**           | Saved templates for recurring task types                                      |

### Enterprise Infrastructure

| Feature                        | Description                                                                   |
| ------------------------------ | ----------------------------------------------------------------------------- |
| 🏢 **Multi-Org Identity**      | Organizations, workspaces, users with full RBAC                               |
| 🔐 **Secret Manager**          | AES-256-GCM encrypted credential storage                                     |
| 📜 **Policy Engine**           | Approve/deny/dry-run rules for tool calls                                    |
| 🔄 **Durable Execution**       | Attempt tracking, idempotency keys, heartbeats, fault recovery               |
| 📦 **Artifact Versioning**     | Provenance chains with PPTX-specific stable anchors                          |
| 🔎 **Evidence Tracking**       | Facts linked to source data for auditability                                 |
| 💰 **Token Budgeting**         | Per-job and per-org cost tracking and limits                                 |
| 📊 **Telemetry & Audit**       | Full audit logging of all operations                                         |

### Google Workspace Tools

| Feature                        | Description                                                                   |
| ------------------------------ | ----------------------------------------------------------------------------- |
| 📝 **Google Docs**             | Read, create, insert, replace, and append text in Google Docs                 |
| 📁 **Google Drive**            | List, search, upload, download, move, and copy files                          |
| 📊 **Google Sheets**           | Full Sheets API — read/write ranges, formatting, formulas (14 operations)     |
| 📰 **Google Slides**           | Read, create, edit, and manage presentations                                  |
| 📧 **Gmail**                   | Search, read, and manage email                                               |

### Desktop & Developer Tools

| Feature                        | Description                                                                   |
| ------------------------------ | ----------------------------------------------------------------------------- |
| 🗣️ **Voice Output**            | Selectable voices via Web Speech API                                          |
| 📷 **Camera/Vision**           | Capture images from webcam and ask the AI to analyze them                     |
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

### Office Document Tools

| Feature                        | Description                                                                   |
| ------------------------------ | ----------------------------------------------------------------------------- |
| 📊 **Excel**                   | Read/write Excel spreadsheets locally                                        |
| 📄 **PDF**                     | Extract text and data from PDF files                                         |
| 📰 **PowerPoint**              | Create and edit PPTX presentations                                           |
| 📝 **Word**                    | Read and write DOCX documents                                                |

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
│   ├── core/                  # Enterprise control plane
│   │   ├── identity.py        # Multi-org/workspace/user model with RBAC
│   │   ├── schema.py          # Enterprise DB schema (orgs, workspaces, OAuth, quotas)
│   │   ├── providers.py       # Provider config management (Gemini, Ollama, Slack, Google)
│   │   ├── secret_manager.py  # AES-256-GCM encrypted secret storage
│   │   ├── policy.py          # Policy engine (approve/deny/dry-run tool calls)
│   │   ├── execution.py       # Durable execution (attempts, idempotency, heartbeats)
│   │   ├── artifacts.py       # Artifact versioning + provenance chains
│   │   ├── evidence.py        # Evidence tracking (facts linked to source data)
│   │   ├── feedback.py        # Review feedback + human approval workflows
│   │   ├── eval.py            # Evaluation & success metrics
│   │   ├── eval_runner.py     # Evaluation execution pipeline
│   │   ├── token_budget.py    # Token budgeting & cost tracking
│   │   ├── telemetry.py       # Full audit logging
│   │   ├── scheduler.py       # Job scheduling
│   │   ├── gc.py              # Garbage collection & cleanup
│   │   ├── gpu_manager.py     # GPU resource management
│   │   ├── error_strategy.py  # Error handling & recovery strategies
│   │   └── atomic_io.py       # Atomic file operations
│   ├── jobs/                  # Job pipeline ("The Train")
│   │   ├── models.py          # Job, node, artifact data models
│   │   ├── planner.py         # Plan generation (node graph layout)
│   │   ├── executor.py        # Node execution, tool dispatch, retry
│   │   ├── queue.py           # SQLite-backed job queue
│   │   ├── reviewer.py        # 3-tier QA (format → LLM critique → human)
│   │   └── worker.py          # Worker process lifecycle + heartbeats
│   ├── integrations/          # External service integrations
│   │   └── slack_bot.py       # Slack bot (Socket Mode, job lifecycle sync)
│   ├── inference/             # Advanced inference capabilities
│   │   ├── lora_manager.py    # LoRA adapter management
│   │   ├── model_selector.py  # Optimal model selection by task
│   │   └── best_of_n.py       # Best-of-N generation with PRM scoring
│   ├── security/              # Security layer
│   │   ├── auth.py            # OAuth + JWT authentication
│   │   ├── rbac.py            # Role-based access control
│   │   ├── paths.py           # Path sandboxing (directory traversal prevention)
│   │   ├── memory_encryption.py # Encrypted memory storage
│   │   ├── prompt_guard.py    # Prompt injection detection
│   │   └── recycle_bin.py     # Soft-delete, file recovery
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
│   ├── tools/                 # 30+ plugin-based AI tools
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
│   │   ├── self_extend.py     # AI writes new tool plugins
│   │   ├── propose_action.py  # Proposal creation tool
│   │   ├── generate_ui.py     # Dynamic UI generation
│   │   ├── mcp_browser.py     # MCP browser integration
│   │   ├── manage_model.py    # Ollama model management
│   │   ├── dependency_manager.py # Package dependency tool
│   │   ├── project_context.py # Project structure context
│   │   ├── google_docs_tool.py # Google Docs read/write
│   │   ├── google_drive_tool.py # Google Drive file management
│   │   ├── google_sheets_tool.py # Google Sheets (14 operations)
│   │   ├── google_slides_tool.py # Google Slides read/write/edit
│   │   ├── gmail_tool.py      # Gmail search and management
│   │   ├── excel_tool.py      # Excel spreadsheet operations
│   │   ├── pdf_tool.py        # PDF text extraction
│   │   ├── pptx_tool.py       # PowerPoint creation/editing
│   │   ├── word_tool.py       # Word document operations
│   │   ├── browser_tool.py    # Web browser automation
│   │   ├── terminal.py        # Terminal command execution
│   │   └── ast_analyzer.py    # Python AST analysis
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
│       ├── tools.py           # /api/tools execution
│       ├── jobs.py            # /api/jobs CRUD + pipeline control
│       ├── admin.py           # /api/admin management endpoints
│       ├── google_auth.py     # /api/auth/google OAuth flow
│       ├── swarm_routes.py    # /api/swarm multi-agent coordination
│       └── validation_routes.py # /api/validate input validation
├── frontend/
│   ├── index.html             # SPA shell + all UI panels
│   ├── app.js                 # Entry point, module init
│   ├── styles.css             # Core styles (Tailwind companion)
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
│       ├── live_reload.js     # Dev hot-reload
│       ├── jobs_ui.js         # Job creation, filtering, status monitoring
│       ├── templates_ui.js    # Saved job templates
│       ├── approvals_ui.js    # Human approval workflows
│       ├── swarm_ui.js        # Multi-agent swarm visualization
│       ├── streaming.js       # SSE streaming utilities
│       └── tools.js           # Tool execution UI
├── docs/
│   ├── PROGRESS.md            # Development progress & roadmap
│   ├── FEATURES.md            # Feature request backlog
│   ├── BUGS.md                # Bug tracker
│   ├── TASK.md                # Phase completion tracking
│   ├── PLAN.md                # Enterprise architecture planning
│   ├── RESEARCH.md            # Research notes
│   ├── MARKET_RESEARCH.md     # Competitive analysis
│   ├── BUSINESS_STRATEGY.md   # Business plan
│   └── audits/                # Code audit reports
├── tests/                     # pytest test suites (2,000+ tests)
├── scripts/
│   ├── bump_build.py          # Auto-increment build number
│   └── generate_docs.py       # API doc generator
├── Dockerfile                 # Docker image
├── docker-compose.yml         # Docker Compose setup
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

The launcher opens `http://localhost:8001` automatically.

### 3. Docker (Alternative)

```bash
docker-compose up -d
```

### 4. Mobile Access (Optional)

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

## 🔒 Safety & Security

- **OAuth + JWT authentication** — Secure login via Google OAuth with JWT session tokens
- **Role-based access control** — Org admin, workspace member, viewer roles with granular permissions
- **Encrypted secrets** — AES-256-GCM encrypted credential storage (never plaintext)
- **Policy engine** — Approve/deny/dry-run rules for tool calls before execution
- **Prompt injection guard** — Detects and blocks prompt injection attempts
- **Path sandboxing** — Directory traversal prevention on all file operations
- **Recycle bin** — Soft-delete with recovery instead of permanent deletion
- **Memory encryption** — Encrypted memory storage at rest
- **Sandboxed workspace** — All file ops restricted to `~/LocalMind_Workspace`
- **Code execution safety** — Timeout (30s), blocked imports (`os.system`, `subprocess`, `shutil.rmtree`)
- **Infrastructure guard** — Engine cannot modify its own core runtime files
- **Git branching** — All edits happen on feature branches, only merged after tests pass
- **Confidence gating** — Proposals below 30% confidence are auto-denied
- **Audit logging** — Full telemetry of all operations for compliance

## 🐳 Deployment

### Local (Development)

```powershell
.\install.ps1    # Downloads Ollama, models, creates venv
.\LocalMind.bat  # Starts server at http://localhost:8001
```

### Docker

```bash
docker-compose up -d
```

### Slack Integration

LocalMind connects to Slack via Socket Mode. Users can submit jobs, check status, and receive delivered artifacts directly in Slack channels.

## 📦 Dependencies

All open-source and free:

| Package | Purpose |
|---|---|
| [Ollama](https://ollama.com) | Local LLM inference |
| [FastAPI](https://fastapi.tiangolo.com) | Backend web framework |
| [ChromaDB](https://www.trychroma.com) | Vector database for memories |
| [Monaco Editor](https://microsoft.github.io/monaco-editor/) | VS Code editor engine (CDN) |
| [slack-sdk](https://slack.dev/python-slack-sdk/) | Slack bot integration |
| [google-api-python-client](https://github.com/googleapis/google-api-python-client) | Google Workspace APIs |
| [python-jose](https://github.com/mpdavis/python-jose) | JWT token handling |
| [cryptography](https://cryptography.io) | AES-256-GCM secret encryption |
| [python-pptx](https://python-pptx.readthedocs.io) | PowerPoint generation |
| [python-docx](https://python-docx.readthedocs.io) | Word document operations |
| [openpyxl](https://openpyxl.readthedocs.io) | Excel spreadsheet operations |
| [PyPDF2](https://pypdf2.readthedocs.io) | PDF text extraction |
| [httpx](https://www.python-httpx.org) | Async HTTP client |
| [mss](https://pypi.org/project/mss/) | Screenshot capture |
| [Pillow](https://pillow.readthedocs.io) | Image processing |

## 📄 License

MIT — do whatever you want with it.
