## 2025-02-14 - SQLite Database Indexes for Chat
**Learning:** Found missing indexes for `SELECT * FROM conversations ORDER BY updated_at DESC` and `SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at`. In SQLite databases used for chat applications, these lookups happen constantly. A missing index on `conversation_id` in the `messages` table can cause full table scans every time a chat history is loaded.
**Action:** Always add indexes for foreign keys (like `conversation_id`) and frequently sorted fields (like `updated_at` or `created_at`) when defining SQLite schema for chat history.
## 2025-05-15 - Non-blocking System Metrics in FastAPI
**Learning:** Using `psutil.cpu_percent(interval=0.1)` in an async FastAPI route blocks the entire event loop for 100ms. This significantly increases latency for all concurrent requests.
**Action:** Always prime `psutil.cpu_percent(interval=None)` at module load or startup and use `interval=None` in async handlers to retrieve metrics instantly without blocking.

## 2025-05-16 - Offloading Telemetry Aggregation to SQL
**Learning:** The `MetricsCollector.get_metrics_summary` and `AlertManager.check_thresholds` methods were fetching thousands of metric rows into Python memory and performing manual `json.loads` and aggregation loops. This created a significant CPU and memory bottleneck as the metrics table grew. Modern SQLite (3.30+) supports `FILTER` clauses and `json_extract`, allowing these aggregations to happen natively in the database engine.
**Action:** For frequently queried metrics stored as JSON, use SQL aggregation functions (`SUM`, `COUNT`, `AVG`) with `FILTER` and `json_extract` (or `->>`) to offload processing to SQLite. Use Window Functions like `ROW_NUMBER() OVER (...)` for percentile calculations (like P95) to avoid fetching and sorting large datasets in application code.
