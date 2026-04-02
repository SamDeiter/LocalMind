import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger("localmind.db")

from backend.config import DB_PATH

def init_db():
    """Create conversations and messages tables if they don't exist."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            model TEXT NOT NULL,
            system_prompt TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        )
    """)

    # Performance Optimization: Bolt
    # Composite index to speed up message retrieval when loading a chat:
    # "SELECT ... FROM messages WHERE conversation_id = ? ORDER BY created_at"
    # This turns an O(N) full table scan into an O(log N) lookup.
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_conversation_id_created_at
        ON messages (conversation_id, created_at)
    """)
    conn.commit()
    conn.close()

def get_db():
    """Get a database connection with row factory enabled."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn

# For backwards compatibility with any existing code expecting get_db_connection
get_db_connection = get_db
