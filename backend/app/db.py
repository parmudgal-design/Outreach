"""SQLite storage. One short-lived connection per unit of work keeps this safe to
use from the API handlers and the worker thread at the same time (WAL mode lets
readers and the single writer overlap)."""
import json
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contacts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  type          TEXT NOT NULL DEFAULT 'doctor',   -- doctor | hospital | company | partner | brand | professional
  name          TEXT NOT NULL,
  email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
  specialty     TEXT NOT NULL DEFAULT '',
  specialty_key TEXT,                             -- canonical specialty (services/specialties.py), doctors only
  specialty_source TEXT NOT NULL DEFAULT '',      -- where it was detected from, or 'manual'
  product       TEXT NOT NULL DEFAULT '',         -- the specific product, for company/partner pitches
  clinic        TEXT NOT NULL DEFAULT '',
  city          TEXT NOT NULL DEFAULT '',
  notes         TEXT NOT NULL DEFAULT '',
  status        TEXT NOT NULL DEFAULT 'new',      -- see models.CONTACT_STATUSES
  source        TEXT NOT NULL DEFAULT 'manual',
  followups_sent INTEGER NOT NULL DEFAULT 0,
  last_sent_at  REAL,
  replied_at    REAL,
  draft_attempts INTEGER NOT NULL DEFAULT 0,
  last_error    TEXT,
  created_at    REAL NOT NULL,
  updated_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contacts_status ON contacts(status);

CREATE TABLE IF NOT EXISTS messages (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id   INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL DEFAULT 'initial',   -- initial | followup
  subject      TEXT NOT NULL DEFAULT '',
  body         TEXT NOT NULL DEFAULT '',
  status       TEXT NOT NULL DEFAULT 'draft',     -- draft | approved | sending | sent | failed | discarded
  drafted_by   TEXT NOT NULL DEFAULT 'manual',    -- claude | template | manual
  transport    TEXT,                              -- smtp | dry_run
  message_id   TEXT,                              -- RFC 5322 Message-ID we sent with
  in_reply_to  TEXT,
  error        TEXT,
  created_at   REAL NOT NULL,
  updated_at   REAL NOT NULL,
  sent_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);
CREATE INDEX IF NOT EXISTS idx_messages_contact ON messages(contact_id);
CREATE INDEX IF NOT EXISTS idx_messages_msgid ON messages(message_id);

CREATE TABLE IF NOT EXISTS inbound (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  contact_id  INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
  uid_key     TEXT NOT NULL UNIQUE,               -- "<uidvalidity>:<uid>" so re-polls are idempotent
  kind        TEXT NOT NULL,                      -- reply | bounce | auto_reply | unsubscribe | unmatched
  from_addr   TEXT NOT NULL DEFAULT '',
  subject     TEXT NOT NULL DEFAULT '',
  snippet     TEXT NOT NULL DEFAULT '',
  received_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS suppression (
  email      TEXT PRIMARY KEY COLLATE NOCASE,
  reason     TEXT NOT NULL,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         REAL NOT NULL,
  level      TEXT NOT NULL DEFAULT 'info',        -- info | warn | error
  kind       TEXT NOT NULL,
  message    TEXT NOT NULL,
  contact_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

-- Uploads waiting for the user to confirm the import (kept here, not in memory, so a restart doesn't lose them).
CREATE TABLE IF NOT EXISTS pending_imports (
  token      TEXT PRIMARY KEY,
  filename   TEXT NOT NULL DEFAULT '',
  kind       TEXT NOT NULL,                       -- table | partner_workbook
  payload    TEXT NOT NULL,                       -- JSON
  created_at REAL NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def connect():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Columns added after the first release: (table, column, definition).
MIGRATIONS = [
    ("contacts", "specialty_key", "TEXT"),
    ("contacts", "specialty_source", "TEXT NOT NULL DEFAULT ''"),
    ("contacts", "product", "TEXT NOT NULL DEFAULT ''"),
    ("contacts", "draft_attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("contacts", "last_error", "TEXT"),
    # Marketplace partners (brand / professional contacts).
    ("contacts", "partner_id", "TEXT NOT NULL DEFAULT ''"),     # founder's sheet ID, e.g. B-SUP-T2-MUM-01
    ("contacts", "category", "TEXT NOT NULL DEFAULT ''"),       # FD/SUP/PRO/DV or NT/GP/SPC/MH/PT (services/marketplace.py)
    ("contacts", "tier", "TEXT NOT NULL DEFAULT ''"),
    ("contacts", "phone", "TEXT NOT NULL DEFAULT ''"),
    ("contacts", "website", "TEXT NOT NULL DEFAULT ''"),
    ("contacts", "description", "TEXT NOT NULL DEFAULT ''"),    # what they sell or do, from the sheet's notes
    ("contacts", "source_url", "TEXT NOT NULL DEFAULT ''"),
    ("contacts", "needs_check", "TEXT NOT NULL DEFAULT ''"),    # why a person should check this row first
]


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        for table, column, definition in MIGRATIONS:
            existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        recover_interrupted_sends(conn)
    # The database holds API keys and mail passwords: keep it owner-only.
    try:
        config.DB_PATH.chmod(0o600)
    except OSError:
        pass


INTERRUPTED_NOTE = "Interrupted while sending (app restarted). Check your Sent folder before retrying."


def recover_interrupted_sends(conn) -> int:
    """A message still 'sending' at startup may or may not have reached the mail server.
    Mark it failed (never re-queue it) so a person checks before anything is sent twice."""
    stuck = conn.execute("SELECT id, contact_id FROM messages WHERE status = 'sending'").fetchall()
    for r in stuck:
        conn.execute("UPDATE messages SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
                     (INTERRUPTED_NOTE, time.time(), r["id"]))
        log_event("send", INTERRUPTED_NOTE, level="error", contact_id=r["contact_id"], conn=conn)
    return len(stuck)


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def one(cur) -> dict | None:
    r = cur.fetchone()
    return dict(r) if r else None


def log_event(kind: str, message: str, level: str = "info", contact_id: int | None = None, conn=None) -> None:
    sql = "INSERT INTO events (ts, level, kind, message, contact_id) VALUES (?, ?, ?, ?, ?)"
    args = (time.time(), level, kind, message, contact_id)
    if conn is not None:
        conn.execute(sql, args)
        return
    with connect() as c:
        c.execute(sql, args)


def get_json(conn, key: str, default=None):
    r = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def set_json(conn, key: str, value) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )
