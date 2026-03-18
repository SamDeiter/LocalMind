# 🐛 Bug Tracker

- [x] **2026-03-18T17:01:09** — ~~Memory system not working~~ **FIXED v0.5.0** — Root cause: `nomic-embed-text` embedding model was not installed in Ollama. Pulled model + fixed missing `await` and param names.
- [x] **2026-03-18T17:03:24** — ~~No way to stop the AI~~ **FIXED v0.5.0** — Added abort/stop button with pulsing CSS animation.
- [x] **2026-03-18T17:04:21** — ~~Auto-scroll prevents scrolling up~~ **FIXED v0.5.0** — Smart auto-scroll respects user scroll position.
- [x] **2026-03-18T17:06:09** — ~~Old chats not loading~~ **FIXED v0.5.0** — Added `renderMessages()` call after loading conversation.
- [ ] **2026-03-18T17:31:57** — Memories not storing between conversations and memory icon shows 0. Root cause identified: `nomic-embed-text` model was missing. Fix applied (model pulled). Needs verification.
