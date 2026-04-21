# LocalMind v1.0.0 "Ignition" — Single Source of Truth

> **This is the ONE document.** All decisions, status, architecture, and open questions live here.
> Last updated: 2026-04-20 | Session: bd5d46cb

---

## 🗺️ Status Dashboard

| Phase | What | Status |
|-------|------|--------|
| D1 | AgentFixer RCA + Calibration Fix | ✅ Complete |
| D2 | Git Awareness Tools | ✅ Complete |
| D3 | Cross-Project Hub | ✅ Complete |
| D4 | Model Roster — Gemma 4 + MedGemma + Cleanup | ✅ Complete |
| D5 | Streaming Resilience — Token Checkpoint-Resume | ✅ Complete |
| D6 | Google Workspace OAuth Layer | ✅ Complete |
| D7 | Repo Cleanup | 🚀 Next up |
| F | Autonomous Agent Mode + Coworker Experience | 🔜 Needs design session |
| F2 | Report Forge (Sister Use Case) | 🔜 Planned |
| E | VS Code Extension | 🔜 After F |
| G | v1.0.0 Launch Hardening | 🔜 Last |

---

## 🔍 Codebase Audit — What's Already Built

> Full audit run 2026-04-20. Do not plan to re-build any of these.

| Module | Path | Notes |
|--------|------|-------|
| Memory Palace | `backend/memory/palace_manager.py` | Full persistent memory system |
| Session Cache | `backend/memory/session_cache.py` | |
| Memory Manager | `backend/metacognition/memory_manager.py` | Wired into cognitive loop |
| Memory Encryption | `backend/security/memory_encryption.py` | Memories encrypted at rest |
| Shared Memory | `backend/swarm/shared_memory.py` | Cross-agent memory |
| Time Machine | `backend/time_machine/recorder.py` + `replay.py` | Versioned action replay |
| Web Search | `backend/tools/web_search.py` | ✅ Already exists |
| Browser Tool | `backend/tools/browser_tool.py` | ✅ Already exists |
| Google Drive | `backend/tools/google_drive_tool.py` | ✅ Auth wiring needed only |
| Google Docs | `backend/tools/google_docs_tool.py` | ✅ Auth wiring needed only |
| Gmail | `backend/tools/gmail_tool.py` | ✅ Auth wiring needed only |
| Google Sheets | `backend/tools/google_sheets_tool.py` | ✅ Auth wiring needed only |
| Google Slides | `backend/tools/google_slides_tool.py` | ✅ Auth wiring needed only |
| TTS (Piper) | `backend/tts/piper_service.py` | ✅ Fully local voice output |
| RAG | `backend/tools/rag.py` | Retrieval-augmented generation |
| Swarm Coordinator | `backend/swarm/coordinator.py` | Multi-agent orchestration |
| Research Agent | `backend/swarm/agents/research_agent.py` | |
| Task Queue | `backend/swarm/task_queue.py` | |
| PDF / Word / PPTX / Excel | `backend/tools/` | All document formats covered |
| RBAC | `backend/security/rbac.py` | Role-based access control |
| Prompt Guard | `backend/security/prompt_guard.py` | |
| Skill Learning | `backend/routes/skill_learning.py` | Agent self-improvement |

---

## ✅ Phase D — Foundation Hardening

### D1. AgentFixer RCA + Calibration Fix *(Complete)*
- Fixed commented-out `CalibrationTracker` in `metacognition/controller.py`
- Wired `TraceAnalyzer` into `post_process` failure path — every SelfChecker failure now generates a structured `TraceEntry`

### D2. Git Awareness Tools *(Complete)*
Added to `backend/git_ops.py`:
- `git_status()` → structured dict (branch, staged, unstaged, untracked)
- `git_diff_staged()` → diff string, capped at 8000 chars
- `git_log_short(n)` → last N commits as list of dicts
- `git_commit_staged(message)` → safe commit with confirmation gate

### D3. Cross-Project Hub *(Complete)*
- Created `backend/integrations/cross_project_hub.py`
- Tech stack detection for 9 runtimes
- Endpoint: `GET /api/hub/context?project_path=...` in `server.py`

### D4. Model Roster *(Complete — committed bc4ac8d)*

**Removed (~70 GB freed):**

| Model | Size | Reason |
|-------|------|--------|
| `ue5-slides:latest` / `v2` / `q8` / `q8v2` / `q4` / `q4v2` | ~51 GB | Local training — cheaper online |
| `deepseek-r1:14b` | 9.0 GB | Not in router |
| `deepseek-r1:7b` | 4.7 GB | Not in router |
| `qwen3:8b` | 5.2 GB | Not in router |
| `qwen2.5:7b-instruct` | 4.7 GB | Replaced by coder variant |
| `gemma3:4b` | 3.3 GB | Replaced by `gemma4:e2b` |

**Active model router:**

| Slot | Model | Purpose |
|------|-------|---------|
| `local_micro` | `gemma4:e2b` | Fast, light tasks |
| `local_micro_plus` | `gemma4:e4b` | Stronger light coding |
| `local_light` | `qwen2.5-coder:7b` | Code tasks |
| `local_heavy` | `qwen2.5-coder:14b` | Complex code |
| `local_ultra` | `qwen2.5-coder:32b` | Max local power |
| `local_vision` | `gemma4:27b` | Multimodal, 128K context |
| `local_medical` | `medgemma:4b` | Clinical reasoning, FERPA-safe |
| `cloud_flash` | `gemini-2.0-flash` | Fast cloud |
| `cloud_pro` | `gemini-2.5-pro` | Max cloud |

> **MedGemma:** Confirmed for personal medical assistant (family health Q&A, medication lookups, symptom checking). Fully local — zero health data leaves the machine.

---

## ✅ Phase D5 — Streaming Resilience *(Complete)*

> **Novel feature.** No existing local AI tool (Aider, Continue.dev, OpenClaw) does token preservation on stream drop. This is part of LocalMind's moat.

**The solution — `backend/inference/streaming_client.py`:**
```
TokenCheckpoint buffer
  → accumulates tokens as they arrive
  → on connection drop, tokens are saved
  → on retry, checkpoint content is injected as assistant prefix
  → model continues from exactly where it dropped
  → if all retries fail, partial content returned (not nothing)
  → final fallback: non-streaming single-shot
```

**What gets built:**
- `backend/inference/streaming_client.py` — `stream_ollama_chat()` generator with `TokenCheckpoint` class, 3 retries, 45s heartbeat timeout
- `backend/agent.py` — `agent_chat_streaming()` updated to use new client

**Verification:** Kill Ollama mid-stream → confirm tokens received before drop appear in final output.

---

## ✅ Phase D6 — Google Workspace OAuth Layer *(Complete)*

> All Google tool files already exist. This is **auth wiring only**.

```
backend/integrations/google_workspace/
  ├── auth.py      ← OAuth 2.0 flow + encrypted local token store (NEW)
  └── __init__.py  ← wires auth into all existing Google tools (NEW)
```

**Auth flow:** Settings → "Connect Google Account" → browser OAuth consent → token encrypted locally → auto-refreshes silently. Never hardcoded, never in git.

**Prerequisite:** Google Cloud Project + OAuth Client ID/Secret → documented in `docs/google_setup.md`.

---

## 🔜 Phase D7 — Repo Cleanup *(~1 hour)*

- Archive/remove one-off migration scripts from `scripts/`
- Remove temp/scratch files from Phase D
- Verify `.gitignore` covers all exclusions
- Confirm clean `git status`

---

## 🔜 Phase F — Autonomous Agent Mode

> **Core product vision: a coworker you brief, not a chatbot you query.**

### The Coworker Opening Experience *(Must-Have)*

Every session starts with LocalMind greeting you like someone who actually knows what's going on:

> *"Yo — what are we doing today? Yesterday you were deep in the Blueprint module gap analysis. I flagged something overnight you might want to see. Also your calendar has that team meeting at 2."*

It reads this intel **autonomously** from Drive meeting notes, calendar, and overnight agent activity — you don't have to tell it anything. It finds it and brings it to you.

**Three layers:**

| Layer | What it does |
|-------|-------------|
| **Daily briefing** | Memory of yesterday + calendar + overnight flags, synthesized naturally |
| **Personality & tone** | Casual, knows you — configurable to how you actually talk |
| **Ambient intel capture** | Reads Drive/meeting notes overnight, surfaces key decisions in the morning |

> **Accessibility:** `tts/piper_service.py` (Piper TTS, fully local) makes LocalMind work entirely without a screen. Voice in, voice out. Genuine accessibility story for blind/low-vision users — already built.

---

### Agent Design Principle — One Question Rule

> The agent asks **at most one clarifying question at intake**, then starts working. Mid-task questions happen only at genuine decision forks. Never a list of questions upfront.

```
✅ Good:
User: "Find me a Tag Heuer deal"
Agent: "Any specific model, or best value across the lineup?" → starts immediately
Agent (mid-task): "Mostly pre-owned results — authorized dealer only?" → forks only when needed

❌ Bad: "Before I start, I need to know: 1) Budget? 2) New or pre-owned? 3) Model?..."
```

---

### Two Task Trigger Types — Same Engine

| Type | Example | How triggered |
|------|---------|--------------|
| **Scheduled intelligence** | UE5 weekly sentiment report, content gap monitoring | Time-triggered (e.g. Friday 8am) |
| **One-shot mission** | Find a watch deal, research a topic, compare options | User gives a task, agent executes and reports back |

---

### Content Intelligence Loop

When LocalMind detects something notable in your data sources:

| Signal | What LocalMind Does |
|--------|-------------------|
| **Trending tutorial** | "This blew up this week — want me to feature it in the digest?" + source links |
| **Content gap** | "People are asking about X but we have no tutorial. Want me to draft one?" |
| **Negative spike** | "This module is getting hammered — here are 3 better-performing analogues" |
| **Scheduled report** | Full weekly Google Doc, every claim linked to its source data |

**Output format — Google Doc with:**
- Executive summary
- Key findings with inline citation links
- "What we have" — existing content matching demand
- "What we're missing" — gaps with draft outlines ready to expand
- Anomaly callouts (spikes/drops) with supporting data

---

### What Gets Built

- `backend/autonomy/goal_planner.py` — goal → structured task tree
- `backend/autonomy/engine.py` — plan → execute → reflect loop, hard time-box
- `backend/autonomy/intelligence_monitor.py` — event-driven + time-triggered (NOT polling). Drive changes use Google webhook push; scheduled tasks fire at defined times
- `backend/autonomy/sourced_report.py` — Google Docs with every claim sourced
- `backend/autonomy/content_assist.py` — gap detected → draft outline/script generated
- **Proactive alerts:** Desktop notification + email (via Gmail tool) — quiet during normal weeks
- **Frontend:** Agent Mode toggle, live task tree, scheduled task manager, notifications inbox

> **⚠️ Needs a dedicated design session before execution** — finalize: task input UI, web research tool permissions, standing watch/alert persistence, general vs. specialized task router.

---

### Open Questions for Phase F

1. **UE5 dashboard data source** — Web URL / Google Sheets / API? Determines which tool the agent uses for the weekly report.
2. **Notification channel** — Desktop popup, email, or both?

---

## 🔜 Phase F2 — Report Forge *(Sister Use Case)*

> Automated pipeline: school interview notes → formatted reports. Zero manual writing.

**The Workflow:**
```
[School — Phone]
  Finishes interview → drops notes into Google Drive "Report Forge Inbox"
  (Google Doc, voice memo, photo, .docx — any format)

[Home — LocalMind]
  Google Drive webhook fires when file lands → LocalMind wakes up
  → Transcribes if audio/image (Whisper / local OCR)
  → MedGemma formats into structured report using few-shot examples
  → Immutable audit log entry created (input hash, model, output hash)
  → Report waiting in "Report Forge" tab

[Review]
  She reads → edits in diff view → clicks Approve
  → Report hash-signed and locked
  → Saved locally + optionally written to Google Drive
```

| Component | File | Purpose |
|-----------|------|---------|
| Drive watcher | `backend/report_forge/drive_watcher.py` | Webhook receiver — wakes on file drop |
| Formatter | `backend/report_forge/formatter.py` | Notes → MedGemma → structured report |
| Few-shot loader | `backend/report_forge/templates.py` | 2–5 example reports as formatting guides |
| Audit log | `backend/report_forge/audit_log.py` | Immutable log: input hash, model, output hash, approval |
| UI tab | Frontend | Pending queue, diff view, approve button |

**Training:** Few-shot first (5 example reports). If not precise enough → LoRA fine-tune via `lora_manager.py` with 50–100 examples.

**Privacy:** Fully local processing. Google Drive covered under school district's existing Workspace FERPA agreement.

---

## 🔜 Phase E — VS Code Extension

`vscode-extension/` scaffold already exists.

- Sidebar `WebviewPanel` embedding LocalMind chat UI
- Commands: `LocalMind: Ask AI`, `Explain Selection`, `Fix Selection`
- `contextExtractor.ts` — active file, selection, git blame as context payload
- Backend via `LOCALMIND_BASE_URL` setting

---

## 🔜 Phase G — v1.0.0 Launch Hardening

> ⚠️ Do NOT version-bump to 1.0 until this is done.

- `README.md` updated for all Phase 9 features
- `CHANGELOG.md` created
- Test coverage: validation pipeline, RCA, streaming resilience
- `install.ps1` with model suggestions + self-test on first run
- `version.json` → `1.0.0`, codename: **"Ignition"**
- Final pre-push security scan

---

## 📋 Execution Order

| Phase | Focus | Est. Effort | Status |
|-------|-------|-------------|--------|
| D1–D4 | Foundation + Models | — | ✅ Done |
| **D5** | Streaming resilience | — | ✅ Done |
| **D6** | Google OAuth wiring | — | ✅ Done |
| **D7** | Repo cleanup | ~1 hour | 🚀 Next |
| **F** | Agent Mode + Coworker Experience | ~1–2 weeks | 🔜 Design first |
| **F2** | Report Forge | ~1 week | 🔜 After D6 |
| **E** | VS Code Extension | ~3–4 days | 🔜 After F |
| **G** | Launch Hardening | ~2 days | 🔜 Last |

---

## ✅ Confirmed Decisions

| Decision | Answer |
|----------|--------|
| Model cleanup | All 11 non-router models removed, ~70 GB freed |
| MedGemma | `medgemma:4b` — personal medical assistant, fully local |
| Google Workspace | OAuth layer only needed (all tools already exist) |
| Drive intake (F2) | Webhook-based, not polling |
| Phase order | D5 → D6 → D7 → F → F2 → E → G |
| Agent conversation rule | One question max at intake |
| Proactive alerts | Email + desktop notification |
| Accessibility | TTS (Piper) already built — voice-first works today |
| Local training (ue5-slides) | Removed — cheaper and faster to train online |
| Report Forge privacy | FERPA compliant via school's existing Google Workspace agreement |
