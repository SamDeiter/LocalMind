## 2025-02-14 - SQLite Database Indexes for Chat
**Learning:** Found missing indexes for `SELECT * FROM conversations ORDER BY updated_at DESC` and `SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at`. In SQLite databases used for chat applications, these lookups happen constantly. A missing index on `conversation_id` in the `messages` table can cause full table scans every time a chat history is loaded.
**Action:** Always add indexes for foreign keys (like `conversation_id`) and frequently sorted fields (like `updated_at` or `created_at`) when defining SQLite schema for chat history.
## 2025-05-15 - Non-blocking System Metrics in FastAPI
**Learning:** Using `psutil.cpu_percent(interval=0.1)` in an async FastAPI route blocks the entire event loop for 100ms. This significantly increases latency for all concurrent requests.
**Action:** Always prime `psutil.cpu_percent(interval=None)` at module load or startup and use `interval=None` in async handlers to retrieve metrics instantly without blocking.

## 2025-05-24 - SQL-side Aggregation for Telemetry
**Learning:** Performing Python-side aggregation for telemetry metrics (looping over rows and parsing JSON) scales poorly. For 50,000 records, processing in Python took ~310ms. Moving this to SQLite using `json_extract()`, `TOTAL()`, and `FILTER` clauses reduced latency to ~135ms (~56% improvement).
**Action:** Use SQL-side aggregation (e.g., `SUM`, `COUNT`, `AVG`, `GROUP BY`) with `json_extract()` for telemetry/metrics data to process large datasets efficiently directly in the database.
