---
description: Log a bug without derailing current work
---

# /bug — Quick Bug Logger

When the user uses `/bug <description>`, append it to the bug tracker file without interrupting the current task.

1. Read the bug description from the user's message (everything after `/bug`).

2. Append the bug to `c:\Users\Sam Deiter\Documents\GitHub\LocalMind\docs\BUGS.md` in this format:
   ```
   - [ ] **<timestamp>** — <bug description>
   ```

3. If `BUGS.md` doesn't exist yet, create it with a `# 🐛 Bug Tracker` header first.

4. Confirm with a ONE LINE response like: `🐛 Logged: "<short summary>"`

5. **Do NOT change your current task or investigate the bug.** Just log it and continue what you were doing.
