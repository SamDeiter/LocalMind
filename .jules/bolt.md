## 2024-03-24 - SQLite Index on `messages.conversation_id`
**Learning:** The application heavily queries the `messages` table filtered by `conversation_id` for displaying chat history and deleting entire conversations. Without an index on `conversation_id`, these operations require full table scans, becoming a performance bottleneck as the number of accumulated messages grows.
**Action:** Added a `idx_messages_conversation_id` index in `backend/db.py` to change these operations from O(N) to O(log N). Always look for `WHERE` clauses on foreign keys and ensure they have indices in SQLite.
