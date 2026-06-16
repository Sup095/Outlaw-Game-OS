"""Workspace tree view — a QTreeWidget rooted at workspace_root.

Phase 14b: the tree now AUTO-UPDATES from what's actually on disk. A
QFileSystemWatcher watches every (non-skipped) directory in the project and a
short debounce coalesces bursts of changes (e.g. the agent writing several files
at once) into a single refresh, so the tree always mirrors the real project
without the user pressing Refresh. Expanded folders stay expanded across an
auto-refresh.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QFileSystemWatcher, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


SKIP = {"__pycache__", "node_modules", ".git", ".godot/cache"}


class WorkspaceView(QWidget):
    file_selected = pyqtSignal(str)  # relative path

    def __init__(self, workspace_root: str):
        super().__init__()
        self.root = Path(workspace_root)
        self.root.mkdir(parents=True, exist_ok=True)

        header = QFrame()
        header.setObjectName("PanelHeader")
        hbox = QHBoxLayout(header)
        hbox.setContentsMargins(10, 6, 10, 6)
        title = QLabel("WORKSPACE")
        title.setObjectName("PanelTitle")
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        hbox.addWidget(title)
        hbox.addStretch(1)
        hbox.addWidget(refresh)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemDoubleClicked.connect(self._on_double_click)

        # Phase 14b — live auto-refresh. The watcher fires on directory content
        # changes; the debounce turns a burst into one refresh ~0.4s after it
        # settles, so rapid agent writes don't thrash the UI.
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(lambda _p: self._debounce.start())
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(400)
        self._debounce.timeout.connect(self.refresh)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self.tree, 1)

        self.refresh()

    def refresh(self) -> None:
        expanded = self._expanded_paths()
        self.tree.clear()
        root_item = QTreeWidgetItem([self.root.name])
        root_item.setData(0, Qt.ItemDataRole.UserRole, "")  # root = empty rel path
        self.tree.addTopLevelItem(root_item)
        self._populate(root_item, self.root)
        root_item.setExpanded(True)
        self._restore_expanded(expanded)
        self._rearm_watcher()

    def _populate(self, parent_item: QTreeWidgetItem, parent_path: Path) -> None:
        try:
            entries = sorted(parent_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except (PermissionError, FileNotFoundError):
            return
        for entry in entries:
            if entry.name in SKIP or entry.name.startswith("."):
                continue
            item = QTreeWidgetItem([entry.name])
            item.setData(0, Qt.ItemDataRole.UserRole, str(entry.relative_to(self.root)).replace("\\", "/"))
            parent_item.addChild(item)
            if entry.is_dir():
                self._populate(item, entry)

    def _on_double_click(self, item: QTreeWidgetItem, _col: int) -> None:
        rel = item.data(0, Qt.ItemDataRole.UserRole)
        if rel:
            self.file_selected.emit(rel)

    # ------------------------------------------------------------------
    # Auto-refresh helpers (Phase 14b)
    # ------------------------------------------------------------------

    def _expanded_paths(self) -> set[str]:
        """Relative paths of every currently-expanded folder, so a refresh can
        restore the user's open folders instead of collapsing everything."""
        out: set[str] = set()

        def walk(item: QTreeWidgetItem) -> None:
            for i in range(item.childCount()):
                child = item.child(i)
                if child.isExpanded():
                    rel = child.data(0, Qt.ItemDataRole.UserRole)
                    if rel:
                        out.add(rel)
                walk(child)

        if self.tree.topLevelItemCount():
            walk(self.tree.topLevelItem(0))
        return out

    def _restore_expanded(self, expanded: set[str]) -> None:
        if not expanded:
            return

        def walk(item: QTreeWidgetItem) -> None:
            for i in range(item.childCount()):
                child = item.child(i)
                if child.data(0, Qt.ItemDataRole.UserRole) in expanded:
                    child.setExpanded(True)
                walk(child)

        if self.tree.topLevelItemCount():
            walk(self.tree.topLevelItem(0))

    def _collect_dirs(self, path: Path, acc: list[str]) -> None:
        acc.append(str(path))
        try:
            for entry in path.iterdir():
                if entry.is_dir() and entry.name not in SKIP and not entry.name.startswith("."):
                    self._collect_dirs(entry, acc)
        except (PermissionError, FileNotFoundError):
            pass

    def _rearm_watcher(self) -> None:
        """Re-point the watcher at the current set of project directories (new
        folders appear, deleted ones drop off)."""
        old = self._watcher.directories()
        if old:
            self._watcher.removePaths(old)
        dirs: list[str] = []
        self._collect_dirs(self.root, dirs)
        if dirs:
            self._watcher.addPaths(dirs)
