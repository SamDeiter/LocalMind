# LocalMind

> An autonomous task worker that thinks, executes, and delivers — powered by local AI.

LocalMind is an **autonomous task worker** (not a chat assistant). It accepts jobs via Slack or the web UI, plans multi-step execution, runs tools, reviews its own output with 3-tier QA, and delivers artifacts back — all on your infrastructure. Powered by [Ollama](https://ollama.com) for local LLMs, [ChromaDB](https://www.trychroma.com) for persistent memory, and a durable **Job Pipeline** with RBAC, policy enforcement, and human approval workflows.

**Current Version:** v0.9.1 · Phase 9 — Intelligence

---

## Features

### Core Intelligence

| Feature | Description |
| --- | --- |
| **Autonomy Engine** | Self-improving AI — reflects on codebase, proposes fixes, executes edits |
| **Metacognition** | Uncertainty gating, intent parsing, self-checking, and calibration |
| **Action Stream** | Live pipeline of proposed/approved/completed/failed tasks with full reasoning |
| **Multi-Model Router** | Routes tasks to fast 7B, deep 14B, or cloud Gemini based on complexity |
| **Research Engine** | Background web + academic research fed into reflection prompts |
| **Long-term Memory** | Remembers facts across conversations (ChromaDB + embeddings + session cache) |
| **Self-Discovery** | AI discovers its own capabilities and builds an identity profile over time |
| **Skill Learning** | Learns new skills from interactions and stores them for reuse |
| **Knowledge Graph** | Builds and queries a knowledge graph of learned concepts |
| **Time Machine** | Record and replay past agent states for debugging and analysis |

### Job Pipeline

| Feature | Description |
| --- | --- |
| **Job Queue** | SQLite-backed durable job queue with planning, execution, and review stages |
| **Node Graph Planner** | Breaks jobs into node graphs — plans before executing |
| **3-Tier QA Reviewer** | Format checks → LLM self-critique → human approval before delivery |
| **Slack Bot Integration** | Submit jobs, get status updates, and receive artifacts directly in Slack |
| **Approval Workflows** | Human-in-the-loop approval gates for sensitive operations |
| **Job Templates** | Saved templates for recurring task types |
| **SMS/Push Notifications** | Twilio SMS alerts and push notifications for job status changes |

### Multi-Agent Swarm

| Feature | Description |
| --- | --- |
| **Worker Pool** | Multiple specialized AI agents executing tasks in parallel |
| **Agent Types** | LLM agent, research agent, scanner agent, test agent |
| **Shared Memory** | Agents share context via a shared memory bus |
| **Resource Locking** | Prevents conflicts when agents access shared resources |
| **Task Delegation** | Coordinator routes tasks to the best-suited agent |
| **Message Passing** | Inter-agent communication via a message queue |

### Enterprise Infrastructure

| Feature | Description |
| --- | --- |
| **Multi-Org Identity** | Organizations, workspaces, users with full RBAC |
| **Secret Manager** | AES-256-GCM encrypted credential storage |
| **Policy Engine** | Approve/deny/dry-run rules for tool calls |
| **Durable Execution** | Attempt tracking, idempotency keys, heartbeats, fault recovery |
| **Artifact Versioning** | Provenance chains with PPTX-specific stable anchors |
| **Evidence Tracking** | Facts linked to source data for auditability |
| **Token Budgeting** | Per-job and per-org cost tracking and limits |
| **Telemetry & Audit** | Full audit logging of all operations |
| **Evaluation Harness** | Run test cases against the AI and track success rates |
| **Project Registry** | Track multiple projects and cross-project patterns |
| **Validation Pipeline** | Multi-stage prompt, output, and cross-stage validators |

### Tool Ecosystem (35+ built-in)

| Category | Tools |
| --- | --- |
| **Web & Research** | Multi-provider web search (DuckDuckGo → Google → Brave), browser automation |
| **File Operations** | Read, write, list files — sandboxed to `~/LocalMind_Workspace` |
| **Code** | Python execution with timeout + blocklist, AST analysis |
| **Git** | Status, diff, log, commit, branch management |
| **Vision** | Camera capture, screenshot, image analysis via Ollama |
| **Google Workspace** | Docs, Drive, Sheets (14 ops), Slides, Gmail |
| **Office Documents** | Excel, Word, PowerPoint, PDF extraction |
| **Self-Improvement** | Self-edit, self-reflect, self-test, self-extend (AI writes new tools) |
| **Memory** | ChromaDB vector memory, document RAG (index + query) |
| **Infrastructure** | Model management, dependency management, project context, clipboard |
| **AI-Generated Tools** | AI can write, test, and deploy new tool plugins at runtime |

### Desktop & Web UI

| Feature | Description |
| --- | --- |
| **Dashboard** | Real-time task pipeline, action stream, workflow stepper |
| **Chat Interface** | Streaming responses with markdown rendering and syntax highlighting |
| **Code Editor** | Monaco Editor (VS Code engine) with file tree and AI context integration |
| **Hardware Monitor** | Live CPU, RAM, VRAM metrics in the status bar |
| **Worker Pool Dashboard** | Visualize multi-agent activity, success rates, and queue depth |
| **Learning Lab** | Browse what the AI has learned and trigger new learning cycles |
| **AI Profile** | View the AI's self-discovered identity and capability map |
| **Cross-Project Hub** | Patterns and insights across registered projects |
| **Voice I/O** | Speech-to-text input and selectable TTS voices |
| **PWA** | Install on mobile for home screen access |
| **Remote Access** | Access from anywhere via Tailscale (WireGuard encryption) |

## Architecture

```text
LocalMind/
├── backend/
│   ├── server.py              # FastAPI app, route mounting, startup
│   ├── agent.py               # Core AI agent with tool-calling
│   ├── config.py              # Centralized configuration
│   ├── model_router.py        # Routes to 7B/14B/Gemini by task complexity
│   ├── code_editor.py         # AI-driven code editing (search/replace diffs)
│   ├── git_ops.py             # Git operations (commit, branch, revert)
│   ├── gemini_client.py       # Optional Gemini cloud fallback
│   ├── notifications.py       # SMS/push notification dispatch
│   ├── db.py                  # Database initialization and helpers
│   ├── core/                  # Enterprise control plane
│   │   ├── identity.py        # Multi-org/workspace/user model with RBAC
│   │   ├── schema.py          # Enterprise DB schema
│   │   ├── providers.py       # Provider config management
│   │   ├── secret_manager.py  # AES-256-GCM encrypted secret storage
│   │   ├── policy.py          # Policy engine (approve/deny/dry-run)
│   │   ├── execution.py       # Durable execution (attempts, idempotency)
│   │   ├── artifacts.py       # Artifact versioning + provenance chains
│   │   ├── evidence.py        # Evidence tracking
│   │   ├── feedback.py        # Review feedback + human approval
│   │   ├── eval.py            # Evaluation & success metrics
│   │   ├── eval_runner.py     # Evaluation execution pipeline
│   │   ├── token_budget.py    # Token budgeting & cost tracking
│   │   ├── telemetry.py       # Full audit logging
│   │   ├── scheduler.py       # Job scheduling
│   │   ├── gpu_manager.py     # GPU resource management
│   │   ├── project_registry.py # Multi-project tracking
│   │   ├── audit.py           # Audit trail management
│   │   ├── gc.py              # Garbage collection & cleanup
│   │   ├── error_strategy.py  # Error handling & recovery strategies
│   │   └── atomic_io.py       # Atomic file operations
│   ├── jobs/                  # Job pipeline
│   │   ├── models.py          # Job, node, artifact data models
│   │   ├── planner.py         # Plan generation (node graph layout)
│   │   ├── executor.py        # Node execution, tool dispatch, retry
│   │   ├── queue.py           # SQLite-backed job queue
│   │   ├── reviewer.py        # 3-tier QA (format → LLM → human)
│   │   └── worker.py          # Worker process lifecycle
│   ├── autonomy/              # Autonomous operation
│   │   ├── cloud_brain.py     # Cloud-enhanced reasoning
│   │   ├── self_discovery.py  # Capability self-discovery
│   │   ├── skill_learner.py   # Skill acquisition from interactions
│   │   ├── services/          # Autonomy service layer
│   │   └── loops/
│   │       └── research.py    # Background research scheduler
│   ├── swarm/                 # Multi-agent system
│   │   ├── coordinator.py     # Swarm orchestrator
│   │   ├── delegation.py      # Task delegation logic
│   │   ├── messaging.py       # Inter-agent message passing
│   │   ├── shared_memory.py   # Shared context bus
│   │   ├── resource_lock.py   # Resource conflict prevention
│   │   ├── task_queue.py      # Swarm task queue
│   │   └── agents/            # Specialized agents
│   │       ├── llm_agent.py   # General LLM reasoning agent
│   │       ├── research_agent.py  # Web/academic research agent
│   │       ├── scanner_agent.py   # Codebase scanner agent
│   │       └── test_agent.py      # Test execution agent
│   ├── logic/                 # Chat & LLM orchestration
│   │   ├── llm_client.py      # Ollama streaming client with retry
│   │   ├── chat_service.py    # Chat orchestration and tool dispatch
│   │   ├── prompt_factory.py  # System prompt construction
│   │   ├── context_builder.py # Context window assembly
│   │   ├── token_manager.py   # Context window management
│   │   ├── tool_dispatcher.py # Tool call routing and execution
│   │   ├── load_monitor.py    # System load monitoring
│   │   └── summarizer.py      # Conversation summarization
│   ├── metacognition/         # Self-awareness layer
│   │   ├── controller.py      # Metacognition orchestrator
│   │   ├── intent_parser.py   # User intent classification
│   │   ├── uncertainty_gate.py # Confidence-based routing
│   │   ├── self_checker.py    # Output quality validation
│   │   ├── calibration.py     # Model calibration tracking
│   │   ├── tool_router.py     # Smart tool selection
│   │   ├── memory_manager.py  # Memory relevance scoring
│   │   ├── ui_generator.py    # Dynamic UI generation
│   │   └── revision_controller.py # Edit revision logic
│   ├── research/              # Research capabilities
│   │   ├── web.py             # Web research (academic + general)
│   │   ├── scanner.py         # Codebase complexity/smell scanner
│   │   └── analyzer.py        # Failure analysis + success tracking
│   ├── time_machine/          # State recording & replay
│   │   ├── recorder.py        # State snapshot recording
│   │   └── replay.py          # State replay and comparison
│   ├── eval/                  # Evaluation framework
│   │   ├── harness.py         # Test harness runner
│   │   └── cases.py           # Test case definitions
│   ├── validation/            # Input/output validation
│   │   ├── base.py            # Validator base class
│   │   ├── prompt_validators.py  # Prompt quality checks
│   │   ├── output_validators.py  # Output quality checks
│   │   ├── cross_stage_validators.py  # Cross-stage consistency
│   │   ├── robust_parser.py   # Resilient response parsing
│   │   └── root_cause.py      # Root cause analysis
│   ├── tts/                   # Text-to-speech
│   │   └── piper_service.py   # Piper TTS integration
│   ├── memory/                # Memory subsystem
│   │   └── session_cache.py   # In-memory session cache
│   ├── inference/             # Advanced inference
│   │   ├── lora_manager.py    # LoRA adapter management
│   │   ├── model_selector.py  # Optimal model selection
│   │   └── best_of_n.py       # Best-of-N generation with PRM scoring
│   ├── security/              # Security layer
│   │   ├── auth.py            # OAuth + JWT authentication
│   │   ├── rbac.py            # Role-based access control
│   │   ├── paths.py           # Path sandboxing
│   │   ├── memory_encryption.py # Encrypted memory storage
│   │   ├── prompt_guard.py    # Prompt injection detection
│   │   └── recycle_bin.py     # Soft-delete, file recovery
│   ├── integrations/
│   │   └── slack_bot.py       # Slack bot (Socket Mode)
│   ├── tools/                 # 35+ plugin-based AI tools
│   │   ├── base.py            # Abstract tool base class
│   │   ├── registry.py        # Auto-discovers and routes tools
│   │   ├── tool_generator.py  # AI generates new tools at runtime
│   │   ├── generated/         # AI-created tool plugins
│   │   └── ...                # web_search, file_tools, vision, etc.
│   └── routes/                # API endpoints
│       ├── chat.py            # /api/chat SSE streaming
│       ├── jobs.py            # /api/jobs CRUD + pipeline control
│       ├── tools.py           # /api/tools execution
│       ├── tools_generated.py # /api/tools/generated management
│       ├── swarm_routes.py    # /api/swarm multi-agent coordination
│       ├── hub.py             # /api/hub cross-project insights
│       ├── knowledge_graph.py # /api/knowledge-graph
│       ├── self_discovery.py  # /api/self-discovery
│       ├── skill_learning.py  # /api/skills learning endpoints
│       ├── time_machine.py    # /api/time-machine record/replay
│       ├── eval_routes.py     # /api/eval evaluation runs
│       ├── tts.py             # /api/tts text-to-speech
│       ├── research_routes.py # /api/research endpoints
│       ├── conversations.py   # /api/conversations CRUD
│       ├── documents.py       # /api/documents RAG endpoints
│       ├── files.py           # /api/files sandboxed ops
│       ├── memory.py          # /api/memory CRUD
│       ├── settings.py        # /api/settings
│       ├── system.py          # /api/system health + metrics
│       ├── admin.py           # /api/admin management endpoints
│       ├── google_auth.py     # /api/auth/google OAuth flow
│       └── validation_routes.py # /api/validate input validation
├── frontend/
│   ├── index.html             # SPA shell + all UI panels
│   ├── app.js                 # Entry point, module init
│   ├── styles.css             # Core styles (Tailwind companion)
│   ├── manifest.json          # PWA manifest
│   ├── sw.js                  # Service worker for offline
│   └── modules/               # 32 ES6 modules
│       ├── chat.js            # Chat interface + SSE streaming
│       ├── jobs_ui.js         # Job creation, filtering, monitoring
│       ├── swarm_ui.js        # Multi-agent swarm dashboard
│       ├── task_creation.js   # Progressive-disclosure task creator
│       ├── approvals_ui.js    # Human approval workflows
│       ├── templates_ui.js    # Saved job templates
│       ├── ai_profile.js      # AI self-discovery profile view
│       ├── learning_ui.js     # Learning Lab interface
│       ├── hub.js             # Cross-project hub
│       ├── brain_graph.js     # Knowledge graph visualization
│       ├── time_machine.js    # State replay UI
│       ├── eval_ui.js         # Evaluation runner UI
│       ├── monitoring_ui.js   # System monitoring
│       ├── editor.js          # Monaco code editor
│       ├── token_panel.js     # Token usage tracking
│       ├── onboarding.js      # First-run onboarding flow
│       ├── tools_generated.js # AI-generated tools panel
│       └── ...                # state, events, streaming, etc.
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
├── tests/                     # pytest test suites
├── scripts/
│   ├── bump_build.py          # Auto-increment build number
│   └── generate_docs.py       # API doc generator
├── Dockerfile                 # Docker image
├── docker-compose.yml         # Docker Compose setup
├── install.ps1                # One-click installer
├── uninstall.ps1              # Clean uninstaller
├── LocalMind.bat              # Launch script
├── run.py                     # Python entry point
├── pyproject.toml             # Python project config
├── requirements.txt           # Production dependencies
└── requirements-dev.txt       # Dev dependencies
```

## Quick Start

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

## Tool Plugin System

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

### AI-Generated Tools

LocalMind can also **write its own tools** at runtime. When the AI encounters a task that no existing tool covers, it can generate a new tool plugin, test it, and deploy it — all without restarting. Generated tools appear in `backend/tools/generated/` and show up in the UI's Generated Tools panel.

## Autonomy Engine

The engine runs continuous background loops:

| Loop | Interval | Purpose |
| --- | --- | --- |
| **Research** | Every 30 min | Web + academic research to inform decisions |
| **Self-Discovery** | Periodic | Maps its own capabilities and builds an identity profile |
| **Skill Learning** | Continuous | Learns patterns from successful interactions |
| **Cloud Brain** | On-demand | Cloud-enhanced reasoning for complex tasks |

### Safety Features

- **Confidence gating** — Proposals below 30% confidence are auto-denied
- **Infrastructure protection** — Critical files blocked from self-edit
- **Git branching** — All edits happen on feature branches, only merged after tests pass
- **Max retries** — Failed proposals capped at 5 retries before permanent archival
- **Deduplication** — Synonym-aware title matching prevents duplicate proposals
- **Validation pipeline** — Multi-stage validators catch issues before they reach users

## Safety & Security

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
- **Audit logging** — Full telemetry of all operations for compliance
- **Robust parsing** — Resilient response parsing with root cause analysis on failures

## Deployment

### Local (Development)

```powershell
.\install.ps1    # Downloads Ollama, models, creates venv
.\LocalMind.bat  # Starts server at http://localhost:8001
```

### Docker

```bash
docker-compose up -d
```

### Slack Integration (Optional)

LocalMind can connect to Slack via Socket Mode (`slack-bolt[async]`). Once configured, users can submit jobs, check status, and receive delivered artifacts directly in Slack channels.

## Dependencies

All open-source and free:

| Package | Purpose |
| --- | --- |
| [Ollama](https://ollama.com) | Local LLM inference |
| [FastAPI](https://fastapi.tiangolo.com) | Backend web framework |
| [ChromaDB](https://www.trychroma.com) | Vector database for memories |
| [Monaco Editor](https://microsoft.github.io/monaco-editor/) | VS Code editor engine (CDN) |
| [D3.js](https://d3js.org) | Knowledge graph visualization |
| [google-api-python-client](https://github.com/googleapis/google-api-python-client) | Google Workspace APIs |
| [cryptography](https://cryptography.io) | AES-256-GCM secret encryption |
| [python-pptx](https://python-pptx.readthedocs.io) | PowerPoint generation |
| [openpyxl](https://openpyxl.readthedocs.io) | Excel spreadsheet operations |
| [python-docx](https://python-docx.readthedocs.io) | Word document operations |
| [slack-bolt](https://slack.dev/bolt-python/) | Slack bot framework (optional) |
| [httpx](https://www.python-httpx.org) | Async HTTP client |
| [mss](https://pypi.org/project/mss/) | Screenshot capture |
| [Pillow](https://pillow.readthedocs.io) | Image processing |

## License

MIT — do whatever you want with it.
