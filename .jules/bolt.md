## 2025-02-14 - SQLite Database Indexes for Chat
**Learning:** Found missing indexes for `SELECT * FROM conversations ORDER BY updated_at DESC` and `SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at`. In SQLite databases used for chat applications, these lookups happen constantly. A missing index on `conversation_id` in the `messages` table can cause full table scans every time a chat history is loaded.
**Action:** Always add indexes for foreign keys (like `conversation_id`) and frequently sorted fields (like `updated_at` or `created_at`) when defining SQLite schema for chat history.
## 2025-05-15 - Non-blocking System Metrics in FastAPI
**Learning:** Using `psutil.cpu_percent(interval=0.1)` in an async FastAPI route blocks the entire event loop for 100ms. This significantly increases latency for all concurrent requests.
**Action:** Always prime `psutil.cpu_percent(interval=None)` at module load or startup and use `interval=None` in async handlers to retrieve metrics instantly without blocking.
## 2024-05-18 - SQLite Composite Indexes for Listing and Filtering
**Learning:** For dashboard-style listing queries that combine filtering (e.g., `workspace_id`, `status`) and sorting (e.g., `created_at DESC`), single-column indexes are inefficient as SQLite can only use one index effectively or must perform a merge. Composite indexes covering all `WHERE` and `ORDER BY` columns in the correct order can reduce query latency by 60-80% on medium-sized datasets (5k+ rows).
**Action:** Implement composite indexes for common multi-column query patterns, ensuring the sort direction in the index matches the `ORDER BY` clause.
