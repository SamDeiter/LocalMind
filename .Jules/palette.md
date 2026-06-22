## 2025-05-15 - Stop Button Micro-UX
**Learning:** Adding a "Stop" button for AI generation requires tight synchronization between state, UI visibility, and network AbortControllers. Without the logic to back it up, a button is just visual noise.
**Action:** When implementing interruptible long-running tasks, always ensure the AbortController is wired through to the fetch layer and that the UI responds immediately to the "abort" event.
