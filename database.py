"""
Database module — SQLite storage for knowledge base, conversations, and notifications.
"""

import logging
import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

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
                id                          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id                     TEXT NOT NULL,
                summary_text                TEXT NOT NULL,
                message_count               INTEGER NOT NULL DEFAULT 0,
                last_summarized_message_id  INTEGER NOT NULL DEFAULT 0,
                created_at                  TEXT DEFAULT (datetime('now'))
            );

            -- Live chat sessions (business owner takes over a conversation)
            CREATE TABLE IF NOT EXISTS live_chats (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                username    TEXT DEFAULT '',
                is_active   INTEGER DEFAULT 1,
                started_at  TEXT DEFAULT (datetime('now')),
                ended_at    TEXT
            );

            -- Unanswered questions (knowledge gaps)
            CREATE TABLE IF NOT EXISTS unanswered_questions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                username    TEXT DEFAULT '',
                question    TEXT NOT NULL,
                status      TEXT DEFAULT 'open' CHECK(status IN ('open', 'resolved')),
                created_at  TEXT DEFAULT (datetime('now')),
                resolved_at TEXT
            );

            -- Business hours (weekly schedule)
            CREATE TABLE IF NOT EXISTS business_hours (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                day_of_week INTEGER NOT NULL CHECK(day_of_week BETWEEN 0 AND 6),
                open_time   TEXT,
                close_time  TEXT,
                is_closed   INTEGER DEFAULT 0,
                UNIQUE(day_of_week)
            );

            -- Special days (holidays, one-time closures, custom hours)
            CREATE TABLE IF NOT EXISTS special_days (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT NOT NULL UNIQUE,
                name        TEXT NOT NULL,
                open_time   TEXT,
                close_time  TEXT,
                is_closed   INTEGER DEFAULT 1,
                notes       TEXT DEFAULT '',
                created_at  TEXT DEFAULT (datetime('now'))
            );

            -- Referrals (מערכת הפניות)
            CREATE TABLE IF NOT EXISTS referrals (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id     TEXT NOT NULL,
                referred_id     TEXT,
                code            TEXT NOT NULL UNIQUE,
                status          TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'completed')),
                created_at      TEXT DEFAULT (datetime('now')),
                completed_at    TEXT
            );

            -- Referral credits (זיכויים מהפניות)
            CREATE TABLE IF NOT EXISTS credits (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         TEXT NOT NULL,
                amount          REAL NOT NULL,
                type            TEXT NOT NULL CHECK(type IN ('referrer', 'referred')),
                reason          TEXT DEFAULT '',
                used            INTEGER DEFAULT 0,
                expires_at      TEXT NOT NULL,
                created_at      TEXT DEFAULT (datetime('now'))
            );

            -- Vacation mode (שורה בודדת — תמיד id=1)
            CREATE TABLE IF NOT EXISTS vacation_mode (
                id                  INTEGER PRIMARY KEY CHECK(id = 1),
                is_active           INTEGER DEFAULT 0,
                vacation_end_date   TEXT DEFAULT '',
                vacation_message    TEXT DEFAULT '',
                updated_at          TEXT DEFAULT (datetime('now'))
            );
            INSERT OR IGNORE INTO vacation_mode (id) VALUES (1);

            -- Create indexes
            CREATE INDEX IF NOT EXISTS idx_kb_entries_category ON kb_entries(category);
            CREATE INDEX IF NOT EXISTS idx_kb_chunks_entry ON kb_chunks(entry_id);
            CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id);
            CREATE INDEX IF NOT EXISTS idx_agent_requests_status ON agent_requests(status);
            CREATE INDEX IF NOT EXISTS idx_conversation_summaries_user ON conversation_summaries(user_id);
            CREATE INDEX IF NOT EXISTS idx_live_chats_user_active ON live_chats(user_id, is_active);
            CREATE INDEX IF NOT EXISTS idx_unanswered_questions_status ON unanswered_questions(status);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_special_days_date_unique ON special_days(date);
            CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id);
            CREATE INDEX IF NOT EXISTS idx_referrals_referred ON referrals(referred_id);
            CREATE INDEX IF NOT EXISTS idx_referrals_code ON referrals(code);
            CREATE INDEX IF NOT EXISTS idx_credits_user ON credits(user_id);
        """)

        # Lightweight migrations for existing databases (SQLite can only ADD COLUMN).
        def _ensure_column(table: str, column: str, ddl_suffix: str) -> None:
            cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
            if any(r["name"] == column for r in cols):
                return
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_suffix}")

        _ensure_column("agent_requests", "telegram_username", "TEXT DEFAULT ''")
        _ensure_column("appointments", "telegram_username", "TEXT DEFAULT ''")
        _ensure_column(
            "conversation_summaries",
            "last_summarized_message_id",
            "INTEGER NOT NULL DEFAULT 0",
        )

        # Back-fill last_summarized_message_id for rows migrated from the old
        # COUNT-based offset scheme.  For each user whose summary still has
        # last_summarized_message_id=0 *and* a positive message_count, look up
        # the N-th conversation row (ordered by id ASC) and store its id.
        rows = conn.execute(
            "SELECT id, user_id, message_count FROM conversation_summaries "
            "WHERE last_summarized_message_id = 0 AND message_count > 0"
        ).fetchall()
        for row in rows:
            last_msg = conn.execute(
                "SELECT id FROM conversations WHERE user_id = ? "
                "ORDER BY id ASC LIMIT 1 OFFSET ?",
                (row["user_id"], row["message_count"] - 1),
            ).fetchone()
            if last_msg:
                conn.execute(
                    "UPDATE conversation_summaries SET last_summarized_message_id = ? WHERE id = ?",
                    (last_msg["id"], row["id"]),
                )

        # Migrate special_days: deduplicate and add UNIQUE index on date.
        # Check if the unique index already exists.
        existing_indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='special_days' AND name='idx_special_days_date_unique'"
        ).fetchone()
        if not existing_indexes:
            # Remove duplicates, keeping the most recent entry per date
            conn.execute("""
                DELETE FROM special_days WHERE id NOT IN (
                    SELECT MAX(id) FROM special_days GROUP BY date
                )
            """)
            # The idx_special_days_date index (non-unique) was created above;
            # drop it and create a unique one instead.
            conn.execute("DROP INDEX IF EXISTS idx_special_days_date")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_special_days_date_unique ON special_days(date)"
            )


def cleanup_stale_live_chats():
    """Deactivate live chat sessions left over from a previous bot run.

    Called from the bot startup path only — not from init_db() — so that
    a bot-only restart doesn't silently end sessions still managed by
    the admin panel running in a separate process.
    """
    with get_connection() as conn:
        stale = conn.execute(
            "SELECT COUNT(*) AS cnt FROM live_chats WHERE is_active = 1"
        ).fetchone()["cnt"]
        if stale:
            conn.execute(
                "UPDATE live_chats SET is_active = 0, ended_at = datetime('now') WHERE is_active = 1"
            )
            logger.info("Cleaned up %d stale live chat session(s) from previous run.", stale)


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


def get_chunks_for_entries(entry_ids: list[int]) -> dict[int, list[dict]]:
    """Get existing chunks (with embeddings) grouped by entry_id.

    Only returns chunks whose embedding is not NULL, suitable for reuse
    during incremental index rebuilds.
    """
    if not entry_ids:
        return {}
    with get_connection() as conn:
        placeholders = ",".join("?" for _ in entry_ids)
        rows = conn.execute(
            f"""SELECT c.id, c.entry_id, c.chunk_index, c.chunk_text, c.embedding,
                       e.category, e.title
                FROM kb_chunks c
                JOIN kb_entries e ON c.entry_id = e.id
                WHERE c.entry_id IN ({placeholders}) AND c.embedding IS NOT NULL
                ORDER BY c.entry_id, c.chunk_index""",
            entry_ids,
        ).fetchall()
        result: dict[int, list[dict]] = {}
        for r in rows:
            d = dict(r)
            result.setdefault(d["entry_id"], []).append(d)
        return result


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
            """SELECT role, username, message, sources, created_at
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


def get_username_for_user(user_id: str) -> Optional[str]:
    """Look up the display name for a single user without scanning all users."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT username FROM conversations WHERE user_id = ? AND username != '' "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return row["username"] if row else None


def _last_summarized_message_id(conn, user_id: str) -> int:
    """Return the highest conversation id already covered by a summary (0 if none)."""
    row = conn.execute(
        "SELECT COALESCE(MAX(last_summarized_message_id), 0) AS last_id "
        "FROM conversation_summaries WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return int(row["last_id"])


def get_unsummarized_message_count(user_id: str) -> int:
    """Count messages for a user that haven't been included in any summary yet.

    Uses `last_summarized_message_id` so the count stays correct even when
    older messages are deleted.
    """
    with get_connection() as conn:
        last_id = _last_summarized_message_id(conn, user_id)

        row = conn.execute(
            "SELECT COUNT(*) AS count FROM conversations "
            "WHERE user_id = ? AND id > ?",
            (user_id, last_id),
        ).fetchone()
        return int(row["count"])


def get_messages_for_summarization(user_id: str, limit: int) -> list[dict]:
    """Get the oldest unsummarized messages for a user (to create a summary from).

    Returns up to *limit* messages whose ``id`` is greater than the
    ``last_summarized_message_id`` stored in the latest summary.
    Each returned dict includes the conversation row ``id`` so that
    :func:`save_conversation_summary` can record the new high-water mark.
    """
    with get_connection() as conn:
        last_id = _last_summarized_message_id(conn, user_id)

        rows = conn.execute(
            """SELECT id, role, message, created_at
               FROM conversations WHERE user_id = ? AND id > ?
               ORDER BY id ASC LIMIT ?""",
            (user_id, last_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def save_conversation_summary(
    user_id: str,
    summary_text: str,
    message_count: int,
    last_summarized_message_id: int = 0,
):
    """
    Save a conversation summary for a user.

    Replaces all previous summaries with a single merged summary.
    ``last_summarized_message_id`` is the ``conversations.id`` of the newest
    message included in this summary — subsequent queries use it as a
    high-water mark so that counting stays correct even when rows are deleted.
    ``message_count`` is accumulated for informational / admin-display purposes.
    """
    with get_connection() as conn:
        # Accumulate total message count from existing summaries
        row = conn.execute(
            "SELECT COALESCE(SUM(message_count), 0) AS total FROM conversation_summaries WHERE user_id=?",
            (user_id,)
        ).fetchone()
        total_message_count = int(row["total"]) + message_count

        # If no explicit high-water mark was given, keep the previous one
        if not last_summarized_message_id:
            last_summarized_message_id = _last_summarized_message_id(conn, user_id)

        # Replace all previous summaries with the new merged one
        conn.execute("DELETE FROM conversation_summaries WHERE user_id=?", (user_id,))
        conn.execute(
            "INSERT INTO conversation_summaries "
            "(user_id, summary_text, message_count, last_summarized_message_id) "
            "VALUES (?, ?, ?, ?)",
            (user_id, summary_text, total_message_count, last_summarized_message_id),
        )


def get_latest_summary(user_id: str) -> dict | None:
    """Get the latest (single) conversation summary for a user."""
    with get_connection() as conn:
        row = conn.execute(
            """SELECT summary_text, message_count, last_summarized_message_id, created_at
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


# ─── Live Chats ─────────────────────────────────────────────────────────────

def start_live_chat(user_id: str, username: str = "") -> int:
    """Start a live chat session for a user. Returns the session ID."""
    with get_connection() as conn:
        # End any existing active session for this user first
        conn.execute(
            "UPDATE live_chats SET is_active=0, ended_at=datetime('now') WHERE user_id=? AND is_active=1",
            (user_id,)
        )
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO live_chats (user_id, username) VALUES (?, ?)",
            (user_id, username)
        )
        return cursor.lastrowid


def end_live_chat(user_id: str):
    """End the active live chat session for a user."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE live_chats SET is_active=0, ended_at=datetime('now') WHERE user_id=? AND is_active=1",
            (user_id,)
        )


def get_active_live_chat(user_id: str) -> Optional[dict]:
    """Get the active live chat session for a user, or None."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM live_chats WHERE user_id=? AND is_active=1 ORDER BY id DESC LIMIT 1",
            (user_id,)
        ).fetchone()
        return dict(row) if row else None


def is_live_chat_active(user_id: str) -> bool:
    """Check if a user has an active live chat session."""
    return get_active_live_chat(user_id) is not None


def get_all_active_live_chats() -> list[dict]:
    """Get all currently active live chat sessions."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM live_chats WHERE is_active=1 ORDER BY started_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def count_active_live_chats() -> int:
    """Count currently active live chat sessions."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM live_chats WHERE is_active=1"
        ).fetchone()
        return int(row["count"]) if row else 0


# ─── Unanswered Questions (Knowledge Gaps) ──────────────────────────────────

def save_unanswered_question(user_id: str, username: str, question: str):
    """Log a question that the bot could not answer (fallback triggered)."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO unanswered_questions (user_id, username, question) VALUES (?, ?, ?)",
            (user_id, username, question),
        )


def get_unanswered_questions(status: str | None = None, limit: int | None = None) -> list[dict]:
    """Get unanswered questions, optionally filtered by status."""
    with get_connection() as conn:
        params: list[object] = []
        if status:
            query = "SELECT * FROM unanswered_questions WHERE status=? ORDER BY created_at DESC"
            params.append(status)
        else:
            query = "SELECT * FROM unanswered_questions ORDER BY created_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def count_unanswered_questions(status: str | None = None) -> int:
    """Count unanswered questions, optionally filtered by status."""
    with get_connection() as conn:
        if status:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM unanswered_questions WHERE status=?",
                (status,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM unanswered_questions"
            ).fetchone()
        return int(row["count"]) if row else 0


def update_unanswered_question_status(question_id: int, status: str):
    """Update the status of an unanswered question."""
    with get_connection() as conn:
        resolved_at = "datetime('now')" if status == "resolved" else "NULL"
        conn.execute(
            f"UPDATE unanswered_questions SET status=?, resolved_at={resolved_at} WHERE id=?",
            (status, question_id),
        )


def get_unanswered_question(question_id: int) -> Optional[dict]:
    """Get a single unanswered question by ID."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM unanswered_questions WHERE id=?", (question_id,)
        ).fetchone()
        return dict(row) if row else None


# ─── Business Hours ─────────────────────────────────────────────────────────

def get_all_business_hours() -> list[dict]:
    """Get all business hours entries, ordered by day of week."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM business_hours ORDER BY day_of_week"
        ).fetchall()
        return [dict(r) for r in rows]


def get_business_hours_for_day(day_of_week: int) -> Optional[dict]:
    """Get business hours for a specific day of week (0=Sunday .. 6=Saturday)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM business_hours WHERE day_of_week=?",
            (day_of_week,),
        ).fetchone()
        return dict(row) if row else None


def upsert_business_hours(day_of_week: int, open_time: str, close_time: str, is_closed: bool):
    """Insert or update business hours for a day of week."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO business_hours (day_of_week, open_time, close_time, is_closed)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(day_of_week)
               DO UPDATE SET open_time=excluded.open_time,
                             close_time=excluded.close_time,
                             is_closed=excluded.is_closed""",
            (day_of_week, open_time, close_time, int(is_closed)),
        )


def seed_default_business_hours():
    """Populate default business hours if table is empty."""
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM business_hours").fetchone()["c"]
        if count > 0:
            return
        defaults = [
            # day_of_week, open_time, close_time, is_closed
            (0, "09:00", "19:00", 0),  # Sunday
            (1, "09:00", "19:00", 0),  # Monday
            (2, "09:00", "20:00", 0),  # Tuesday
            (3, "09:00", "19:00", 0),  # Wednesday
            (4, "09:00", "19:00", 0),  # Thursday
            (5, "09:00", "14:00", 0),  # Friday
            (6, None, None, 1),        # Saturday — closed
        ]
        conn.executemany(
            "INSERT INTO business_hours (day_of_week, open_time, close_time, is_closed) VALUES (?, ?, ?, ?)",
            defaults,
        )


# ─── Special Days (Holidays & Exceptions) ───────────────────────────────────

def get_all_special_days() -> list[dict]:
    """Get all special days, ordered by date."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM special_days ORDER BY date"
        ).fetchall()
        return [dict(r) for r in rows]


def get_special_day_by_date(date_str: str) -> Optional[dict]:
    """Get a special day entry for a given date (YYYY-MM-DD)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM special_days WHERE date=?", (date_str,)
        ).fetchone()
        return dict(row) if row else None


def add_special_day(
    date_str: str,
    name: str,
    is_closed: bool = True,
    open_time: str = None,
    close_time: str = None,
    notes: str = "",
) -> int:
    """Add or replace a special day for the given date. Returns the entry ID.

    Uses INSERT OR REPLACE so that admin overrides for an existing date
    (e.g. overriding a seeded holiday) take effect instead of silently
    creating a duplicate.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT OR REPLACE INTO special_days (date, name, open_time, close_time, is_closed, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (date_str, name, open_time, close_time, int(is_closed), notes),
        )
        return cursor.lastrowid


def update_special_day(
    special_day_id: int,
    date_str: str,
    name: str,
    is_closed: bool = True,
    open_time: str = None,
    close_time: str = None,
    notes: str = "",
):
    """Update an existing special day."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE special_days
               SET date=?, name=?, open_time=?, close_time=?, is_closed=?, notes=?
               WHERE id=?""",
            (date_str, name, open_time, close_time, int(is_closed), notes, special_day_id),
        )


def delete_special_day(special_day_id: int):
    """Delete a special day entry."""
    with get_connection() as conn:
        conn.execute("DELETE FROM special_days WHERE id=?", (special_day_id,))


# ─── Vacation Mode ──────────────────────────────────────────────────────────

def get_vacation_mode() -> dict:
    """קבלת מצב חופשה נוכחי. מחזיר dict עם is_active, vacation_end_date, vacation_message."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM vacation_mode WHERE id = 1").fetchone()
        if row:
            return dict(row)
        # fallback — לא אמור לקרות כי init_db מכניס שורה
        return {"id": 1, "is_active": 0, "vacation_end_date": "", "vacation_message": "", "updated_at": ""}


def update_vacation_mode(is_active: bool, vacation_end_date: str = "", vacation_message: str = ""):
    """עדכון הגדרות מצב חופשה."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE vacation_mode
               SET is_active = ?, vacation_end_date = ?, vacation_message = ?,
                   updated_at = datetime('now')
               WHERE id = 1""",
            (int(is_active), vacation_end_date, vacation_message),
        )


# ─── Referrals (מערכת הפניות) ────────────────────────────────────────────

def generate_referral_code(user_id: str) -> str:
    """יצירת קוד הפניה ייחודי למשתמש. אם כבר קיים — מחזיר את הקוד הקיים."""
    import hashlib
    with get_connection() as conn:
        row = conn.execute(
            "SELECT code FROM referrals WHERE referrer_id = ? LIMIT 1",
            (user_id,),
        ).fetchone()
        if row:
            return row["code"]

        # יצירת קוד ייחודי על בסיס user_id + timestamp
        raw = f"{user_id}_{datetime.now().isoformat()}"
        short_hash = hashlib.sha256(raw.encode()).hexdigest()[:8].upper()
        code = f"REF_{short_hash}"

        # וידוא ייחודיות (מקרה קצה נדיר של התנגשות)
        while conn.execute("SELECT 1 FROM referrals WHERE code = ?", (code,)).fetchone():
            raw += "_retry"
            short_hash = hashlib.sha256(raw.encode()).hexdigest()[:8].upper()
            code = f"REF_{short_hash}"

        conn.execute(
            "INSERT INTO referrals (referrer_id, code) VALUES (?, ?)",
            (user_id, code),
        )
        return code


def get_referral_by_code(code: str) -> Optional[dict]:
    """חיפוש הפניה לפי קוד."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM referrals WHERE code = ?", (code,)
        ).fetchone()
        return dict(row) if row else None


def register_referral(code: str, referred_id: str) -> bool:
    """רישום הפניה — מקשר את המשתמש החדש לקוד ההפניה.

    מחזיר True אם הרישום הצליח, False אם הקוד לא קיים, כבר מנוצל,
    או שהמשתמש מנסה להפנות את עצמו.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM referrals WHERE code = ?", (code,)
        ).fetchone()
        if not row:
            return False
        # לא מאפשרים הפניה עצמית
        if row["referrer_id"] == referred_id:
            return False
        # הקוד כבר שויך למשתמש אחר
        if row["referred_id"]:
            return False
        # בדיקה שהמשתמש החדש לא כבר הופנה על ידי מישהו אחר
        existing = conn.execute(
            "SELECT 1 FROM referrals WHERE referred_id = ?", (referred_id,)
        ).fetchone()
        if existing:
            return False

        conn.execute(
            "UPDATE referrals SET referred_id = ? WHERE code = ?",
            (referred_id, code),
        )
        return True


def complete_referral(referred_id: str) -> bool:
    """הפעלת ההפניה — נקרא לאחר שהלקוח המופנה השלים תור ראשון.

    יוצר זיכויים (credits) לשני הצדדים. מחזיר True אם הופעל בהצלחה.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM referrals WHERE referred_id = ? AND status = 'pending'",
            (referred_id,),
        ).fetchone()
        if not row:
            return False

        now = datetime.now()
        # תוקף הזיכוי — חודשיים מרגע ההפעלה
        expires_at = (now + timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")

        # סימון ההפניה כהושלמה
        conn.execute(
            "UPDATE referrals SET status = 'completed', completed_at = datetime('now') WHERE id = ?",
            (row["id"],),
        )

        # זיכוי למפנה — 10% הנחה
        conn.execute(
            "INSERT INTO credits (user_id, amount, type, reason, expires_at) VALUES (?, ?, ?, ?, ?)",
            (row["referrer_id"], 10.0, "referrer", f"הפניית לקוח חדש (קוד: {row['code']})", expires_at),
        )

        # זיכוי למופנה — 10% הנחה
        conn.execute(
            "INSERT INTO credits (user_id, amount, type, reason, expires_at) VALUES (?, ?, ?, ?, ?)",
            (referred_id, 10.0, "referred", f"הצטרפות דרך הפניה (קוד: {row['code']})", expires_at),
        )

        return True


def get_user_referral_code(user_id: str) -> Optional[str]:
    """החזרת קוד ההפניה של משתמש (אם קיים)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT code FROM referrals WHERE referrer_id = ? LIMIT 1",
            (user_id,),
        ).fetchone()
        return row["code"] if row else None


def get_active_credits(user_id: str) -> list[dict]:
    """החזרת זיכויים פעילים (לא נוצלו ולא פגו) של משתמש."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM credits
               WHERE user_id = ? AND used = 0 AND expires_at > datetime('now')
               ORDER BY expires_at ASC""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def use_credit(credit_id: int):
    """סימון זיכוי כמנוצל."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE credits SET used = 1 WHERE id = ?",
            (credit_id,),
        )


def count_referrals(user_id: str, status: str | None = None) -> int:
    """ספירת הפניות של משתמש מפנה."""
    with get_connection() as conn:
        if status:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM referrals WHERE referrer_id = ? AND status = ?",
                (user_id, status),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM referrals WHERE referrer_id = ? AND referred_id IS NOT NULL",
                (user_id,),
            ).fetchone()
        return int(row["count"]) if row else 0


def get_referral_stats() -> dict:
    """סטטיסטיקות הפניות לדשבורד האדמין."""
    with get_connection() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM referrals WHERE referred_id IS NOT NULL"
        ).fetchone()["c"]
        completed = conn.execute(
            "SELECT COUNT(*) AS c FROM referrals WHERE status = 'completed'"
        ).fetchone()["c"]
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM referrals WHERE status = 'pending' AND referred_id IS NOT NULL"
        ).fetchone()["c"]
        active_credits = conn.execute(
            "SELECT COUNT(*) AS c FROM credits WHERE used = 0 AND expires_at > datetime('now')"
        ).fetchone()["c"]
        return {
            "total_referrals": total,
            "completed_referrals": completed,
            "pending_referrals": pending,
            "active_credits": active_credits,
        }


def get_top_referrers(limit: int = 10) -> list[dict]:
    """החזרת מפנים מובילים (לפי כמות הפניות שהושלמו)."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT r.referrer_id,
                      COUNT(*) AS total_referrals,
                      SUM(CASE WHEN r.status = 'completed' THEN 1 ELSE 0 END) AS completed_referrals
               FROM referrals r
               WHERE r.referred_id IS NOT NULL
               GROUP BY r.referrer_id
               ORDER BY completed_referrals DESC, total_referrals DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_referrals(limit: int | None = None) -> list[dict]:
    """החזרת כל ההפניות לפאנל האדמין."""
    with get_connection() as conn:
        query = """SELECT r.*,
                          c_referrer.username AS referrer_name,
                          c_referred.username AS referred_name
                   FROM referrals r
                   LEFT JOIN (SELECT user_id, username FROM conversations WHERE username != ''
                              GROUP BY user_id) c_referrer ON r.referrer_id = c_referrer.user_id
                   LEFT JOIN (SELECT user_id, username FROM conversations WHERE username != ''
                              GROUP BY user_id) c_referred ON r.referred_id = c_referred.user_id
                   ORDER BY r.created_at DESC"""
        params: list[object] = []
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def has_pending_referral(user_id: str) -> bool:
    """בדיקה האם למשתמש יש הפניה ממתינה (נרשם דרך קוד אבל עוד לא השלים תור)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM referrals WHERE referred_id = ? AND status = 'pending'",
            (user_id,),
        ).fetchone()
        return row is not None


def has_completed_appointment(user_id: str) -> bool:
    """בדיקה האם למשתמש יש לפחות תור אחד שהושלם (confirmed)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM appointments WHERE user_id = ? AND status = 'confirmed'",
            (user_id,),
        ).fetchone()
        return row is not None
