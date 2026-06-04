"""SQLite schema + lightweight DAO for Outlaw CodeMaker.

Tables
------
sessions               : One row per chat/work session
messages               : All user/assistant/tool messages, FK to sessions
workspace_snapshots    : Periodic dumps of the virtual workspace tree
proactive_advice       : History of unsolicited suggestions the agent has made
reasoning_traces       : Plan/Review/Execute artifacts per task, FK to messages
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT    NOT NULL,
    workspace     TEXT    NOT NULL,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role          TEXT    NOT NULL CHECK (role IN ('user','assistant','tool','system')),
    content       TEXT    NOT NULL,
    metadata      TEXT,
    created_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);

CREATE TABLE IF NOT EXISTS workspace_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    tree_json     TEXT    NOT NULL,
    file_count    INTEGER NOT NULL,
    captured_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS proactive_advice (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    advice        TEXT    NOT NULL,
    accepted      INTEGER,
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS reasoning_traces (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id    INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    stage         TEXT    NOT NULL CHECK (stage IN ('plan','review','execute')),
    body          TEXT    NOT NULL,
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS evolution_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    mode          TEXT    NOT NULL,
    rationale     TEXT,
    files_changed TEXT,
    validation    TEXT,
    swapped       INTEGER NOT NULL DEFAULT 0,
    dry_run       INTEGER NOT NULL DEFAULT 0,
    detail        TEXT,
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS roadmap (
    workspace     TEXT    PRIMARY KEY,
    data_json     TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS task_state (
    session_id        INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    user_task         TEXT    NOT NULL,
    reviewed_plan     TEXT,
    observations_json TEXT,
    status            TEXT    NOT NULL DEFAULT 'paused',
    updated_at        TEXT    NOT NULL
);
"""


@dataclass
class Session:
    id: int
    title: str
    workspace: str
    created_at: str
    updated_at: str


@dataclass
class Message:
    id: int
    session_id: int
    role: str
    content: str
    metadata: dict | None
    created_at: str


def _now() -> str:
    # Local time, no tz suffix — keeps the history list readable ("Jan 03 · 14:22").
    return datetime.now().isoformat(timespec="seconds")


def init_database(db_path: str | Path) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)


class MemoryStore:
    """Thin DAO wrapper. Each call opens its own connection (cheap on SQLite, thread-safe)."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def create_session(self, title: str, workspace: str) -> int:
        now = _now()
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO sessions(title, workspace, created_at, updated_at) VALUES (?,?,?,?)",
                (title, workspace, now, now),
            )
            return cur.lastrowid

    def list_sessions(self) -> list[Session]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
            return [Session(**dict(r)) for r in rows]

    def get_session(self, session_id: int) -> Session | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            return Session(**dict(row)) if row else None

    def rename_session(self, session_id: int, title: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title, _now(), session_id),
            )

    def delete_session(self, session_id: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def list_session_summaries(self) -> list[dict]:
        """Sessions enriched with message count + first user-message preview, newest first."""
        sql = """
            SELECT s.id, s.title, s.workspace, s.created_at, s.updated_at,
                   COUNT(m.id) AS message_count,
                   (SELECT content FROM messages
                      WHERE session_id = s.id AND role = 'user'
                      ORDER BY id ASC LIMIT 1) AS preview
            FROM sessions s
            LEFT JOIN messages m ON m.session_id = s.id
            GROUP BY s.id
            ORDER BY s.updated_at DESC
        """
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql).fetchall()]

    def get_reasoning_traces(self, message_id: int) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT stage, body, created_at FROM reasoning_traces "
                "WHERE message_id = ? ORDER BY id ASC",
                (message_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def is_session_empty(self, session_id: int) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE session_id = ?", (session_id,)
            ).fetchone()
            return (row["n"] if row else 0) == 0

    def add_message(
        self,
        session_id: int,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO messages(session_id, role, content, metadata, created_at) VALUES (?,?,?,?,?)",
                (
                    session_id,
                    role,
                    content,
                    json.dumps(metadata) if metadata else None,
                    _now(),
                ),
            )
            c.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (_now(), session_id),
            )
            return cur.lastrowid

    def get_messages(self, session_id: int, limit: int = 200) -> list[Message]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            return [
                Message(
                    id=r["id"],
                    session_id=r["session_id"],
                    role=r["role"],
                    content=r["content"],
                    metadata=json.loads(r["metadata"]) if r["metadata"] else None,
                    created_at=r["created_at"],
                )
                for r in rows
            ]

    def save_reasoning_trace(self, message_id: int, stage: str, body: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO reasoning_traces(message_id, stage, body, created_at) VALUES (?,?,?,?)",
                (message_id, stage, body, _now()),
            )

    def save_workspace_snapshot(
        self, session_id: int, tree: dict, file_count: int
    ) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO workspace_snapshots(session_id, tree_json, file_count, captured_at) VALUES (?,?,?,?)",
                (session_id, json.dumps(tree), file_count, _now()),
            )

    def save_proactive_advice(self, session_id: int, advice: str) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO proactive_advice(session_id, advice, accepted, created_at) VALUES (?,?,?,?)",
                (session_id, advice, None, _now()),
            )
            return cur.lastrowid

    def mark_advice_accepted(self, advice_id: int, accepted: bool) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE proactive_advice SET accepted = ? WHERE id = ?",
                (1 if accepted else 0, advice_id),
            )

    # ----- evolution log --------------------------------------------------

    def log_evolution(
        self,
        mode: str,
        rationale: str,
        files_changed: list[str],
        validation: bool,
        swapped: bool,
        dry_run: bool,
        detail: str = "",
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO evolution_log(mode, rationale, files_changed, validation, "
                "swapped, dry_run, detail, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    mode,
                    rationale,
                    json.dumps(files_changed),
                    "PASS" if validation else "FAIL",
                    1 if swapped else 0,
                    1 if dry_run else 0,
                    detail[:4000],
                    _now(),
                ),
            )
            return cur.lastrowid

    def list_evolution(self, limit: int = 100) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM evolution_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["files_changed"] = json.loads(d["files_changed"]) if d["files_changed"] else []
                except json.JSONDecodeError:
                    d["files_changed"] = []
                out.append(d)
            return out

    # ----- roadmap (per project) -----------------------------------------

    def save_roadmap(self, workspace: str, data: dict) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO roadmap(workspace, data_json, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(workspace) DO UPDATE SET data_json=excluded.data_json, "
                "updated_at=excluded.updated_at",
                (workspace, json.dumps(data), _now()),
            )

    def get_roadmap(self, workspace: str) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT data_json FROM roadmap WHERE workspace = ?", (workspace,)
            ).fetchone()
            if not row:
                return None
            try:
                return json.loads(row["data_json"])
            except json.JSONDecodeError:
                return None

    # ----- task state (resume checkpoint per session) --------------------

    def save_task_state(
        self, session_id: int, user_task: str, reviewed_plan: str,
        observations: list[str], status: str = "paused",
    ) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO task_state(session_id, user_task, reviewed_plan, "
                "observations_json, status, updated_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET user_task=excluded.user_task, "
                "reviewed_plan=excluded.reviewed_plan, observations_json=excluded.observations_json, "
                "status=excluded.status, updated_at=excluded.updated_at",
                (session_id, user_task, reviewed_plan, json.dumps(observations), status, _now()),
            )

    def get_task_state(self, session_id: int) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM task_state WHERE session_id = ?", (session_id,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["observations"] = json.loads(d["observations_json"]) if d["observations_json"] else []
            except json.JSONDecodeError:
                d["observations"] = []
            return d

    def clear_task_state(self, session_id: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM task_state WHERE session_id = ?", (session_id,))
