# 📊 LocalMind — Development Progress

> Last updated: March 8, 2026

## Current Version: v0.3.0 #5

---

## ✅ Sprint 1 — "The Moat" (Complete)

| Feature                             | Status  | Notes                                       |
| ----------------------------------- | ------- | ------------------------------------------- |
| Document RAG ("Talk to Your Files") | ✅ Done | Index + query via ChromaDB                  |
| Hardware Dashboard                  | ✅ Done | Live CPU/RAM/VRAM bars in status bar        |
| Multi-Model Router                  | ✅ Done | ⚡Fast (7B) / 🤖Auto / 🧠Deep (32B)         |
| Monaco Code Editor                  | ✅ Done | File tree, Ctrl+S, syntax highlighting      |
| Multi-Provider Web Search           | ✅ Done | DDG → Google → Brave auto-fallback          |
| Memory Viewer UI                    | ✅ Done | List/delete memories in sidebar             |
| Tool Execution Bug Fixes            | ✅ Done | Async execute, text fallback, serialization |
| Build Versioning                    | ✅ Done | v0.3.0 #5 auto-incrementing                 |
| Port Guard                          | ✅ Done | Auto-kills duplicate server processes       |

**Not started:**

- Screen Region Capture
- Plugin/Tool System (public marketplace)

---

## 🔨 In Progress — Editor Enhancements

| Feature                   | Status        | Notes                                   |
| ------------------------- | ------------- | --------------------------------------- |
| Send to AI auto-sends     | 🔨 Code added | Clicks send automatically               |
| Drag & drop files to chat | 🔨 Code added | FileReader for text + base64 for images |
| ▶️ Run Python button      | 🔨 Code added | Needs `/api/tools/run` endpoint         |
| Resizable panels          | 🔨 Code added | Horizontal + vertical drag handles      |
| Auto-context injection    | 📋 Planned    | Editor file auto-included in chat       |
| CSS for new features      | 📋 Planned    | Drop zone, output panel styles          |

---

## 📋 Sprint 2 — "The Edge" (Upcoming)

| Feature                                       | Status     |
| --------------------------------------------- | ---------- |
| Git Awareness (status, diff, commit tools)    | 📋 Planned |
| Project Context Loading (dir tree as context) | 📋 Planned |
| Voice Input (faster-whisper + mic UI)         | 📋 Planned |

## 📋 Sprint 3 — "The Polish"

| Feature                    | Status     |
| -------------------------- | ---------- |
| Response Streaming Quality | 📋 Planned |
| Mobile PWA Polish          | 📋 Planned |
| Voice Quality (Piper TTS)  | 📋 Planned |

## 📋 Sprint 4 — "The Future"

| Feature            | Status     |
| ------------------ | ---------- |
| VS Code Extension  | 📋 Planned |
| Team/Shared Memory | 📋 Planned |

---

## 🚦 Next Steps (Priority Order)

When resuming, complete these in order:

1. **Finish editor wiring** — Add `/api/tools/run` endpoint in `server.py`, HTML elements for ▶️ Run button + output panel, CSS for drop zone + output panel
2. **Auto-context injection** — Modify `sendMessage()` so the AI automatically sees the file open in the Monaco editor (inject as system context, not user message)
3. **Test all 4 enhancements end-to-end** — Send to AI, drag-drop, run button, resizable panels
4. **Git Awareness tool** — New `backend/tools/git_tools.py` with status, diff, commit, log
5. **Project Context Loading** — Send directory tree as context so AI understands your project structure
6. **Voice Input** — faster-whisper integration with mic button in the UI

---

## 🏛️ Tech Stack

- **Backend:** Python, FastAPI, Ollama, ChromaDB, SQLite
- **Frontend:** Vanilla HTML/CSS/JS, Monaco Editor (CDN)
- **AI Models:** qwen2.5-coder:7b (fast), qwen2.5-coder:32b (deep)
- **Hardware:** NVIDIA RTX 3080 (10GB VRAM)
