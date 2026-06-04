"""Roadmap tab — the living plan the agent builds at project start and updates.

Roadmap data shape (flexible):
    {"title": "My Game",
     "milestones": [
        {"name": "Core movement", "status": "done|active|todo",
         "items": [{"text": "WASD walk", "done": true}, "jump"]}
     ]}
"""

from __future__ import annotations

import html

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .styles import COLORS


_STATUS = {
    "done":   (COLORS["ok"],    "#11251a", "✔ done"),
    "active": (COLORS["accent2"], "#241d10", "▸ in progress"),
    "todo":   (COLORS["muted"], "#141925", "○ planned"),
}


class RoadmapView(QWidget):
    def __init__(self):
        super().__init__()
        header = QFrame()
        header.setObjectName("PanelHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(10, 6, 10, 6)
        title = QLabel("ROADMAP")
        title.setObjectName("PanelTitle")
        refresh = QPushButton("⟳")
        refresh.setFixedWidth(34)
        refresh.setToolTip("Refresh")
        refresh.clicked.connect(self.refresh)
        h.addWidget(title)
        h.addStretch(1)
        h.addWidget(refresh)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self.view, 1)

        self._provider = None
        self.show_data(None)

    def bind(self, provider) -> None:
        """provider() -> roadmap dict | None."""
        self._provider = provider

    def refresh(self) -> None:
        if self._provider:
            self.show_data(self._provider())

    def show_data(self, data: dict | None) -> None:
        if not data or not data.get("milestones"):
            self.view.setHtml(
                f'<div style="color:{COLORS["muted"]};padding:24px;text-align:center;">'
                "<h3>No roadmap yet</h3>"
                "Tell the agent to build a game and it will draft a roadmap here, "
                "then check items off as it goes.</div>"
            )
            return
        self.view.setHtml(self._render(data))

    def _render(self, data: dict) -> str:
        title = html.escape(str(data.get("title", "Project Roadmap")))
        milestones = data.get("milestones", [])
        total = done = 0
        body = []
        for ms in milestones:
            name = html.escape(str(ms.get("name", "Milestone")))
            status = str(ms.get("status", "todo")).lower()
            fg, bg, label = _STATUS.get(status, _STATUS["todo"])
            chip = (
                f'<span style="background:{bg};color:{fg};border:1px solid {fg};'
                f'border-radius:8px;padding:1px 8px;font-size:8pt;">{label}</span>'
            )
            body.append(
                f'<div style="margin:10px 0 4px;"><span style="color:{COLORS["text"]};'
                f'font-weight:700;font-size:11pt;">{name}</span> &nbsp; {chip}</div>'
            )
            for it in ms.get("items", []):
                if isinstance(it, dict):
                    text = html.escape(str(it.get("text", "")))
                    is_done = bool(it.get("done"))
                else:
                    text = html.escape(str(it))
                    is_done = status == "done"
                total += 1
                done += 1 if is_done else 0
                mark = f'<span style="color:{COLORS["ok"]};">✔</span>' if is_done \
                    else f'<span style="color:{COLORS["muted"]};">○</span>'
                color = COLORS["muted"] if is_done else COLORS["text"]
                deco = "text-decoration:line-through;" if is_done else ""
                body.append(
                    f'<div style="margin-left:14px;color:{color};{deco}">{mark} {text}</div>'
                )

        pct = int(done / total * 100) if total else 0
        head = (
            f'<h2 style="color:{COLORS["accent2"]};margin-bottom:2px;">{title}</h2>'
            f'<div style="color:{COLORS["muted"]};margin-bottom:8px;">'
            f'{done}/{total} items done · {pct}%</div>'
        )
        return f'<div style="padding:10px 14px;">{head}{"".join(body)}</div>'
