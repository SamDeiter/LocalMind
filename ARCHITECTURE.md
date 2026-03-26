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

## Architecture

### Backend (Python/FastAPI)
```
backend/
├── server.py           — FastAPI app, route mounting, startup
├── agent.py            — Core AI agent with tool-calling
├── gemini_client.py    — Optional Gemini cloud fallback (PII-scrubbed)
├── model_router.py     — Routes tasks to 7B (fast) or 14B+ (complex) models
├── proposals.py        — Proposal lifecycle (create, approve, deny, execute)
├── priority_queue.py   — User priority directives
├── autonomy/
│   ├── engine.py       — Main autonomy engine (reflection, research, execution loops)
│   ├── config.py       — Engine configuration constants
│   ├── execution.py    — Proposal execution pipeline
│   └── loops/
│       ├── health.py   — Self-healing health monitor (auto-restart, pre-warm)
│       ├── reflection.py — Periodic self-reflection
│       ├── research.py — Background research tasks
│       └── digest.py   — Daily summary generation
├── research/
│   ├── web.py          — Web research (academic + general)
│   ├── scanner.py      — Codebase complexity/smell scanner
│   └── analyzer.py     — Failure analysis + success tracking
├── tools/              — Agent tools (file ops, git, shell)
└── routes/             — API endpoints
```

### Frontend (Vanilla JS, ES Modules)
```
frontend/
├── app.js              — Entry point, module init
├── index.html          — SPA shell
├── modules/
│   ├── autonomy/       — Brain dashboard (9 modular files)
│   ├── chat.js         — Chat interface + streaming
│   ├── editor.js       — Monaco code editor
│   ├── events.js       — DOM event binding
│   ├── state.js        — Shared state + DOM refs
│   └── settings_ui.js  — Settings modal
```

## Current Priorities
- Make the engine generate higher-quality, actionable proposals
- Enable multi-file task completion (not just single-file fixes)
- Improve research context fed into reflection prompts
- Keep everything running locally and cheaply (Ollama primary)

## Quality Standards
- No hardcoded secrets (use .env)
- All edits via Python scripts
- Git backup after every meaningful change
- Security scan before every push
