"""
מיגרציות קלות ל-SQLite — נקראות מ-init_db() בכל הפעלה.

SQLite תומך רק ב-ADD COLUMN, כך שמיגרציות מורכבות יותר (כמו שינוי
UNIQUE constraint או מעבר סכימה) דורשות CREATE TABLE + INSERT + DROP.
"""

import logging

logger = logging.getLogger(__name__)


def _ensure_column(conn, table: str, column: str, ddl_suffix: str) -> None:
    """הוספת עמודה אם לא קיימת (SQLite ADD COLUMN בלבד)."""
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if any(r["name"] == column for r in cols):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_suffix}")


def run_migrations(conn) -> None:
    """הפעלת כל המיגרציות הקלות — נקראת מתוך init_db() עם חיבור פתוח."""

    # ─── ADD COLUMN מיגרציות ───────────────────────────────────────────────
    _ensure_column(conn, "agent_requests", "telegram_username", "TEXT DEFAULT ''")
    _ensure_column(conn, "appointments", "telegram_username", "TEXT DEFAULT ''")
    _ensure_column(
        conn,
        "conversation_summaries",
        "last_summarized_message_id",
        "INTEGER NOT NULL DEFAULT 0",
    )

    # ─── Back-fill last_summarized_message_id ─────────────────────────────
    # שורות ישנות שמיגרו מהסכימה הקודמת (COUNT-based offset) — מחשבים את
    # ה-high-water mark מהיסטוריית השיחות.
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

    # ─── special_days: כפילויות + UNIQUE index ────────────────────────────
    existing_indexes = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='special_days' AND name='idx_special_days_date_unique'"
    ).fetchone()
    if not existing_indexes:
        # מחיקת כפילויות — שומר רק את הרשומה האחרונה לכל תאריך
        dup_cursor = conn.execute("""
            SELECT COUNT(*) AS cnt FROM special_days WHERE id NOT IN (
                SELECT MAX(id) FROM special_days GROUP BY date
            )
        """)
        dup_count = dup_cursor.fetchone()["cnt"]
        if dup_count:
            logger.warning("Removing %d duplicate special_days entries during migration", dup_count)
        conn.execute("""
            DELETE FROM special_days WHERE id NOT IN (
                SELECT MAX(id) FROM special_days GROUP BY date
            )
        """)
        conn.execute("DROP INDEX IF EXISTS idx_special_days_date")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_special_days_date_unique ON special_days(date)"
        )

    # ─── referrals: מודל הפניה-בודדת → ריבוי-הפניות ──────────────────────
    referral_cols = {
        c["name"]: c
        for c in conn.execute("PRAGMA table_info(referrals)").fetchall()
    }
    referred_id_col = referral_cols.get("referred_id")
    if referred_id_col and not referred_id_col["notnull"]:
        # סכימה ישנה — referred_id nullable → צריך מיגרציה
        conn.execute("""
            INSERT OR IGNORE INTO referral_codes (user_id, code, created_at)
            SELECT referrer_id, code, MIN(created_at)
            FROM referrals GROUP BY referrer_id
        """)
        conn.execute("ALTER TABLE referrals RENAME TO _referrals_old")
        conn.execute("""
            CREATE TABLE referrals (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id     TEXT NOT NULL,
                referred_id     TEXT NOT NULL UNIQUE,
                code            TEXT NOT NULL,
                status          TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'completed')),
                created_at      TEXT DEFAULT (datetime('now')),
                completed_at    TEXT
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO referrals
                (referrer_id, referred_id, code, status, created_at, completed_at)
            SELECT referrer_id, referred_id, code, status, created_at, completed_at
            FROM _referrals_old WHERE referred_id IS NOT NULL
        """)
        conn.execute("DROP TABLE _referrals_old")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_referrals_referred ON referrals(referred_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_referrals_code ON referrals(code)")
        logger.info("Migrated referrals table to multi-referral schema")

    _ensure_column(conn, "referral_codes", "sent", "INTEGER DEFAULT 0")

    # ─── live_chats: עמודת updated_at למעקב אחר פעילות אחרונה ─────────────
    _ensure_column(conn, "live_chats", "updated_at", "TEXT DEFAULT ''")
    # Back-fill: שורות קיימות מקבלות את started_at כ-updated_at
    conn.execute(
        "UPDATE live_chats SET updated_at = started_at WHERE updated_at IS NULL OR updated_at = ''"
    )

    # ─── referrals: UNIQUE(referrer_id, referred_id) → UNIQUE(referred_id)
    create_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='referrals'"
    ).fetchone()
    if create_sql and "UNIQUE(referrer_id, referred_id)" in (create_sql["sql"] or ""):
        conn.execute("ALTER TABLE referrals RENAME TO _referrals_old2")
        conn.execute("""
            CREATE TABLE referrals (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id     TEXT NOT NULL,
                referred_id     TEXT NOT NULL UNIQUE,
                code            TEXT NOT NULL,
                status          TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'completed')),
                created_at      TEXT DEFAULT (datetime('now')),
                completed_at    TEXT
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO referrals
                (referrer_id, referred_id, code, status, created_at, completed_at)
            SELECT referrer_id, referred_id, code, status, created_at, completed_at
            FROM _referrals_old2
        """)
        conn.execute("DROP TABLE _referrals_old2")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_referrals_referred ON referrals(referred_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_referrals_code ON referrals(code)")
        logger.info("Migrated referrals: UNIQUE(referrer_id, referred_id) → UNIQUE(referred_id)")

    # ─── appointments: UNIQUE partial index למניעת תורים כפולים ────────────
    existing_appt_idx = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='appointments' AND name='idx_appointments_user_datetime'"
    ).fetchone()
    if not existing_appt_idx:
        # מחיקת כפילויות (שומר רק את הרשומה האחרונה לכל user+date+time)
        dup_cursor = conn.execute("""
            SELECT COUNT(*) AS cnt FROM appointments
            WHERE preferred_date != '' AND preferred_time != ''
              AND id NOT IN (
                  SELECT MAX(id) FROM appointments
                  WHERE preferred_date != '' AND preferred_time != ''
                  GROUP BY user_id, preferred_date, preferred_time
              )
        """)
        dup_count = dup_cursor.fetchone()["cnt"]
        if dup_count:
            logger.warning("Removing %d duplicate appointments during migration", dup_count)
            conn.execute("""
                DELETE FROM appointments
                WHERE preferred_date != '' AND preferred_time != ''
                  AND id NOT IN (
                      SELECT MAX(id) FROM appointments
                      WHERE preferred_date != '' AND preferred_time != ''
                      GROUP BY user_id, preferred_date, preferred_time
                  )
            """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_appointments_user_datetime
                ON appointments(user_id, preferred_date, preferred_time)
                WHERE preferred_date != '' AND preferred_time != ''
        """)
