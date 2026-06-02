## 2025-02-14 - SQLite Database Indexes for Chat
**Learning:** Found missing indexes for `SELECT * FROM conversations ORDER BY updated_at DESC` and `SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at`. In SQLite databases used for chat applications, these lookups happen constantly. A missing index on `conversation_id` in the `messages` table can cause full table scans every time a chat history is loaded.
**Action:** Always add indexes for foreign keys (like `conversation_id`) and frequently sorted fields (like `updated_at` or `created_at`) when defining SQLite schema for chat history.
## 2025-05-15 - Non-blocking System Metrics in FastAPI
**Learning:** Using `psutil.cpu_percent(interval=0.1)` in an async FastAPI route blocks the entire event loop for 100ms. This significantly increases latency for all concurrent requests.
**Action:** Always prime `psutil.cpu_percent(interval=None)` at module load or startup and use `interval=None` in async handlers to retrieve metrics instantly without blocking.

## 2025-05-20 - SQL-side Telemetry Aggregation
**Learning:** Shifting telemetry aggregation from Python loops to SQLite using  and  yielded a ~3.2x speedup for 10,000 records. SQLite's  is preferred over  as it returns  instead of  for empty sets, preventing Python .
**Action:** Always prefer SQL-side aggregation for telemetry and metrics to minimize data transfer and Python CPU overhead.

## 2025-05-20 - SQL-side Telemetry Aggregation
**Learning:** Shifting telemetry aggregation from Python loops to SQLite using `FILTER` and `json_extract()` yielded a ~3.2x speedup for 10,000 records. SQLite's `TOTAL()` is preferred over `SUM()` as it returns `0.0` instead of `NULL` for empty sets, preventing Python `TypeError`.
**Action:** Always prefer SQL-side aggregation for telemetry and metrics to minimize data transfer and Python CPU overhead.
