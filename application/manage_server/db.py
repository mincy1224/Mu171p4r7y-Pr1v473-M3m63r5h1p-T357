# draft
"""
SQLite database layer — users + operations + tokens with FIFO queue.

Schema
  users
    user_id   TEXT PRIMARY KEY
    role      TEXT     — 'set_holder' | 'querier'
    status    TEXT     — set_holder: NOT_JOINED|JOINED|QUITTED
                         querier:    ACTIVE
    created_at TEXT
    updated_at TEXT

  operations
    op_id          INTEGER PRIMARY KEY AUTOINCREMENT
    user_id        TEXT NOT NULL  REFERENCES users(user_id)
    prot_type      TEXT NOT NULL  — JOIN|UPDATE|QUERY|QUIT
    status         TEXT NOT NULL  — QUEUED|ACTIVE|BUSY|DONE|FAILED|REMOVED
    queue_pos      INTEGER
    overtime_count INTEGER NOT NULL DEFAULT 0
    created_at     TEXT NOT NULL
    updated_at     TEXT NOT NULL

  user_agent_tokens
    user_id     TEXT NOT NULL  REFERENCES users(user_id)
    agent_role  TEXT NOT NULL  — STEWARD|PEER0|PEER1
    token      TEXT NOT NULL
    updated_at TEXT NOT NULL
    PRIMARY KEY (user_id, agent_role)
"""

import os
import sqlite3
import threading
import time

from _c3_io import ensure_dir, read_json

_cfg_root = read_json(os.path.join(os.path.dirname(__file__), "..", "config.json"))
_cfg = _cfg_root["manage_server"]
DB_PATH = os.path.join(os.path.dirname(__file__), "..",
                       _cfg_root["storage_root_dir"], "manage_server", _cfg["db_name"])


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


class DB:
    def __init__(self, path: str = DB_PATH):
        ensure_dir(os.path.dirname(path))
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.RLock()
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id    TEXT PRIMARY KEY,
                role       TEXT NOT NULL,
                status     TEXT NOT NULL DEFAULT 'NOT_JOINED',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS operations (
                op_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        TEXT NOT NULL,
                prot_type      TEXT NOT NULL,
                status         TEXT NOT NULL DEFAULT 'QUEUED',
                queue_pos      INTEGER,
                overtime_count INTEGER NOT NULL DEFAULT 0,
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );
            CREATE TABLE IF NOT EXISTS user_agent_tokens (
                user_id    TEXT NOT NULL,
                agent_role TEXT NOT NULL,
                token      TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, agent_role),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_ops_queue
                ON operations(queue_pos) WHERE status = 'QUEUED';
            CREATE INDEX IF NOT EXISTS idx_ops_status
                ON operations(status);
            CREATE INDEX IF NOT EXISTS idx_ops_user
                ON operations(user_id);
        """)
        # migration: error_code column (added after initial schema)
        try:
            self._conn.execute(
                "ALTER TABLE operations ADD COLUMN error_code TEXT")
        except sqlite3.OperationalError:
            pass
        # one live operation per user (defense-in-depth against races)
        try:
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_one_live_op_per_user "
                "ON operations(user_id) "
                "WHERE status IN ('QUEUED','ACTIVE','BUSY')")
        except sqlite3.OperationalError:
            pass
        self._conn.commit()

    # users

    def ensure_user(self, user_id: str, role: str) -> None:
        """Insert user if not exists (idempotent)."""
        with self._lock:
            now = _now()
            default_status = "ACTIVE" if role == "querier" else "NOT_JOINED"
            self._conn.execute(
                "INSERT OR IGNORE INTO users (user_id, role, status, created_at, updated_at) "
                "VALUES (?,?,?,?,?)",
                (user_id, role, default_status, now, now),
            )
            self._conn.commit()

    def get_user(self, user_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE user_id=?", (user_id,),
            ).fetchone()
            return dict(row) if row else None

    def user_exists(self, user_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM users WHERE user_id=?", (user_id,),
            ).fetchone()
            return row is not None

    def update_user_status(self, user_id: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE users SET status=?, updated_at=? WHERE user_id=?",
                (status, _now(), user_id),
            )
            self._conn.commit()

    def seed_users(self, pretreat_dir: str) -> None:
        """Load user_ids from pretreat JSONs into users table (idempotent)."""
        for filename, role in [("set_holder_users.json", "set_holder"),
                               ("querier_users.json", "querier")]:
            path = os.path.join(pretreat_dir, filename)
            if not os.path.isfile(path):
                continue
            data = read_json(path)
            for user_id in data:
                self.ensure_user(user_id, role)

    # operations

    def create_operation(self, user_id: str, prot_type: str,
                         queue_pos: int) -> int:
        """Insert a new QUEUED operation.  Returns op_id."""
        with self._lock:
            now = _now()
            cur = self._conn.execute(
                "INSERT INTO operations (user_id, prot_type, status, queue_pos, "
                "overtime_count, created_at, updated_at) "
                "VALUES (?,?,'QUEUED',?,0,?,?)",
                (user_id, prot_type, queue_pos, now, now),
            )
            self._conn.commit()
            return cur.lastrowid

    def get_operation(self, op_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM operations WHERE op_id=?", (op_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_operation_by_user(self, user_id: str, prot_type: str
                              ) -> dict | None:
        """Return the latest operation for *user_id* with given prot_type."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM operations WHERE user_id=? AND prot_type=? "
                "ORDER BY op_id DESC LIMIT 1",
                (user_id, prot_type),
            ).fetchone()
            return dict(row) if row else None

    def is_already_queued(self, user_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM operations "
                "WHERE user_id=? AND status IN ('QUEUED','ACTIVE','BUSY')",
                (user_id,),
            ).fetchone()
            return row is not None

    def get_active_operation_by_user(self, user_id: str) -> dict | None:
        """Return the live operation for *user_id*, if any."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM operations "
                "WHERE user_id=? AND status IN ('QUEUED','ACTIVE','BUSY') "
                "ORDER BY op_id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            return dict(row) if row else None

    def update_operation(self, op_id: int, status: str, **kwargs) -> None:
        """Update status + optional fields (queue_pos, overtime_count, error_code)."""
        with self._lock:
            now = _now()
            sets = ["status=?", "updated_at=?"]
            params = [status, now]
            for col in ("queue_pos", "overtime_count", "error_code"):
                if col in kwargs:
                    sets.append(f"{col}=?")
                    params.append(kwargs[col])
            params.append(op_id)
            self._conn.execute(
                f"UPDATE operations SET {', '.join(sets)} WHERE op_id=?",
                params,
            )
            self._conn.commit()

    # tokens

    # tokens — per-agent (STEWARD / PEER0 / PEER1 each have their own)

    def set_agent_token(self, user_id: str, agent_role: str, token: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO user_agent_tokens "
                "(user_id, agent_role, token, updated_at) VALUES (?,?,?,?)",
                (user_id, agent_role, token, _now()),
            )
            self._conn.commit()

    def get_agent_token(self, user_id: str, agent_role: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT token FROM user_agent_tokens "
                "WHERE user_id=? AND agent_role=?",
                (user_id, agent_role),
            ).fetchone()
            return row["token"] if row else None

    def delete_agent_tokens(self, user_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM user_agent_tokens WHERE user_id=?", (user_id,),
            )
            self._conn.commit()

    # atomic multi-table operations

    def complete_join(self, op_id: int, user_id: str,
                      tokens: dict[str, str]) -> None:
        """Atomically mark operation DONE + store per-agent tokens + set user JOINED.
        *tokens* maps agent role (STEWARD/PEER0/PEER1) → token string."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                now = _now()
                self._conn.execute(
                    "UPDATE operations SET status='DONE', queue_pos=NULL, "
                    "updated_at=? WHERE op_id=?", (now, op_id))
                for role, token in tokens.items():
                    if token:
                        self._conn.execute(
                            "INSERT OR REPLACE INTO user_agent_tokens "
                            "(user_id, agent_role, token, updated_at) "
                            "VALUES (?,?,?,?)",
                            (user_id, role, token, now))
                self._conn.execute(
                    "UPDATE users SET status='JOINED', updated_at=? "
                    "WHERE user_id=?", (now, user_id))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def complete_quit(self, op_id: int, user_id: str) -> None:
        """Atomically mark operation DONE + delete all agent tokens + set user QUITTED."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                now = _now()
                self._conn.execute(
                    "UPDATE operations SET status='DONE', queue_pos=NULL, "
                    "updated_at=? WHERE op_id=?", (now, op_id))
                self._conn.execute(
                    "DELETE FROM user_agent_tokens WHERE user_id=?", (user_id,))
                self._conn.execute(
                    "UPDATE users SET status='QUITTED', updated_at=? "
                    "WHERE user_id=?", (now, user_id))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def fail_operation(self, op_id: int, error_code: str) -> None:
        """Mark operation FAILED with an error code."""
        self.update_operation(op_id, "FAILED", queue_pos=None,
                            error_code=error_code)

    def fail_all_live_operations(self, error_code: str) -> None:
        """Fail every QUEUED/ACTIVE/BUSY operation (cracked task cleanup)."""
        with self._lock:
            self._conn.execute(
                "UPDATE operations SET status='FAILED', queue_pos=NULL, "
                "error_code=?, updated_at=? "
                "WHERE status IN ('QUEUED','ACTIVE','BUSY')",
                (error_code, _now()),
            )
            self._conn.commit()

    def reconcile_on_startup(self) -> dict:
        """Fail any ACTIVE/BUSY operations left over from a previous crash.
        Returns ``{"count": N, "uncertain": bool}`` — *uncertain* is True
        when at least one BUSY row existed (EXECUTE was already in flight)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT op_id, status, prot_type FROM operations "
                "WHERE status IN ('ACTIVE','BUSY')"
            ).fetchall()
            had_busy = any(r["status"] == "BUSY" for r in rows)
            self._conn.execute(
                "UPDATE operations SET status='FAILED', queue_pos=NULL, "
                "error_code='MANAGER_RESTART', updated_at=? "
                "WHERE status IN ('ACTIVE','BUSY')", (_now(),))
            self._conn.commit()
            return {"count": len(rows), "uncertain": had_busy}

    # queue

    def next_queue_pos(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(queue_pos), 0) + 1 FROM operations "
                "WHERE status IN ('QUEUED','ACTIVE','BUSY')",
            ).fetchone()
            return row[0]

    def first_queued(self) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM operations WHERE status='QUEUED' "
                "ORDER BY queue_pos LIMIT 1",
            ).fetchone()
            return dict(row) if row else None

    def get_active(self) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM operations WHERE status IN ('ACTIVE','BUSY') "
                "ORDER BY queue_pos LIMIT 1",
            ).fetchone()
            return dict(row) if row else None

    def queue_position_of(self, op_id: int) -> int | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT queue_pos FROM operations WHERE op_id=? AND status='QUEUED'",
                (op_id,),
            ).fetchone()
            if not row:
                return None
            cnt = self._conn.execute(
                "SELECT COUNT(*) FROM operations WHERE status='QUEUED' "
                "AND queue_pos < ?",
                (row["queue_pos"],),
            ).fetchone()
            return cnt[0] + 1

    def queue_size(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM operations WHERE status='QUEUED'",
            ).fetchone()
            return row[0]

    def touch_operation(self, op_id: int) -> None:
        """Update updated_at timestamp (client heartbeat for queue lease)."""
        with self._lock:
            self._conn.execute(
                "UPDATE operations SET updated_at=? WHERE op_id=?",
                (_now(), op_id),
            )
            self._conn.commit()

    def reorder_queue(self) -> None:
        """Compact queue_pos to 1..N for all QUEUED rows."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT op_id FROM operations WHERE status='QUEUED' "
                "ORDER BY queue_pos",
            ).fetchall()
            for i, r in enumerate(rows, 1):
                self._conn.execute(
                    "UPDATE operations SET queue_pos=? WHERE op_id=?", (i, r["op_id"]),
                )
            self._conn.commit()

    def move_to_pos(self, op_id: int, new_pos: int) -> None:
        """Move a QUEUED operation to *new_pos* (1-based), shifting others."""
        with self._lock:
            row = self._conn.execute(
                "SELECT queue_pos FROM operations WHERE op_id=? AND status='QUEUED'",
                (op_id,),
            ).fetchone()
            if not row:
                return
            old = row["queue_pos"]
            if old < new_pos:
                self._conn.execute(
                    "UPDATE operations SET queue_pos = queue_pos - 1 "
                    "WHERE status='QUEUED' AND queue_pos > ? AND queue_pos <= ?",
                    (old, new_pos),
                )
            elif old > new_pos:
                self._conn.execute(
                    "UPDATE operations SET queue_pos = queue_pos + 1 "
                    "WHERE status='QUEUED' AND queue_pos >= ? AND queue_pos < ?",
                    (new_pos, old),
                )
            self._conn.execute(
                "UPDATE operations SET queue_pos=? WHERE op_id=?", (new_pos, op_id),
            )
            self._conn.commit()
            self.reorder_queue()

    def move_to_end(self, op_id: int) -> None:
        self.move_to_pos(op_id, self.queue_size())

    def remove_from_queue(self, op_id: int) -> None:
        """Mark operation REMOVED, clear queue_pos, set overtime_count=3."""
        self.update_operation(op_id, "REMOVED", queue_pos=None,
                            overtime_count=3, error_code="OVERTIME_REMOVED")
        self.reorder_queue()

    def promote_first_queued(self) -> dict | None:
        """Promote the first QUEUED operation to ACTIVE.  Returns the row."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM operations WHERE status='QUEUED' "
                "ORDER BY queue_pos LIMIT 1",
            ).fetchone()
            if not row:
                return None
            self._conn.execute(
                "UPDATE operations SET status='ACTIVE', updated_at=? "
                "WHERE op_id=?", (_now(), row["op_id"]))
            # compact queue_pos for remaining QUEUED rows
            rows = self._conn.execute(
                "SELECT op_id FROM operations WHERE status='QUEUED' "
                "ORDER BY queue_pos",
            ).fetchall()
            for i, r in enumerate(rows, 1):
                self._conn.execute(
                    "UPDATE operations SET queue_pos=? WHERE op_id=?",
                    (i, r["op_id"]),
                )
            self._conn.commit()
            return dict(self._conn.execute(
                "SELECT * FROM operations WHERE op_id=?",
                (row["op_id"],),
            ).fetchone())


# singleton
db = DB()
