---
description: Log a task to work on without derailing current work
---

# /task — Quick Task Logger

When the user uses `/task <description>`, append it to the task tracker without interrupting the current work.

1. Read the task description from the user's message (everything after `/task`).

2. Append the task to `c:\Users\Sam Deiter\Documents\GitHub\LocalMind\docs\TASKS_BACKLOG.md` in this format:
   ```
   - [ ] **<timestamp>** — <task description>
   ```

3. If `TASKS_BACKLOG.md` doesn't exist yet, create it with a `# 📋 Task Backlog` header first.

4. Confirm with a ONE LINE response like: `📋 Logged task: "<short summary>"`

5. **Do NOT change your current task or start working on the logged task.** Just log it and continue what you were doing.
