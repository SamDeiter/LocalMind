# 🐛 Bug Tracker

- [ ] **2026-03-18T17:01:09** — Memory system not working: user tells AI something and it doesn't remember it. Root cause identified: missing `await` on async `execute()` + wrong param name. Fix committed but server needs restart to apply.
- [ ] **2026-03-18T17:03:24** — No way to stop the AI if it gets stuck in a loop. Need a cancel/abort button in the UI.
