"""Workspace tree view — lazy-loaded QTreeWidget rooted at workspace_root."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self.tree, 1)

        self.refresh()

    def refresh(self) -> None:
        self.tree.clear()
        root_item = QTreeWidgetItem([self.root.name])
        self.tree.addTopLevelItem(root_item)
        self._populate(root_item, self.root)
        root_item.setExpanded(True)

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
