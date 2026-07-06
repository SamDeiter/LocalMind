## 2025-02-14 - SQLite Database Indexes for Chat
**Learning:** Found missing indexes for `SELECT * FROM conversations ORDER BY updated_at DESC` and `SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at`. In SQLite databases used for chat applications, these lookups happen constantly. A missing index on `conversation_id` in the `messages` table can cause full table scans every time a chat history is loaded.
**Action:** Always add indexes for foreign keys (like `conversation_id`) and frequently sorted fields (like `updated_at` or `created_at`) when defining SQLite schema for chat history.
## 2025-05-15 - Non-blocking System Metrics in FastAPI
**Learning:** Using `psutil.cpu_percent(interval=0.1)` in an async FastAPI route blocks the entire event loop for 100ms. This significantly increases latency for all concurrent requests.
**Action:** Always prime `psutil.cpu_percent(interval=None)` at module load or startup and use `interval=None` in async handlers to retrieve metrics instantly without blocking.
## 2026-07-06 - [SQL Aggregation for Telemetry]
**Learning:** Offloading JSON parsing and aggregation (SUM, COUNT, window functions) to SQLite in `backend/core/telemetry.py` reduced latency for 30,000 records from ~155ms to ~48ms (approx. 70% speedup). SQLite's `FILTER` and `json_extract` are significantly more efficient than fetching thousands of rows and parsing them in Python.
**Action:** Use `COALESCE(SUM(...), 0)` and `COALESCE(COUNT(...), 0)` to ensure robustness against missing rows or NULL JSON fields, preventing `TypeError` in downstream Python logic.
