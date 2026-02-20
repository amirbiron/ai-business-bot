"""
Database module — SQLite storage for knowledge base, conversations, and notifications.
"""

import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from ai_chatbot.config import DB_PATH


@contextmanager
def get_connection():
    """Yield a SQLite connection and always close it safely."""
    # The app can run the Telegram bot (asyncio) and the Flask admin panel in the
    # same process. We create a fresh connection per operation, but still set a
    # generous timeout and busy_timeout to reduce "database is locked" errors
    # under concurrent writes, and allow cross-thread usage if needed.
    conn = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create all tables if they don't exist."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.executescript("""
            -- Knowledge Base entries
            CREATE TABLE IF NOT EXISTS kb_entries (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                category    TEXT NOT NULL,
                title       TEXT NOT NULL,
                content     TEXT NOT NULL,
                metadata    TEXT DEFAULT '{}',
                is_active   INTEGER DEFAULT 1,
                created_at  TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now'))
            );

            -- Chunked versions for RAG
            CREATE TABLE IF NOT EXISTS kb_chunks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id    INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                chunk_text  TEXT NOT NULL,
                embedding   BLOB,
                created_at  TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (entry_id) REFERENCES kb_entries(id) ON DELETE CASCADE
            );

            -- Conversation history
            CREATE TABLE IF NOT EXISTS conversations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                username    TEXT DEFAULT '',
                role        TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                message     TEXT NOT NULL,
                sources     TEXT DEFAULT '',
                created_at  TEXT DEFAULT (datetime('now'))
            );

            -- Agent transfer notifications
            CREATE TABLE IF NOT EXISTS agent_requests (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                username    TEXT DEFAULT '',
                telegram_username TEXT DEFAULT '',
                message     TEXT DEFAULT '',
                status      TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'handled', 'dismissed')),
                created_at  TEXT DEFAULT (datetime('now')),
                handled_at  TEXT
            );

            -- Appointment bookings
            CREATE TABLE IF NOT EXISTS appointments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                username    TEXT DEFAULT '',
                telegram_username TEXT DEFAULT '',
                service     TEXT DEFAULT '',
                preferred_date TEXT DEFAULT '',
                preferred_time TEXT DEFAULT '',
                notes       TEXT DEFAULT '',
                status      TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'confirmed', 'cancelled')),
                created_at  TEXT DEFAULT (datetime('now'))
            );

            -- Conversation summaries for long-term memory
            CREATE TABLE IF NOT EXISTS conversation_summaries (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         TEXT NOT NULL,
                summary_text    TEXT NOT NULL,
                message_count   INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT DEFAULT (datetime('now'))
            );

            -- Create indexes
            CREATE INDEX IF NOT EXISTS idx_kb_entries_category ON kb_entries(category);
            CREATE INDEX IF NOT EXISTS idx_kb_chunks_entry ON kb_chunks(entry_id);
            CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id);
            CREATE INDEX IF NOT EXISTS idx_agent_requests_status ON agent_requests(status);
            CREATE INDEX IF NOT EXISTS idx_conversation_summaries_user ON conversation_summaries(user_id);
        """)

        # Lightweight migrations for existing databases (SQLite can only ADD COLUMN).
        def _ensure_column(table: str, column: str, ddl_suffix: str) -> None:
            cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
            if any(r["name"] == column for r in cols):
                return
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_suffix}")

        _ensure_column("agent_requests", "telegram_username", "TEXT DEFAULT ''")
        _ensure_column("appointments", "telegram_username", "TEXT DEFAULT ''")


# ─── Knowledge Base CRUD ─────────────────────────────────────────────────────

def add_kb_entry(category: str, title: str, content: str, metadata: dict = None) -> int:
    """Add a new knowledge base entry. Returns the entry ID."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO kb_entries (category, title, content, metadata) VALUES (?, ?, ?, ?)",
            (category, title, content, json.dumps(metadata or {}))
        )
        return cursor.lastrowid


def update_kb_entry(entry_id: int, category: str, title: str, content: str, metadata: dict = None):
    """Update an existing knowledge base entry."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE kb_entries 
               SET category=?, title=?, content=?, metadata=?, updated_at=datetime('now') 
               WHERE id=?""",
            (category, title, content, json.dumps(metadata or {}), entry_id)
        )


def delete_kb_entry(entry_id: int):
    """Delete a knowledge base entry and its chunks."""
    with get_connection() as conn:
        conn.execute("DELETE FROM kb_entries WHERE id=?", (entry_id,))


def get_kb_entry(entry_id: int) -> Optional[dict]:
    """Get a single KB entry by ID."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM kb_entries WHERE id=?", (entry_id,)).fetchone()
        return dict(row) if row else None


def get_all_kb_entries(category: str = None, active_only: bool = True) -> list[dict]:
    """Get all KB entries, optionally filtered by category."""
    with get_connection() as conn:
        query = "SELECT * FROM kb_entries WHERE 1=1"
        params = []
        if active_only:
            query += " AND is_active=1"
        if category:
            query += " AND category=?"
            params.append(category)
        query += " ORDER BY category, title"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_kb_categories() -> list[str]:
    """Get distinct categories from the knowledge base."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT category FROM kb_entries WHERE is_active=1 ORDER BY category"
        ).fetchall()
        return [r["category"] for r in rows]


def count_kb_entries(category: str | None = None, active_only: bool = True) -> int:
    """Count KB entries, optionally filtered by category."""
    with get_connection() as conn:
        query = "SELECT COUNT(*) AS count FROM kb_entries WHERE 1=1"
        params: list[object] = []
        if active_only:
            query += " AND is_active=1"
        if category:
            query += " AND category=?"
            params.append(category)
        row = conn.execute(query, params).fetchone()
        return int(row["count"]) if row else 0


def count_kb_categories(active_only: bool = True) -> int:
    """Count distinct KB categories."""
    with get_connection() as conn:
        if active_only:
            row = conn.execute(
                "SELECT COUNT(DISTINCT category) AS count FROM kb_entries WHERE is_active=1"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(DISTINCT category) AS count FROM kb_entries"
            ).fetchone()
        return int(row["count"]) if row else 0


# ─── Chunks ──────────────────────────────────────────────────────────────────

def save_chunks(entry_id: int, chunks: list[dict]):
    """Save chunks for a KB entry (replaces existing chunks)."""
    with get_connection() as conn:
        conn.execute("DELETE FROM kb_chunks WHERE entry_id=?", (entry_id,))
        for chunk in chunks:
            conn.execute(
                "INSERT INTO kb_chunks (entry_id, chunk_index, chunk_text, embedding) VALUES (?, ?, ?, ?)",
                (entry_id, chunk["index"], chunk["text"], chunk.get("embedding"))
            )


def get_all_chunks() -> list[dict]:
    """Get all chunks with their entry info for building the FAISS index."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT c.id, c.entry_id, c.chunk_index, c.chunk_text, c.embedding,
                   e.category, e.title
            FROM kb_chunks c
            JOIN kb_entries e ON c.entry_id = e.id
            WHERE e.is_active = 1
            ORDER BY c.id
        """).fetchall()
        return [dict(r) for r in rows]


# ─── Conversations ───────────────────────────────────────────────────────────

def save_message(user_id: str, username: str, role: str, message: str, sources: str = ""):
    """Save a conversation message."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO conversations (user_id, username, role, message, sources) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, role, message, sources)
        )


def get_conversation_history(user_id: str, limit: int = 20) -> list[dict]:
    """Get recent conversation history for a user."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT role, message, sources, created_at 
               FROM conversations WHERE user_id=? 
               ORDER BY id DESC LIMIT ?""",
            (user_id, limit)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def get_all_conversations(limit: int = 100) -> list[dict]:
    """Get all conversations for the admin panel."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT user_id, username, role, message, sources, created_at 
               FROM conversations ORDER BY id DESC LIMIT ?""",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_unique_users() -> list[dict]:
    """Get list of unique users with their last message time."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT user_id, username, 
                   MAX(created_at) as last_active,
                   COUNT(*) as message_count
            FROM conversations 
            GROUP BY user_id 
            ORDER BY last_active DESC
        """).fetchall()
        return [dict(r) for r in rows]


def get_unsummarized_message_count(user_id: str) -> int:
    """Count messages for a user that haven't been included in any summary yet."""
    with get_connection() as conn:
        # Get the total messages covered by summaries
        summary_row = conn.execute(
            "SELECT COALESCE(SUM(message_count), 0) AS total FROM conversation_summaries WHERE user_id=?",
            (user_id,)
        ).fetchone()
        summarized_count = int(summary_row["total"])

        total_row = conn.execute(
            "SELECT COUNT(*) AS count FROM conversations WHERE user_id=?",
            (user_id,)
        ).fetchone()
        total_count = int(total_row["count"])

        return max(0, total_count - summarized_count)


def get_messages_for_summarization(user_id: str, limit: int) -> list[dict]:
    """Get the oldest unsummarized messages for a user (to create a summary from)."""
    with get_connection() as conn:
        # Get total summarized count to know the offset
        summary_row = conn.execute(
            "SELECT COALESCE(SUM(message_count), 0) AS total FROM conversation_summaries WHERE user_id=?",
            (user_id,)
        ).fetchone()
        offset = int(summary_row["total"])

        rows = conn.execute(
            """SELECT role, message, created_at
               FROM conversations WHERE user_id=?
               ORDER BY id ASC LIMIT ? OFFSET ?""",
            (user_id, limit, offset)
        ).fetchall()
        return [dict(r) for r in rows]


def save_conversation_summary(user_id: str, summary_text: str, message_count: int):
    """
    Save a conversation summary for a user.

    Replaces all previous summaries with a single merged summary.
    The message_count is accumulated from prior summaries so that
    offset tracking remains correct.
    """
    with get_connection() as conn:
        # Accumulate total message count from existing summaries
        row = conn.execute(
            "SELECT COALESCE(SUM(message_count), 0) AS total FROM conversation_summaries WHERE user_id=?",
            (user_id,)
        ).fetchone()
        total_message_count = int(row["total"]) + message_count

        # Replace all previous summaries with the new merged one
        conn.execute("DELETE FROM conversation_summaries WHERE user_id=?", (user_id,))
        conn.execute(
            "INSERT INTO conversation_summaries (user_id, summary_text, message_count) VALUES (?, ?, ?)",
            (user_id, summary_text, total_message_count)
        )


def get_latest_summary(user_id: str) -> dict | None:
    """Get the latest (single) conversation summary for a user."""
    with get_connection() as conn:
        row = conn.execute(
            """SELECT summary_text, message_count, created_at
               FROM conversation_summaries WHERE user_id=?
               ORDER BY id DESC LIMIT 1""",
            (user_id,)
        ).fetchone()
        return dict(row) if row else None


def count_unique_users() -> int:
    """Count distinct users in conversation history."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT user_id) AS count FROM conversations"
        ).fetchone()
        return int(row["count"]) if row else 0


# ─── Agent Requests ──────────────────────────────────────────────────────────

def create_agent_request(
    user_id: str,
    username: str,
    message: str = "",
    telegram_username: str = "",
) -> int:
    """Create a new agent transfer request."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO agent_requests (user_id, username, telegram_username, message) VALUES (?, ?, ?, ?)",
            (user_id, username, telegram_username or "", message)
        )
        return cursor.lastrowid


def get_agent_requests(status: str | None = None, limit: int | None = None) -> list[dict]:
    """Get agent requests, optionally filtered by status."""
    with get_connection() as conn:
        params: list[object] = []
        if status:
            query = "SELECT * FROM agent_requests WHERE status=? ORDER BY created_at DESC"
            params.append(status)
        else:
            query = "SELECT * FROM agent_requests ORDER BY created_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def count_agent_requests(status: str | None = None) -> int:
    """Count agent requests, optionally filtered by status."""
    with get_connection() as conn:
        if status:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM agent_requests WHERE status=?",
                (status,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM agent_requests"
            ).fetchone()
        return int(row["count"]) if row else 0


def update_agent_request_status(request_id: int, status: str):
    """Update the status of an agent request."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE agent_requests SET status=?, handled_at=datetime('now') WHERE id=?",
            (status, request_id)
        )


def get_agent_request(request_id: int) -> Optional[dict]:
    """Get a single agent request by ID."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM agent_requests WHERE id=?", (request_id,)).fetchone()
        return dict(row) if row else None


# ─── Appointments ────────────────────────────────────────────────────────────

def create_appointment(
    user_id: str,
    username: str,
    service: str = "",
    preferred_date: str = "",
    preferred_time: str = "",
    notes: str = "",
    telegram_username: str = "",
) -> int:
    """Create a new appointment booking."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO appointments (user_id, username, telegram_username, service, preferred_date, preferred_time, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, username, telegram_username or "", service, preferred_date, preferred_time, notes)
        )
        return cursor.lastrowid


def get_appointments(status: str | None = None, limit: int | None = None) -> list[dict]:
    """Get appointments, optionally filtered by status."""
    with get_connection() as conn:
        params: list[object] = []
        if status:
            query = "SELECT * FROM appointments WHERE status=? ORDER BY created_at DESC"
            params.append(status)
        else:
            query = "SELECT * FROM appointments ORDER BY created_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def count_appointments(status: str | None = None) -> int:
    """Count appointments, optionally filtered by status."""
    with get_connection() as conn:
        if status:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM appointments WHERE status=?",
                (status,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM appointments"
            ).fetchone()
        return int(row["count"]) if row else 0


def update_appointment_status(appt_id: int, status: str):
    """Update appointment status."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE appointments SET status=? WHERE id=?",
            (status, appt_id)
        )


def get_appointment(appt_id: int) -> Optional[dict]:
    """Get a single appointment by ID."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM appointments WHERE id=?", (appt_id,)).fetchone()
        return dict(row) if row else None
