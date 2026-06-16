"""Design tab (Phase 14b) — the per-project game "description sheet".

A shared picture of what's being built: type/genre, who it's for, the premise,
key features, and notes. Both the user and the AI read + edit it (the AI side
lands in a later slice via a ``<<DESCRIPTION>>`` block). Stored per-project.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .styles import COLORS


# Stable keys — the AI will fill/update the same ones. A project with no sheet
# yet starts blank from this template.
TEMPLATE = {
    "title": "",
    "type": "",
    "audience": "",
    "premise": "",
    "features": "",
    "notes": "",
}

# (key, label, multiline, placeholder)
_FIELDS = [
    ("title",    "Title",           False, "The working name of the game."),
    ("type",     "Type / genre",    False, "e.g. 2D platformer, top-down RPG, puzzle…"),
    ("audience", "For / audience",  False, "Who is it for?"),
    ("premise",  "About / premise", True,  "What is the game about?"),
    ("features", "Key features",    True,  "One per line."),
    ("notes",    "Notes",           True,  "Anything else you or the AI should remember."),
]


class DescriptionView(QWidget):
    def __init__(self):
        super().__init__()
        self._get = None
        self._save = None
        self._editors: dict[str, QWidget] = {}

        header = QFrame()
        header.setObjectName("PanelHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(10, 6, 10, 6)
        title = QLabel("DESIGN SHEET")
        title.setObjectName("PanelTitle")
        reload_btn = QPushButton("⟳")
        reload_btn.setFixedWidth(34)
        reload_btn.setToolTip("Reload from project")
        reload_btn.clicked.connect(self.refresh)
        self._save_btn = QPushButton("Save")
        self._save_btn.clicked.connect(self._on_save)
        h.addWidget(title)
        h.addStretch(1)
        h.addWidget(reload_btn)
        h.addWidget(self._save_btn)

        form_host = QWidget()
        form = QVBoxLayout(form_host)
        form.setContentsMargins(14, 10, 14, 14)
        form.setSpacing(8)
        for key, label, multiline, hint in _FIELDS:
            lbl = QLabel(label)
            lbl.setStyleSheet(f"color:{COLORS['accent2']};font-weight:700;")
            form.addWidget(lbl)
            if multiline:
                ed: QWidget = QPlainTextEdit()
                ed.setPlaceholderText(hint)
                ed.setFixedHeight(82)
            else:
                ed = QLineEdit()
                ed.setPlaceholderText(hint)
            self._editors[key] = ed
            form.addWidget(ed)
        form.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_host)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(scroll, 1)

    def bind(self, get_provider, save_provider) -> None:
        """get_provider() -> dict | None ; save_provider(dict) -> None."""
        self._get = get_provider
        self._save = save_provider
        self.refresh()

    def refresh(self) -> None:
        data = (self._get() if self._get else None) or {}
        for key, ed in self._editors.items():
            val = str(data.get(key, "") or "")
            if isinstance(ed, QPlainTextEdit):
                ed.setPlainText(val)
            else:
                ed.setText(val)

    def show_data(self, _data: dict | None = None) -> None:
        # Hook for when the AI updates the sheet (future slice): just reload from
        # the store so the on-screen form reflects the saved sheet.
        self.refresh()

    def _collect(self) -> dict:
        out = dict(TEMPLATE)
        for key, ed in self._editors.items():
            out[key] = (ed.toPlainText() if isinstance(ed, QPlainTextEdit) else ed.text()).strip()
        return out

    def _on_save(self) -> None:
        if self._save:
            self._save(self._collect())
