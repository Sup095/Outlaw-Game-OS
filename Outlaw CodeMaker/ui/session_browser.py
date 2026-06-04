"""Session history browser — lists past sessions (across runs) and resumes them."""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


def _fmt_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%b %d · %H:%M")
    except Exception:  # noqa: BLE001
        return iso or ""


class _SessionRow(QFrame):
    open_requested = pyqtSignal(int)
    delete_requested = pyqtSignal(int)

    def __init__(self, summary: dict, is_current: bool):
        super().__init__()
        self.session_id = int(summary["id"])
        self.setObjectName("SessionRow")
        self.setProperty("current", is_current)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        title = summary.get("title") or "Untitled"
        count = summary.get("message_count", 0)
        meta = f"{_fmt_time(summary.get('updated_at', ''))}  ·  {count} msg"
        preview = (summary.get("preview") or "").strip().replace("\n", " ")
        if len(preview) > 60:
            preview = preview[:60] + "…"

        title_lbl = QLabel(("● " if is_current else "") + title)
        title_lbl.setObjectName("SessionTitle")
        title_lbl.setWordWrap(True)
        meta_lbl = QLabel(meta + (f"  —  {preview}" if preview else ""))
        meta_lbl.setObjectName("SessionMeta")
        meta_lbl.setWordWrap(True)

        del_btn = QToolButton()
        del_btn.setText("✕")
        del_btn.setObjectName("SessionDelete")
        del_btn.setToolTip("Delete this session")
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.clicked.connect(lambda: self.delete_requested.emit(self.session_id))

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        text_col.addWidget(title_lbl)
        text_col.addWidget(meta_lbl)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 8, 8)
        row.addLayout(text_col, 1)
        row.addWidget(del_btn, 0, Qt.AlignmentFlag.AlignTop)

        self.setStyleSheet(self._qss(is_current))

    @staticmethod
    def _qss(current: bool) -> str:
        border = "#e0b341" if current else "#2a3242"
        bg = "#1d2433" if current else "#141925"
        return f"""
            QFrame#SessionRow {{
                background: {bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            QFrame#SessionRow:hover {{ border: 1px solid #3a4456; background: #1b2130; }}
            QLabel#SessionTitle {{ color: #f4d27a; font-weight: 700; }}
            QLabel#SessionMeta {{ color: #838c9c; font-size: 8pt; }}
            QToolButton#SessionDelete {{
                color: #838c9c; border: none; font-weight: bold; padding: 2px 6px;
                background: transparent; border-radius: 4px;
            }}
            QToolButton#SessionDelete:hover {{ color: #e0625e; background: #2a3242; }}
        """

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.open_requested.emit(self.session_id)
        super().mouseDoubleClickEvent(event)


class SessionBrowser(QWidget):
    session_open = pyqtSignal(int)
    session_new = pyqtSignal()
    session_delete = pyqtSignal(int)

    def __init__(self):
        super().__init__()

        header = QFrame()
        header.setObjectName("PanelHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(10, 6, 10, 6)
        title = QLabel("HISTORY")
        title.setObjectName("PanelTitle")
        new_btn = QPushButton("+ New")
        new_btn.setObjectName("PrimaryButton")
        new_btn.clicked.connect(self.session_new)
        refresh = QPushButton("⟳")
        refresh.setToolTip("Refresh")
        refresh.setFixedWidth(34)
        refresh.clicked.connect(self.refresh)
        h.addWidget(title)
        h.addStretch(1)
        h.addWidget(new_btn)
        h.addWidget(refresh)

        self.list = QListWidget()
        self.list.setSpacing(4)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)

        self.empty_label = QLabel("No conversations yet.\nStart one below — it'll be saved here.")
        self.empty_label.setObjectName("SessionMeta")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("color:#838c9c; padding: 20px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.list, 1)

        self._summaries_provider = None  # set by MainWindow
        self._current_id: int | None = None

    def bind(self, summaries_provider) -> None:
        """summaries_provider() -> list[dict]."""
        self._summaries_provider = summaries_provider

    def set_current(self, session_id: int | None) -> None:
        self._current_id = session_id

    def refresh(self) -> None:
        if not self._summaries_provider:
            return
        self.list.clear()
        summaries = self._summaries_provider()
        self.empty_label.setVisible(not summaries)
        self.list.setVisible(bool(summaries))
        for s in summaries:
            row = _SessionRow(s, is_current=(int(s["id"]) == self._current_id))
            row.open_requested.connect(self.session_open)
            row.delete_requested.connect(self.session_delete)
            item = QListWidgetItem(self.list)
            item.setSizeHint(row.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
