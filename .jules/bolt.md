## 2025-02-14 - SQLite Database Indexes for Chat
**Learning:** Found missing indexes for `SELECT * FROM conversations ORDER BY updated_at DESC` and `SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at`. In SQLite databases used for chat applications, these lookups happen constantly. A missing index on `conversation_id` in the `messages` table can cause full table scans every time a chat history is loaded.
**Action:** Always add indexes for foreign keys (like `conversation_id`) and frequently sorted fields (like `updated_at` or `created_at`) when defining SQLite schema for chat history.
## 2025-05-15 - Non-blocking System Metrics in FastAPI
**Learning:** Using `psutil.cpu_percent(interval=0.1)` in an async FastAPI route blocks the entire event loop for 100ms. This significantly increases latency for all concurrent requests.
**Action:** Always prime `psutil.cpu_percent(interval=None)` at module load or startup and use `interval=None` in async handlers to retrieve metrics instantly without blocking.
## 2026-05-26 - SQL Aggregation for Telemetry
**Learning:** Moving telemetry aggregation from Python loops to SQL (`json_extract`, `FILTER`, `TOTAL`) reduces latency by ~70% for large datasets. However, when calculating percentiles like P95, avoid combining aggregates (COUNT) and raw values in one query without GROUP BY, as it collapses the result set to one row.
**Action:** Use SQL aggregates for simple summaries. For complex stats like P95, fetch only the required fields via `json_extract` to avoid full JSON parsing overhead while keeping the full result set for Python-side computation.
