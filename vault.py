"""Encrypted storage for the journal.

How the encryption works, in plain terms:

1. When you first set up the app, it creates a random "data key". Every entry
   and summary is encrypted with this key before it is written to disk.
2. The data key itself is locked with your password. Your password is stretched
   with scrypt (a deliberately slow function) into a "key-encryption key", and
   that key encrypts the data key. Only the locked version is saved.
3. When you log in, the app uses your password to unlock the data key and keeps
   it in memory only. Logging out, going idle, or restarting the app forgets it.

So the database file on disk is unreadable without your password. The one
exception is timestamps, which are stored as plain text so entries can be
sorted and filtered by date.

If you forget your password, the data cannot be recovered. That is the price of
real encryption.
"""

import base64
import hashlib
import os
import sqlite3
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken

# scrypt settings. N=2**15 takes roughly 0.1s and 32 MB of memory per attempt,
# which is unnoticeable when you log in but slows down password guessing a lot.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    name  TEXT PRIMARY KEY,
    value BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,      -- UTC, ISO 8601 (not encrypted)
    body       BLOB NOT NULL       -- encrypted text
);
CREATE TABLE IF NOT EXISTS therapy_sessions (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    held_at TEXT NOT NULL          -- UTC, ISO 8601
);
CREATE TABLE IF NOT EXISTS summaries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    period_start TEXT,
    period_end   TEXT NOT NULL,
    body         BLOB NOT NULL     -- encrypted text
);
"""


class WrongPassword(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _derive_kek(password: str, salt: bytes) -> bytes:
    raw = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=64 * 1024 * 1024,
        dklen=32,
    )
    return base64.urlsafe_b64encode(raw)


class Vault:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        with self._connect() as db:
            db.executescript(SCHEMA)
        try:
            os.chmod(db_path, 0o600)  # only your user account can read the file
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db

    # ---- password / keys -------------------------------------------------

    def is_set_up(self) -> bool:
        with self._connect() as db:
            row = db.execute("SELECT 1 FROM settings WHERE name='wrapped_key'").fetchone()
        return row is not None

    def set_up(self, password: str) -> bytes:
        if self.is_set_up():
            raise RuntimeError("Journal is already set up")
        salt = os.urandom(16)
        data_key = Fernet.generate_key()
        wrapped = Fernet(_derive_kek(password, salt)).encrypt(data_key)
        with self._connect() as db:
            db.execute("INSERT INTO settings VALUES ('salt', ?)", (salt,))
            db.execute("INSERT INTO settings VALUES ('wrapped_key', ?)", (wrapped,))
        return data_key

    def unlock(self, password: str) -> bytes:
        with self._connect() as db:
            rows = dict(db.execute("SELECT name, value FROM settings").fetchall())
        try:
            return Fernet(_derive_kek(password, rows["salt"])).decrypt(rows["wrapped_key"])
        except (InvalidToken, KeyError):
            raise WrongPassword()

    # ---- entries ---------------------------------------------------------

    def add_entry(self, key: bytes, text: str) -> int:
        with self._connect() as db:
            cur = db.execute(
                "INSERT INTO entries (created_at, body) VALUES (?, ?)",
                (utc_now(), Fernet(key).encrypt(text.encode("utf-8"))),
            )
            return cur.lastrowid

    def list_entries(self, key: bytes, since: str | None = None) -> list[dict]:
        sql = "SELECT id, created_at, body FROM entries"
        args: tuple = ()
        if since:
            sql += " WHERE created_at > ?"
            args = (since,)
        sql += " ORDER BY created_at DESC, id DESC"
        f = Fernet(key)
        with self._connect() as db:
            rows = db.execute(sql, args).fetchall()
        return [
            {"id": r["id"], "created_at": r["created_at"], "text": f.decrypt(r["body"]).decode("utf-8")}
            for r in rows
        ]

    def delete_entry(self, entry_id: int) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM entries WHERE id = ?", (entry_id,))

    # ---- therapy sessions ------------------------------------------------

    def mark_session(self) -> str:
        now = utc_now()
        with self._connect() as db:
            db.execute("INSERT INTO therapy_sessions (held_at) VALUES (?)", (now,))
        return now

    def last_session(self) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT MAX(held_at) AS t FROM therapy_sessions").fetchone()
        return row["t"]

    # ---- summaries -------------------------------------------------------

    def save_summary(self, key: bytes, text: str, period_start: str | None, period_end: str) -> int:
        with self._connect() as db:
            cur = db.execute(
                "INSERT INTO summaries (created_at, period_start, period_end, body) VALUES (?, ?, ?, ?)",
                (utc_now(), period_start, period_end, Fernet(key).encrypt(text.encode("utf-8"))),
            )
            return cur.lastrowid

    def update_summary(self, key: bytes, summary_id: int, text: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE summaries SET body = ? WHERE id = ?",
                (Fernet(key).encrypt(text.encode("utf-8")), summary_id),
            )

    def get_summary(self, key: bytes, summary_id: int) -> dict | None:
        with self._connect() as db:
            r = db.execute("SELECT * FROM summaries WHERE id = ?", (summary_id,)).fetchone()
        if r is None:
            return None
        return {
            "id": r["id"],
            "created_at": r["created_at"],
            "period_start": r["period_start"],
            "period_end": r["period_end"],
            "text": Fernet(key).decrypt(r["body"]).decode("utf-8"),
        }

    def list_summaries(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, created_at, period_start, period_end FROM summaries ORDER BY id DESC"
            ).fetchall()
        return [dict(r) for r in rows]
