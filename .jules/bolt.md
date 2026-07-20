## 2025-02-14 - SQLite Database Indexes for Chat
**Learning:** Found missing indexes for `SELECT * FROM conversations ORDER BY updated_at DESC` and `SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at`. In SQLite databases used for chat applications, these lookups happen constantly. A missing index on `conversation_id` in the `messages` table can cause full table scans every time a chat history is loaded.
**Action:** Always add indexes for foreign keys (like `conversation_id`) and frequently sorted fields (like `updated_at` or `created_at`) when defining SQLite schema for chat history.

## 2025-05-15 - Non-blocking System Metrics in FastAPI
**Learning:** Using `psutil.cpu_percent(interval=0.1)` in an async FastAPI route blocks the entire event loop for 100ms. This significantly increases latency for all concurrent requests.
**Action:** Always prime `psutil.cpu_percent(interval=None)` at module load or startup and use `interval=None` in async handlers to retrieve metrics instantly without blocking.

## 2025-05-16 - SQLite JSON Extraction and FILTER Aggregation for Telemetry
**Learning:** Deserializing JSON records in Python to compute metrics summary and percentile-based threshold alerts leads to massive overhead under scale (O(N) memory allocation and json.loads() calls). Offloading the JSON extraction and conditional summation to SQLite's native engine via `json_extract` and `FILTER` clauses resolves this bottleneck entirely.
**Action:** Avoid raw Python loops for database row aggregation; use SQLite `json_extract` with aggregate `FILTER` clauses and limit/offsets for percentile values to aggregate data entirely within the database engine.
