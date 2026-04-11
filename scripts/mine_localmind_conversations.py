"""
mine_localmind_conversations.py

Exports all conversation turns from LocalMind's conversations.db and
files them into MemPalace as verbatim hall_events in wing_localmind.

Usage:
    cd LocalMind project root
    venv/Scripts/python.exe scripts/mine_localmind_conversations.py

Mines from: backend/conversations.db (and ~/LocalMind_Workspace/localmind.db)
Palace target: ~/.mempalace/palace  wing=wing_localmind  hall=hall_events  room=chat-sessions
"""

import sys
import sqlite3
from pathlib import Path

# ── Add project root so imports work ────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.memory.palace_manager import initialize_palace, save, get_status

print("=" * 60)
print("  LocalMind → MemPalace conversation miner")
print("=" * 60)

# Initialize palace
if not initialize_palace():
    print("ERROR: MemPalace failed to initialize. Is chromadb installed?")
    sys.exit(1)

status = get_status()
print(f"  Palace: {status['palace_path']}")
print(f"  Drawers before: {status['drawer_count']}")
print()

# Candidate DB paths (backend's dev DB + workspace production DB)
candidate_dbs = [
    Path(__file__).parent.parent / "backend" / "conversations.db",
    Path.home() / "LocalMind_Workspace" / "localmind.db",
]

total_saved = 0
total_skipped = 0

for db_path in candidate_dbs:
    if not db_path.exists():
        print(f"  Skipping (not found): {db_path}")
        continue

    print(f"  Mining: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Try to get conversation turns as paired messages
    try:
        rows = conn.execute("""
            SELECT m.conversation_id, m.role, m.content, m.created_at,
                   c.title
            FROM messages m
            LEFT JOIN conversations c ON c.id = m.conversation_id
            ORDER BY m.conversation_id, m.created_at
        """).fetchall()
    except sqlite3.OperationalError as e:
        print(f"    Schema error: {e} — trying simple messages query")
        try:
            rows = conn.execute("""
                SELECT conversation_id, role, content, created_at, '' as title
                FROM messages
                ORDER BY conversation_id, created_at
            """).fetchall()
        except Exception as e2:
            print(f"    Failed: {e2}")
            conn.close()
            continue

    conn.close()

    # Group into (user, assistant) pairs per conversation
    from collections import defaultdict
    by_conv = defaultdict(list)
    for row in rows:
        if row["role"] in ("user", "assistant") and row["content"]:
            by_conv[row["conversation_id"]].append(dict(row))

    for conv_id, msgs in by_conv.items():
        title = msgs[0].get("title", "") or ""
        pairs = []
        i = 0
        while i < len(msgs):
            if msgs[i]["role"] == "user":
                user_msg = msgs[i]["content"]
                asst_msg = ""
                if i + 1 < len(msgs) and msgs[i + 1]["role"] == "assistant":
                    asst_msg = msgs[i + 1]["content"]
                    i += 2
                else:
                    i += 1
                if user_msg and asst_msg:
                    pairs.append((user_msg, asst_msg))
            else:
                i += 1

        for user_msg, asst_msg in pairs:
            # Skip very short exchanges (greetings, acks, etc.)
            if len(asst_msg) < 80:
                total_skipped += 1
                continue

            content = (
                f"[CONVERSATION: {conv_id[:8]}] {title[:60]}\n"
                f"USER: {user_msg[:800]}\n"
                f"ASSISTANT: {asst_msg[:1200]}"
            )

            result = save(
                content=content,
                wing="wing_localmind",
                hall="hall_events",
                room="chat-sessions",
            )

            if result.get("success"):
                total_saved += 1
            else:
                total_skipped += 1

        if pairs:
            saved_from_conv = len([p for p in pairs if len(p[1]) >= 80])
            print(f"    [{conv_id[:8]}] '{title[:40]}' → {saved_from_conv} turns filed")

print()
print("=" * 60)
print(f"  Done. Saved: {total_saved}  |  Skipped (too short): {total_skipped}")
final = get_status()
print(f"  Total drawers now: {final['drawer_count']}")
print("=" * 60)
