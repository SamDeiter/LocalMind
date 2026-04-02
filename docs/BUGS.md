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

- [ ] **2026-03-31T09:13:00** — Chat streaming re-broken by engine self-edit of `llm_client.py`. File re-corrupted with duplicate code blocks. Fixed again + verified BLOCKED_NAMES protection exists. **Requires server restart.**
- [ ] **2026-03-31T09:12:00** — "Done" section shows "+N more records archived" but cards don't render. Root cause: `displayItems = items.slice(-3)` artificially limits visible cards. Fixed by showing all items when expanded.

## Known Limitations

- ESLint errors persist in `autonomy_ui.js` (pre-existing undefined globals like `d`, `Prism`, `brainPulse`)
- Monolithic functions remain in `chat.py` and `engine.py` (tech debt)
- Ollama URLs still hardcoded (should migrate to `.env` → `OLLAMA_BASE_URL`)
