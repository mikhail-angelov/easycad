"""SQLite store for accounts + settings (SPEC13).

Durable storage for *accounts only* — never for CAD sessions (those are
in-memory, see `session_registry`). One `users` table; magic-link tokens are
stateless JWTs (no token table), matching playground.

Per-user `settings` is a JSON blob `{provider, model, key}`. The BYOK key is
stored as plaintext (confirmed decision, as in playground); it is protected by
the DB file being app-only and never web-served, and is never logged or returned
by any endpoint.
"""

import json
import os
import sqlite3
import threading
from pathlib import Path

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_conn_path: str | None = None


def _db_path() -> str:
    return os.getenv("EASYCAD_DB_PATH", str(Path.home() / ".easycad" / "easycad.db"))


def _get() -> sqlite3.Connection:
    """Lazily open (or reopen if the configured path changed — used by tests)."""
    global _conn, _conn_path
    path = _db_path()
    if _conn is None or _conn_path != path:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                email      TEXT NOT NULL UNIQUE,
                settings   TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        _conn.commit()
        _conn_path = path
    return _conn


def _row_to_user(row: sqlite3.Row) -> dict:
    try:
        settings = json.loads(row["settings"]) if row["settings"] else {}
    except (json.JSONDecodeError, TypeError):
        settings = {}
    return {"id": row["id"], "email": row["email"], "settings": settings}


def get_or_create_user(email: str) -> dict:
    email = email.strip().lower()
    with _lock:
        conn = _get()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
            cur = conn.execute("INSERT INTO users (email) VALUES (?)", (email,))
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_user(row)


def get_user(user_id: int) -> dict | None:
    with _lock:
        row = _get().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _row_to_user(row) if row else None


def update_settings(user_id: int, settings: dict) -> None:
    with _lock:
        conn = _get()
        conn.execute(
            "UPDATE users SET settings = ? WHERE id = ?",
            (json.dumps(settings, ensure_ascii=False), user_id),
        )
        conn.commit()


def delete_user(user_id: int) -> None:
    with _lock:
        conn = _get()
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()


def _reset_for_tests() -> None:
    """Drop all rows (test isolation)."""
    with _lock:
        conn = _get()
        conn.execute("DELETE FROM users")
        conn.commit()
