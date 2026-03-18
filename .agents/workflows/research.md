---
description: Research a feature idea before implementing it
---

# /research — Feature Research

When the user uses `/research <topic>`, create a focused research note without interrupting the current task.

1. Read the research topic from the user's message (everything after `/research`).

2. Create or append to `c:\Users\Sam Deiter\Documents\GitHub\LocalMind\docs\RESEARCH.md` with a new section:
   ```
   ## <topic> — <timestamp>
   
   **Status:** 🔍 Needs Research
   **Question:** <what the user wants to know>
   
   ### Findings
   _To be filled in during next research session_
   ```

3. Confirm with a ONE LINE response like: `🔍 Research queued: "<short summary>"`

4. **Do NOT start researching immediately.** Just log the topic and continue what you were doing. The research will be done in a dedicated session later.
