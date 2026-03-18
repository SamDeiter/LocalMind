---
description: Update project documentation (README, docs, etc.) to reflect current state
---

# /docs — Documentation Updater

When the user uses `/docs` or `/docs <specific file or topic>`, update the project documentation to reflect the current state of the codebase.

## Steps

1. Read the optional target from the user's message (everything after `/docs`). If no target is specified, update all major docs.

2. Scan the current codebase state:
   - Check `version.json` for the current version
   - Check `docs/TASK.md` for completed phases
   - Check `backend/tools/` for available tools
   - Check `backend/server.py` for API endpoints
   - Check `frontend/` for UI features

3. Update the relevant documentation files:
   - `README.md` — Architecture tree, feature table, safety section, dependencies
   - `docs/TASK.md` — Phase completion status
   - `docs/BUGS.md` — Mark fixed bugs, add new ones
   - `docs/FEATURES.md` — Cross-reference with implemented features
   - `version.json` — Bump version if significant changes were made

4. For each file updated, show a brief summary of what changed.

5. Commit the documentation updates to git with message: `docs: update documentation to reflect current state`
