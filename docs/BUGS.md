# 🐛 Bug Tracker

## Fixed

- [x] **2026-03-18T17:01:09** — ~~Memory system not working~~ **FIXED v0.5.0** — Root cause: `nomic-embed-text` embedding model was not installed in Ollama. Pulled model + fixed missing `await` and param names.
- [x] **2026-03-18T17:03:24** — ~~No way to stop the AI~~ **FIXED v0.5.0** — Added abort/stop button with pulsing CSS animation.
- [x] **2026-03-18T17:04:21** — ~~Auto-scroll prevents scrolling up~~ **FIXED v0.5.0** — Smart auto-scroll respects user scroll position.
- [x] **2026-03-18T17:06:09** — ~~Old chats not loading~~ **FIXED v0.5.0** — Added `renderMessages()` call after loading conversation.
- [x] **2026-03-18T17:31:57** — ~~Memories not storing between conversations~~ **FIXED v0.5.0** — `nomic-embed-text` model was missing.
- [x] **2026-03-18T18:44:00** — ~~Response times extremely slow (8.5s, 1.2 tok/s)~~ **FIXED v0.5.0** — Smart memory path, deferred auto-save, `keep_alive: 30m`, ChromaDB singleton.
- [x] **2026-03-31T02:00:00** — ~~Chat streaming completely broken~~ **FIXED v0.6.0** — `llm_client.py` had triple-duplicated code blocks from AI self-edit. Response iteration was outside the retry loop. Full rewrite restored streaming.
- [x] **2026-03-31T02:15:00** — ~~Edit failures show generic "could not parse" message~~ **FIXED v0.6.0** — `code_editor.py` now provides specific diagnostics (empty response, missing keys, search text mismatches).
- [x] **2026-03-31T02:30:00** — ~~Action stream collapsible groups reset on SSE re-render~~ **FIXED v0.6.0** — Implemented `_userToggledGroups` Set for persistent state.

## Active

*(No active bugs)*

## Recently Fixed (v0.6.1)

- [x] **2026-03-31T09:13:00** — ~~Chat streaming re-broken by engine self-edit of `llm_client.py`~~ **FIXED v0.6.1** — File re-corrupted with duplicate code blocks. Fixed + `llm_client.py` added to BLOCKED_NAMES.
- [x] **2026-03-31T09:12:00** — ~~"Done" section cards not rendering~~ **FIXED v0.6.1** — `displayItems = items.slice(-3)` limit removed; shows all items when expanded.

## Known Limitations

- Monolithic functions remain in `chat_service.py` and `engine.py` (tech debt — Sprint 2 target)
- Google OAuth requires manual credential setup (no admin UI yet)
- Slack bot requires `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` environment variables (documented in `.env.example`)
- Best-of-N PRM integration is stub-only (LLM-as-judge and heuristic scoring work; PRM deferred to Phase 9)

## Test Coverage

**Total: 382 tests collected, 381 passed, 1 skipped**

### What IS tested

| Test file | Count | Covers |
|---|---|---|
| `test_approvals.py` | 42 | Policy CRUD, approval lifecycle, expiry, priority, conditions, built-in policies, API endpoints, end-to-end approve/deny/expire |
| `test_artifacts.py` | 15 | Artifact creation, SHA-256 hashing, tamper detection, lineage chains, provenance trace, listing, immutability, metadata |
| `test_cost_tracking.py` | 14 | Usage recording, job-level aggregation, cost accumulation, serialization, stats endpoint, metrics summary |
| `test_job_pipeline.py` | 89 | Job CRUD, node CRUD, DAG ordering, templates, planner (complexity, validation, circular deps), executor (tools, timeout, errors), reviewer (tiers, human flag), worker (full cycle, circuit breaker, dequeue), models, file operations |
| `test_pipeline_e2e.py` | 21 | Multi-node pipeline creation, output piping, partial failure, rerun/resume, template save/load, DAG integrity, audit trail |
| `test_security.py` | 75 | API key auth, bootstrap mode, RBAC (admin/operator/viewer), policy engine (egress, rate limit, SSRF, bulk delete, approval), prompt guard (injection, jailbreak, homoglyphs, secret scrubbing, anomaly detection), path jailing (traversal, symlinks, null bytes, filename sanitization, upload validation) |
| `test_slack_bot.py` | 66 | Bot init, authorization (team/user allowlists), progress bar, mention/message/file-shared/action handlers, worker event routing, lifecycle start/stop, factory function, job creation from Slack, rate limiting, file upload |
| `test_tools_integration.py` | 59 | PPTX (read/extract/edit/styles), Excel (read/write/range/sheets/formulas), Word (read/edit/styles/metadata/tables), PDF (extract/metadata/search), file tools (read/write/list/binary detection/path traversal) |

### What is NOT tested yet

- **TTS** (`backend/tts/piper_service.py`) -- text-to-speech via Piper
- **Browser tool** (`backend/tools/browser_tool.py`, `mcp_browser.py`) -- headless browser automation
- **Android emulator** (`backend/tools/android_emulator.py`) -- ADB-based device control
- **Google OAuth flow** (`backend/routes/google_auth.py`) -- OAuth2 callback handling
- **Google integrations** (`google_docs_tool.py`, `google_drive_tool.py`, `google_sheets_tool.py`, `google_slides_tool.py`, `gmail_tool.py`) -- all Google Workspace tools
- **Chat routes** (`backend/routes/chat.py`) -- main chat/streaming endpoint
- **LLM client** (`backend/inference/`, `backend/model_router.py`) -- Ollama/Gemini inference layer
- **Memory system** (`backend/memory/`, `backend/tools/memory.py`, `backend/tools/mempalace_tool.py`) -- MemPalace, ChromaDB RAG
- **RAG** (`backend/tools/rag.py`) -- retrieval-augmented generation
- **Code editor** (`backend/code_editor.py`) -- AI-driven code editing
- **Git tools** (`backend/tools/git_tools.py`) -- git operations
- **Terminal / run_code** (`backend/tools/terminal.py`, `backend/tools/run_code.py`) -- shell and code execution
- **Screenshot / vision** (`backend/tools/screenshot.py`, `backend/tools/vision.py`) -- screen capture, image analysis
- **Self-edit / self-extend / self-reflect / self-test** -- metacognition tools
- **Web search** (`backend/tools/web_search.py`) -- DuckDuckGo search
- **Swarm routes** (`backend/routes/swarm_routes.py`) -- multi-agent swarm orchestration
- **Knowledge graph** (`backend/routes/knowledge_graph.py`) -- entity/relationship storage
- **Time machine** (`backend/time_machine/`) -- state snapshots and rollback
- **Middleware** (`backend/middleware/`) -- request/response middleware
- **Validation** (`backend/validation/`) -- input validation layer
- **Tool registry** (`backend/tools/registry.py`) -- dynamic tool registration
- **Notifications** (`backend/notifications.py`) -- push notification system
