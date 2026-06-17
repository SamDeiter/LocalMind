## 2025-02-14 - SQLite Database Indexes for Chat
**Learning:** Found missing indexes for `SELECT * FROM conversations ORDER BY updated_at DESC` and `SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at`. In SQLite databases used for chat applications, these lookups happen constantly. A missing index on `conversation_id` in the `messages` table can cause full table scans every time a chat history is loaded.
**Action:** Always add indexes for foreign keys (like `conversation_id`) and frequently sorted fields (like `updated_at` or `created_at`) when defining SQLite schema for chat history.
## 2025-05-15 - Non-blocking System Metrics in FastAPI
**Learning:** Using `psutil.cpu_percent(interval=0.1)` in an async FastAPI route blocks the entire event loop for 100ms. This significantly increases latency for all concurrent requests.
**Action:** Always prime `psutil.cpu_percent(interval=None)` at module load or startup and use `interval=None` in async handlers to retrieve metrics instantly without blocking.
## 2025-06-10 - SQL-based Telemetry Aggregation
**Learning:** Moving telemetry aggregation from Python memory to SQL (using `FILTER`, `json_extract`, and Window Functions) significantly reduces latency and memory pressure. For 10,000 records, `get_metrics_summary` latency dropped from ~96ms to ~21ms. Calculating P95 latency in SQL using `ROW_NUMBER() OVER` is more efficient than fetching all rows and sorting in Python.
**Action:** Prefer SQL-level aggregations for metrics and telemetry to avoid expensive data transfer and processing in the application layer. Use CTEs and Window Functions for complex aggregations like percentiles.
