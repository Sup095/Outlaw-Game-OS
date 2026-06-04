"""Snapshots tab — browse the agent's automatic before-write captures and
roll back a specific file (or a whole session's worth) if something went
wrong.

UX shape
--------
A tree grouped by session. Each session row shows the session id, time
range, and file count. Expanding reveals the per-snapshot entries (timestamp,
path, kind). Buttons:

* **Refresh** — re-read the snapshots folder.
* **View diff…** — open the existing :class:`DiffDialog` showing
  snapshot-vs-current.
* **Restore file** — call :meth:`Orchestrator.restore_snapshot`. Goes through
  the same approval gate as a normal agent write, so the user sees the
  side-by-side diff and approves before the file changes.
* **Restore session** — calls :meth:`Orchestrator.restore_session`. Each file
  individually shows its own approval diff.
* **Clear all** — nuke every snapshot under ``<project>/.outlaw/snapshots/``.

We never block the worker thread from here: restores run synchronously on
the UI thread because they're short and the user is staring at the result.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.snapshots import SessionGroup, SnapshotMeta

from .styles import COLORS


logger = logging.getLogger(__name__)


# Roles on tree items so the click handlers can pick up the right id.
_ROLE_SNAPSHOT_ID = Qt.ItemDataRole.UserRole
_ROLE_SESSION_ID = Qt.ItemDataRole.UserRole + 1
_ROLE_KIND = Qt.ItemDataRole.UserRole + 2  # "session" | "snapshot"


class SnapshotsView(QWidget):
    """Project-scoped snapshot browser. Pass the live orchestrator at construction.

    Falls back to a friendly empty state when no project is open or no
    snapshots exist yet — newcomers who haven't run any agent tasks won't see
    a scary blank tree.
    """

    def __init__(self, orchestrator, parent=None):
        super().__init__(parent)
        self.orch = orchestrator
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Header
        header = QFrame()
        header.setObjectName("PanelHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(10, 6, 10, 6)
        title = QLabel("SNAPSHOTS")
        title.setObjectName("PanelTitle")
        self.refresh_btn = QPushButton("⟳")
        self.refresh_btn.setFixedWidth(34)
        self.refresh_btn.setToolTip("Re-scan the snapshots folder")
        self.refresh_btn.clicked.connect(self.refresh)
        h.addWidget(title)
        h.addStretch(1)
        h.addWidget(self.refresh_btn)

        # Stats / empty state
        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet(
            f"color:{COLORS['muted']};padding:4px 12px;"
        )

        # Tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["When", "Path", "Kind"])
        self.tree.setColumnWidth(0, 160)
        self.tree.setColumnWidth(1, 280)
        self.tree.itemSelectionChanged.connect(self._update_button_state)
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)

        # Action row
        self.diff_btn = QPushButton("View diff…")
        self.diff_btn.clicked.connect(self._on_view_diff)
        self.restore_file_btn = QPushButton("Restore file")
        self.restore_file_btn.setObjectName("PrimaryButton")
        self.restore_file_btn.clicked.connect(self._on_restore_file)
        self.restore_session_btn = QPushButton("Restore session…")
        self.restore_session_btn.clicked.connect(self._on_restore_session)
        self.clear_btn = QPushButton("Clear all…")
        self.clear_btn.setObjectName("DangerButton")
        self.clear_btn.clicked.connect(self._on_clear_all)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(8, 4, 8, 8)
        action_row.setSpacing(6)
        action_row.addWidget(self.diff_btn)
        action_row.addWidget(self.restore_file_btn)
        action_row.addStretch(1)
        action_row.addWidget(self.restore_session_btn)
        action_row.addWidget(self.clear_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self.stats_label)
        layout.addWidget(self.tree, 1)
        layout.addLayout(action_row)

        self._update_button_state()

    # ------------------------------------------------------------------
    # Refresh / populate
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Re-read the snapshots dir and rebuild the tree."""
        self.tree.clear()
        if not self.orch or not self.orch.snapshots:
            self.stats_label.setText("No project open.")
            self._update_button_state()
            return

        try:
            stats = self.orch.snapshots.stats()
            groups = self.orch.snapshots.grouped_by_session()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Snapshot refresh failed")
            self.stats_label.setText(f"<span style='color:{COLORS['err']};'>Snapshot refresh failed: {html.escape(str(exc))}</span>")
            self._update_button_state()
            return

        # Stats row
        size_mb = stats["bytes"] / (1024 * 1024) if stats["bytes"] else 0
        if stats["count"] == 0:
            self.stats_label.setText(
                "No snapshots yet — they'll appear here automatically the first time "
                "the agent edits a file in this project."
            )
        else:
            self.stats_label.setText(
                f"{stats['count']} snapshot{'s' if stats['count'] != 1 else ''} · "
                f"{stats['sessions']} session{'s' if stats['sessions'] != 1 else ''} · "
                f"{size_mb:.1f} MB on disk"
            )

        # Tree
        for group in groups:
            item = self._make_session_item(group)
            self.tree.addTopLevelItem(item)
            for snap in group.snapshots:
                child = self._make_snapshot_item(snap)
                item.addChild(child)
        # Expand the newest session by default; rest collapsed for tidiness.
        if self.tree.topLevelItemCount():
            self.tree.topLevelItem(0).setExpanded(True)
        self._update_button_state()

    def _make_session_item(self, group: SessionGroup) -> QTreeWidgetItem:
        when = _short_when(group.latest_timestamp)
        files = group.file_count
        label = f"Session #{group.session_id} · {files} file{'s' if files != 1 else ''}"
        item = QTreeWidgetItem([when, label, ""])
        # Color the session row so it reads as a header.
        for col in range(3):
            item.setForeground(col, _qcolor(COLORS["accent2"]))
        item.setData(0, _ROLE_KIND, "session")
        item.setData(0, _ROLE_SESSION_ID, group.session_id)
        return item

    def _make_snapshot_item(self, snap: SnapshotMeta) -> QTreeWidgetItem:
        when = _short_when(snap.timestamp)
        # Trim long paths in the middle — agents nest things deep.
        path = snap.path
        if len(path) > 60:
            path = path[:28] + "…" + path[-30:]
        kind_color = {
            "write":   COLORS["info"],
            "create":  COLORS["ok"],
            "append":  COLORS["info"],
            "delete":  COLORS["err"],
            "move":    COLORS["warn"],
            "restore": COLORS["accent2"],
        }.get(snap.kind, COLORS["muted"])
        item = QTreeWidgetItem([when, path, snap.kind])
        item.setForeground(2, _qcolor(kind_color))
        item.setData(0, _ROLE_KIND, "snapshot")
        item.setData(0, _ROLE_SNAPSHOT_ID, snap.snapshot_id)
        item.setData(0, _ROLE_SESSION_ID, snap.session_id)
        item.setToolTip(1, snap.path)
        return item

    # ------------------------------------------------------------------
    # Selection state → which buttons are enabled
    # ------------------------------------------------------------------

    def _selected_snapshot_id(self) -> str | None:
        it = self.tree.currentItem()
        if not it:
            return None
        if it.data(0, _ROLE_KIND) != "snapshot":
            return None
        return it.data(0, _ROLE_SNAPSHOT_ID)

    def _selected_session_id(self) -> int | None:
        it = self.tree.currentItem()
        if not it:
            return None
        return it.data(0, _ROLE_SESSION_ID)

    def _update_button_state(self) -> None:
        has_orch = bool(self.orch)
        has_session = self._selected_session_id() is not None
        has_snapshot = self._selected_snapshot_id() is not None
        self.diff_btn.setEnabled(has_snapshot)
        self.restore_file_btn.setEnabled(has_snapshot)
        self.restore_session_btn.setEnabled(has_session)
        self.clear_btn.setEnabled(has_orch and self.tree.topLevelItemCount() > 0)

    def _on_double_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        if item.data(0, _ROLE_KIND) == "snapshot":
            self._on_view_diff()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_view_diff(self) -> None:
        sid = self._selected_snapshot_id()
        if not sid:
            return
        meta = self.orch.snapshots.get(sid)
        if not meta:
            return
        old = self.orch.snapshots.read_content(sid) or ""
        current = self.orch.file_controller.current_text(meta.path)
        if old == current:
            QMessageBox.information(
                self, "No diff",
                f"The current file matches this snapshot — nothing to compare.",
            )
            return
        from .diff_dialog import DiffDialog
        # We re-use the approval dialog as a read-only diff viewer. "Approve"
        # would mean "make current become the snapshot" — but the user explicitly
        # asked for a diff view, so we tell them that and ignore their answer.
        dlg = DiffDialog(
            meta.path,
            current,         # left = "before" = current on-disk
            old,             # right = "after" = the snapshotted older state
            kind="snapshot",
            parent=self,
        )
        dlg.exec()  # we don't act on dlg.approved here — restore is a separate button

    def _on_restore_file(self) -> None:
        sid = self._selected_snapshot_id()
        if not sid:
            return
        meta = self.orch.snapshots.get(sid)
        if not meta:
            return
        confirm = QMessageBox.question(
            self,
            "Restore from snapshot?",
            f"Restore '{meta.path}' to the state captured on {_short_when(meta.timestamp)}?\n\n"
            "You'll see the diff in the approval dialog and can still cancel.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        result = self.orch.restore_snapshot(sid)
        QMessageBox.information(self, "Restore", result)
        self.refresh()

    def _on_restore_session(self) -> None:
        session_id = self._selected_session_id()
        if session_id is None:
            return
        confirm = QMessageBox.question(
            self,
            "Restore whole session?",
            f"Roll back every file the agent touched in session #{session_id} to its "
            "earliest captured state in that session?\n\n"
            "Each file will show its own diff in the approval dialog — you can "
            "approve or skip per file.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        results = self.orch.restore_session(session_id)
        QMessageBox.information(self, "Restore session", "\n".join(results))
        self.refresh()

    def _on_clear_all(self) -> None:
        n = self.orch.snapshots.stats()["count"]
        if n == 0:
            return
        confirm = QMessageBox.warning(
            self,
            "Clear all snapshots?",
            f"Delete all {n} snapshot{'s' if n != 1 else ''} for this project?\n\n"
            "This cannot be undone. Your project files are unaffected — only the "
            "agent's automatic safety captures are removed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        purged = self.orch.snapshots.purge_all()
        QMessageBox.information(self, "Cleared", f"Removed {purged} snapshot folder(s).")
        self.refresh()

    # ------------------------------------------------------------------
    # External hooks
    # ------------------------------------------------------------------

    @pyqtSlot()
    def on_workspace_changed(self) -> None:
        """Called by MainWindow when the orchestrator's workspace changes."""
        self.refresh()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _short_when(iso: str) -> str:
    """Render an ISO timestamp as 'Jun 01 14:32' or similar — easy to scan."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return iso
    return dt.strftime("%b %d %H:%M")


def _qcolor(hex_str: str):
    """Lazy import so this module doesn't pull QtGui at top level."""
    from PyQt6.QtGui import QBrush, QColor
    return QBrush(QColor(hex_str))
