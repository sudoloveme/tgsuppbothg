"""
Database operations module.
All functions for working with SQLite database.
"""
import logging
import sqlite3
from pathlib import Path
from typing import Optional

from config import DB_PATH, SUPPORT_CHAT_ID

logger = logging.getLogger("support-bot")


def _db_connect() -> sqlite3.Connection:
    """Create database connection and ensure tables exist."""
    path = Path(DB_PATH)
    # Ensure directory exists if path includes a directory
    if path.parent and not path.parent.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row  # Enable column access by name
    # New normalized table keyed by (support_chat_id, user_id)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS user_topics_v2 (\n"
        "  support_chat_id INTEGER NOT NULL,\n"
        "  user_id INTEGER NOT NULL,\n"
        "  thread_id INTEGER NOT NULL,\n"
        "  created_at TEXT DEFAULT CURRENT_TIMESTAMP,\n"
        "  PRIMARY KEY (support_chat_id, user_id)\n"
        ")"
    )
    # Thread state table
    conn.execute(
        "CREATE TABLE IF NOT EXISTS thread_states (\n"
        "  support_chat_id INTEGER NOT NULL,\n"
        "  thread_id INTEGER NOT NULL,\n"
        "  status TEXT NOT NULL DEFAULT 'active',\n"
        "  archived INTEGER NOT NULL DEFAULT 0,\n"
        "  last_activity TEXT DEFAULT CURRENT_TIMESTAMP,\n"
        "  PRIMARY KEY (support_chat_id, thread_id)\n"
        ")"
    )
    # Ratings table
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ratings (\n"
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        "  user_id INTEGER NOT NULL,\n"
        "  thread_id INTEGER,\n"
        "  rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),\n"
        "  created_at TEXT DEFAULT CURRENT_TIMESTAMP\n"
        ")"
    )
    # User backend data table (UUID, email from API)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS user_backend_data (\n"
        "  user_id INTEGER NOT NULL,\n"
        "  support_chat_id INTEGER,\n"
        "  uuid TEXT,\n"
        "  email TEXT,\n"
        "  updated_at TEXT DEFAULT CURRENT_TIMESTAMP,\n"
        "  PRIMARY KEY (user_id, support_chat_id)\n"
        ")"
    )
    # Migrate from legacy table if present
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS user_topics (user_id INTEGER PRIMARY KEY, thread_id INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        if SUPPORT_CHAT_ID is not None:
            conn.execute(
                "INSERT OR IGNORE INTO user_topics_v2 (support_chat_id, user_id, thread_id)\n"
                "SELECT ?, user_id, thread_id FROM user_topics",
                (SUPPORT_CHAT_ID,),
            )
            conn.commit()
    except Exception:
        # Best-effort migration
        pass
    return conn


def db_get_thread_id(user_id: int) -> Optional[int]:
    """Get thread_id for a user_id."""
    try:
        conn = _db_connect()
        if SUPPORT_CHAT_ID is not None:
            cur = conn.execute(
                "SELECT thread_id FROM user_topics_v2 WHERE support_chat_id=? AND user_id=?",
                (SUPPORT_CHAT_ID, user_id),
            )
        else:
            cur = conn.execute("SELECT thread_id FROM user_topics WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        conn.close()
        tid = int(row[0]) if row else None
        logger.info("DB get thread: support_chat_id=%s user_id=%s -> %s", str(SUPPORT_CHAT_ID), user_id, str(tid))
        return tid
    except Exception:
        logger.exception("DB read failed (get thread): user_id=%s", user_id)
        return None


def db_get_user_id(thread_id: int) -> Optional[int]:
    """Get user_id from thread_id."""
    try:
        conn = _db_connect()
        if SUPPORT_CHAT_ID is not None:
            cur = conn.execute(
                "SELECT user_id FROM user_topics_v2 WHERE support_chat_id=? AND thread_id=?",
                (SUPPORT_CHAT_ID, thread_id),
            )
        else:
            cur = conn.execute("SELECT user_id FROM user_topics WHERE thread_id=?", (thread_id,))
        row = cur.fetchone()
        conn.close()
        uid = int(row[0]) if row else None
        logger.info("DB get user: support_chat_id=%s thread_id=%s -> %s", str(SUPPORT_CHAT_ID), thread_id, str(uid))
        return uid
    except Exception:
        logger.exception("DB read failed (get user): thread_id=%s", thread_id)
        return None


def db_set_thread_id(user_id: int, thread_id: int) -> None:
    """Set thread_id for a user_id."""
    try:
        conn = _db_connect()
        if SUPPORT_CHAT_ID is not None:
            conn.execute(
                "INSERT INTO user_topics_v2(support_chat_id, user_id, thread_id) VALUES(?, ?, ?)\n"
                "ON CONFLICT(support_chat_id, user_id) DO UPDATE SET thread_id=excluded.thread_id",
                (SUPPORT_CHAT_ID, user_id, thread_id),
            )
        else:
            conn.execute(
                "INSERT INTO user_topics(user_id, thread_id) VALUES(?, ?) ON CONFLICT(user_id) DO UPDATE SET thread_id=excluded.thread_id",
                (user_id, thread_id),
            )
        conn.commit()
        conn.close()
        logger.info("DB set thread: support_chat_id=%s user_id=%s -> %s", str(SUPPORT_CHAT_ID), user_id, thread_id)
    except Exception:
        logger.exception("DB write failed (set thread): user_id=%s thread_id=%s", user_id, thread_id)


def db_upsert_thread_state(thread_id: int, status: str = "active", archived: int = 0) -> None:
    """Update thread state in database."""
    try:
        conn = _db_connect()
        conn.execute(
            "INSERT INTO thread_states (support_chat_id, thread_id, status, archived) VALUES (?, ?, ?, ?)\n"
            "ON CONFLICT(support_chat_id, thread_id) DO UPDATE SET status=excluded.status, archived=excluded.archived, last_activity=CURRENT_TIMESTAMP",
            (SUPPORT_CHAT_ID, thread_id, status, archived),
        )
        conn.commit()
        conn.close()
    except Exception:
        logger.exception("DB write failed (upsert thread state): thread_id=%s", thread_id)


def db_touch_activity(thread_id: int) -> None:
    """Update last activity timestamp for a thread."""
    try:
        conn = _db_connect()
        conn.execute(
            "UPDATE thread_states SET last_activity=CURRENT_TIMESTAMP WHERE support_chat_id=? AND thread_id=?",
            (SUPPORT_CHAT_ID, thread_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        logger.exception("DB write failed (touch activity): thread_id=%s", thread_id)


def db_get_thread_state(thread_id: int) -> Optional[tuple[str, int]]:
    """Get thread state (status, archived) from database."""
    try:
        conn = _db_connect()
        cur = conn.execute(
            "SELECT status, archived FROM thread_states WHERE support_chat_id=? AND thread_id=?",
            (SUPPORT_CHAT_ID, thread_id),
        )
        row = cur.fetchone()
        conn.close()
        return (row[0], int(row[1])) if row else None
    except Exception:
        logger.exception("DB read failed (get thread state): thread_id=%s", thread_id)
        return None


def db_save_rating(user_id: int, rating: int, thread_id: Optional[int] = None) -> None:
    """Save rating to database."""
    try:
        conn = _db_connect()
        conn.execute(
            "INSERT INTO ratings (user_id, thread_id, rating) VALUES (?, ?, ?)",
            (user_id, thread_id, rating),
        )
        conn.commit()
        conn.close()
        logger.info("Saved rating: user_id=%s thread_id=%s rating=%s", user_id, thread_id, rating)
    except Exception:
        logger.exception("DB write failed (save rating): user_id=%s rating=%s", user_id, rating)


def db_get_ratings_stats() -> dict:
    """Get statistics about ratings."""
    try:
        conn = _db_connect()
        # Total count
        cur = conn.execute("SELECT COUNT(*) FROM ratings")
        total = cur.fetchone()[0]
        
        # Average rating
        cur = conn.execute("SELECT AVG(rating) FROM ratings")
        avg_rating = cur.fetchone()[0]
        avg_rating = round(avg_rating, 2) if avg_rating else 0
        
        # Ratings distribution
        cur = conn.execute(
            "SELECT rating, COUNT(*) FROM ratings GROUP BY rating ORDER BY rating"
        )
        distribution = {row[0]: row[1] for row in cur.fetchall()}
        
        conn.close()
        
        return {
            "total": total,
            "average": avg_rating,
            "distribution": distribution,
        }
    except Exception:
        logger.exception("DB read failed (get ratings stats)")
        return {"total": 0, "average": 0, "distribution": {}}


def db_get_user_ratings(user_id: int) -> list[tuple]:
    """Get all ratings from a specific user."""
    try:
        conn = _db_connect()
        cur = conn.execute(
            "SELECT rating, thread_id, created_at FROM ratings WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        )
        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception:
        logger.exception("DB read failed (get user ratings): user_id=%s", user_id)
        return []


def db_save_user_backend_data(user_id: int, uuid: str, email: str) -> None:
    """Save user backend data (UUID, email) to database."""
    try:
        conn = _db_connect()
        if SUPPORT_CHAT_ID is not None:
            conn.execute(
                "INSERT INTO user_backend_data (user_id, support_chat_id, uuid, email) VALUES (?, ?, ?, ?)\n"
                "ON CONFLICT(user_id, support_chat_id) DO UPDATE SET uuid=excluded.uuid, email=excluded.email, updated_at=CURRENT_TIMESTAMP",
                (user_id, SUPPORT_CHAT_ID, uuid, email),
            )
        else:
            conn.execute(
                "INSERT INTO user_backend_data (user_id, support_chat_id, uuid, email) VALUES (?, ?, ?, ?)\n"
                "ON CONFLICT(user_id, support_chat_id) DO UPDATE SET uuid=excluded.uuid, email=excluded.email, updated_at=CURRENT_TIMESTAMP",
                (user_id, None, uuid, email),
            )
        conn.commit()
        conn.close()
        logger.info("Saved backend data: user_id=%s uuid=%s email=%s", user_id, uuid, email)
    except Exception:
        logger.exception("DB write failed (save user backend data): user_id=%s uuid=%s", user_id, uuid)


def db_get_user_backend_data(user_id: int) -> Optional[tuple[str, str]]:
    """Get user backend data (UUID, email) from database."""
    try:
        conn = _db_connect()
        if SUPPORT_CHAT_ID is not None:
            cur = conn.execute(
                "SELECT uuid, email FROM user_backend_data WHERE user_id=? AND support_chat_id=?",
                (user_id, SUPPORT_CHAT_ID),
            )
        else:
            cur = conn.execute(
                "SELECT uuid, email FROM user_backend_data WHERE user_id=? AND support_chat_id IS NULL",
                (user_id,),
            )
        row = cur.fetchone()
        conn.close()
        return (row[0], row[1]) if row else None
    except Exception:
        logger.exception("DB read failed (get user backend data): user_id=%s", user_id)
        return None

