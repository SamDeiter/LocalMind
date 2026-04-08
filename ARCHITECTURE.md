# LocalMind Architecture

## Vision
Local-first AI development assistant that runs autonomously on your machine.
Operates primarily on Ollama (free, private) with optional Gemini escalation.

## Core Goals
1. **Autonomous Code Improvement** — Scan codebase, find issues, propose fixes, execute them
2. **Self-Healing** — Auto-recover from failures, restart services, pre-warm models
3. **Research-Driven** — Use web + academic research to inform proposals
4. **Privacy-First** — All data stays local unless user explicitly enables cloud
5. **Task Completion** — Move beyond proposals to actually completing development tasks
6. **Self-Protection** — Critical infrastructure files blocked from self-edit

## Architecture

### Backend (Python/FastAPI)
```
backend/
├── server.py               — FastAPI app, route mounting, startup
├── agent.py                — Core AI agent with tool-calling
├── config.py               — Centralized configuration constants
├── gemini_client.py        — Optional Gemini cloud fallback (PII-scrubbed)
├── model_router.py         — Routes tasks to 7B (fast) or 14B+ (complex) models
├── proposals.py            — Proposal lifecycle (create, dedup, approve, execute)
├── code_editor.py          — AI code editing (4-layer search/replace matching)
├── git_ops.py              — Git operations (commit, branch, revert, merge)
├── self_improver.py        — Self-improvement logic
├── meta_critic.py          — Meta-analysis of engine decisions
├── priority_queue.py       — User priority directives
├── digest.py               — Summary generation
├── notifications.py        — Notification system
├── todo_harvester.py       — TODO comment extraction
├── autonomy/
│   ├── engine.py           — Main autonomy engine (reflection, research, execution loops)
│   ├── execution.py        — Proposal execution pipeline
│   ├── reflection.py       — Codebase self-reflection
│   ├── task_executor.py    — General task executor (research, docs, data)
│   ├── config.py           — Engine configuration constants
│   ├── utils.py            — Shared autonomy utilities
│   └── loops/
│       ├── health.py       — Self-healing health monitor (auto-restart, pre-warm)
│       ├── reflection.py   — Periodic self-reflection scheduler
│       ├── research.py     — Background research scheduler
│       ├── execution.py    — Execution loop scheduler
│       └── digest.py       — Daily summary generation
├── logic/
│   ├── llm_client.py       — Ollama/Gemini streaming client with retry ⚠️ PROTECTED
│   ├── chat_service.py     — Chat orchestration and tool dispatch ⚠️ PROTECTED
│   ├── prompt_factory.py   — System prompt construction
│   ├── token_manager.py    — Context window management
│   └── summarizer.py       — Conversation summarization
├── metacognition/
│   ├── controller.py       — Metacognition orchestrator
│   ├── intent_parser.py    — User intent classification
│   ├── uncertainty_gate.py — Confidence-based routing
│   ├── self_checker.py     — Output quality validation
│   ├── calibration.py      — Model calibration tracking
│   ├── tool_router.py      — Smart tool selection
│   ├── memory_manager.py   — Memory relevance scoring
│   ├── ui_generator.py     — Dynamic UI generation
│   └── revision_controller.py — Edit revision logic
├── research/
│   ├── web.py              — Web research (academic + general)
│   ├── scanner.py          — Codebase complexity/smell scanner
│   └── analyzer.py         — Failure analysis + success tracking
├── tools/                  — 15+ plugin-based AI tools
│   ├── base.py             — Abstract tool base class
│   ├── registry.py         — Auto-discovers and routes tools
│   └── ...                 — web_search, file_tools, git_tools, etc.
└── routes/                 — API endpoints
    ├── chat.py             — /api/chat SSE streaming
    ├── autonomy_routes.py  — /api/autonomy/* engine control
    ├── research_routes.py  — /api/research/* endpoints
    └── ...                 — conversations, documents, files, memory, etc.
```

### Frontend (Vanilla JS, ES Modules)
```
frontend/
├── app.js              — Entry point, module init
├── index.html          — SPA shell
├── styles.css          — Core styles (Tailwind companion)
├── manifest.json       — PWA manifest
├── sw.js               — Service worker
└── modules/
    ├── autonomy_ui.js  — Brain dashboard + action stream
    ├── chat.js         — Chat interface + SSE streaming
    ├── editor.js       — Monaco code editor
    ├── events.js       — DOM event binding
    ├── state.js        — Shared state + DOM refs
    ├── settings_ui.js  — Settings modal
    ├── sidebar.js      — Sidebar navigation
    ├── dashboard.js    — Hardware dashboard
    ├── conversations.js — Conversation management
    ├── proposals_ui.js — Proposal review UI
    ├── research_ui.js  — Research results UI
    ├── media.js        — Camera/voice/media
    ├── utils.js        — Shared utilities
    └── live_reload.js  — Dev hot-reload
```

## Safety Architecture

### Infrastructure Protection
Files marked ⚠️ PROTECTED in `code_editor.py` → `BLOCKED_NAMES`:
- `llm_client.py`, `chat_service.py`, `code_editor.py`, `model_router.py`
- `server.py`, `run.py`, `config.py`, `autonomy.py`
- All `.env` files

### Confidence Gating
- Proposals scored 0-100 based on: category success rate, file familiarity, effort level
- Below 30% → auto-denied
- Failed proposals capped at 5 retries → permanent archival

### Git Safety
- All autonomous edits happen on feature branches
- Only merged after tests pass
- Failed edits auto-reverted

## Current Priorities
- Protect chat streaming from engine self-corruption
- Make the engine generate higher-quality, actionable proposals
- Enable multi-file task completion (not just single-file fixes)
- Improve research context fed into reflection prompts
- Keep everything running locally and cheaply (Ollama primary)

## Quality Standards
- No hardcoded secrets (use .env)
- All edits via Python scripts
- Git backup after every meaningful change
- Security scan before every push
