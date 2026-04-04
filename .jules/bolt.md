## 2025-02-14 - SQLite Database Indexes for Chat
**Learning:** Found missing indexes for `SELECT * FROM conversations ORDER BY updated_at DESC` and `SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at`. In SQLite databases used for chat applications, these lookups happen constantly. A missing index on `conversation_id` in the `messages` table can cause full table scans every time a chat history is loaded.
**Action:** Always add indexes for foreign keys (like `conversation_id`) and frequently sorted fields (like `updated_at` or `created_at`) when defining SQLite schema for chat history.

## 2025-05-15 - Non-blocking CPU Monitoring in FastAPI
**Learning:** Using `psutil.cpu_percent(interval=0.1)` inside an async FastAPI route blocks the entire event loop for 100ms per request. This prevents the server from handling any other concurrent tasks (like chat streaming or autonomy loops) during that time.
**Action:** Always use `psutil.cpu_percent(interval=None)` for non-blocking retrieval in async handlers. Ensure the measurement is "primed" by calling it once at the module level during startup.