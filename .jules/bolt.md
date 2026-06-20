## 2025-02-14 - SQLite Database Indexes for Chat
**Learning:** Found missing indexes for `SELECT * FROM conversations ORDER BY updated_at DESC` and `SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at`. In SQLite databases used for chat applications, these lookups happen constantly. A missing index on `conversation_id` in the `messages` table can cause full table scans every time a chat history is loaded.
**Action:** Always add indexes for foreign keys (like `conversation_id`) and frequently sorted fields (like `updated_at` or `created_at`) when defining SQLite schema for chat history.
## 2025-05-15 - Non-blocking System Metrics in FastAPI
**Learning:** Using `psutil.cpu_percent(interval=0.1)` in an async FastAPI route blocks the entire event loop for 100ms. This significantly increases latency for all concurrent requests.
**Action:** Always prime `psutil.cpu_percent(interval=None)` at module load or startup and use `interval=None` in async handlers to retrieve metrics instantly without blocking.
## 2026-06-20 - SQL Aggregations for Telemetry
**Learning:** Pulling thousands of telemetry rows from SQLite to Python for manual aggregation is a major bottleneck (126ms for 10k rows). Using SQL `FILTER`, `json_extract`, and window functions offloads this to the database engine, reducing latency by ~64% (to 45ms).
**Action:** Always prefer SQL-side aggregations and JSON processing for large datasets. Use `TOTAL()` instead of `SUM()` to avoid `NULL` results, and use `IS NOT` for null-safe inequality checks in SQLite.
