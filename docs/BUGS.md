# 🐛 Bug Tracker

- [ ] **2026-03-18T17:01:09** — Memory system not working: user tells AI something and it doesn't remember it. Root cause identified: missing `await` on async `execute()` + wrong param name. Fix committed but server needs restart to apply.
- [ ] **2026-03-18T17:03:24** — No way to stop the AI if it gets stuck in a loop. Need a cancel/abort button in the UI.
- [ ] **2026-03-18T17:04:21** — Auto-scroll prevents scrolling up during AI response. Need a toggle to enable/disable auto-scroll, or auto-disable when user scrolls up.
- [ ] **2026-03-18T17:06:09** — Clicking on old chats in the sidebar does not load them. Expected: clicking a chat loads the conversation history.
