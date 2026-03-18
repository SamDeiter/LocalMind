# 💡 Feature Requests

- [ ] **2026-03-18T16:54:28** — Onboarding flow: AI asks user questions about themselves (name, preferences, interests) on first launch to personalize memory and personality. Personal data stored in an encrypted file to prevent unauthorized access.
- [ ] **2026-03-18T16:55:28** — Dual memory architecture: KV Cache for short-term/session memory, RAG database (ChromaDB) for long-term persistent memory. Investigate whether current setup already covers this or needs a dedicated KV cache layer.
- [ ] **2026-03-18T16:59:00** — Token visualization panel: Show the tokenization process in the UI menu — how words are broken down into tokens, token counts, and generation stats from the server logs. Educational and useful for debugging.
- [ ] **2026-03-18T17:04:58** — AI self-discovery: Have the AI browse the internet to build a profile of itself, including generating an avatar/image of what it might look like. Part of the "digital life" personality system.
- [ ] **2026-03-18T17:23:18** — Open-source Docker container that can be used by the AI for sandboxed code execution, or to contain/isolate the AI itself
- [ ] **2026-03-18T17:28:24** — Gmail integration: AI reads user's Gmail to learn about them and personalize interactions. Must address security and privacy concerns (local-only processing, PII scrubbing, OAuth scopes, user approval flow, no cloud forwarding)
- [ ] **2026-03-18T17:37:07** — Build number visible in the app UI, auto-incremented on each build/deploy. Wire `scripts/bump_build.py` + `version.json` into `run.py` so the build number bumps automatically every time the server starts in production mode.
