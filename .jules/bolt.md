## 2025-02-14 - SQLite Database Indexes for Chat
**Learning:** Found missing indexes for `SELECT * FROM conversations ORDER BY updated_at DESC` and `SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at`. In SQLite databases used for chat applications, these lookups happen constantly. A missing index on `conversation_id` in the `messages` table can cause full table scans every time a chat history is loaded.
**Action:** Always add indexes for foreign keys (like `conversation_id`) and frequently sorted fields (like `updated_at` or `created_at`) when defining SQLite schema for chat history.
## 2025-05-15 - Non-blocking System Metrics in FastAPI
**Learning:** Using `psutil.cpu_percent(interval=0.1)` in an async FastAPI route blocks the entire event loop for 100ms. This significantly increases latency for all concurrent requests.
**Action:** Always prime `psutil.cpu_percent(interval=None)` at module load or startup and use `interval=None` in async handlers to retrieve metrics instantly without blocking.
## 2025-05-22 - Shared AsyncClient for Connection Pooling
**Learning:** Instantiating a new `httpx.AsyncClient` for every LLM request or health check adds significant overhead due to repeated TCP/TLS handshakes. A shared singleton enables connection pooling, reducing latency. However, when refactoring, it's critical to: 1) maintain per-request timeouts (shared clients may have different defaults), and 2) meticulously check indentation when removing `async with` blocks to avoid syntax errors.
**Action:** Use `backend.utils.http_client.get_async_client()` for all HTTP requests and ensure the client is closed in the application lifespan.
