"""SQLite store for accounts + settings (SPEC13).

Durable storage for *accounts only* — never for CAD sessions (those are
in-memory, see `session_registry`). One `users` table; magic-link tokens are
stateless JWTs (no token table), matching playground.

Per-user `settings` is a JSON blob `{provider, model, key}`. The BYOK key is
**encrypted at rest** (`app/crypto.py`, SPEC14): the stored column holds
ciphertext, decrypted only in-process on read. It is additionally never logged or
returned by any endpoint. Legacy plaintext keys read through and re-encrypt on
the next save.
"""

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from . import crypto

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
        # isolation_level=None → autocommit mode: each statement commits on its
        # own, and explicit BEGIN IMMEDIATE / COMMIT (create_token_if_under_limit)
        # works without sqlite3's implicit-transaction machinery fighting it. The
        # existing single-statement writers stay correct; their .commit() is a
        # harmless no-op.
        _conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        # ON DELETE CASCADE (tokens → users) is silently ignored unless foreign
        # keys are enabled per-connection (SQLite default is OFF). Set it before
        # any DDL/DML so deleting an account also drops its PAT rows (SPEC22).
        _conn.execute("PRAGMA foreign_keys=ON")
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
        # User feedback (in-app "leave feedback" button). email/rating optional;
        # anonymous feedback keeps email NULL.
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                email      TEXT,
                message    TEXT NOT NULL,
                rating     INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Personal Access Tokens (SPEC22): long-lived, revocable agent creds.
        # `hash` is sha256 of the secret (raw token never stored); UNIQUE indexes
        # the exchange lookup. ON DELETE CASCADE removes tokens with their owner.
        _conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tokens (
                id         INTEGER PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name       TEXT NOT NULL,
                hash       TEXT NOT NULL UNIQUE,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                revoked_at INTEGER
            )
            """
        )
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(user_id)")
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
    # Decrypt the BYOK key in-process; drop it if it can't be decrypted (secret
    # changed) so the caller sees no key and the user simply re-enters it.
    if isinstance(settings, dict) and settings.get("key"):
        plain = crypto.decrypt(settings["key"])
        if plain is None:
            settings.pop("key", None)
        else:
            settings["key"] = plain
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
    # Encrypt the BYOK key before it touches disk (leave already-ciphertext and
    # other fields as-is). Copy so the caller's in-memory dict stays plaintext.
    to_store = dict(settings)
    key = to_store.get("key")
    if key and not crypto.is_encrypted(key):
        to_store["key"] = crypto.encrypt(key)
    with _lock:
        conn = _get()
        conn.execute(
            "UPDATE users SET settings = ? WHERE id = ?",
            (json.dumps(to_store, ensure_ascii=False), user_id),
        )
        conn.commit()


def delete_user(user_id: int) -> None:
    with _lock:
        conn = _get()
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()


# ── Personal Access Tokens (SPEC22) ───────────────────────────────────────────
#
# Long-lived, revocable agent credentials. The raw secret is shown once at
# creation and never stored — only sha256(secret). Verification hashes the
# presented secret and looks it up by the indexed `hash` column; the indexed
# equality IS the constant-time-irrelevant compare (32 bytes of entropy, no
# low-entropy prefix to leak).

TOKEN_TTL_SECONDS = 30 * 24 * 3600  # 30 days
MAX_ACTIVE_TOKENS = 10  # per user (non-revoked, non-expired)


class TokenLimitError(Exception):
    """Raised by create_token_if_under_limit when the caller is at the cap."""


def create_token_if_under_limit(user_id: int, name: str, token_hash: str) -> dict:
    """Atomically enforce the per-user active-token cap and insert.

    Counting then inserting in one critical section (`_lock` + a single SQLite
    transaction) prevents two parallel requests from racing past the cap. Raises
    TokenLimitError when already at MAX_ACTIVE_TOKENS active tokens.
    """
    now = int(time.time())
    with _lock:
        conn = _get()
        # BEGIN IMMEDIATE takes the write lock up front so the count-then-insert is
        # one atomic critical section against a concurrent writer (another process
        # or connection), not just against threads `_lock` already serializes —
        # otherwise two racing creates could both read 9 and both insert (SPEC22 §2.3).
        conn.execute("BEGIN IMMEDIATE")
        try:
            active = conn.execute(
                "SELECT COUNT(*) AS n FROM tokens "
                "WHERE user_id = ? AND revoked_at IS NULL AND expires_at > ?",
                (user_id, now),
            ).fetchone()["n"]
            if active >= MAX_ACTIVE_TOKENS:
                raise TokenLimitError()
            expires_at = now + TOKEN_TTL_SECONDS
            cur = conn.execute(
                "INSERT INTO tokens (user_id, name, hash, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, name, token_hash, now, expires_at),
            )
        except BaseException:
            conn.rollback()
            raise
        conn.commit()
        return {"id": cur.lastrowid, "name": name, "created_at": now, "expires_at": expires_at}


def list_tokens(user_id: int) -> list[dict]:
    """The caller's tokens (no secrets), newest first."""
    with _lock:
        conn = _get()
        rows = conn.execute(
            "SELECT id, name, created_at, expires_at, revoked_at FROM tokens "
            "WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def revoke_token(token_id: int, user_id: int) -> bool:
    """Revoke a token the caller owns. Scoped by user_id so sequential ids can't
    revoke another user's token (IDOR). Returns True if a row was updated."""
    with _lock:
        conn = _get()
        cur = conn.execute(
            "UPDATE tokens SET revoked_at = ? "
            "WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
            (int(time.time()), token_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0


def get_active_token_by_hash(token_hash: str) -> dict | None:
    """Look up an active (non-revoked, non-expired) token by its hash. Used by
    the PAT→cookie exchange. Returns {id, user_id} or None."""
    with _lock:
        conn = _get()
        row = conn.execute(
            "SELECT id, user_id FROM tokens "
            "WHERE hash = ? AND revoked_at IS NULL AND expires_at > ?",
            (token_hash, int(time.time())),
        ).fetchone()
        return dict(row) if row else None


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


# ── Feedback ──────────────────────────────────────────────────────────────────


def add_feedback(message: str, email: str | None = None, rating: int | None = None) -> int:
    """Store one feedback entry; return its id."""
    with _lock:
        conn = _get()
        cur = conn.execute(
            "INSERT INTO feedback (email, message, rating) VALUES (?, ?, ?)",
            (email, message, rating),
        )
        conn.commit()
        return cur.lastrowid


def list_feedback(limit: int = 50) -> list[dict]:
    """Most recent feedback first (for the admin view)."""
    with _lock:
        conn = _get()
        rows = conn.execute(
            "SELECT id, email, message, rating, created_at FROM feedback "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def count_feedback() -> int:
    with _lock:
        conn = _get()
        return conn.execute("SELECT COUNT(*) AS n FROM feedback").fetchone()["n"]


def count_users() -> int:
    """Total registered accounts (signups), for the admin dashboard (SPEC19 W2)."""
    with _lock:
        conn = _get()
        return conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


def _reset_for_tests() -> None:
    """Drop all rows (test isolation)."""
    with _lock:
        conn = _get()
        conn.execute("DELETE FROM users")
        conn.execute("DELETE FROM anon_trial")
        conn.execute("DELETE FROM feedback")
        conn.execute("DELETE FROM tokens")
        conn.commit()
