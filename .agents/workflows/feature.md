---
description: Log a feature request without derailing current work
---

# /feature — Quick Feature Logger

When the user uses `/feature <description>`, append it to the feature tracker file without interrupting the current task.

1. Read the feature description from the user's message (everything after `/feature`).

2. Append the feature to `c:\Users\Sam Deiter\Documents\GitHub\LocalMind\docs\FEATURES.md` in this format:
   ```
   - [ ] **<timestamp>** — <feature description>
   ```

3. If `FEATURES.md` doesn't exist yet, create it with a `# 💡 Feature Requests` header first.

4. Confirm with a ONE LINE response like: `💡 Logged: "<short summary>"`

5. **Do NOT change your current task or start implementing the feature.** Just log it and continue what you were doing.
