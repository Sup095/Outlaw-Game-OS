"""Snapshot manager — the safety net under every agent file write.

Every time the agent calls ``file_write`` / ``file_append`` / ``file_delete``
/ ``file_move`` through the orchestrator's guarded helpers, the OLD state of
the target file is copied into a project-local snapshot directory **before**
the new content lands. If the agent's edit corrupts a scene file or wipes
the wrong script, the user can browse to the Snapshots tab, see exactly which
session and turn caused it, and restore — gated by the same approval diff
the original write was.

Layout
------
::

    <project>/.outlaw/snapshots/
    ├── 20260601-143201-001/
    │   ├── content.txt   ← the OLD file content (or empty for "delete" / "create")
    │   └── meta.json     ← path, kind, session_id, time, original_size
    ├── 20260601-143305-002/
    └── ...

Each snapshot is a self-contained folder. We do not store snapshots in
sqlite — they need to survive a corrupted DB or a stale session, and the
filesystem is the truth. Sessions are metadata (a column in meta.json), not
a directory layer, so pruning is a flat "delete oldest N" sort.

Retention
---------
* Hard cap on count (default 200 entries).
* Hard cap on total disk use (default 100 MB).
* Whichever cap trips first, oldest snapshots are deleted until both are
  satisfied. Empty snapshot dirs (e.g., for deletes of empty files) are
  pruned at the same time.

What we DO NOT snapshot
-----------------------
* Reads (obviously).
* Writes that the approval gate denied (caller doesn't reach the snapshot
  call in that case).
* `.outlaw/snapshots/**` itself (would loop forever).
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable


logger = logging.getLogger(__name__)


SNAPSHOT_DIR_NAME = ".outlaw/snapshots"
DEFAULT_MAX_COUNT = 200
DEFAULT_MAX_BYTES = 100 * 1024 * 1024  # 100 MB


@dataclass(frozen=True)
class SnapshotMeta:
    """Metadata for one snapshot. Mirrors meta.json on disk."""
    snapshot_id: str         # the folder name, e.g. "20260601-143201-001"
    path: str                # relative path inside the workspace
    kind: str                # "write" | "append" | "delete" | "move" | "create"
    session_id: int          # MemoryStore session that triggered it
    timestamp: str           # ISO 8601 local time
    original_size: int       # bytes of the OLD content (0 for new files)
    note: str = ""           # optional human label

    @classmethod
    def from_dict(cls, snapshot_id: str, d: dict) -> "SnapshotMeta":
        return cls(
            snapshot_id=snapshot_id,
            path=str(d.get("path", "")),
            kind=str(d.get("kind", "write")),
            session_id=int(d.get("session_id", 0)),
            timestamp=str(d.get("timestamp", "")),
            original_size=int(d.get("original_size", 0)),
            note=str(d.get("note", "")),
        )

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "kind": self.kind,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "original_size": self.original_size,
            "note": self.note,
        }


@dataclass
class SessionGroup:
    """A session's worth of snapshots, for UI grouping."""
    session_id: int
    snapshots: list[SnapshotMeta] = field(default_factory=list)

    @property
    def latest_timestamp(self) -> str:
        return self.snapshots[-1].timestamp if self.snapshots else ""

    @property
    def file_count(self) -> int:
        return len({s.path for s in self.snapshots})


class SnapshotManager:
    """Project-scoped snapshot capture + browse + restore."""

    def __init__(
        self,
        workspace_root: str | Path,
        max_count: int = DEFAULT_MAX_COUNT,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ):
        self._lock = threading.Lock()  # capture can be called from the worker thread
        self._counter = 0
        self.max_count = max_count
        self.max_bytes = max_bytes
        self.set_workspace(workspace_root)

    # ------------------------------------------------------------------
    # Workspace switching
    # ------------------------------------------------------------------

    def set_workspace(self, workspace_root: str | Path) -> None:
        """Re-point at a different project. Snapshots are per-project on disk."""
        with self._lock:
            self.workspace_root = Path(workspace_root) if workspace_root else None
            self._counter = 0  # the per-second counter resets per-project
            if self.workspace_root:
                self.snapshot_root = self.workspace_root / SNAPSHOT_DIR_NAME
                try:
                    self.snapshot_root.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    logger.warning("Could not create snapshot dir %s: %s",
                                   self.snapshot_root, exc)
                    self.snapshot_root = None
            else:
                self.snapshot_root = None

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def capture(
        self,
        rel_path: str,
        old_content: str,
        kind: str,
        session_id: int,
        note: str = "",
    ) -> SnapshotMeta | None:
        """Save the OLD content of ``rel_path`` to a fresh snapshot folder.

        Returns the SnapshotMeta on success or None if snapshotting is
        disabled (no workspace) or fails. Never raises — a snapshot failure
        shouldn't block the agent from doing its work.
        """
        if not self.snapshot_root:
            return None
        # Don't snapshot writes inside the snapshots directory itself.
        norm = (rel_path or "").replace("\\", "/").lstrip("/")
        if norm.startswith(".outlaw/snapshots"):
            return None
        with self._lock:
            try:
                meta = self._do_capture(norm, old_content, kind, session_id, note)
            except OSError as exc:
                logger.warning("Snapshot capture failed for %s: %s", rel_path, exc)
                return None
            # Prune AFTER writing so we never delete what we just made.
            try:
                self._prune()
            except OSError as exc:
                logger.warning("Snapshot prune failed: %s", exc)
            return meta

    def _do_capture(
        self,
        rel_path: str,
        old_content: str,
        kind: str,
        session_id: int,
        note: str,
    ) -> SnapshotMeta:
        # Folder name: <local ISO no colons>-<counter for sub-second collisions>.
        now = datetime.now()
        self._counter = (self._counter + 1) % 1000
        snapshot_id = f"{now.strftime('%Y%m%d-%H%M%S')}-{self._counter:03d}"
        folder = self.snapshot_root / snapshot_id
        folder.mkdir(parents=True, exist_ok=False)

        content_bytes = (old_content or "").encode("utf-8", errors="replace")
        (folder / "content.txt").write_bytes(content_bytes)

        meta = SnapshotMeta(
            snapshot_id=snapshot_id,
            path=rel_path,
            kind=kind,
            session_id=session_id,
            timestamp=now.isoformat(timespec="seconds"),
            original_size=len(content_bytes),
            note=note,
        )
        (folder / "meta.json").write_text(json.dumps(meta.to_dict(), indent=2), encoding="utf-8")
        return meta

    # ------------------------------------------------------------------
    # Read / browse
    # ------------------------------------------------------------------

    def list_snapshots(self) -> list[SnapshotMeta]:
        """All snapshots, sorted oldest-first."""
        if not self.snapshot_root or not self.snapshot_root.exists():
            return []
        out: list[SnapshotMeta] = []
        for child in sorted(self.snapshot_root.iterdir(), key=lambda p: p.name):
            if not child.is_dir():
                continue
            meta_path = child / "meta.json"
            if not meta_path.exists():
                continue
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                out.append(SnapshotMeta.from_dict(child.name, data))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Bad snapshot meta in %s: %s", child, exc)
        return out

    def grouped_by_session(self) -> list[SessionGroup]:
        """All snapshots, grouped by session, newest session first."""
        groups: dict[int, SessionGroup] = {}
        for snap in self.list_snapshots():
            grp = groups.setdefault(snap.session_id, SessionGroup(snap.session_id))
            grp.snapshots.append(snap)
        # Sort within each group by timestamp ascending so the most recent edit is last.
        for grp in groups.values():
            grp.snapshots.sort(key=lambda s: s.timestamp)
        return sorted(groups.values(),
                      key=lambda g: g.latest_timestamp,
                      reverse=True)

    def read_content(self, snapshot_id: str) -> str | None:
        """Return the OLD content stored in the snapshot, or None if absent."""
        if not self.snapshot_root:
            return None
        content_path = self.snapshot_root / snapshot_id / "content.txt"
        if not content_path.exists():
            return None
        try:
            return content_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Could not read snapshot %s: %s", snapshot_id, exc)
            return None

    def get(self, snapshot_id: str) -> SnapshotMeta | None:
        meta_path = self.snapshot_root and (self.snapshot_root / snapshot_id / "meta.json")
        if not meta_path or not meta_path.exists():
            return None
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            return SnapshotMeta.from_dict(snapshot_id, data)
        except (OSError, json.JSONDecodeError):
            return None

    def stats(self) -> dict:
        if not self.snapshot_root or not self.snapshot_root.exists():
            return {"count": 0, "bytes": 0, "sessions": 0}
        bytes_used = 0
        count = 0
        sessions: set[int] = set()
        for snap in self.list_snapshots():
            count += 1
            sessions.add(snap.session_id)
            bytes_used += snap.original_size
        return {"count": count, "bytes": bytes_used, "sessions": len(sessions)}

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------

    def _prune(self) -> int:
        """Delete oldest snapshots until both count + size caps are satisfied."""
        if not self.snapshot_root or not self.snapshot_root.exists():
            return 0
        # Build (id, ts, bytes-on-disk) — bytes-on-disk includes meta.json so
        # the cap is honest about its overhead.
        entries: list[tuple[str, str, int]] = []
        total = 0
        for child in self.snapshot_root.iterdir():
            if not child.is_dir():
                continue
            try:
                size = sum(p.stat().st_size for p in child.rglob("*") if p.is_file())
            except OSError:
                size = 0
            entries.append((child.name, child.name, size))
            total += size
        entries.sort(key=lambda e: e[1])  # oldest first by id (timestamp prefix)
        removed = 0
        while entries and (len(entries) > self.max_count or total > self.max_bytes):
            sid, _, size = entries.pop(0)
            shutil.rmtree(self.snapshot_root / sid, ignore_errors=True)
            total -= size
            removed += 1
        return removed

    def purge_all(self) -> int:
        """Manual nuke — used by the Snapshots panel's *Clear all* button."""
        if not self.snapshot_root or not self.snapshot_root.exists():
            return 0
        n = 0
        for child in list(self.snapshot_root.iterdir()):
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
                n += 1
        return n


# ---------------------------------------------------------------------------
# Bulk-restore plan
# ---------------------------------------------------------------------------


@dataclass
class RestorePlanItem:
    """One file to restore: the snapshot we're reverting to + its meta."""
    meta: SnapshotMeta
    old_content: str


def build_session_restore_plan(
    manager: SnapshotManager,
    session_id: int,
    paths: Iterable[str] | None = None,
) -> list[RestorePlanItem]:
    """For each unique path touched in ``session_id``, pick the *earliest*
    snapshot (its OLDEST captured state). That's the right semantic for
    "undo the agent's work in this session" — we want the file as it was
    before the session started touching it, not after the first turn.
    """
    target_paths = set(paths) if paths else None
    snapshots = sorted(
        (s for s in manager.list_snapshots() if s.session_id == session_id),
        key=lambda s: s.timestamp,
    )
    earliest: dict[str, SnapshotMeta] = {}
    for snap in snapshots:
        if target_paths is not None and snap.path not in target_paths:
            continue
        # First time we see this path in the sorted-ascending order = oldest.
        earliest.setdefault(snap.path, snap)

    plan: list[RestorePlanItem] = []
    for meta in earliest.values():
        content = manager.read_content(meta.snapshot_id)
        if content is None:
            continue
        plan.append(RestorePlanItem(meta=meta, old_content=content))
    return plan
