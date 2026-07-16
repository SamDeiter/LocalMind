## 2025-02-14 - SQLite Database Indexes for Chat
**Learning:** Found missing indexes for `SELECT * FROM conversations ORDER BY updated_at DESC` and `SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at`. In SQLite databases used for chat applications, these lookups happen constantly. A missing index on `conversation_id` in the `messages` table can cause full table scans every time a chat history is loaded.
**Action:** Always add indexes for foreign keys (like `conversation_id`) and frequently sorted fields (like `updated_at` or `created_at`) when defining SQLite schema for chat history.
## 2025-05-15 - Non-blocking System Metrics in FastAPI
**Learning:** Using `psutil.cpu_percent(interval=0.1)` in an async FastAPI route blocks the entire event loop for 100ms. This significantly increases latency for all concurrent requests.
**Action:** Always prime `psutil.cpu_percent(interval=None)` at module load or startup and use `interval=None` in async handlers to retrieve metrics instantly without blocking.

## 2025-05-20 - High-Performance SQL Aggregation for Telemetry
**Learning:** Offloading telemetry aggregation from Python iteration to SQLite using `FILTER` clauses and `json_extract` reduced `get_metrics_summary` latency from ~123ms to ~34ms for 20,000 records (~72% speedup). SQLite's native JSON support is significantly faster than fetching thousands of rows and parsing/aggregating them in Python.
**Action:** Use SQL-level aggregation (especially with `FILTER` and `json_extract`) for dashboards and metrics endpoints to minimize data transfer and Python overhead.
