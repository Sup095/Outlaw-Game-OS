"""Terminal-style log panel — colored levels, monospace, auto-scroll."""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


LEVEL_COLORS = {
    "info":  "#9aa6b2",
    "warn":  "#e0a043",
    "error": "#d65a5a",
    "ok":    "#5fb763",
    "tool":  "#d4a017",
}


class TerminalLog(QWidget):
    def __init__(self):
        super().__init__()

        header = QFrame()
        header.setObjectName("PanelHeader")
        hbox = QHBoxLayout(header)
        hbox.setContentsMargins(10, 6, 10, 6)
        title = QLabel("TERMINAL")
        title.setObjectName("PanelTitle")
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.clear)
        hbox.addWidget(title)
        hbox.addStretch(1)
        hbox.addWidget(clear_btn)

        self.view = QPlainTextEdit()
        self.view.setObjectName("TerminalLog")
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(2000)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self.view, 1)

    def append(self, level: str, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        color = LEVEL_COLORS.get(level.lower(), "#9aa6b2")
        html = (
            f'<span style="color:#5a6573">[{ts}]</span> '
            f'<span style="color:{color};font-weight:bold;">{level.upper():5}</span> '
            f'<span style="color:#dde3ea">{_escape(message)}</span>'
        )
        self.view.appendHtml(html)
        self.view.moveCursor(QTextCursor.MoveOperation.End)

    def clear(self) -> None:
        self.view.clear()


def _escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
    )
