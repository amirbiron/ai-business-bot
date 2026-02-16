"""
Database module — SQLite storage for knowledge base, conversations, and notifications.
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from ai_chatbot.config import DB_PATH


def get_connection() -> sqlite3.Connection:
    """Get a SQLite connection with row factory enabled."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create all tables if they don't exist."""
    conn = get_connection()
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
            service     TEXT DEFAULT '',
            preferred_date TEXT DEFAULT '',
            preferred_time TEXT DEFAULT '',
            notes       TEXT DEFAULT '',
            status      TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'confirmed', 'cancelled')),
            created_at  TEXT DEFAULT (datetime('now'))
        );

        -- Create indexes
        CREATE INDEX IF NOT EXISTS idx_kb_entries_category ON kb_entries(category);
        CREATE INDEX IF NOT EXISTS idx_kb_chunks_entry ON kb_chunks(entry_id);
        CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id);
        CREATE INDEX IF NOT EXISTS idx_agent_requests_status ON agent_requests(status);
    """)

    conn.commit()
    conn.close()


# ─── Knowledge Base CRUD ─────────────────────────────────────────────────────

def add_kb_entry(category: str, title: str, content: str, metadata: dict = None) -> int:
    """Add a new knowledge base entry. Returns the entry ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO kb_entries (category, title, content, metadata) VALUES (?, ?, ?, ?)",
        (category, title, content, json.dumps(metadata or {}))
    )
    entry_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return entry_id


def update_kb_entry(entry_id: int, category: str, title: str, content: str, metadata: dict = None):
    """Update an existing knowledge base entry."""
    conn = get_connection()
    conn.execute(
        """UPDATE kb_entries 
           SET category=?, title=?, content=?, metadata=?, updated_at=datetime('now') 
           WHERE id=?""",
        (category, title, content, json.dumps(metadata or {}), entry_id)
    )
    conn.commit()
    conn.close()


def delete_kb_entry(entry_id: int):
    """Delete a knowledge base entry and its chunks."""
    conn = get_connection()
    conn.execute("DELETE FROM kb_entries WHERE id=?", (entry_id,))
    conn.commit()
    conn.close()


def get_kb_entry(entry_id: int) -> Optional[dict]:
    """Get a single KB entry by ID."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM kb_entries WHERE id=?", (entry_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_kb_entries(category: str = None, active_only: bool = True) -> list[dict]:
    """Get all KB entries, optionally filtered by category."""
    conn = get_connection()
    query = "SELECT * FROM kb_entries WHERE 1=1"
    params = []
    if active_only:
        query += " AND is_active=1"
    if category:
        query += " AND category=?"
        params.append(category)
    query += " ORDER BY category, title"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_kb_categories() -> list[str]:
    """Get distinct categories from the knowledge base."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT category FROM kb_entries WHERE is_active=1 ORDER BY category"
    ).fetchall()
    conn.close()
    return [r["category"] for r in rows]


# ─── Chunks ──────────────────────────────────────────────────────────────────

def save_chunks(entry_id: int, chunks: list[dict]):
    """Save chunks for a KB entry (replaces existing chunks)."""
    conn = get_connection()
    conn.execute("DELETE FROM kb_chunks WHERE entry_id=?", (entry_id,))
    for chunk in chunks:
        conn.execute(
            "INSERT INTO kb_chunks (entry_id, chunk_index, chunk_text, embedding) VALUES (?, ?, ?, ?)",
            (entry_id, chunk["index"], chunk["text"], chunk.get("embedding"))
        )
    conn.commit()
    conn.close()


def get_all_chunks() -> list[dict]:
    """Get all chunks with their entry info for building the FAISS index."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT c.id, c.entry_id, c.chunk_index, c.chunk_text, c.embedding,
               e.category, e.title
        FROM kb_chunks c
        JOIN kb_entries e ON c.entry_id = e.id
        WHERE e.is_active = 1
        ORDER BY c.id
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Conversations ───────────────────────────────────────────────────────────

def save_message(user_id: str, username: str, role: str, message: str, sources: str = ""):
    """Save a conversation message."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO conversations (user_id, username, role, message, sources) VALUES (?, ?, ?, ?, ?)",
        (user_id, username, role, message, sources)
    )
    conn.commit()
    conn.close()


def get_conversation_history(user_id: str, limit: int = 20) -> list[dict]:
    """Get recent conversation history for a user."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT role, message, sources, created_at 
           FROM conversations WHERE user_id=? 
           ORDER BY created_at DESC LIMIT ?""",
        (user_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


def get_all_conversations(limit: int = 100) -> list[dict]:
    """Get all conversations for the admin panel."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT user_id, username, role, message, sources, created_at 
           FROM conversations ORDER BY created_at DESC LIMIT ?""",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_unique_users() -> list[dict]:
    """Get list of unique users with their last message time."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT user_id, username, 
               MAX(created_at) as last_active,
               COUNT(*) as message_count
        FROM conversations 
        GROUP BY user_id 
        ORDER BY last_active DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Agent Requests ──────────────────────────────────────────────────────────

def create_agent_request(user_id: str, username: str, message: str = "") -> int:
    """Create a new agent transfer request."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO agent_requests (user_id, username, message) VALUES (?, ?, ?)",
        (user_id, username, message)
    )
    request_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return request_id


def get_agent_requests(status: str = None) -> list[dict]:
    """Get agent requests, optionally filtered by status."""
    conn = get_connection()
    if status:
        rows = conn.execute(
            "SELECT * FROM agent_requests WHERE status=? ORDER BY created_at DESC",
            (status,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM agent_requests ORDER BY created_at DESC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_agent_request_status(request_id: int, status: str):
    """Update the status of an agent request."""
    conn = get_connection()
    conn.execute(
        "UPDATE agent_requests SET status=?, handled_at=datetime('now') WHERE id=?",
        (status, request_id)
    )
    conn.commit()
    conn.close()


# ─── Appointments ────────────────────────────────────────────────────────────

def create_appointment(user_id: str, username: str, service: str = "",
                       preferred_date: str = "", preferred_time: str = "",
                       notes: str = "") -> int:
    """Create a new appointment booking."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO appointments (user_id, username, service, preferred_date, preferred_time, notes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, username, service, preferred_date, preferred_time, notes)
    )
    appt_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return appt_id


def get_appointments(status: str = None) -> list[dict]:
    """Get appointments, optionally filtered by status."""
    conn = get_connection()
    if status:
        rows = conn.execute(
            "SELECT * FROM appointments WHERE status=? ORDER BY created_at DESC",
            (status,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM appointments ORDER BY created_at DESC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_appointment_status(appt_id: int, status: str):
    """Update appointment status."""
    conn = get_connection()
    conn.execute(
        "UPDATE appointments SET status=? WHERE id=?",
        (status, appt_id)
    )
    conn.commit()
    conn.close()
