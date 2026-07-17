## 2025-02-14 - SQLite Database Indexes for Chat
**Learning:** Found missing indexes for `SELECT * FROM conversations ORDER BY updated_at DESC` and `SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at`. In SQLite databases used for chat applications, these lookups happen constantly. A missing index on `conversation_id` in the `messages` table can cause full table scans every time a chat history is loaded.
**Action:** Always add indexes for foreign keys (like `conversation_id`) and frequently sorted fields (like `updated_at` or `created_at`) when defining SQLite schema for chat history.
## 2025-05-15 - Non-blocking System Metrics in FastAPI
**Learning:** Using `psutil.cpu_percent(interval=0.1)` in an async FastAPI route blocks the entire event loop for 100ms. This significantly increases latency for all concurrent requests.
**Action:** Always prime `psutil.cpu_percent(interval=None)` at module load or startup and use `interval=None` in async handlers to retrieve metrics instantly without blocking.

## 2025-10-24 - Single-Query SQLite Aggregation with FILTER and json_extract
**Learning:** Querying all rows from a telemetry metric/log table and iterating/parsing JSON values in Python causes O(N) memory allocation and expensive CPU overhead. SQLite natively supports powerful JSON extracting functions (`json_extract`) and aggregate `FILTER` clauses which can shift the entire processing into the database, providing a massive speedup (~70% reduction in latency).
**Action:** Replace manual Python iteration and multiple database queries over logs/metrics with a single aggregate SQLite query using `FILTER` and `json_extract`.
