"""Side-by-side diff viewer with one-keystroke Approve / Deny.

Renders an aligned 4-column table (old #, old line, new #, new line) with
red/green tints for removed/added lines. Enter or Y approves; Esc or N denies.
"""

from __future__ import annotations

import difflib
import html

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from .styles import COLORS


def build_diff_rows(old: str, new: str) -> list[tuple]:
    """Pure aligner. Returns rows of
    (left_no, left_text, left_tag, right_no, right_text, right_tag),
    where tag ∈ {'eq','del','add','pad'}. Kept Qt-free for unit testing.
    """
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    rows: list[tuple] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                rows.append((i1 + k + 1, old_lines[i1 + k], "eq",
                             j1 + k + 1, new_lines[j1 + k], "eq"))
        elif tag == "delete":
            for k in range(i1, i2):
                rows.append((k + 1, old_lines[k], "del", "", "", "pad"))
        elif tag == "insert":
            for k in range(j1, j2):
                rows.append(("", "", "pad", k + 1, new_lines[k], "add"))
        elif tag == "replace":
            left = [(i + 1, old_lines[i]) for i in range(i1, i2)]
            right = [(j + 1, new_lines[j]) for j in range(j1, j2)]
            for idx in range(max(len(left), len(right))):
                ln, lt = left[idx] if idx < len(left) else ("", "")
                rn, rt = right[idx] if idx < len(right) else ("", "")
                rows.append((ln, lt, "del" if lt != "" or ln != "" else "pad",
                             rn, rt, "add" if rt != "" or rn != "" else "pad"))
    return rows


def diff_stats(old: str, new: str) -> tuple[int, int]:
    """(added, removed) line counts."""
    added = removed = 0
    for _, _, lt, _, _, rt in build_diff_rows(old, new):
        if rt == "add":
            added += 1
        if lt == "del":
            removed += 1
    return added, removed


_CELL_BG = {
    "eq": "transparent",
    "del": "#2c1517",
    "add": "#13241a",
    "pad": "#0c0f16",
}
_CODE_COLOR = {
    "eq": "#c8ced8",
    "del": "#f0a8a4",
    "add": "#a6e6bd",
    "pad": "#3a4456",
}


class DiffDialog(QDialog):
    def __init__(self, path: str, old: str, new: str, kind: str = "write",
                 parent=None, abs_path: str = ""):
        super().__init__(parent)
        self.approved = False
        verb = {"write": "Write", "delete": "Delete", "tscn": "Edit scene"}.get(kind, "Change")
        self.setWindowTitle(f"Review · {verb} · {path}")
        self.resize(1180, 720)

        added, removed = diff_stats(old, new)
        target = abs_path or path
        head = QLabel(
            f'<span style="color:{COLORS["accent2"]};font-weight:700;">{verb}</span> '
            f'<span style="color:{COLORS["text"]};">{html.escape(path)}</span> &nbsp; '
            f'<span style="color:{COLORS["ok"]};">+{added}</span> '
            f'<span style="color:{COLORS["err"]};">-{removed}</span>'
            f'<br><span style="color:{COLORS["muted"]};font-size:9pt;">'
            f'writes to: {html.escape(target)}</span>'
        )
        head.setTextFormat(Qt.TextFormat.RichText)

        self.view = QTextEdit()
        self.view.setObjectName("CodeView")
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.view.setHtml(self._render(old, new, kind))

        approve_text = "✓  Approve & delete  (Enter)" if kind == "delete" \
            else "✓  Approve & write to project  (Enter)"
        approve = QPushButton(approve_text)
        approve.setObjectName("PrimaryButton")
        approve.clicked.connect(self._approve)
        deny = QPushButton("✕  Deny  (Esc)")
        deny.setObjectName("DangerButton")
        deny.clicked.connect(self.reject)

        btn_row = QHBoxLayout()
        btn_row.addWidget(QLabel("Approving saves this to your project on disk · Enter / Y = approve · Esc / N = deny"))
        btn_row.addStretch(1)
        btn_row.addWidget(deny)
        btn_row.addWidget(approve)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(head)
        layout.addWidget(self.view, 1)
        layout.addLayout(btn_row)

        for seq in ("Return", "Enter", "Y"):
            QShortcut(QKeySequence(seq), self, activated=self._approve)
        for seq in ("N",):
            QShortcut(QKeySequence(seq), self, activated=self.reject)
        approve.setDefault(True)
        approve.setFocus()

    def _approve(self) -> None:
        self.approved = True
        self.accept()

    def _render(self, old: str, new: str, kind: str) -> str:
        rows = build_diff_rows(old, new)
        out = [
            f'<table cellspacing="0" cellpadding="0" width="100%" '
            f'style="font-family:Consolas,monospace;font-size:10pt;">'
        ]
        for ln, lt, ltag, rn, rt, rtag in rows:
            out.append(
                "<tr>"
                + self._cell(ln, lt, ltag)
                + self._cell(rn, rt, rtag)
                + "</tr>"
            )
        out.append("</table>")
        return "".join(out)

    def _cell(self, no, text, tag) -> str:
        bg = _CELL_BG[tag]
        fg = _CODE_COLOR[tag]
        gutter = {"del": "−", "add": "+", "eq": " ", "pad": " "}[tag]
        safe = html.escape(text).replace(" ", "&nbsp;") if text else "&nbsp;"
        return (
            f'<td width="4%" style="color:{COLORS["muted"]};background:{bg};'
            f'text-align:right;padding-right:6px;">{no}</td>'
            f'<td width="46%" style="color:{fg};background:{bg};white-space:pre;">'
            f'<span style="color:{COLORS["muted"]};">{gutter}</span> {safe}</td>'
        )
