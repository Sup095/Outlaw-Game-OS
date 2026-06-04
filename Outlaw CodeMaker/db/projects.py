"""Projects registry for Outlaw CodeMaker.

A lightweight catalogue of every Godot project Outlaw knows about. Shares
outlaw.db with the MemoryStore; we add our own table via IF NOT EXISTS so the
two coexist cleanly.

Used by:
- Project Picker: lists recent projects on launch
- New Game wizard: registers a freshly-scaffolded project
- Steam pipeline (future): reads steam_app_id + target_platforms when shipping
- Recent Projects menu: jump back to a previous game

Paths are stored as resolved, forward-slash strings so they compare cleanly
across Windows / Linux.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT    NOT NULL,
    path               TEXT    NOT NULL UNIQUE,
    godot_version      TEXT,
    dimension          TEXT,
    genre              TEXT,
    steam_app_id       TEXT,
    target_platforms   TEXT,
    thumbnail_path     TEXT,
    created_at         TEXT    NOT NULL,
    last_opened_at     TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_projects_last_opened
    ON projects(last_opened_at DESC);
"""


@dataclass
class Project:
    id: int
    name: str
    path: str
    godot_version: str | None
    dimension: str | None
    genre: str | None
    steam_app_id: str | None
    target_platforms: list[str]
    thumbnail_path: str | None
    created_at: str
    last_opened_at: str


def _now() -> str:
    # Microsecond precision so two registers in the same second still sort
    # deterministically. MemoryStore uses seconds-precision but only stores one
    # timestamp per *session* (which never collides), so the cross-table sort
    # used by the History view still works fine.
    return datetime.now().isoformat(timespec="microseconds")


def _canonical(path: str | Path) -> str:
    """Resolve + normalize a path to forward slashes — our DB-side identity."""
    return str(Path(path).expanduser().resolve()).replace("\\", "/")


def init_projects_table(db_path: str | Path) -> None:
    """Ensure the projects table exists. Safe to call repeatedly."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)


class ProjectsStore:
    """DAO for the projects registry. Mirrors MemoryStore's connection pattern."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        init_projects_table(self.db_path)

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

    def _row_to_project(self, row: sqlite3.Row) -> Project:
        d = dict(row)
        try:
            platforms = json.loads(d["target_platforms"]) if d["target_platforms"] else []
        except json.JSONDecodeError:
            platforms = []
        return Project(
            id=d["id"],
            name=d["name"],
            path=d["path"],
            godot_version=d["godot_version"],
            dimension=d["dimension"],
            genre=d["genre"],
            steam_app_id=d["steam_app_id"],
            target_platforms=platforms,
            thumbnail_path=d["thumbnail_path"],
            created_at=d["created_at"],
            last_opened_at=d["last_opened_at"],
        )

    # ------------------------------------------------------------------
    # Write paths
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        path: str | Path,
        *,
        godot_version: str | None = None,
        dimension: str | None = None,
        genre: str | None = None,
        steam_app_id: str | None = None,
        target_platforms: list[str] | None = None,
        thumbnail_path: str | None = None,
    ) -> int:
        """Register or update a project, then mark it as just-opened.

        Idempotent: re-registering an existing path patches its metadata
        (COALESCE so None values don't blow away existing fields) and bumps
        last_opened_at. Returns the project id either way.
        """
        now = _now()
        canon = _canonical(path)
        platforms_json = json.dumps(target_platforms) if target_platforms is not None else None
        with self._conn() as c:
            existing = c.execute("SELECT id FROM projects WHERE path = ?", (canon,)).fetchone()
            if existing:
                c.execute(
                    "UPDATE projects SET "
                    "name = ?, "
                    "godot_version = COALESCE(?, godot_version), "
                    "dimension = COALESCE(?, dimension), "
                    "genre = COALESCE(?, genre), "
                    "steam_app_id = COALESCE(?, steam_app_id), "
                    "target_platforms = COALESCE(?, target_platforms), "
                    "thumbnail_path = COALESCE(?, thumbnail_path), "
                    "last_opened_at = ? "
                    "WHERE id = ?",
                    (name, godot_version, dimension, genre, steam_app_id,
                     platforms_json, thumbnail_path, now, existing["id"]),
                )
                return existing["id"]
            cur = c.execute(
                "INSERT INTO projects("
                "name, path, godot_version, dimension, genre, "
                "steam_app_id, target_platforms, thumbnail_path, "
                "created_at, last_opened_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?)",
                (name, canon, godot_version, dimension, genre, steam_app_id,
                 platforms_json or json.dumps([]), thumbnail_path, now, now),
            )
            return cur.lastrowid

    def touch(self, path: str | Path) -> None:
        """Mark a project as the most recently opened. No-op if path isn't registered."""
        canon = _canonical(path)
        with self._conn() as c:
            c.execute(
                "UPDATE projects SET last_opened_at = ? WHERE path = ?",
                (_now(), canon),
            )

    def update_steam_info(
        self, project_id: int, steam_app_id: str | None, target_platforms: list[str],
    ) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE projects SET steam_app_id = ?, target_platforms = ? WHERE id = ?",
                (steam_app_id, json.dumps(target_platforms), project_id),
            )

    def rename(self, project_id: int, name: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE projects SET name = ? WHERE id = ?", (name, project_id))

    def remove(self, project_id: int) -> None:
        """Forget a project. Does NOT delete files on disk."""
        with self._conn() as c:
            c.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    def remove_missing(self) -> list[str]:
        """Prune entries whose path no longer exists. Returns the removed paths."""
        removed: list[str] = []
        for proj in self.list_recent(limit=1000):
            if not Path(proj.path).exists():
                self.remove(proj.id)
                removed.append(proj.path)
        return removed

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    def get(self, project_id: int) -> Project | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            return self._row_to_project(row) if row else None

    def get_by_path(self, path: str | Path) -> Project | None:
        canon = _canonical(path)
        with self._conn() as c:
            row = c.execute("SELECT * FROM projects WHERE path = ?", (canon,)).fetchone()
            return self._row_to_project(row) if row else None

    def list_recent(self, limit: int = 20) -> list[Project]:
        with self._conn() as c:
            # id DESC as a tiebreaker so even microsecond-identical timestamps
            # produce a deterministic "newest wins" order.
            rows = c.execute(
                "SELECT * FROM projects ORDER BY last_opened_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._row_to_project(r) for r in rows]

    def most_recent(self) -> Project | None:
        recent = self.list_recent(limit=1)
        return recent[0] if recent else None

    def count(self) -> int:
        with self._conn() as c:
            row = c.execute("SELECT COUNT(*) AS n FROM projects").fetchone()
            return int(row["n"]) if row else 0
