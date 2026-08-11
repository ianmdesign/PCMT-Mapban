from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class Database:
    def __init__(self, data_dir: str | Path | None = None):
        self.data_dir = Path(data_dir or os.getenv("DATA_DIR", "/data"))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "mapban.sqlite3"
        self._lock = threading.RLock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    veto_started_at INTEGER,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_expires
                    ON sessions(expires_at);

                CREATE TABLE IF NOT EXISTS actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    actor_team_id TEXT NOT NULL,
                    submitted_by TEXT NOT NULL,
                    action_kind TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                    UNIQUE(session_id, sequence)
                );
                """
            )

    def create_session(self, session_id: str, payload: dict[str, Any]) -> None:
        now = int(payload["createdAt"])
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions(id, created_at, expires_at, status, veto_started_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    now,
                    int(payload["expiresAt"]),
                    payload["status"],
                    payload.get("vetoStartedAt"),
                    json.dumps(payload, separators=(",", ":")),
                ),
            )

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if not row:
                return None
            return json.loads(row["payload_json"])

    def save_session(self, payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE sessions
                SET status = ?, veto_started_at = ?, payload_json = ?
                WHERE id = ?
                """,
                (
                    payload["status"],
                    payload.get("vetoStartedAt"),
                    json.dumps(payload, separators=(",", ":")),
                    payload["id"],
                ),
            )

    def append_action(
        self,
        *,
        session_id: str,
        sequence: int,
        actor_team_id: str,
        submitted_by: str,
        action_kind: str,
        summary: str,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO actions(
                    session_id, sequence, created_at, actor_team_id, submitted_by,
                    action_kind, summary, before_json, after_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    sequence,
                    int(time.time()),
                    actor_team_id,
                    submitted_by,
                    action_kind,
                    summary,
                    json.dumps(before, separators=(",", ":")),
                    json.dumps(after, separators=(",", ":")),
                ),
            )

    def history(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT sequence, created_at, actor_team_id, submitted_by, action_kind, summary
                FROM actions
                WHERE session_id = ?
                ORDER BY sequence ASC
                """,
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def pop_last_action(self, session_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, before_json
                FROM actions
                WHERE session_id = ?
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            if not row:
                return None
            before = json.loads(row["before_json"])
            conn.execute("DELETE FROM actions WHERE id = ?", (row["id"],))
            return before

    def clear_actions(self, session_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM actions WHERE session_id = ?", (session_id,))

    def delete_expired(self, now: int | None = None) -> int:
        cutoff = int(now or time.time())
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (cutoff,))
            return cur.rowcount
