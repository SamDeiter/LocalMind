## 2025-02-14 - SQLite Database Indexes for Chat
**Learning:** Found missing indexes for `SELECT * FROM conversations ORDER BY updated_at DESC` and `SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at`. In SQLite databases used for chat applications, these lookups happen constantly. A missing index on `conversation_id` in the `messages` table can cause full table scans every time a chat history is loaded.
**Action:** Always add indexes for foreign keys (like `conversation_id`) and frequently sorted fields (like `updated_at` or `created_at`) when defining SQLite schema for chat history.
## 2025-05-15 - Non-blocking System Metrics in FastAPI
**Learning:** Using `psutil.cpu_percent(interval=0.1)` in an async FastAPI route blocks the entire event loop for 100ms. This significantly increases latency for all concurrent requests.
**Action:** Always prime `psutil.cpu_percent(interval=None)` at module load or startup and use `interval=None` in async handlers to retrieve metrics instantly without blocking.
## 2025-05-20 - SQL Aggregation over Empty Sets in SQLite
**Learning:** When using aggregate SQL functions (like `COUNT`, `SUM`, `AVG`) without a `GROUP BY` clause, SQLite returns a single row even if no rows match the `WHERE` criteria. In this case, non-count columns and window function results (like `total_count` in a CTE) become `NULL`, which causes `TypeError` in Python when compared (e.g. `row["total_count"] > 0`).
**Action:** Always use truthiness checks (e.g., `if row and row["total_count"] and row["total_count"] > 0:`) or `COALESCE(col, 0)` when consuming aggregate results from SQLite that might target empty sets.
