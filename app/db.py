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
import time
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
                trial_used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # SPEC14: durable free-trial counters. Per-user count lives on `users`;
        # anonymous count is keyed by client IP (the in-memory session is evicted
        # on TTL/restart and the cookie is trivially cleared, so neither is a real
        # limit — SQLite by IP is the source of truth).
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS anon_trial (
                ip         TEXT PRIMARY KEY,
                used       INTEGER NOT NULL DEFAULT 0,
                first_seen REAL
            )
            """
        )
        _migrate_add_trial_used(_conn)
        _conn.commit()
        _conn_path = path
    return _conn


def _migrate_add_trial_used(conn: sqlite3.Connection) -> None:
    """Add `trial_used` to a pre-SPEC14 `users` table if it predates the column."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    if "trial_used" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN trial_used INTEGER NOT NULL DEFAULT 0")


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


# ── Free-trial counters (SPEC14) ──────────────────────────────────────────────
#
# All increments are atomic single statements. The grant is lifetime/cumulative
# and never resets — "N free generations" is a one-time onboarding grant, not a
# quota. Callers read the counter before the LLM call and increment only on a
# successful generation, so a failed call never burns the trial.


def get_user_trial(user_id: int) -> int:
    with _lock:
        row = _get().execute(
            "SELECT trial_used FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return int(row["trial_used"]) if row else 0


def incr_user_trial(user_id: int) -> int:
    with _lock:
        conn = _get()
        row = conn.execute(
            "UPDATE users SET trial_used = trial_used + 1 WHERE id = ? RETURNING trial_used",
            (user_id,),
        ).fetchone()
        conn.commit()
        return int(row["trial_used"]) if row else 0


def get_anon_trial(ip: str) -> int:
    with _lock:
        row = _get().execute(
            "SELECT used FROM anon_trial WHERE ip = ?", (ip,)
        ).fetchone()
        return int(row["used"]) if row else 0


def incr_anon_trial(ip: str) -> int:
    with _lock:
        conn = _get()
        row = conn.execute(
            """
            INSERT INTO anon_trial (ip, used, first_seen)
            VALUES (?, 1, ?)
            ON CONFLICT(ip) DO UPDATE SET used = used + 1
            RETURNING used
            """,
            (ip, time.time()),
        ).fetchone()
        conn.commit()
        return int(row["used"])


def sweep_anon_trial(max_age_seconds: float) -> int:
    """Delete anon rows older than `max_age_seconds` by first_seen. A returning
    IP simply re-inserts and gets its grant again — acceptable for a 1-request
    anon allowance. Returns the number of rows pruned."""
    cutoff = time.time() - max_age_seconds
    with _lock:
        conn = _get()
        cur = conn.execute(
            "DELETE FROM anon_trial WHERE first_seen IS NOT NULL AND first_seen < ?",
            (cutoff,),
        )
        conn.commit()
        return cur.rowcount


def _reset_for_tests() -> None:
    """Drop all rows (test isolation)."""
    with _lock:
        conn = _get()
        conn.execute("DELETE FROM users")
        conn.execute("DELETE FROM anon_trial")
        conn.commit()
